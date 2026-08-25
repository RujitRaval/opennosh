import { ApiProblem } from "@/lib/api/domain/problem";
import type {
  ContributionCapability,
  ContributionFieldName,
  ContributionFieldPatch,
  ContributionFields,
  ContributionStage,
  LocalContributionDraft,
  PendingContributionField,
} from "@/lib/contributions/domain";
import { serializeLocalContributionDraft } from "@/lib/contributions/local-draft";

export const contributionAutosaveIdleMs = 750;
export const contributionAutosaveMaxWaitMs = 5_000;
export const contributionAutosaveQueueLimit = 25;

type Timer = ReturnType<typeof setTimeout>;

export type ContributionAutosaveClock = {
  now(): number;
  setTimeout(callback: () => void, delay: number): Timer;
  clearTimeout(timer: Timer): void;
};

export type ContributionPatchInput = {
  expected_draft_version: number;
  operation_id: string;
  patches: Array<{
    field: ContributionFieldName;
    value: ContributionFields[ContributionFieldName];
    base_value: ContributionFields[ContributionFieldName];
    base_version: number;
  }>;
  requested_stage?: ContributionStage;
};

export type ContributionAutosaveMetric = {
  name:
    | "patches_per_active_minute"
    | "coalescing_ratio"
    | "payload_bytes"
    | "acknowledgement_ms"
    | "conflicts_per_active_minute"
    | "offline_queue_age_ms";
  value: number;
};

export type ContributionAutosaveDependencies = {
  storage: Pick<Storage, "setItem">;
  storageKey: string;
  clock?: ContributionAutosaveClock;
  operationId?: () => string;
  writerId?: () => string;
  patch(draftId: string, input: ContributionPatchInput): Promise<ContributionCapability>;
  reload(draftId: string, stage?: ContributionStage): Promise<ContributionCapability>;
  onDraft(draft: LocalContributionDraft): void;
  onMetric?(metric: ContributionAutosaveMetric): void;
};

const browserClock: ContributionAutosaveClock = {
  now: () => Date.now(),
  setTimeout: (callback, delay) => setTimeout(callback, delay),
  clearTimeout: (timer) => clearTimeout(timer),
};

function patchFromPending(
  field: ContributionFieldName,
  pending: PendingContributionField,
): ContributionFieldPatch {
  return {
    field,
    value: pending.value,
    baseValue: pending.baseValue,
    baseVersion: pending.baseVersion,
  };
}

export function versionedFieldPatch(patch: ContributionFieldPatch) {
  if (patch.baseVersion === undefined || patch.baseValue === undefined) {
    throw new Error("Versioned contribution patches require a base value and version.");
  }
  return {
    field: patch.field,
    value: patch.value,
    base_value: patch.baseValue,
    base_version: patch.baseVersion,
  };
}

export function draftFromCapability(
  capability: ContributionCapability,
  stored: LocalContributionDraft | null,
): LocalContributionDraft {
  const matchingStored = stored?.clientDraftId === capability.draftId ? stored : null;
  const pendingFields = matchingStored
    ? { ...matchingStored.pendingFields }
    : {};
  const inFlightFields = new Set(
    matchingStored?.inFlightOperation?.patches.map((patch) => patch.field) ?? [],
  );
  const localFields = { ...capability.fields };
  for (const field of Object.keys(capability.fields) as ContributionFieldName[]) {
    if (!matchingStored) continue;
    const locallyChanged = matchingStored.serverFields === null
      ? !Object.is(matchingStored.fields[field], capability.fields[field])
      : !Object.is(matchingStored.fields[field], matchingStored.serverFields[field]);
    if (!locallyChanged) continue;
    localFields[field] = matchingStored.fields[field] as never;
    if (pendingFields[field] || inFlightFields.has(field)) continue;
    pendingFields[field] = {
      value: matchingStored.fields[field],
      baseValue: capability.fields[field],
      baseVersion: capability.draftVersion,
      editedAt: matchingStored.savedAt,
    };
  }
  const hasPending = Object.keys(pendingFields).length > 0;
  return {
    ...(matchingStored ?? {
      schemaVersion: "2",
      clientDraftId: capability.draftId,
      duplicateCandidates: [],
      duplicateQuery: null,
      pendingSince: null,
      inFlightOperation: null,
      storageRevision: 0,
      storageWriterId: "",
      conflictFields: [],
      repairReason: null,
    }),
    schemaVersion: "2",
    clientDraftId: capability.draftId,
    fields: { ...capability.fields, ...localFields },
    duplicateCandidates: [...capability.duplicateCandidates],
    savedAt: matchingStored?.savedAt ?? capability.savedAt,
    saveState: matchingStored?.conflictFields.length
      ? "conflict"
      : matchingStored?.inFlightOperation
        ? "offline"
        : hasPending ? "sync_scheduled" : "synced",
    serverDraftId: capability.draftId,
    serverVersion: capability.draftVersion,
    serverFields: { ...capability.fields },
    pendingFields,
    pendingSince: hasPending
      ? matchingStored?.pendingSince ?? matchingStored?.savedAt ?? capability.savedAt
      : null,
  };
}

