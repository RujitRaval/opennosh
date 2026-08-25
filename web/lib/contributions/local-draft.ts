import {
  contributionStages,
  type ContributionBlocker,
  type ContributionFieldName,
  type ContributionFields,
  type ContributionStage,
  type DuplicateCandidate,
  type LocalContributionDraft,
} from "@/lib/contributions/domain";

export const localContributionStorageKey = "opennosh.contribution.local.v1";
export const contributionDraftSchemaVersion = "2" as const;
export const contributionQueueMaxAgeMs = 7 * 24 * 60 * 60 * 1_000;
export const contributionDraftMaxBytes = 64 * 1_024;

export function contributionDraftStorageKey(routeDraftId: string): string {
  return routeDraftId === "local"
    ? localContributionStorageKey
    : `opennosh.contribution.remote.v1.${routeDraftId}`;
}

export function serverCandidatesNeedReview(
  draft: LocalContributionDraft,
  serverCandidates: readonly DuplicateCandidate[],
): boolean {
  if (serverCandidates.length === 0) return false;
  if (!draft.fields.duplicates_resolved) return true;
  const reviewed = new Set(
    draft.duplicateCandidates.map((candidate) => `${candidate.source}:${candidate.sourceId}`),
  );
  return serverCandidates.some(
    (candidate) => !reviewed.has(`${candidate.source}:${candidate.sourceId}`),
  );
}

export const emptyContributionFields: ContributionFields = {
  evidence_type: null,
  source_uri: "",
  rights_acknowledged: false,
  name: "",
  name_local: "",
  locale: "",
  category: "",
  portion_description: "",
  portion_amount: "",
  portion_unit: "g",
  portion_grams: "",
  energy_kcal: "",
  protein_g: "",
  fat_g: "",
  carbohydrate_g: "",
  ingredients: "",
  duplicates_resolved: false,
  pack_id: "",
  source_date: "",
  attribution: "",
  source_license: null,
  review_acknowledged: false,
};

export function newLocalContributionDraft(id = crypto.randomUUID()): LocalContributionDraft {
  return {
    schemaVersion: contributionDraftSchemaVersion,
    clientDraftId: id,
    fields: { ...emptyContributionFields },
    duplicateCandidates: [],
    duplicateQuery: null,
    savedAt: new Date().toISOString(),
    saveState: "saved_on_device",
    serverDraftId: null,
    serverVersion: null,
    serverFields: null,
    pendingFields: {},
    pendingSince: null,
    inFlightOperation: null,
    storageRevision: 0,
    storageWriterId: "",
    conflictFields: [],
    repairReason: null,
  };
}

function present(value: string) {
  return value.trim().length > 0;
}

function positive(value: string) {
  const parsed = Number(value);
  return Number.isFinite(parsed) && parsed >= 0 && value.trim() !== "";
}

