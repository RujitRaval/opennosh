import type {
  MissionActivityState as TransportActivityState,
  MissionCatalogState as TransportCatalogState,
  PublicMissionActivityMap as TransportActivityMap,
  PublicMissionCatalog as TransportCatalog,
} from "@/lib/generated/client/types.gen";
import type {
  MissionActivityState,
  MissionCatalogState,
  MissionGapKind,
  MissionLifecycleState,
  MissionUnavailableReason,
  PublicMission,
  PublicMissionActivityMap,
  PublicMissionActivityRegion,
  PublicMissionCatalog,
  PublicMissionState,
} from "@/lib/api/domain/public-missions";

type JsonRecord = Record<string, unknown>;

const catalogStates = new Set<MissionCatalogState>(["unavailable", "zero", "live"]);
const activityStates = new Set<MissionActivityState>(["unavailable", "zero", "live"]);
const unavailableReasons = new Set<MissionUnavailableReason>(["disabled", "proof_unavailable"]);
const gapKinds = new Set<MissionGapKind>([
  "cuisine", "locale", "institution", "dataset", "missing_field",
]);
const lifecycleStates = new Set<MissionLifecycleState>([
  "active", "paused", "completed", "released", "closed",
]);
const progressStates = new Set<PublicMissionState>([
  "unavailable", "zero", "partial", "live", "stale", "paused", "completed", "released", "closed",
]);
const uuid = /^[0-9a-f]{8}-[0-9a-f]{4}-[1-5][0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/i;
const digest = /^[0-9a-f]{64}$/;
const country = /^[A-Z]{2}$/;
const macroregion = /^\d{3}$/;
const timestampOffset = /(?:Z|[+-]\d{2}:\d{2})$/;
const maximumActivityRegions = 300;

const catalogKeys = new Set(["schema_version", "state", "reason", "missions"]);
const missionKeys = new Set([
  "mission_id", "definition_id", "definition_version", "gap_kind", "title", "summary",
  "target_pack_id", "target_dataset", "acceptance_target", "acceptance_criteria",
  "lifecycle_state", "progress_state", "public_reason", "next_review_at", "accepted_count",
  "matched_event_count", "checkpoint_id", "checkpoint_built_at", "release_receipt_digest",
]);
const activityKeys = new Set(["schema_version", "state", "reason", "minimum_cohort", "regions"]);
const regionKeys = new Set(["region_code", "level", "accepted_count"]);

function malformed(field: string): never {
  throw new Error(`Malformed public missions ${field}`);
}

function requireRecord(value: unknown, field: string): JsonRecord {
  if (!value || typeof value !== "object" || Array.isArray(value)) malformed(field);
  return value as JsonRecord;
}

function requireExactKeys(input: JsonRecord, allowed: ReadonlySet<string>, field: string) {
  if (Object.keys(input).some((key) => !allowed.has(key))) malformed(`${field} fields`);
}

function requireString(value: unknown, field: string, maxLength: number): string {
  if (typeof value !== "string" || value.length === 0 || value.length > maxLength) malformed(field);
  return value;
}

function requirePattern(value: unknown, field: string, pattern: RegExp, maxLength: number): string {
  const result = requireString(value, field, maxLength);
  if (!pattern.test(result)) malformed(field);
  return result;
}

function requireInteger(value: unknown, field: string, minimum: number, maximum = Number.MAX_SAFE_INTEGER) {
  if (!Number.isSafeInteger(value) || (value as number) < minimum || (value as number) > maximum) {
    malformed(field);
  }
  return value as number;
}

function requireNullableTimestamp(value: unknown, field: string): string | null {
  if (value === null || value === undefined) return null;
  const result = requireString(value, field, 64);
  if (!timestampOffset.test(result) || !Number.isFinite(Date.parse(result))) malformed(field);
  return result;
}

function requireNullableInteger(value: unknown, field: string): number | null {
  return value === null || value === undefined ? null : requireInteger(value, field, 0);
}

function requireNullableUuid(value: unknown, field: string): string | null {
  return value === null || value === undefined
    ? null
    : requirePattern(value, field, uuid, 36);
}

function mission(value: unknown): PublicMission {
  const input = requireRecord(value, "mission");
  requireExactKeys(input, missionKeys, "mission");
  const gapKind = requireString(input.gap_kind, "gap kind", 32);
  const lifecycleState = requireString(input.lifecycle_state, "lifecycle state", 16);
  const progressState = requireString(input.progress_state, "progress state", 16);
  if (!gapKinds.has(gapKind as MissionGapKind)) malformed("gap kind");
  if (!lifecycleStates.has(lifecycleState as MissionLifecycleState)) malformed("lifecycle state");
  if (!progressStates.has(progressState as PublicMissionState)) malformed("progress state");

  const acceptedCount = requireNullableInteger(input.accepted_count, "accepted count");
  const matchedEventCount = requireNullableInteger(input.matched_event_count, "matched event count");
  const checkpointId = requireNullableUuid(input.checkpoint_id, "checkpoint ID");
  const checkpointBuiltAt = requireNullableTimestamp(input.checkpoint_built_at, "checkpoint time");
  const nextReviewAt = requireNullableTimestamp(input.next_review_at, "next review time");
  const releaseReceipt = input.release_receipt_digest === null || input.release_receipt_digest === undefined
    ? null
    : requirePattern(input.release_receipt_digest, "release receipt digest", digest, 64);
  const acceptanceTarget = requireInteger(input.acceptance_target, "acceptance target", 1, 100_000);

  const checkpointParts = [acceptedCount, matchedEventCount, checkpointId, checkpointBuiltAt];
  if (checkpointParts.some((part) => part === null) && checkpointParts.some((part) => part !== null)) {
    malformed("checkpoint proof");
  }
  if (acceptedCount !== null && matchedEventCount !== null && acceptedCount > matchedEventCount) {
    malformed("checkpoint counts");
  }
  if ((lifecycleState === "released") !== (releaseReceipt !== null)) malformed("release proof");
  if ((lifecycleState === "paused") !== (nextReviewAt !== null)) malformed("review time");

  const progressMatches = {
    unavailable: acceptedCount === null,
    zero: lifecycleState === "active" && acceptedCount === 0,
    partial: lifecycleState === "active" && acceptedCount !== null && acceptedCount > 0 && acceptedCount < acceptanceTarget,
    live: lifecycleState === "active" && acceptedCount !== null && acceptedCount >= acceptanceTarget,
    stale: acceptedCount !== null,
    paused: lifecycleState === "paused" && acceptedCount !== null,
    completed: lifecycleState === "completed" && acceptedCount !== null,
    released: lifecycleState === "released" && acceptedCount !== null,
    closed: lifecycleState === "closed" && acceptedCount !== null,
  }[progressState as PublicMissionState];
  if (!progressMatches) malformed("progress state shape");

  return {
    mission_id: requirePattern(input.mission_id, "mission ID", uuid, 36),
    definition_id: requirePattern(input.definition_id, "definition ID", uuid, 36),
    definition_version: requireInteger(input.definition_version, "definition version", 1),
    gap_kind: gapKind as MissionGapKind,
    title: requireString(input.title, "title", 160),
    summary: requireString(input.summary, "summary", 1000),
    target_pack_id: requireString(input.target_pack_id, "target pack", 160),
    target_dataset: requireString(input.target_dataset, "target dataset", 256),
    acceptance_target: acceptanceTarget,
    acceptance_criteria: requireString(input.acceptance_criteria, "acceptance criteria", 2000),
    lifecycle_state: lifecycleState as MissionLifecycleState,
    progress_state: progressState as PublicMissionState,
    public_reason: requireString(input.public_reason, "public reason", 2000),
    next_review_at: nextReviewAt,
    accepted_count: acceptedCount,
    matched_event_count: matchedEventCount,
    checkpoint_id: checkpointId,
    checkpoint_built_at: checkpointBuiltAt,
    release_receipt_digest: releaseReceipt,
  };
}

export function publicMissionCatalog(value: unknown): PublicMissionCatalog {
  const input = requireRecord(value, "catalog");
  const transport = input as Partial<TransportCatalog>;
  requireExactKeys(input, catalogKeys, "catalog");
  if (transport.schema_version !== "1.0") throw new Error("Unsupported public missions catalog version");
  if (!catalogStates.has(transport.state as TransportCatalogState)) malformed("catalog state");
  if (!Array.isArray(transport.missions) || transport.missions.length > 100) malformed("catalog missions");
  const state = transport.state as MissionCatalogState;
  const reason = transport.reason ?? null;
  if (reason !== null && !unavailableReasons.has(reason)) malformed("catalog reason");
  const missions = transport.missions.map(mission);
  if ((state === "unavailable") !== (reason !== null)) malformed("catalog availability");
  if ((state === "live") !== (missions.length > 0) || (state === "unavailable" && missions.length > 0)) {
    malformed("catalog state shape");
  }
  if (new Set(missions.map((item) => item.mission_id)).size !== missions.length) malformed("mission identity");
  return { schema_version: "1.0", state, reason, missions };
}

function activityRegion(value: unknown): PublicMissionActivityRegion {
  const input = requireRecord(value, "activity region");
  requireExactKeys(input, regionKeys, "activity region");
  if (input.level !== "country" && input.level !== "macroregion") malformed("activity region level");
  return {
    region_code: requirePattern(
      input.region_code,
      "activity region code",
      input.level === "country" ? country : macroregion,
      3,
    ),
    level: input.level,
    accepted_count: requireInteger(input.accepted_count, "activity accepted count", 10, 10_000),
  };
}

export function publicMissionActivityMap(value: unknown): PublicMissionActivityMap {
  const input = requireRecord(value, "activity map");
  const transport = input as Partial<TransportActivityMap>;
  requireExactKeys(input, activityKeys, "activity map");
  if (transport.schema_version !== "1.0") throw new Error("Unsupported public mission activity version");
  if (!activityStates.has(transport.state as TransportActivityState)) malformed("activity state");
  if (transport.minimum_cohort !== 10) malformed("activity cohort");
  if (!Array.isArray(transport.regions) || transport.regions.length > maximumActivityRegions) malformed("activity regions");
  const state = transport.state as MissionActivityState;
  const reason = transport.reason ?? null;
  if (reason !== null && !unavailableReasons.has(reason)) malformed("activity reason");
  const regions = transport.regions.map(activityRegion);
  if ((state === "unavailable") !== (reason !== null)) malformed("activity availability");
  if ((state === "live") !== (regions.length > 0) || (state === "unavailable" && regions.length > 0)) {
    malformed("activity state shape");
  }
  if (new Set(regions.map((item) => `${item.level}:${item.region_code}`)).size !== regions.length) {
    malformed("activity region identity");
  }
  return { schema_version: "1.0", state, reason, minimum_cohort: 10, regions };
}
