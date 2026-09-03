export type MissionCatalogState = "unavailable" | "zero" | "live";
export type MissionActivityState = "unavailable" | "zero" | "live";
export type MissionUnavailableReason = "disabled" | "proof_unavailable";
export type MissionGapKind = "cuisine" | "locale" | "institution" | "dataset" | "missing_field";
export type MissionLifecycleState = "active" | "paused" | "completed" | "released" | "closed";
export type PublicMissionState =
  | "unavailable"
  | "zero"
  | "partial"
  | "live"
  | "stale"
  | "paused"
  | "completed"
  | "released"
  | "closed";

export type PublicMission = {
  mission_id: string;
  definition_id: string;
  definition_version: number;
  gap_kind: MissionGapKind;
  title: string;
  summary: string;
  target_pack_id: string;
  target_dataset: string;
  acceptance_target: number;
  acceptance_criteria: string;
  lifecycle_state: MissionLifecycleState;
  progress_state: PublicMissionState;
  public_reason: string;
  next_review_at: string | null;
  accepted_count: number | null;
  matched_event_count: number | null;
  checkpoint_id: string | null;
  checkpoint_built_at: string | null;
  release_receipt_digest: string | null;
};

export type PublicMissionCatalog = {
  schema_version: "1.0";
  state: MissionCatalogState;
  reason: MissionUnavailableReason | null;
  missions: PublicMission[];
};

export type PublicMissionActivityRegion = {
  region_code: string;
  level: "country" | "macroregion";
  accepted_count: number;
};

export type PublicMissionActivityMap = {
  schema_version: "1.0";
  state: MissionActivityState;
  reason: MissionUnavailableReason | null;
  minimum_cohort: 10;
  regions: PublicMissionActivityRegion[];
};

export type PublicMissionsSnapshot = {
  catalog: PublicMissionCatalog;
  activity: PublicMissionActivityMap;
};
