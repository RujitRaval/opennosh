import type { PublicCommonsSnapshot } from "@/lib/api/domain/public-commons";

const release = {
  version: "0.30.0.0",
  manifest_digest: "a".repeat(64),
  publication_receipt_digest: "b".repeat(64),
  published_at: "2026-08-23T18:00:00Z",
} as const;

const event = {
  event_id: "accepted-dhokla",
  event_type: "food" as const,
  food_or_pack_id: "dhokla-gujarati",
  food_locale: "Gujarat, India",
  accepted_at: "2026-08-23T17:30:00Z",
  source_commit: "abcdef1234567890",
  href: "/en/explore/foods/community/dhokla-gujarati",
  summary: "Accepted Dhokla as a verified food record.",
  public_contributor_credit: "Community contributor",
};

const base: PublicCommonsSnapshot = {
  schema_version: "1",
  snapshot_id: "fixture-snapshot",
  as_of: "2026-08-23T18:00:00Z",
  state: "live",
  release,
  verified_record_count: 18_429,
  activity: {
    starts_at: "2026-08-22T18:00:00Z",
    ends_at: "2026-08-23T18:00:00Z",
    accepted_count: 1,
    events: [event],
    most_recent_verified_record: {
      record_id: "khichdi-gujarati",
      name: "Khichdi",
      food_locale: "Gujarat, India",
      verified_at: "2026-08-20T10:00:00Z",
      href: "/en/explore/khichdi-gujarati",
    },
  },
  freshness: {
    release: "verified",
    activity: "verified",
    checked_at: "2026-08-23T18:00:00Z",
    stale_since: null,
  },
  reasons: [],
};

export function publicCommonsFixture(
  state: PublicCommonsSnapshot["state"],
): PublicCommonsSnapshot {
  if (state === "unavailable" || state === "illustrative") {
    return {
      ...base,
      state,
      release: null,
      verified_record_count: null,
      activity: {
        ...base.activity,
        accepted_count: state === "illustrative" ? 1 : 0,
        events: state === "illustrative" ? [event] : [],
        most_recent_verified_record: null,
      },
      freshness: {
        release: "unavailable",
        activity: "unavailable",
        checked_at: base.as_of,
        stale_since: null,
      },
    };
  }
  if (state === "quiet") {
    return {
      ...base,
      state,
      activity: { ...base.activity, accepted_count: 0, events: [] },
    };
  }
  if (state === "stale") {
    return {
      ...base,
      state,
      freshness: {
        release: "stale",
        activity: "stale",
        checked_at: "2026-08-23T20:00:00Z",
        stale_since: "2026-08-23T18:05:00Z",
      },
      reasons: ["latest_release_unavailable"],
    };
  }
  if (state === "partial") {
    return {
      ...base,
      state,
      freshness: { ...base.freshness, activity: "partial" },
      reasons: ["activity_projection_lag"],
    };
  }
  return base;
}
