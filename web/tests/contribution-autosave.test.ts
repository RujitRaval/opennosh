import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { ApiProblem } from "@/lib/api/domain/problem";
import {
  ContributionAutosave,
  contributionAutosaveIdleMs,
  contributionAutosaveMaxWaitMs,
  draftFromCapability,
  type ContributionAutosaveMetric,
  type ContributionPatchInput,
} from "@/lib/contributions/autosave";
import type {
  ContributionCapability,
  ContributionFields,
  LocalContributionDraft,
} from "@/lib/contributions/domain";
import {
  emptyContributionFields,
  contributionDraftMaxBytes,
  newLocalContributionDraft,
  readLocalContributionDraft,
  serializeLocalContributionDraft,
} from "@/lib/contributions/local-draft";

const start = new Date("2026-08-24T12:00:00Z");

function capability(
  version: number,
  fields: Partial<ContributionFields> = {},
): ContributionCapability {
  return {
    draftId: "server-draft",
    draftVersion: version,
    reviewState: "draft",
    completedStages: [],
    accessibleStages: ["evidence"],
    blockers: [],
    nextSafeStage: "evidence",
    requestedStage: "evidence",
    resolvedStage: "evidence",
    repairReason: null,
    savedAt: new Date(start.getTime() + version * 1_000).toISOString(),
    fields: { ...emptyContributionFields, ...fields },
    duplicateCandidates: [],
    receipt: null,
  };
}

function storage() {
  const values = new Map<string, string>();
  return {
    values,
    api: {
      setItem(key: string, value: string) { values.set(key, value); },
    },
  };
}

function remoteDraft(fields: Partial<ContributionFields> = {}): LocalContributionDraft {
  return draftFromCapability(capability(1, fields), null);
}

function deferred<T>() {
  let resolve!: (value: T) => void;
  let reject!: (reason: unknown) => void;
  const promise = new Promise<T>((yes, no) => {
    resolve = yes;
    reject = no;
  });
  return { promise, resolve, reject };
}

beforeEach(() => {
  vi.useFakeTimers();
  vi.setSystemTime(start);
});

afterEach(() => {
  vi.useRealTimers();
});

