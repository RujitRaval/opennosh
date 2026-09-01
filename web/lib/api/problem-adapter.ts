import type {
  ProblemCode,
  ProblemDetails,
} from "@/lib/generated/client/types.gen";
import { reviewedApiErrorMessage } from "@/lib/health-safety";

import { ApiProblem, type ProblemKind } from "./domain/problem";

const requestIdPattern =
  /^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/;
const problemTypePattern = /^https:\/\/opennosh\.org\/problems\/[a-z0-9-]+$/;
const pointerPattern = /^\/(?:[^/~]|~[01])+(?:\/(?:[^/~]|~[01])+)*$/;
const codePattern = /^[a-z0-9_]+$/;
const recoveryIds = [
  "retry",
  "sign_in",
  "reload",
  "review_fields",
  "restart_search",
] as const;
const problemCodes: readonly ProblemCode[] = [
  "invalid_request",
  "authentication_required",
  "authorization_denied",
  "resource_not_found",
  "conflict",
  "validation_failed",
  "rate_limited",
  "upstream_unavailable",
  "service_unavailable",
  "database_capacity_exhausted",
  "internal_error",
  "search_cursor_invalid",
  "search_cursor_restart",
  "evidence_upload_conflict",
  "evidence_upload_expired",
  "evidence_upload_unavailable",
];

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}

function isProblemCode(value: unknown): value is ProblemCode {
  return typeof value === "string" && problemCodes.includes(value as ProblemCode);
}

function isFieldError(value: unknown): boolean {
  return (
    isRecord(value) &&
    typeof value.pointer === "string" &&
    pointerPattern.test(value.pointer) &&
    typeof value.code === "string" &&
    value.code.length >= 1 &&
    value.code.length <= 80 &&
    codePattern.test(value.code) &&
    typeof value.message === "string" &&
    value.message.length >= 1 &&
    value.message.length <= 240
  );
}

function isLatestState(value: unknown): boolean {
  return (
    isRecord(value) &&
    typeof value.resource_type === "string" &&
    value.resource_type.length >= 1 &&
    value.resource_type.length <= 80 &&
    typeof value.resource_id === "string" &&
    value.resource_id.length >= 1 &&
    value.resource_id.length <= 160 &&
    typeof value.version === "string" &&
    value.version.length >= 1 &&
    value.version.length <= 80
  );
}

function isRecoveryAction(value: unknown): boolean {
  return (
    isRecord(value) &&
    recoveryIds.includes(value.id as (typeof recoveryIds)[number]) &&
    typeof value.label === "string" &&
    value.label.length >= 1 &&
    value.label.length <= 120 &&
    (value.href === undefined ||
      value.href === null ||
      (typeof value.href === "string" &&
        value.href.startsWith("/") &&
        !value.href.startsWith("//") &&
        !value.href.includes("\0")))
  );
}

function isOptionalArray(
  value: unknown,
  limit: number,
  itemCheck: (item: unknown) => boolean,
): boolean {
  return (
    value === undefined ||
    value === null ||
    (Array.isArray(value) && value.length <= limit && value.every(itemCheck))
  );
}

function safeReference(headerValue: string | null): string {
  return headerValue && requestIdPattern.test(headerValue) ? headerValue : "unavailable";
}

function isProblemDetails(value: unknown): value is ProblemDetails {
  if (!isRecord(value)) return false;
  return (
    value.schema_version === "1.0" &&
    typeof value.type === "string" &&
    problemTypePattern.test(value.type) &&
    typeof value.title === "string" &&
    value.title.length >= 1 &&
    value.title.length <= 120 &&
    typeof value.status === "number" &&
    Number.isInteger(value.status) &&
    value.status >= 400 &&
    value.status <= 599 &&
    typeof value.detail === "string" &&
    value.detail.length >= 1 &&
    value.detail.length <= 500 &&
    isProblemCode(value.code) &&
    typeof value.request_id === "string" &&
    requestIdPattern.test(value.request_id) &&
    (value.retry_after === undefined ||
      value.retry_after === null ||
      (typeof value.retry_after === "number" &&
        Number.isInteger(value.retry_after) &&
        value.retry_after >= 1 &&
        value.retry_after <= 86_400)) &&
    isOptionalArray(value.field_errors, 100, isFieldError) &&
    (value.latest_state === undefined ||
      value.latest_state === null ||
      isLatestState(value.latest_state)) &&
    isOptionalArray(value.recovery_actions, 8, isRecoveryAction)
  );
}