export function localStageBlockers(
  draft: LocalContributionDraft,
  stage: ContributionStage,
): ContributionBlocker[] {
  const fields = draft.fields;
  const required = (
    field: keyof ContributionFields,
    condition: boolean,
    message: string,
  ): ContributionBlocker[] => condition ? [] : [{ stage, field, code: "required", message }];
  switch (stage) {
    case "evidence":
      return [
        ...required("evidence_type", fields.evidence_type !== null, "Choose the source type."),
        ...required("source_uri", /^https:\/\/[^\s]+$/i.test(fields.source_uri), "Add a public HTTPS source URL."),
        ...required("rights_acknowledged", fields.rights_acknowledged, "Confirm the source-reference terms."),
      ];
    case "details":
      return [
        ...required("name", present(fields.name), "Add the food name."),
        ...required("locale", present(fields.locale), "Add the food locale."),
        ...required("category", present(fields.category), "Add a category."),
        ...required("portion_description", present(fields.portion_description), "Describe the portion."),
        ...required("portion_amount", positive(fields.portion_amount), "Add the original portion amount."),
        ...required("portion_grams", positive(fields.portion_grams), "Add the canonical gram weight."),
        ...required("energy_kcal", positive(fields.energy_kcal), "Add energy per portion."),
        ...required("protein_g", positive(fields.protein_g), "Add protein per portion."),
        ...required("fat_g", positive(fields.fat_g), "Add fat per portion."),
        ...required("carbohydrate_g", positive(fields.carbohydrate_g), "Add carbohydrate per portion."),
      ];
    case "duplicates":
      if (draft.duplicateQuery !== `${fields.name.trim()}|${fields.locale.trim()}`) {
        return [{ stage, field: null, code: "duplicate_check_required", message: "Check the current food index before continuing." }];
      }
      return draft.duplicateCandidates.length > 0 && !fields.duplicates_resolved
        ? [{ stage, field: "duplicates_resolved", code: "candidate_unresolved", message: "Review the possible matches and confirm this proposal is still needed." }]
        : [];
    case "provenance":
      return [
        ...required("pack_id", present(fields.pack_id), "Choose a target pack."),
        ...required("source_date", present(fields.source_date), "Add the source date."),
        ...required("attribution", present(fields.attribution), "Add the public contributor credit."),
        ...required("source_license", fields.source_license !== null, "Choose the source license."),
      ];
    case "review":
      return required("review_acknowledged", fields.review_acknowledged, "Confirm the attribution, CC0 terms, and review process.");
    default: {
      const exhaustive: never = stage;
      return exhaustive;
    }
  }
}

export function localCompletedStages(draft: LocalContributionDraft): ContributionStage[] {
  const completed: ContributionStage[] = [];
  for (const stage of contributionStages) {
    if (localStageBlockers(draft, stage).length > 0) break;
    completed.push(stage);
  }
  return completed;
}

export function localAccessibleStages(draft: LocalContributionDraft): ContributionStage[] {
  const completed = localCompletedStages(draft);
  const next = contributionStages[completed.length];
  return next ? [...completed, next] : [...contributionStages];
}

function isContributionFieldName(value: string): value is ContributionFieldName {
  return Object.hasOwn(emptyContributionFields, value);
}

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}

function isIsoDate(value: unknown): value is string {
  return typeof value === "string" && Number.isFinite(Date.parse(value));
}

function isFieldValue(field: ContributionFieldName, value: unknown): boolean {
  if (["rights_acknowledged", "duplicates_resolved", "review_acknowledged"].includes(field)) {
    return typeof value === "boolean";
  }
  if (field === "evidence_type") {
    return value === null || [
      "packaging_label", "government_database", "public_document", "maintainer_attestation",
    ].includes(String(value));
  }
  if (field === "source_license") {
    return value === null || ["contributor-original", "CC0-1.0", "public-domain"].includes(String(value));
  }
  if (field === "portion_unit") {
    return ["g", "oz", "lb", "serving"].includes(String(value));
  }
  return typeof value === "string";
}

function safeFields(value: unknown): Partial<ContributionFields> {
  if (!isRecord(value)) return {};
  return Object.fromEntries(Object.entries(value).filter(([field, fieldValue]) =>
    isContributionFieldName(field) && isFieldValue(field, fieldValue),
  )) as Partial<ContributionFields>;
}

function validPendingFields(value: unknown): value is LocalContributionDraft["pendingFields"] {
  if (!isRecord(value)) return false;
  if (Object.keys(value).length > 25) return false;
  return Object.entries(value).every(([field, pending]) => {
    if (!isContributionFieldName(field) || !isRecord(pending)) return false;
    return isFieldValue(field, pending.value)
      && isFieldValue(field, pending.baseValue)
      && Number.isInteger(pending.baseVersion)
      && Number(pending.baseVersion) > 0
      && isIsoDate(pending.editedAt);
  });
}

