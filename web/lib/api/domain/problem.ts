export type ProblemKind =
  | "invalid-request"
  | "authentication-required"
  | "authorization-denied"
  | "not-found"
  | "conflict"
  | "invalid-field"
  | "rate-limited"
  | "retryable"
  | "stale"
  | "partial"
  | "repair-required"
  | "unexpected"
  | "network";

export type FieldIssue = {
  pointer: string;
  code: string;
  message: string;
};

export type LatestState = {
  resourceType: string;
  resourceId: string;
  version: string;
};

export type ProblemRecoveryAction = {
  id: "retry" | "sign_in" | "reload" | "review_fields";
  label: string;
  href: string | null;
};

export class ApiProblem extends Error {
  readonly name = "ApiError";

  constructor(
    message: string,
    public readonly kind: ProblemKind,
    public readonly reference: string,
    public readonly status?: number,
    public readonly code?: string,
    public readonly retryAfterSeconds?: number,
    public readonly fieldIssues: FieldIssue[] = [],
    public readonly latestState?: LatestState,
    public readonly recoveryActions: ProblemRecoveryAction[] = [],
  ) {
    super(message);
  }
}
