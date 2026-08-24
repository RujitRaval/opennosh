export type CommonsSnapshotState =
  | "live"
  | "quiet"
  | "stale"
  | "partial"
  | "illustrative"
  | "unavailable";

export type CommonsSnapshotReason =
  | "activity_projection_lag"
  | "invalid_latest_pointer"
  | "invalid_release_manifest"
  | "latest_release_unavailable"
  | "no_published_release";

export type AcceptedEventType = "food" | "source" | "portion" | "pack";

export type AcceptedActivityEvent = {
  event_id: string;
  event_type: AcceptedEventType;
  food_or_pack_id: string;
  food_locale: string;
  accepted_at: string;
  source_commit: string;
  href: string;
  summary: string;
  public_contributor_credit?: string | null;
};

export type MostRecentVerifiedRecord = {
  record_id: string;
  name: string;
  food_locale: string;
  verified_at: string;
  href: string;
};

export type PublicReleaseProof = {
  version: string;
  manifest_digest: string;
  publication_receipt_digest: string;
  published_at: string;
};

export type PublicCommonsSnapshot = {
  schema_version: "1";
  snapshot_id: string;
  as_of: string;
  state: CommonsSnapshotState;
  release: PublicReleaseProof | null;
  verified_record_count: number | null;
  activity: {
    starts_at: string;
    ends_at: string;
    accepted_count: number;
    events: AcceptedActivityEvent[];
    most_recent_verified_record: MostRecentVerifiedRecord | null;
  };
  freshness: {
    release: "verified" | "stale" | "unavailable";
    activity: "verified" | "partial" | "stale" | "unavailable";
    checked_at: string;
    stale_since: string | null;
  };
  reasons: CommonsSnapshotReason[];
};