function kindFor(code: ProblemCode, hasLatestState: boolean): ProblemKind {
  switch (code) {
    case "invalid_request":
    case "search_cursor_invalid":
      return "invalid-request";
    case "authentication_required":
      return "authentication-required";
    case "authorization_denied":
      return "authorization-denied";
    case "resource_not_found":
      return "not-found";
    case "conflict":
      return hasLatestState ? "stale" : "conflict";
    case "search_cursor_restart":
    case "evidence_upload_conflict":
    case "evidence_upload_expired":
      return "conflict";
    case "validation_failed":
      return "invalid-field";
    case "rate_limited":
      return "rate-limited";
    case "upstream_unavailable":
    case "service_unavailable":
    case "database_capacity_exhausted":
    case "evidence_upload_unavailable":
      return "retryable";
    case "internal_error":
      return "unexpected";
    default: {
      const exhaustive: never = code;
      return exhaustive;
    }
  }
}

function legacyKind(status: number): ProblemKind {
  if (status === 401) return "authentication-required";
  if (status === 403) return "authorization-denied";
  if (status === 404) return "not-found";
  if (status === 409) return "conflict";
  if (status === 422) return "invalid-field";
  if (status === 429) return "rate-limited";
  if (status >= 500) return "retryable";
  return "invalid-request";
}

function safeMessage(message: string, status: number): string {
  const reviewed = reviewedApiErrorMessage(message);
  const fallback =
    status >= 500
      ? "opennosh could not reach the server. Please try again."
      : "That request could not be completed. Please try again.";
  return reviewed === message ? reviewed : fallback;
}

export function problemFromResponse(
  body: unknown,
  response: Pick<Response, "status" | "headers">,
): ApiProblem {
  const headerReference = safeReference(response.headers.get("X-Request-ID"));
  if (isProblemDetails(body) && body.status === response.status) {
    const fieldIssues = (body.field_errors ?? []).map((item) => ({
      pointer: item.pointer,
      code: item.code,
      message: safeMessage(item.message, body.status),
    }));
    const latestState = body.latest_state
      ? {
          resourceType: body.latest_state.resource_type,
          resourceId: body.latest_state.resource_id,
          version: body.latest_state.version,
        }
      : undefined;
    const recoveryActions = (body.recovery_actions ?? []).map((item) => ({
      id: item.id,
      label: item.label,
      href: item.href ?? null,
    }));

    return new ApiProblem(
      safeMessage(body.detail, body.status),
      kindFor(body.code, latestState !== undefined),
      body.request_id,
      body.status,
      body.code,
      body.retry_after ?? undefined,
      fieldIssues,
      latestState,
      recoveryActions,
    );
  }

  if (
    isRecord(body) &&
    typeof body.detail === "string" &&
    !("schema_version" in body) &&
    !("code" in body)
  ) {
    return new ApiProblem(
      safeMessage(body.detail, response.status),
      legacyKind(response.status),
      headerReference,
      response.status,
    );
  }

  return new ApiProblem(
    response.status >= 500
      ? "opennosh could not reach the server. Please try again."
      : "That request could not be completed. Please try again.",
    "unexpected",
    headerReference,
    response.status,
  );
}

export function networkProblem(): ApiProblem {
  return new ApiProblem(
    "opennosh could not reach the server. Check your connection and retry.",
    "network",
    "unavailable",
  );
}
