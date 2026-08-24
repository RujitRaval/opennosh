import type { PublicCommonsSnapshot as TransportSnapshot } from "@/lib/generated/client/types.gen";
import type {
  AcceptedActivityEvent,
  AcceptedEventType,
  CommonsSnapshotReason,
  CommonsSnapshotState,
  MostRecentVerifiedRecord,
  PublicCommonsSnapshot,
  PublicReleaseProof,
} from "@/lib/api/domain/public-commons";

const states = new Set<CommonsSnapshotState>([
  "live", "quiet", "stale", "partial", "illustrative", "unavailable",
]);
const reasons = new Set<CommonsSnapshotReason>([
  "activity_projection_lag",
  "invalid_latest_pointer",
  "invalid_release_manifest",
  "latest_release_unavailable",
  "no_published_release",
]);
const eventTypes = new Set<AcceptedEventType>(["food", "source", "portion", "pack"]);
const releaseFreshness = new Set(["verified", "stale", "unavailable"]);
const activityFreshness = new Set(["verified", "partial", "stale", "unavailable"]);
const timestampOffset = /(?:Z|[+-]\d{2}:\d{2})$/;
const releaseVersion = /^\d+\.\d+\.\d+\.\d+$/;
const digest = /^[0-9a-f]{64}$/;
const sourceCommit = /^[0-9a-f]{7,64}$/;
const localHref = /^\/(?!\/)[A-Za-z0-9/_?&=.%~-]+$/;

type JsonRecord = Record<string, unknown>;

function malformed(field: string): never {
  throw new Error(`Malformed public commons ${field}`);
}

function requireRecord(value: unknown, field: string): JsonRecord {
  if (!value || typeof value !== "object" || Array.isArray(value)) malformed(field);
  return value as JsonRecord;
}

function requireString(value: unknown, field: string, maxLength: number): string {
  if (typeof value !== "string" || value.length === 0 || value.length > maxLength) {
    malformed(field);
  }
  return value;
}

function requirePattern(
  value: unknown,
  field: string,
  pattern: RegExp,
  maxLength: number,
): string {
  const result = requireString(value, field, maxLength);
  if (!pattern.test(result)) malformed(field);
  return result;
}

function requireTimestamp(value: unknown, field: string): string {
  const result = requireString(value, field, 64);
  if (!timestampOffset.test(result) || !Number.isFinite(Date.parse(result))) malformed(field);
  return result;
}

function requireCount(value: unknown, field: string): number {
  if (typeof value !== "number" || !Number.isSafeInteger(value) || value < 0) malformed(field);
  return value;
}

function event(value: unknown): AcceptedActivityEvent {
  const input = requireRecord(value, "activity event");
  const eventType = requireString(input.event_type, "event type", 16);
  if (!eventTypes.has(eventType as AcceptedEventType)) malformed("event type");
  const contributor = input.public_contributor_credit;
  if (contributor !== undefined && contributor !== null) {
    requireString(contributor, "contributor credit", 100);
  }
  return {
    event_id: requireString(input.event_id, "event ID", 128),
    event_type: eventType as AcceptedEventType,
    food_or_pack_id: requireString(input.food_or_pack_id, "food or pack ID", 160),
    food_locale: requireString(input.food_locale, "food locale", 80),
    accepted_at: requireTimestamp(input.accepted_at, "accepted time"),
    source_commit: requirePattern(input.source_commit, "source commit", sourceCommit, 64),
    summary: requireString(input.summary, "event summary", 240),
    public_contributor_credit: (contributor as string | null | undefined) ?? null,
  };
}

function recentRecord(value: unknown): MostRecentVerifiedRecord | null {
  if (value === null || value === undefined) return null;
  const input = requireRecord(value, "recent record");
  return {
    record_id: requireString(input.record_id, "recent record ID", 160),
    name: requireString(input.name, "recent record name", 160),
    food_locale: requireString(input.food_locale, "recent record locale", 80),
    verified_at: requireTimestamp(input.verified_at, "recent record verification time"),
    href: requirePattern(input.href, "recent record link", localHref, 512),
  };
}

function release(value: unknown): PublicReleaseProof | null {
  if (value === null || value === undefined) return null;
  const input = requireRecord(value, "release");
  return {
    version: requirePattern(input.version, "release version", releaseVersion, 64),
    manifest_digest: requirePattern(input.manifest_digest, "manifest digest", digest, 64),
    publication_receipt_digest: requirePattern(
      input.publication_receipt_digest,
      "publication receipt digest",
      digest,
      64,
    ),
    published_at: requireTimestamp(input.published_at, "publication time"),
  };
}