export class ContributionAutosave {
  private draft: LocalContributionDraft;
  private readonly dependencies: ContributionAutosaveDependencies;
  private readonly clock: ContributionAutosaveClock;
  private idleTimer: Timer | null = null;
  private maxTimer: Timer | null = null;
  private followupTimer: Timer | null = null;
  private disposed = false;
  private activeRequest: Promise<void> | null = null;
  private editCount = 0;
  private patchCount = 0;
  private conflictCount = 0;
  private editingStartedAt: number | null = null;
  private readonly writerId: string;

  constructor(draft: LocalContributionDraft, dependencies: ContributionAutosaveDependencies) {
    this.draft = draft;
    this.dependencies = dependencies;
    this.clock = dependencies.clock ?? browserClock;
    this.writerId = dependencies.writerId?.() ?? crypto.randomUUID();
    if (draft.pendingSince && !draft.inFlightOperation) this.schedule();
  }

  current(): LocalContributionDraft {
    return this.draft;
  }

  edit<K extends ContributionFieldName>(
    field: K,
    value: ContributionFields[K],
    extraFields: Partial<ContributionFields> = {},
    metadata: Partial<Pick<LocalContributionDraft, "duplicateCandidates" | "duplicateQuery">> = {},
  ): boolean {
    const changed = { [field]: value, ...extraFields } as Partial<ContributionFields>;
    const now = this.clock.now();
    const editedAt = new Date(now).toISOString();
    let next: LocalContributionDraft = {
      ...this.draft,
      fields: { ...this.draft.fields, ...changed },
      ...metadata,
      savedAt: editedAt,
      repairReason: null,
    };
    if (this.draft.serverDraftId && this.draft.serverVersion && this.draft.serverFields) {
      const pending = { ...this.draft.pendingFields };
      for (const changedField of Object.keys(changed) as ContributionFieldName[]) {
        const previous = pending[changedField];
        const resolvesConflict = this.draft.conflictFields.includes(changedField);
        pending[changedField] = {
          value: next.fields[changedField],
          baseValue: resolvesConflict
            ? this.draft.serverFields[changedField]
            : previous?.baseValue ?? this.draft.serverFields[changedField],
          baseVersion: resolvesConflict
            ? this.draft.serverVersion
            : previous?.baseVersion ?? this.draft.serverVersion,
          editedAt,
        };
      }
      if (Object.keys(pending).length > contributionAutosaveQueueLimit) {
        next = { ...next, saveState: "repair_required", repairReason: "schema_changed" };
        this.publishWithoutWrite(next);
        return false;
      }
      next = {
        ...next,
        pendingFields: pending,
        pendingSince: this.draft.pendingSince ?? editedAt,
      };
    }
    const resolvedConflicts = new Set(this.draft.conflictFields);
    for (const changedField of Object.keys(changed) as ContributionFieldName[]) {
      resolvedConflicts.delete(changedField);
    }
    next = { ...next, conflictFields: [...resolvedConflicts] };
    if (!this.persist({
      ...next,
      saveState: resolvedConflicts.size > 0 ? "conflict" : "saved_on_device",
    })) return false;
    this.editCount += Object.keys(changed).length;
    this.editingStartedAt ??= now;
    if (next.serverDraftId && resolvedConflicts.size === 0) {
      this.publishWithoutWrite({ ...this.draft, saveState: "sync_scheduled" });
      this.schedule();
    }
    return true;
  }

