import type {
  PublicMission,
  PublicMissionActivityMap,
  PublicMissionCatalog,
  PublicMissionState,
} from "@/lib/api/domain/public-missions";

const lifecycleForProgress: Record<PublicMissionState, PublicMission["lifecycle_state"]> = {
  unavailable: "active",
  zero: "active",
  partial: "active",
  live: "active",
  stale: "active",
  paused: "paused",
  completed: "completed",
  released: "released",
  closed: "closed",
};

export function publicMissionFixture(progress: PublicMissionState = "partial"): PublicMission {
  const missing = progress === "unavailable";
  const accepted = missing ? null : progress === "zero" ? 0 : progress === "live" ? 12 : 4;
  return {
    mission_id: "11111111-1111-4111-8111-111111111111",
    definition_id: "22222222-2222-4222-8222-222222222222",
    definition_version: 3,
    gap_kind: "locale",
    title: "Document Caribbean breakfast staples",
    summary: "Add source-backed records that preserve preparation and locale context.",
    target_pack_id: "caribbean-community",
    target_dataset: "foods",
    acceptance_target: 10,
    acceptance_criteria: "A record must be accepted into the signed target pack with eligible source proof.",
    lifecycle_state: lifecycleForProgress[progress],
    progress_state: progress,
    public_reason: "The current pack has a measurable preparation gap.",
    next_review_at: progress === "paused" ? "2026-09-15T16:00:00Z" : null,
    accepted_count: accepted,
    matched_event_count: accepted,
    checkpoint_id: missing ? null : "33333333-3333-4333-8333-333333333333",
    checkpoint_built_at: missing ? null : "2026-09-02T18:00:00Z",
    release_receipt_digest: progress === "released" ? "a".repeat(64) : null,
  };
}

export function publicMissionCatalogFixture(
  state: PublicMissionCatalog["state"] = "live",
  progress: PublicMissionState = "partial",
): PublicMissionCatalog {
  return {
    schema_version: "1.0",
    state,
    reason: state === "unavailable" ? "disabled" : null,
    missions: state === "live" ? [publicMissionFixture(progress)] : [],
  };
}

export function publicMissionActivityFixture(
  state: PublicMissionActivityMap["state"] = "live",
): PublicMissionActivityMap {
  return {
    schema_version: "1.0",
    state,
    reason: state === "unavailable" ? "disabled" : null,
    minimum_cohort: 10,
    regions: state === "live"
      ? [
          { region_code: "JM", level: "country", accepted_count: 14 },
          { region_code: "419", level: "macroregion", accepted_count: 10 },
        ]
      : [],
  };
}