export function publicCommonsSnapshot(value: unknown): PublicCommonsSnapshot {
  const input = requireRecord(value, "snapshot");
  const transport = input as Partial<TransportSnapshot>;
  if (transport.schema_version !== "1") {
    throw new Error("Unsupported public commons snapshot version");
  }
  if (!states.has(transport.state as CommonsSnapshotState)) malformed("snapshot state");
  const state = transport.state as CommonsSnapshotState;
  const activityInput = requireRecord(transport.activity, "activity");
  const freshnessInput = requireRecord(transport.freshness, "freshness");
  const acceptedCount = requireCount(activityInput.accepted_count, "accepted count");
  const verifiedRecordCount =
    transport.verified_record_count === null
      ? null
      : requireCount(transport.verified_record_count, "verified record count");
  if (!Array.isArray(activityInput.events)) malformed("activity events");
  const events = activityInput.events.map(event);
  if (events.length > 4 || acceptedCount < events.length) malformed("activity window");
  if (!Array.isArray(transport.reasons)) malformed("snapshot reasons");
  const snapshotReasons = transport.reasons as unknown[];
  if (!snapshotReasons.every((reason) => reasons.has(reason as CommonsSnapshotReason))) {
    malformed("snapshot reason");
  }
  if (!releaseFreshness.has(freshnessInput.release as string)) malformed("release freshness");
  if (!activityFreshness.has(freshnessInput.activity as string)) malformed("activity freshness");

  const startsAt = requireTimestamp(activityInput.starts_at, "activity window start");
  const endsAt = requireTimestamp(activityInput.ends_at, "activity window end");
  const checkedAt = requireTimestamp(freshnessInput.checked_at, "freshness check time");
  const staleSince =
    freshnessInput.stale_since === null || freshnessInput.stale_since === undefined
      ? null
      : requireTimestamp(freshnessInput.stale_since, "stale time");
  const releaseProof = release(transport.release);
  const recent = recentRecord(activityInput.most_recent_verified_record);
  const result: PublicCommonsSnapshot = {
    schema_version: "1",
    snapshot_id: requireString(transport.snapshot_id, "snapshot ID", 160),
    as_of: requireTimestamp(transport.as_of, "snapshot time"),
    state,
    release: releaseProof,
    verified_record_count: verifiedRecordCount,
    activity: {
      starts_at: startsAt,
      ends_at: endsAt,
      accepted_count: acceptedCount,
      events,
      most_recent_verified_record: recent,
    },
    freshness: {
      release: freshnessInput.release as PublicCommonsSnapshot["freshness"]["release"],
      activity: freshnessInput.activity as PublicCommonsSnapshot["freshness"]["activity"],
      checked_at: checkedAt,
      stale_since: staleSince,
    },
    reasons: snapshotReasons as CommonsSnapshotReason[],
  };

  const starts = Date.parse(startsAt);
  const ends = Date.parse(endsAt);
  const asOf = Date.parse(result.as_of);
  if (Date.parse(checkedAt) !== asOf) malformed("freshness check time");
  if (state === "stale" ? ends > asOf : ends !== asOf) malformed("activity cutoff");
  const published = releaseProof ? Date.parse(releaseProof.published_at) : null;
  if (starts >= ends || ends - starts !== 86_400_000) malformed("activity window bounds");
  if (
    events.some((item) => {
      const accepted = Date.parse(item.accepted_at);
      return accepted < starts || accepted > ends || (published !== null && accepted > published);
    })
  ) {
    malformed("event time");
  }
  if (recent && published !== null && Date.parse(recent.verified_at) > published) {
    malformed("recent record time");
  }

  const hasProof = result.release !== null && result.verified_record_count !== null;
  const hasAnyProof = result.release !== null || result.verified_record_count !== null;
  if ((state === "unavailable" || state === "illustrative") && hasAnyProof) {
    throw new Error("Unverified public commons state cannot claim release proof");
  }
  if (state !== "unavailable" && state !== "illustrative" && !hasProof) {
    throw new Error("Verified public commons state is missing release proof");
  }
  const expectedFreshness = {
    live: ["verified", "verified"],
    quiet: ["verified", "verified"],
    stale: ["stale", "stale"],
    partial: ["verified", "partial"],
    illustrative: ["unavailable", "unavailable"],
    unavailable: ["unavailable", "unavailable"],
  }[state];
  if (
    result.freshness.release !== expectedFreshness[0] ||
    result.freshness.activity !== expectedFreshness[1] ||
    (state === "stale") !== (staleSince !== null)
  ) {
    malformed("state freshness");
  }
  if (
    (state === "live" && (acceptedCount === 0 || events.length === 0)) ||
    (state === "quiet" && (acceptedCount !== 0 || events.length !== 0)) ||
    (state === "unavailable" &&
      (acceptedCount !== 0 || events.length !== 0 || recent !== null))
  ) {
    malformed("state activity");
  }
  return result;
}