  resolveConflict(field: ContributionFieldName, choice: "local" | "server"): boolean {
    if (!this.draft.conflictFields.includes(field)
      || !this.draft.serverFields || !this.draft.serverVersion) return false;
    const pendingFields = { ...this.draft.pendingFields };
    const fields = { ...this.draft.fields };
    const now = new Date(this.clock.now()).toISOString();
    if (choice === "server") {
      fields[field] = this.draft.serverFields[field] as never;
      delete pendingFields[field];
    } else {
      pendingFields[field] = {
        value: fields[field],
        baseValue: this.draft.serverFields[field],
        baseVersion: this.draft.serverVersion,
        editedAt: now,
      };
    }
    const conflictFields = this.draft.conflictFields.filter((item) => item !== field);
    const hasPending = Object.keys(pendingFields).length > 0;
    if (!this.persist({
      ...this.draft,
      fields,
      pendingFields,
      pendingSince: hasPending ? this.draft.pendingSince ?? now : null,
      conflictFields,
      savedAt: now,
      saveState: conflictFields.length > 0
        ? "conflict" : hasPending ? "saved_on_device" : "synced",
    })) return false;
    if (conflictFields.length === 0 && hasPending) {
      this.publishWithoutWrite({ ...this.draft, saveState: "sync_scheduled" });
      this.schedule();
    }
    return true;
  }

  flush(requestedStage?: ContributionStage): Promise<void> {
    if (this.disposed || this.draft.conflictFields.length > 0) return Promise.resolve();
    this.clearTimers();
    if (this.draft.inFlightOperation) return this.activeRequest ?? Promise.resolve();
    const entries = Object.entries(this.draft.pendingFields) as Array<
      [ContributionFieldName, PendingContributionField]
    >;
    if (!this.draft.serverDraftId || !this.draft.serverVersion || entries.length === 0) {
      return Promise.resolve();
    }
    const patches = entries.map(([field, pending]) => patchFromPending(field, pending));
    const operation = {
      operationId: this.dependencies.operationId?.() ?? crypto.randomUUID(),
      expectedDraftVersion: this.draft.serverVersion,
      patches,
      requestedStage: requestedStage ?? null,
      sentAt: new Date(this.clock.now()).toISOString(),
    };
    if (!this.persist({
      ...this.draft,
      pendingFields: {},
      pendingSince: null,
      inFlightOperation: operation,
      saveState: "syncing",
    })) return Promise.resolve();
    return this.startSend();
  }

  retry(): Promise<void> {
    if (this.draft.saveState === "conflict") return Promise.resolve();
    if (this.draft.saveState === "repair_required" && this.draft.repairReason !== "session_expired") {
      return Promise.resolve();
    }
    if (this.draft.inFlightOperation) return this.startSend();
    return this.flush();
  }

  async settle(requestedStage?: ContributionStage): Promise<boolean> {
    while (!this.disposed) {
      await this.flush(requestedStage);
      if (["offline", "conflict", "repair_required"].includes(this.draft.saveState)) return false;
      if (!this.draft.inFlightOperation && Object.keys(this.draft.pendingFields).length === 0) {
        return true;
      }
    }
    return false;
  }

  acceptExternalDraft(external: LocalContributionDraft): void {
    if (external.clientDraftId !== this.draft.clientDraftId) return;
    if (external.storageWriterId === this.writerId) return;
    if (external.storageRevision < this.draft.storageRevision) return;
    const mergedPending = { ...external.pendingFields };
    let contributesLocalState = external.storageRevision === this.draft.storageRevision;
    for (const [field, local] of Object.entries(this.draft.pendingFields) as Array<
      [ContributionFieldName, PendingContributionField]
    >) {
      const remote = mergedPending[field];
      const externalInFlight = external.inFlightOperation?.patches.find(
        (patch) => patch.field === field,
      );
      const alreadyRepresented = !remote && (
        Object.is(externalInFlight?.value, local.value)
        || (external.serverVersion !== null
          && external.serverVersion > local.baseVersion
          && Object.is(external.serverFields?.[field] ?? external.fields[field], local.value))
      );
      if (alreadyRepresented) continue;
      const localWins = !remote
        || local.editedAt > remote.editedAt
        || (local.editedAt === remote.editedAt && this.writerId > external.storageWriterId);
      if (localWins) {
        mergedPending[field] = local;
        if (remote !== local) contributesLocalState = true;
      }
    }
    const mergedFields = { ...external.fields };
    for (const [field, pending] of Object.entries(mergedPending) as Array<
      [ContributionFieldName, PendingContributionField]
    >) {
      mergedFields[field] = pending.value as never;
    }
    const conflictFields = [...new Set([
      ...external.conflictFields,
      ...this.draft.conflictFields,
    ])];
    const merged: LocalContributionDraft = {
      ...external,
      fields: mergedFields,
      pendingFields: mergedPending,
      pendingSince: Object.keys(mergedPending).length > 0
        ? [external.pendingSince, this.draft.pendingSince].filter(Boolean).sort()[0] ?? external.savedAt
        : null,
      conflictFields,
      saveState: conflictFields.length > 0
        ? "conflict"
        : external.inFlightOperation ? external.saveState
          : Object.keys(mergedPending).length > 0 ? "sync_scheduled" : external.saveState,
    };
    if (contributesLocalState) {
      this.persist(merged);
    } else {
      this.draft = merged;
      this.dependencies.onDraft(merged);
    }
    if (merged.pendingSince && !merged.inFlightOperation && merged.conflictFields.length === 0) {
      this.schedule();
    }
  }