function validOperation(value: unknown): value is NonNullable<LocalContributionDraft["inFlightOperation"]> {
  if (!isRecord(value) || typeof value.operationId !== "string"
    || !/^[0-9a-f]{8}-[0-9a-f]{4}-[1-8][0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/i.test(value.operationId)) {
    return false;
  }
  if (!Number.isInteger(value.expectedDraftVersion) || Number(value.expectedDraftVersion) < 1) {
    return false;
  }
  if (!Array.isArray(value.patches) || value.patches.length > 25 || !isIsoDate(value.sentAt)) return false;
  if (value.requestedStage !== null && !contributionStages.includes(value.requestedStage as ContributionStage)) {
    return false;
  }
  return value.patches.every((patch) => {
    if (!isRecord(patch) || typeof patch.field !== "string" || !isContributionFieldName(patch.field)) {
      return false;
    }
    return isFieldValue(patch.field, patch.value)
      && isFieldValue(patch.field, patch.baseValue)
      && Number.isInteger(patch.baseVersion)
      && Number(patch.baseVersion) > 0;
  });
}

export function serializeLocalContributionDraft(draft: LocalContributionDraft): string {
  const serialized = JSON.stringify(draft);
  if (new TextEncoder().encode(serialized).byteLength > contributionDraftMaxBytes) {
    throw new Error("Contribution device draft exceeds its storage budget.");
  }
  return serialized;
}

export function readLocalContributionDraft(
  raw: string | null,
  now = Date.now(),
): LocalContributionDraft | null {
  if (!raw) return null;
  try {
    const value = JSON.parse(raw) as Partial<LocalContributionDraft> & { schemaVersion?: string };
    if (
      typeof value.clientDraftId !== "string" ||
      !isRecord(value.fields) ||
      !isIsoDate(value.savedAt) ||
      !Array.isArray(value.duplicateCandidates)
    ) return null;
    const eligibleFields = safeFields(value.fields);
    if (!["1", contributionDraftSchemaVersion].includes(value.schemaVersion ?? "")) {
      return {
        ...newLocalContributionDraft(value.clientDraftId),
        fields: { ...emptyContributionFields, ...eligibleFields },
        duplicateCandidates: value.duplicateCandidates,
        duplicateQuery: value.duplicateQuery ?? null,
        savedAt: value.savedAt,
        saveState: "repair_required",
        repairReason: "schema_changed",
      };
    }
    const queueValid = validPendingFields(value.pendingFields ?? {})
      && (value.inFlightOperation === null || value.inFlightOperation === undefined
        || validOperation(value.inFlightOperation))
      && (value.pendingSince === null || value.pendingSince === undefined || isIsoDate(value.pendingSince))
      && (value.serverDraftId === null || value.serverDraftId === undefined
        || typeof value.serverDraftId === "string")
      && (value.serverVersion === null || value.serverVersion === undefined
        || (Number.isInteger(value.serverVersion) && Number(value.serverVersion) > 0))
      && (value.storageRevision === undefined
        || (Number.isInteger(value.storageRevision) && Number(value.storageRevision) >= 0));
    const migrated: LocalContributionDraft = {
      ...newLocalContributionDraft(value.clientDraftId),
      ...value,
      schemaVersion: contributionDraftSchemaVersion,
      fields: { ...emptyContributionFields, ...eligibleFields },
      duplicateCandidates: value.duplicateCandidates,
      pendingFields: queueValid ? value.pendingFields ?? {} : {},
      inFlightOperation: queueValid ? value.inFlightOperation ?? null : null,
      storageWriterId: typeof value.storageWriterId === "string" ? value.storageWriterId : "",
      conflictFields: Array.isArray(value.conflictFields)
        ? value.conflictFields.filter((field): field is ContributionFieldName =>
          typeof field === "string" && isContributionFieldName(field))
        : [],
      saveState: value.saveState ?? "saved_on_device",
    };
    if (!queueValid) {
      return {
        ...migrated,
        pendingSince: null,
        saveState: "repair_required",
        repairReason: "schema_changed",
      };
    }
    const queueTimes = [
      migrated.pendingSince,
      migrated.inFlightOperation?.sentAt,
    ].filter((item): item is string => typeof item === "string");
    if (queueTimes.some((item) => now - Date.parse(item) > contributionQueueMaxAgeMs)) {
      return {
        ...migrated,
        pendingFields: {},
        pendingSince: null,
        inFlightOperation: null,
        saveState: "repair_required",
        repairReason: "queue_expired",
      };
    }
    return migrated;
  } catch {
    return null;
  }
}