describe("contribution autosave", () => {
  it("persists immediately, coalesces rapid typing, and starts after 750 ms idle", async () => {
    const local = storage();
    const patch = vi.fn().mockResolvedValue(capability(2, { name: "Dal" }));
    let visible = remoteDraft();
    const autosave = new ContributionAutosave(visible, {
      storage: local.api,
      storageKey: "draft",
      operationId: () => "operation-1",
      patch,
      reload: vi.fn(),
      onDraft: (draft) => { visible = draft; },
    });

    autosave.edit("name", "D");
    autosave.edit("name", "Da");
    autosave.edit("name", "Dal");

    expect(JSON.parse(local.values.get("draft")!).fields.name).toBe("Dal");
    expect(visible.saveState).toBe("sync_scheduled");
    expect(patch).not.toHaveBeenCalled();
    await vi.advanceTimersByTimeAsync(contributionAutosaveIdleMs - 1);
    expect(patch).not.toHaveBeenCalled();
    await vi.advanceTimersByTimeAsync(1);

    expect(patch).toHaveBeenCalledTimes(1);
    expect(patch.mock.calls[0]?.[1]).toMatchObject({
      expected_draft_version: 1,
      operation_id: "operation-1",
      patches: [{ field: "name", value: "Dal", base_value: "", base_version: 1 }],
    });
    expect(visible.saveState).toBe("synced");
  });

  it("enforces the five-second maximum wait while typing continues", async () => {
    const patch = vi.fn().mockResolvedValue(capability(2, { name: "abcdefghij" }));
    const autosave = new ContributionAutosave(remoteDraft(), {
      storage: storage().api,
      storageKey: "draft",
      patch,
      reload: vi.fn(),
      onDraft: vi.fn(),
    });

    for (let index = 0; index < 10; index += 1) {
      autosave.edit("name", "abcdefghij".slice(0, index + 1));
      await vi.advanceTimersByTimeAsync(500);
    }
    expect(patch).toHaveBeenCalledTimes(1);
    expect(vi.getMockedSystemTime()!.getTime() - start.getTime()).toBe(
      contributionAutosaveMaxWaitMs,
    );
  });

  it("serializes requests and coalesces edits behind a slow response", async () => {
    const first = deferred<ContributionCapability>();
    const patch = vi.fn()
      .mockReturnValueOnce(first.promise)
      .mockResolvedValueOnce(capability(3, { name: "Newer", category: "meal" }));
    let visible = remoteDraft();
    const autosave = new ContributionAutosave(visible, {
      storage: storage().api,
      storageKey: "draft",
      operationId: vi.fn()
        .mockReturnValueOnce("operation-1")
        .mockReturnValueOnce("operation-2"),
      patch,
      reload: vi.fn(),
      onDraft: (draft) => { visible = draft; },
    });

    autosave.edit("name", "First");
    await vi.advanceTimersByTimeAsync(contributionAutosaveIdleMs);
    autosave.edit("name", "Newer");
    autosave.edit("category", "meal");
    await vi.advanceTimersByTimeAsync(contributionAutosaveIdleMs);
    expect(patch).toHaveBeenCalledTimes(1);

    first.resolve(capability(2, { name: "First" }));
    await first.promise;
    await vi.runAllTimersAsync();

    expect(patch).toHaveBeenCalledTimes(2);
    expect(patch.mock.calls[1]?.[1]).toMatchObject({
      expected_draft_version: 2,
      operation_id: "operation-2",
      patches: expect.arrayContaining([
        { field: "name", value: "Newer", base_value: "First", base_version: 2 },
        { field: "category", value: "meal", base_value: "", base_version: 2 },
      ]),
    });
    expect(visible.saveState).toBe("synced");
  });

  it("lets a stage handoff await every batch queued behind the active request", async () => {
    const first = deferred<ContributionCapability>();
    const patch = vi.fn()
      .mockReturnValueOnce(first.promise)
      .mockResolvedValueOnce(capability(3, { name: "First", category: "meal" }));
    const autosave = new ContributionAutosave(remoteDraft(), {
      storage: storage().api,
      storageKey: "draft",
      patch,
      reload: vi.fn(),
      onDraft: vi.fn(),
    });
    autosave.edit("name", "First");
    await vi.advanceTimersByTimeAsync(contributionAutosaveIdleMs);
    autosave.edit("category", "meal");
    const settled = autosave.settle("review");
    first.resolve(capability(2, { name: "First" }));
    await first.promise;
    await vi.runAllTimersAsync();
    await settled;

    expect(patch).toHaveBeenCalledTimes(2);
    expect(patch.mock.calls[1]?.[1]).toMatchObject({ requested_stage: "review" });
    expect(autosave.current().saveState).toBe("synced");
  });

  it("retries an unknown outcome with the same operation and payload", async () => {
    const network = new ApiProblem("offline", "network", "unavailable");
    const patch = vi.fn()
      .mockRejectedValueOnce(network)
      .mockResolvedValueOnce(capability(2, { name: "Dal" }));
    let visible = remoteDraft();
    const autosave = new ContributionAutosave(visible, {
      storage: storage().api,
      storageKey: "draft",
      operationId: () => "stable-operation",
      patch,
      reload: vi.fn(),
      onDraft: (draft) => { visible = draft; },
    });

    autosave.edit("name", "Dal");
    await vi.advanceTimersByTimeAsync(contributionAutosaveIdleMs);
    expect(visible.saveState).toBe("offline");
    const firstPayload = patch.mock.calls[0]?.[1] as ContributionPatchInput;

    await autosave.retry();
    expect(patch.mock.calls[1]?.[1]).toEqual(firstPayload);
    expect(visible.saveState).toBe("synced");
  });

  it("does not report a server handoff ready while an unknown outcome remains offline", async () => {
    const autosave = new ContributionAutosave(remoteDraft(), {
      storage: storage().api,
      storageKey: "draft",
      patch: vi.fn().mockRejectedValue(new ApiProblem("offline", "network", "unavailable")),
      reload: vi.fn(),
      onDraft: vi.fn(),
    });
    autosave.edit("name", "Device-only");
    expect(await autosave.settle("review")).toBe(false);
    expect(autosave.current().saveState).toBe("offline");
  });

  it("requires repair when the server acknowledges without advancing the version", async () => {
    const autosave = new ContributionAutosave(remoteDraft(), {
      storage: storage().api,
      storageKey: "draft",
      patch: vi.fn().mockResolvedValue(capability(1, { name: "Dal" })),
      reload: vi.fn(),
      onDraft: vi.fn(),
    });
    autosave.edit("name", "Dal");
    await vi.advanceTimersByTimeAsync(contributionAutosaveIdleMs);
    expect(autosave.current()).toMatchObject({
      saveState: "repair_required",
      repairReason: "schema_changed",
      inFlightOperation: { expectedDraftVersion: 1 },
      fields: { name: "Dal" },
    });
  });

  it("requeues terminal server rejections and preserves reload failures for retry", async () => {
    const rejected = new ContributionAutosave(remoteDraft(), {
      storage: storage().api,
      storageKey: "draft",
      patch: vi.fn().mockRejectedValue(
        new ApiProblem("invalid", "invalid-field", "request", 422),
      ),
      reload: vi.fn(),
      onDraft: vi.fn(),
    });
    rejected.edit("name", "Device copy");
    await vi.advanceTimersByTimeAsync(contributionAutosaveIdleMs);
    expect(rejected.current()).toMatchObject({
      saveState: "repair_required",
      repairReason: "server_rejected",
      inFlightOperation: null,
      fields: { name: "Device copy" },
      pendingFields: { name: { value: "Device copy" } },
    });

    const conflict = new ApiProblem("changed", "conflict", "request", 409);
    const reloadFailed = new ContributionAutosave(remoteDraft(), {
      storage: storage().api,
      storageKey: "draft",
      patch: vi.fn().mockRejectedValue(conflict),
      reload: vi.fn().mockRejectedValue(new ApiProblem("offline", "network", "unavailable")),
      onDraft: vi.fn(),
    });
    reloadFailed.edit("name", "Still durable");
    await vi.advanceTimersByTimeAsync(contributionAutosaveIdleMs);
    expect(reloadFailed.current()).toMatchObject({
      saveState: "offline",
      fields: { name: "Still durable" },
      inFlightOperation: { patches: [{ field: "name", value: "Still durable" }] },
    });
  });

  it("rebases disjoint device edits but preserves a same-field conflict", async () => {
    const conflict = new ApiProblem("changed", "conflict", "request", 409);
    const disjointPatch = vi.fn()
      .mockRejectedValueOnce(conflict)
      .mockResolvedValueOnce(capability(3, { name: "Dal", category: "meal" }));
    let disjoint = remoteDraft();
    const disjointAutosave = new ContributionAutosave(disjoint, {
      storage: storage().api,
      storageKey: "draft",
      patch: disjointPatch,
      reload: vi.fn().mockResolvedValue(capability(2, { category: "meal" })),
      onDraft: (draft) => { disjoint = draft; },
    });
    disjointAutosave.edit("name", "Dal");
    await vi.advanceTimersByTimeAsync(contributionAutosaveIdleMs);
    await vi.runAllTimersAsync();
    expect(disjointPatch).toHaveBeenCalledTimes(2);
    expect(disjoint.saveState).toBe("synced");

    const sameFieldPatch = vi.fn().mockRejectedValue(conflict);
    let sameField = remoteDraft();
    const sameFieldAutosave = new ContributionAutosave(sameField, {
      storage: storage().api,
      storageKey: "draft",
      patch: sameFieldPatch,
      reload: vi.fn().mockResolvedValue(capability(2, { name: "Other device" })),
      onDraft: (draft) => { sameField = draft; },
    });
    sameFieldAutosave.edit("name", "My device");
    await vi.advanceTimersByTimeAsync(contributionAutosaveIdleMs);
    expect(sameField.saveState).toBe("conflict");
    expect(sameField.fields.name).toBe("My device");
    expect(sameField.serverFields?.name).toBe("Other device");
  });

  it("keeps session, storage, schema-age, and cross-tab failures repair-visible", async () => {
    let sessionDraft = remoteDraft();
    const sessionAutosave = new ContributionAutosave(sessionDraft, {
      storage: storage().api,
      storageKey: "draft",
      patch: vi.fn().mockRejectedValue(
        new ApiProblem("sign in", "authentication-required", "request", 401),
      ),
      reload: vi.fn(),
      onDraft: (draft) => { sessionDraft = draft; },
    });
    sessionAutosave.edit("name", "Dal");
    await vi.advanceTimersByTimeAsync(contributionAutosaveIdleMs);
    expect(sessionDraft).toMatchObject({
      saveState: "repair_required",
      repairReason: "session_expired",
      fields: { name: "Dal" },
    });

    let failed = remoteDraft();
    const failedAutosave = new ContributionAutosave(failed, {
      storage: { setItem: () => { throw new Error("quota"); } },
      storageKey: "draft",
      patch: vi.fn(),
      reload: vi.fn(),
      onDraft: (draft) => { failed = draft; },
    });
    expect(failedAutosave.edit("name", "Preserved in memory")).toBe(false);
    expect(failed.saveState).toBe("repair_required");
    expect(failed.repairReason).toBe("storage_failed");

    const expired = remoteDraft();
    expired.pendingSince = new Date(start.getTime() - 8 * 24 * 60 * 60 * 1_000).toISOString();
    expired.pendingFields.name = {
      value: "Old",
      baseValue: "",
      baseVersion: 1,
      editedAt: expired.pendingSince,
    };
    expect(readLocalContributionDraft(JSON.stringify(expired), start.getTime())).toMatchObject({
      saveState: "repair_required",
      repairReason: "queue_expired",
      pendingFields: {},
    });

    const external = { ...remoteDraft(), storageRevision: 7, fields: {
      ...emptyContributionFields,
      name: "Other tab",
    } };
    failedAutosave.acceptExternalDraft(external);
    expect(failed.fields.name).toBe("Preserved in memory");
  });

  it("never sends an operation that could not be durably recorded", async () => {
    const setItem = vi.fn()
      .mockImplementationOnce(() => undefined)
      .mockImplementationOnce(() => { throw new Error("quota"); });
    const patch = vi.fn();
    const autosave = new ContributionAutosave(remoteDraft(), {
      storage: { setItem }, storageKey: "draft", patch, reload: vi.fn(), onDraft: vi.fn(),
    });
    autosave.edit("name", "Device copy");
    await vi.advanceTimersByTimeAsync(contributionAutosaveIdleMs);
    expect(patch).not.toHaveBeenCalled();
    expect(autosave.current()).toMatchObject({
      saveState: "repair_required", repairReason: "storage_failed", fields: { name: "Device copy" },
    });
  });

  it("merges simultaneous offline edits from two tabs at the same revision", () => {
    const base = remoteDraft();
    let tabA = structuredClone(base);
    let tabB = structuredClone(base);
    const a = new ContributionAutosave(tabA, {
      storage: storage().api, storageKey: "draft", writerId: () => "writer-a",
      patch: vi.fn(), reload: vi.fn(), onDraft: (draft) => { tabA = draft; },
    });
    const b = new ContributionAutosave(tabB, {
      storage: storage().api, storageKey: "draft", writerId: () => "writer-b",
      patch: vi.fn(), reload: vi.fn(), onDraft: (draft) => { tabB = draft; },
    });
    a.edit("name", "Dal");
    b.edit("category", "meal");
    const aSnapshot = structuredClone(tabA);
    const bSnapshot = structuredClone(tabB);

    a.acceptExternalDraft(bSnapshot);
    b.acceptExternalDraft(a.current());

    expect(a.current().fields).toMatchObject({ name: "Dal", category: "meal" });
    expect(b.current().fields).toMatchObject({ name: "Dal", category: "meal" });
    expect(a.current().storageRevision).toBeGreaterThan(aSnapshot.storageRevision);
  });

  it("does not requeue a pending field already acknowledged by another tab", () => {
    let local = remoteDraft();
    const autosave = new ContributionAutosave(local, {
      storage: storage().api, storageKey: "draft", writerId: () => "writer-a",
      patch: vi.fn(), reload: vi.fn(), onDraft: (draft) => { local = draft; },
    });
    autosave.edit("name", "Dal");
    const acknowledged = draftFromCapability(capability(2, { name: "Dal" }), null);
    acknowledged.storageRevision = local.storageRevision + 1;
    acknowledged.storageWriterId = "writer-b";

    autosave.acceptExternalDraft(acknowledged);

    expect(autosave.current()).toMatchObject({
      fields: { name: "Dal" }, pendingFields: {}, serverVersion: 2, saveState: "synced",
    });
  });

  it("does not retry a same-field conflict until that field is explicitly edited", async () => {
    const conflict = new ApiProblem("changed", "conflict", "request", 409);
    const patch = vi.fn()
      .mockRejectedValueOnce(conflict)
      .mockResolvedValueOnce(capability(3, { name: "Resolved" }));
    const autosave = new ContributionAutosave(remoteDraft(), {
      storage: storage().api,
      storageKey: "draft",
      patch,
      reload: vi.fn().mockResolvedValue(capability(2, { name: "Other device" })),
      onDraft: vi.fn(),
    });
    autosave.edit("name", "My device");
    await vi.advanceTimersByTimeAsync(contributionAutosaveIdleMs);
    await autosave.retry();
    autosave.edit("category", "meal");
    await vi.runAllTimersAsync();
    expect(patch).toHaveBeenCalledTimes(1);
    expect(autosave.current().conflictFields).toEqual(["name"]);

    autosave.edit("name", "Resolved");
    await vi.advanceTimersByTimeAsync(contributionAutosaveIdleMs);
    expect(patch).toHaveBeenCalledTimes(2);
    expect(patch.mock.calls[1]?.[1]).toMatchObject({
      expected_draft_version: 2,
      patches: expect.arrayContaining([
        { field: "name", value: "Resolved", base_value: "Other device", base_version: 2 },
      ]),
    });
  });

  it("resolves a conflict with either the server value or an explicit local rebase", async () => {
    const conflict = new ApiProblem("changed", "conflict", "request", 409);
    const serverChoicePatch = vi.fn().mockRejectedValue(conflict);
    const serverChoice = new ContributionAutosave(remoteDraft(), {
      storage: storage().api,
      storageKey: "draft",
      patch: serverChoicePatch,
      reload: vi.fn().mockResolvedValue(capability(2, { name: "Server value" })),
      onDraft: vi.fn(),
    });
    serverChoice.edit("name", "Local value");
    await vi.advanceTimersByTimeAsync(contributionAutosaveIdleMs);
    expect(serverChoice.resolveConflict("name", "server")).toBe(true);
    expect(serverChoice.current()).toMatchObject({
      saveState: "synced",
      fields: { name: "Server value" },
      pendingFields: {},
      conflictFields: [],
    });
    await vi.runAllTimersAsync();
    expect(serverChoicePatch).toHaveBeenCalledTimes(1);

    const localChoicePatch = vi.fn()
      .mockRejectedValueOnce(conflict)
      .mockResolvedValueOnce(capability(3, { name: "Local value" }));
    const localChoice = new ContributionAutosave(remoteDraft(), {
      storage: storage().api,
      storageKey: "draft",
      operationId: () => "local-resolution",
      patch: localChoicePatch,
      reload: vi.fn().mockResolvedValue(capability(2, { name: "Server value" })),
      onDraft: vi.fn(),
    });
    localChoice.edit("name", "Local value");
    await vi.advanceTimersByTimeAsync(contributionAutosaveIdleMs);
    expect(localChoice.resolveConflict("name", "local")).toBe(true);
    await vi.runAllTimersAsync();
    expect(localChoicePatch).toHaveBeenCalledTimes(2);
    expect(localChoicePatch.mock.calls[1]?.[1]).toMatchObject({
      expected_draft_version: 2,
      operation_id: "local-resolution",
      patches: [{
        field: "name",
        value: "Local value",
        base_value: "Server value",
        base_version: 2,
      }],
    });
    expect(localChoice.current().saveState).toBe("synced");
  });

  it("ignores a late acknowledgement after a newer cross-tab state is accepted", async () => {
    const late = deferred<ContributionCapability>();
    const autosave = new ContributionAutosave(remoteDraft(), {
      storage: storage().api, storageKey: "draft", writerId: () => "writer-a",
      patch: vi.fn().mockReturnValue(late.promise), reload: vi.fn(), onDraft: vi.fn(),
    });
    autosave.edit("name", "Old request");
    await vi.advanceTimersByTimeAsync(contributionAutosaveIdleMs);
    const newer = remoteDraft({ name: "Newer tab" });
    newer.serverVersion = 3;
    newer.storageRevision = autosave.current().storageRevision + 1;
    newer.storageWriterId = "writer-b";
    autosave.acceptExternalDraft(newer);
    late.resolve(capability(2, { name: "Old request" }));
    await late.promise;
    expect(autosave.current().serverVersion).toBe(3);
    expect(autosave.current().fields.name).toBe("Newer tab");
  });

  it("ignores wrong-draft, own-writer, and stale cross-tab snapshots", () => {
    const autosave = new ContributionAutosave(remoteDraft({ name: "Current" }), {
      storage: storage().api,
      storageKey: "draft",
      writerId: () => "writer-a",
      patch: vi.fn(),
      reload: vi.fn(),
      onDraft: vi.fn(),
    });
    const baseline = autosave.current();
    const wrongDraft = { ...remoteDraft({ name: "Wrong" }), clientDraftId: "another-draft" };
    autosave.acceptExternalDraft(wrongDraft);
    autosave.acceptExternalDraft({
      ...remoteDraft({ name: "Own echo" }),
      storageRevision: baseline.storageRevision + 2,
      storageWriterId: "writer-a",
    });
    autosave.acceptExternalDraft({
      ...remoteDraft({ name: "Stale" }),
      storageRevision: baseline.storageRevision - 1,
      storageWriterId: "writer-b",
    });
    expect(autosave.current().fields.name).toBe("Current");
    expect(autosave.current().storageRevision).toBe(baseline.storageRevision);
  });

  it("cancels a queued follow-up batch when disposed", async () => {
    const first = deferred<ContributionCapability>();
    const patch = vi.fn().mockReturnValue(first.promise);
    const autosave = new ContributionAutosave(remoteDraft(), {
      storage: storage().api, storageKey: "draft", patch, reload: vi.fn(), onDraft: vi.fn(),
    });
    autosave.edit("name", "First");
    await vi.advanceTimersByTimeAsync(contributionAutosaveIdleMs);
    autosave.edit("category", "queued");
    first.resolve(capability(2, { name: "First" }));
    await first.promise;
    autosave.dispose();
    await vi.runAllTimersAsync();
    expect(patch).toHaveBeenCalledTimes(1);
  });

  it("turns poisoned persisted operations into repair-required drafts", () => {
    const poisoned = remoteDraft();
    const parsed = readLocalContributionDraft(JSON.stringify({
      ...poisoned,
      pendingSince: start.toISOString(),
      pendingFields: { name: {} },
      inFlightOperation: {
        operationId: "not-a-uuid", expectedDraftVersion: 1, patches: [],
        requestedStage: null, sentAt: start.toISOString(),
      },
    }), start.getTime());
    expect(parsed).toMatchObject({
      saveState: "repair_required", repairReason: "schema_changed",
      pendingFields: {}, inFlightOperation: null,
    });
  });

  it("enforces the 64 KiB serialized draft boundary", () => {
    const draft = newLocalContributionDraft("budget");
    const baseline = new TextEncoder().encode(serializeLocalContributionDraft(draft)).byteLength;
    draft.fields.ingredients = "x".repeat(contributionDraftMaxBytes - baseline - 16);
    expect(new TextEncoder().encode(serializeLocalContributionDraft(draft)).byteLength)
      .toBeLessThanOrEqual(contributionDraftMaxBytes);
    draft.fields.ingredients += "x".repeat(64);
    expect(() => serializeLocalContributionDraft(draft)).toThrow("storage budget");
  });

  it("emits numeric privacy-safe metrics without field names or values", async () => {
    const metrics: ContributionAutosaveMetric[] = [];
    const autosave = new ContributionAutosave(remoteDraft(), {
      storage: storage().api,
      storageKey: "draft",
      patch: vi.fn().mockResolvedValue(capability(2, { name: "Private value" })),
      reload: vi.fn(),
      onDraft: vi.fn(),
      onMetric: (metric) => metrics.push(metric),
    });
    autosave.edit("name", "Private value");
    await vi.advanceTimersByTimeAsync(contributionAutosaveIdleMs);

    expect(metrics.map((metric) => metric.name)).toEqual(expect.arrayContaining([
      "payload_bytes",
      "acknowledgement_ms",
      "patches_per_active_minute",
      "conflicts_per_active_minute",
      "coalescing_ratio",
    ]));
    expect(metrics.every((metric) => typeof metric.value === "number")).toBe(true);
    expect(metrics.every((metric) =>
      Object.keys(metric).sort().join(",") === "name,value",
    )).toBe(true);
    expect(JSON.stringify(metrics)).not.toContain("Private value");
  });

  it("migrates the compatible v1 device shape without inventing a server sync", () => {
    const legacy = newLocalContributionDraft("legacy");
    const raw = JSON.stringify({
      schemaVersion: "1",
      clientDraftId: legacy.clientDraftId,
      fields: { ...legacy.fields, name: "Legacy" },
      duplicateCandidates: [],
      duplicateQuery: null,
      savedAt: legacy.savedAt,
      saveState: "saved_on_device",
    });
    expect(readLocalContributionDraft(raw)).toMatchObject({
      schemaVersion: "2",
      fields: { name: "Legacy" },
      serverDraftId: null,
      pendingFields: {},
    });
  });

  it("preserves eligible fields from an unknown schema for explicit repair", () => {
    const future = newLocalContributionDraft("future");
    expect(readLocalContributionDraft(JSON.stringify({
      ...future,
      schemaVersion: "99",
      fields: { ...future.fields, name: "Keep this work" },
    }))).toMatchObject({
      schemaVersion: "2",
      fields: { name: "Keep this work" },
      saveState: "repair_required",
      repairReason: "schema_changed",
      pendingFields: {},
    });
  });

  it("adopts untouched server fields while preserving only genuine device edits", () => {
    const stored = remoteDraft({ name: "Original", category: "old" });
    stored.fields.category = "device category";
    stored.pendingFields.category = {
      value: "device category",
      baseValue: "old",
      baseVersion: 1,
      editedAt: stored.savedAt,
    };
    stored.pendingSince = stored.savedAt;

    const hydrated = draftFromCapability(
      capability(2, { name: "Other device", category: "old" }),
      stored,
    );
    expect(hydrated.fields.name).toBe("Other device");
    expect(hydrated.fields.category).toBe("device category");
    expect(Object.keys(hydrated.pendingFields)).toEqual(["category"]);
    expect(hydrated.pendingFields.category).toMatchObject({
      baseValue: "old",
      value: "device category",
    });
  });

  it("keeps a persisted conflict blocked after server hydration", () => {
    const stored = remoteDraft({ name: "Original" });
    stored.fields.name = "Device value";
    stored.pendingFields.name = {
      value: "Device value", baseValue: "Original", baseVersion: 1, editedAt: stored.savedAt,
    };
    stored.pendingSince = stored.savedAt;
    stored.conflictFields = ["name"];
    stored.saveState = "conflict";

    const hydrated = draftFromCapability(capability(2, { name: "Other device" }), stored);
    expect(hydrated).toMatchObject({
      saveState: "conflict", conflictFields: ["name"], fields: { name: "Device value" },
      serverFields: { name: "Other device" },
    });
  });
});