  dispose(): void {
    this.disposed = true;
    this.clearTimers();
  }

  private schedule(): void {
    if (this.disposed || this.draft.inFlightOperation || this.draft.conflictFields.length > 0
      || !this.draft.pendingSince) return;
    if (this.idleTimer) this.clock.clearTimeout(this.idleTimer);
    this.idleTimer = this.clock.setTimeout(() => void this.flush(), contributionAutosaveIdleMs);
    if (!this.maxTimer) {
      const age = Math.max(0, this.clock.now() - Date.parse(this.draft.pendingSince));
      this.maxTimer = this.clock.setTimeout(
        () => void this.flush(),
        Math.max(0, contributionAutosaveMaxWaitMs - age),
      );
    }
  }

  private clearTimers(): void {
    if (this.idleTimer) this.clock.clearTimeout(this.idleTimer);
    if (this.maxTimer) this.clock.clearTimeout(this.maxTimer);
    if (this.followupTimer) this.clock.clearTimeout(this.followupTimer);
    this.idleTimer = null;
    this.maxTimer = null;
    this.followupTimer = null;
  }

  private queueFlush(stage?: ContributionStage): void {
    if (this.disposed) return;
    if (this.followupTimer) this.clock.clearTimeout(this.followupTimer);
    this.followupTimer = this.clock.setTimeout(() => {
      this.followupTimer = null;
      void this.flush(stage);
    }, 0);
  }

  private async sendInFlight(): Promise<void> {
    const operation = this.draft.inFlightOperation;
    const draftId = this.draft.serverDraftId;
    if (!operation || !draftId || this.disposed) return;
    try {
      const input: ContributionPatchInput = {
        expected_draft_version: operation.expectedDraftVersion,
        operation_id: operation.operationId,
        patches: operation.patches.map(versionedFieldPatch),
        ...(operation.requestedStage ? { requested_stage: operation.requestedStage } : {}),
      };
      this.patchCount += input.patches.length;
      this.metric("payload_bytes", new TextEncoder().encode(JSON.stringify(input)).byteLength);
      const startedAt = this.clock.now();
      const capability = await this.dependencies.patch(draftId, input);
      if (this.disposed || this.draft.inFlightOperation?.operationId !== operation.operationId) return;
      if (capability.draftVersion < operation.expectedDraftVersion + 1) {
        this.persist({ ...this.draft, saveState: "repair_required", repairReason: "schema_changed" });
        return;
      }
      this.metric("acknowledgement_ms", Math.max(0, this.clock.now() - startedAt));
      const pendingFields = { ...this.draft.pendingFields };
      for (const field of Object.keys(pendingFields) as ContributionFieldName[]) {
        const pending = pendingFields[field]!;
        pendingFields[field] = {
          ...pending,
          baseValue: capability.fields[field],
          baseVersion: capability.draftVersion,
        };
      }
      const hasPending = Object.keys(pendingFields).length > 0;
      this.persist({
        ...this.draft,
        fields: { ...capability.fields, ...Object.fromEntries(
          Object.entries(pendingFields).map(([field, pending]) => [field, pending?.value]),
        ) },
        duplicateCandidates: [...capability.duplicateCandidates],
        serverVersion: capability.draftVersion,
        serverFields: { ...capability.fields },
        pendingFields,
        pendingSince: hasPending ? this.draft.pendingSince ?? new Date(this.clock.now()).toISOString() : null,
        inFlightOperation: null,
        saveState: hasPending ? "sync_scheduled" : "synced",
        conflictFields: [],
        repairReason: null,
      });
      this.emitRateMetrics();
      if (hasPending) {
        this.queueFlush();
      }
    } catch (caught) {
      if (this.disposed || this.draft.inFlightOperation?.operationId !== operation.operationId) return;
      if (caught instanceof ApiProblem && caught.kind === "authentication-required") {
        this.persist({ ...this.draft, saveState: "repair_required", repairReason: "session_expired" });
        return;
      }
      if (caught instanceof ApiProblem && ["conflict", "stale"].includes(caught.kind)) {
        this.conflictCount += 1;
        this.emitRateMetrics();
        await this.recoverConflict(draftId, operation.requestedStage ?? undefined);
        return;
      }
      if (
        caught instanceof ApiProblem
        && !["network", "retryable", "rate-limited"].includes(caught.kind)
      ) {
        this.requeueInFlight("server_rejected");
        return;
      }
      const oldest = this.draft.pendingSince ?? operation.sentAt;
      this.metric("offline_queue_age_ms", Math.max(0, this.clock.now() - Date.parse(oldest)));
      this.persist({ ...this.draft, saveState: "offline" });
    }
  }

