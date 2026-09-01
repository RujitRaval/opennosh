export type GovernanceReviewState =
  | "pending"
  | "in_review"
  | "changes_requested"
  | "approved"
  | "rejected"
  | "disputed"
  | "appealed"
  | "reopened"
  | "closed";

export interface GovernanceReviewEvent {
  sequence: number;
  event_type: string;
  actor_id: string | null;
  public_reason: string | null;
  occurred_at: string;
}

export interface GovernanceDispute {
  dispute_id: string;
  category: string;
  public_reason: string;
  requested_remedy: string;
  state: "open" | "resolved";
  resolution: string | null;
}

export interface GovernanceAppeal {
  appeal_id: string;
  public_reason: string;
  requested_remedy: string;
  state: "open" | "resolved" | "reopened";
  resolution: string | null;
}

export interface GovernanceReviewCase {
  review_case_id: string;
  source_draft_id: string;
  source_draft_version: number;
  pack_id: string;
  submitted_fields: Record<string, unknown>;
  state: GovernanceReviewState;
  revision: number;
  assigned_steward_actor_id: string | null;
  acknowledged_at: string | null;
  pause_reason: string | null;
  next_review_at: string | null;
  opened_at: string;
  updated_at: string;
  closed_at: string | null;
  events?: GovernanceReviewEvent[];
  disputes?: GovernanceDispute[];
  appeals?: GovernanceAppeal[];
}

export interface GovernanceReviewQueue {
  pack_id: string;
  cases: GovernanceReviewCase[];
}
