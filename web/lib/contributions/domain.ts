export const contributionStages = [
  "evidence",
  "details",
  "duplicates",
  "provenance",
  "review",
] as const;

export type ContributionStage = (typeof contributionStages)[number];
export type ContributionChapter = "begin" | "verify" | "send";
export type ContributionReviewState =
  | "draft"
  | "in_review"
  | "changes_requested"
  | "rejected"
  | "approved"
  | "publication_pending"
  | "published";
export type ContributionSaveState =
  | "saved_on_device"
  | "sync_scheduled"
  | "syncing"
  | "synced"
  | "offline"
  | "conflict"
  | "repair_required";

export type ContributionFields = {
  evidence_type: "packaging_label" | "government_database" | "public_document" | "maintainer_attestation" | null;
  source_uri: string;
  rights_acknowledged: boolean;
  name: string;
  name_local: string;
  locale: string;
  category: string;
  portion_description: string;
  portion_amount: string;
  portion_unit: "g" | "oz" | "lb" | "serving";
  portion_grams: string;
  energy_kcal: string;
  protein_g: string;
  fat_g: string;
  carbohydrate_g: string;
  ingredients: string;
  duplicates_resolved: boolean;
  pack_id: string;
  source_date: string;
  attribution: string;
  source_license: "contributor-original" | "CC0-1.0" | "public-domain" | null;
  review_acknowledged: boolean;
};

export type ContributionFieldName = keyof ContributionFields;
export type ContributionFieldPatch = {
  field: ContributionFieldName;
  value: ContributionFields[ContributionFieldName];
  baseValue?: ContributionFields[ContributionFieldName];
  baseVersion?: number;
};

export type PendingContributionField = {
  value: ContributionFields[ContributionFieldName];
  baseValue: ContributionFields[ContributionFieldName];
  baseVersion: number;
  editedAt: string;
};

export type ContributionSyncOperation = {
  operationId: string;
  expectedDraftVersion: number;
  patches: ContributionFieldPatch[];
  requestedStage: ContributionStage | null;
  sentAt: string;
};

export type DuplicateCandidate = {
  source: "community" | "usda";
  sourceId: string;
  name: string;
  locale: string | null;
};

export type ContributionBlocker = {
  stage: ContributionStage;
  field: keyof ContributionFields | null;
  code: string;
  message: string;
};

export type ContributionReceipt = {
  submissionId: string;
  submittedAt: string;
  acknowledgementDueAt: string;
  attribution: string;
  statusHref: string;
};

export type ContributionCapability = {
  draftId: string;
  draftVersion: number;
  reviewState: ContributionReviewState;
  completedStages: readonly ContributionStage[];
  accessibleStages: readonly ContributionStage[];
  blockers: readonly ContributionBlocker[];
  nextSafeStage: ContributionStage;
  requestedStage: ContributionStage;
  resolvedStage: ContributionStage;
  repairReason: "unknown_stage" | "stage_not_accessible" | null;
  savedAt: string;
  fields: ContributionFields;
  duplicateCandidates: readonly DuplicateCandidate[];
  receipt: ContributionReceipt | null;
};

export type LocalContributionDraft = {
  schemaVersion: "2";
  clientDraftId: string;
  fields: ContributionFields;
  duplicateCandidates: DuplicateCandidate[];
  duplicateQuery: string | null;
  savedAt: string;
  saveState: ContributionSaveState;
  serverDraftId: string | null;
  serverVersion: number | null;
  serverFields: ContributionFields | null;
  pendingFields: Partial<Record<ContributionFieldName, PendingContributionField>>;
  pendingSince: string | null;
  inFlightOperation: ContributionSyncOperation | null;
  storageRevision: number;
  storageWriterId: string;
  conflictFields: ContributionFieldName[];
  repairReason:
    | "queue_expired"
    | "storage_failed"
    | "schema_changed"
    | "session_expired"
    | "server_rejected"
    | null;
};