  private requeueInFlight(reason: "server_rejected"): void {
    const operation = this.draft.inFlightOperation;
    if (!operation) return;
    const pendingFields = { ...this.draft.pendingFields };
    for (const patch of operation.patches) {
      if (pendingFields[patch.field]) continue;
      pendingFields[patch.field] = {
        value: patch.value,
        baseValue: patch.baseValue!,
        baseVersion: patch.baseVersion!,
        editedAt: operation.sentAt,
      };
    }
    this.persist({
      ...this.draft,
      pendingFields,
      pendingSince: this.draft.pendingSince ?? operation.sentAt,
      inFlightOperation: null,
      saveState: "repair_required",
      repairReason: reason,
    });
  }

  private startSend(): Promise<void> {
    if (this.activeRequest) return this.activeRequest;
    this.activeRequest = this.sendInFlight().finally(() => {
      this.activeRequest = null;
    });
    return this.activeRequest;
  }

  private async recoverConflict(draftId: string, stage?: ContributionStage): Promise<void> {
    try {
      const capability = await this.dependencies.reload(draftId, stage);
      if (this.disposed || !this.draft.inFlightOperation) return;
      const operation = this.draft.inFlightOperation;
      const requeued = { ...this.draft.pendingFields };
      const conflictFields: ContributionFieldName[] = [];
      for (const patch of operation.patches) {
        const conflict = !Object.is(capability.fields[patch.field], patch.baseValue);
        if (conflict) conflictFields.push(patch.field);
        const existing = requeued[patch.field];
        if (!existing) {
          requeued[patch.field] = {
            value: patch.value,
            baseValue: conflict ? patch.baseValue! : capability.fields[patch.field],
            baseVersion: conflict ? patch.baseVersion! : capability.draftVersion,
            editedAt: operation.sentAt,
          };
        }
      }
      this.persist({
        ...this.draft,
        fields: { ...capability.fields, ...Object.fromEntries(
          Object.entries(requeued).map(([field, pending]) => [field, pending?.value]),
        ) },
        serverVersion: capability.draftVersion,
        serverFields: { ...capability.fields },
        duplicateCandidates: [...capability.duplicateCandidates],
        pendingFields: requeued,
        pendingSince: this.draft.pendingSince ?? operation.sentAt,
        inFlightOperation: null,
        conflictFields,
        saveState: conflictFields.length > 0 ? "conflict" : "sync_scheduled",
      });
      if (conflictFields.length === 0) {
        this.queueFlush(stage);
      }
    } catch {
      this.persist({ ...this.draft, saveState: "offline" });
    }
  }

  private persist(next: LocalContributionDraft): boolean {
    const revisioned = {
      ...next,
      storageRevision: this.draft.storageRevision + 1,
      storageWriterId: this.writerId,
    };
    try {
      this.dependencies.storage.setItem(this.dependencies.storageKey, serializeLocalContributionDraft(revisioned));
      this.draft = revisioned;
      if (!this.disposed) this.dependencies.onDraft(revisioned);
      return true;
    } catch {
      this.publishWithoutWrite({
        ...revisioned,
        saveState: "repair_required",
        repairReason: "storage_failed",
      });
      return false;
    }
  }

  private publishWithoutWrite(next: LocalContributionDraft): void {
    this.draft = next;
    if (!this.disposed) this.dependencies.onDraft(next);
  }

  private metric(name: ContributionAutosaveMetric["name"], value: number): void {
    this.dependencies.onMetric?.({ name, value });
  }

  private emitRateMetrics(): void {
    if (this.editingStartedAt === null || this.editCount === 0) return;
    const activeMinutes = Math.max((this.clock.now() - this.editingStartedAt) / 60_000, 1 / 60);
    this.metric("patches_per_active_minute", this.patchCount / activeMinutes);
    this.metric("conflicts_per_active_minute", this.conflictCount / activeMinutes);
    this.metric("coalescing_ratio", Math.max(0, 1 - this.patchCount / this.editCount));
  }
}

export function browserAutosaveMetric(metric: ContributionAutosaveMetric): void {
  window.dispatchEvent(new CustomEvent("opennosh:autosave-metric", { detail: metric }));
}
