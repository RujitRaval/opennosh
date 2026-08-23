import { describe, expect, it } from "vitest";

import currentValidation from "@/tests/fixtures/contracts/problems/v1-validation.json";
import legacyProblem from "@/tests/fixtures/contracts/problems/v0-detail.json";
import unknownProblem from "@/tests/fixtures/contracts/problems/unknown-code.json";
import { networkProblem, problemFromResponse } from "@/lib/api/problem-adapter";

const requestId = "33333333-3333-4333-8333-333333333333";

function response(status: number, id = requestId) {
  return { status, headers: new Headers({ "X-Request-ID": id }) };
}

describe("problem contract adapter", () => {
  it("maps the current validation contract to stable field outcomes", () => {
    const problem = problemFromResponse(currentValidation, response(422));

    expect(problem).toMatchObject({
      name: "ApiError",
      kind: "invalid-field",
      status: 422,
      code: "validation_failed",
      reference: "11111111-1111-4111-8111-111111111111",
      fieldIssues: [
        {
          pointer: "/body/email",
          code: "string_pattern_mismatch",
          message: "This value has an invalid format.",
        },
      ],
      recoveryActions: [
        {
          id: "review_fields",
          label: "Review highlighted fields",
          href: null,
        },
      ],
    });
  });

  it("supports the N-1 detail contract during rollout", () => {
    expect(problemFromResponse(legacyProblem, response(401))).toMatchObject({
      kind: "authentication-required",
      status: 401,
      reference: requestId,
      message: "Email or password is incorrect",
    });
  });

  it.each([
    ["invalid_request", 400, "invalid-request"],
    ["authentication_required", 401, "authentication-required"],
    ["authorization_denied", 403, "authorization-denied"],
    ["resource_not_found", 404, "not-found"],
    ["conflict", 409, "conflict"],
    ["validation_failed", 422, "invalid-field"],
    ["rate_limited", 429, "rate-limited"],
    ["upstream_unavailable", 502, "retryable"],
    ["service_unavailable", 503, "retryable"],
    ["database_capacity_exhausted", 503, "retryable"],
    ["internal_error", 500, "unexpected"],
    ["search_cursor_invalid", 400, "invalid-request"],
    ["search_cursor_restart", 409, "conflict"],
  ] as const)("maps %s to %s", (code, status, kind) => {
    const problem = problemFromResponse(
      {
        type: `https://opennosh.org/problems/${code.replaceAll("_", "-")}`,
        title: "Expected problem",
        status,
        detail: "That request could not be completed.",
        code,
        schema_version: "1.0",
        request_id: requestId,
      },
      response(status),
    );
    expect(problem.kind).toBe(kind);
  });

  it("maps a cursor restart action to a safe first-page URL", () => {
    const problem = problemFromResponse(
      {
        type: "https://opennosh.org/problems/search-cursor-restart",
        title: "Restart search",
        status: 409,
        detail: "This search snapshot expired.",
        code: "search_cursor_restart",
        schema_version: "1.0",
        request_id: requestId,
        recovery_actions: [
          {
            id: "restart_search",
            label: "Restart search",
            href: "/api/v1/foods/search?q=apple&limit=12",
          },
        ],
      },
      response(409),
    );

    expect(problem).toMatchObject({
      kind: "conflict",
      code: "search_cursor_restart",
      recoveryActions: [
        {
          id: "restart_search",
          href: "/api/v1/foods/search?q=apple&limit=12",
        },
      ],
    });
  });

  it("maps retry and stale conflict extensions without leaking transport fields", () => {
    const retry = problemFromResponse(
      {
        type: "https://opennosh.org/problems/rate-limited",
        title: "Too many requests",
        status: 429,
        detail: "Try again later.",
        code: "rate_limited",
        schema_version: "1.0",
        request_id: requestId,
        retry_after: 45,
      },
      response(429),
    );
    expect(retry.retryAfterSeconds).toBe(45);

    const stale = problemFromResponse(
      {
        type: "https://opennosh.org/problems/conflict",
        title: "Request conflict",
        status: 409,
        detail: "The resource changed.",
        code: "conflict",
        schema_version: "1.0",
        request_id: requestId,
        latest_state: {
          resource_type: "food",
          resource_id: "community:beans",
          version: "3",
        },
      },
      response(409),
    );
    expect(stale).toMatchObject({
      kind: "stale",
      latestState: {
        resourceType: "food",
        resourceId: "community:beans",
        version: "3",
      },
    });
  });

  it("routes unknown and malformed contracts to an unexpected safe outcome", () => {
    expect(problemFromResponse(unknownProblem, response(409))).toMatchObject({
      kind: "unexpected",
      reference: requestId,
      status: 409,
    });
    expect(problemFromResponse({ code: "broken" }, response(500, "attacker"))).toMatchObject({
      kind: "unexpected",
      reference: "unavailable",
      status: 500,
    });
  });

  it("rejects mismatched HTTP status and malformed typed extensions", () => {
    const base = {
      type: "https://opennosh.org/problems/rate-limited",
      title: "Too many requests",
      status: 429,
      detail: "Try again later.",
      code: "rate_limited",
      schema_version: "1.0",
      request_id: requestId,
    };

    expect(problemFromResponse(base, response(503)).kind).toBe("unexpected");
    expect(
      problemFromResponse({ ...base, retry_after: -1 }, response(429)).kind,
    ).toBe("unexpected");
    expect(
      problemFromResponse(
        { ...base, field_errors: [{ pointer: "bad", code: "invalid", message: "Bad" }] },
        response(429),
      ).kind,
    ).toBe("unexpected");
  });

  it("keeps network failures distinct from HTTP status codes", () => {
    const problem = networkProblem();
    expect(problem.kind).toBe("network");
    expect(problem.status).toBeUndefined();
  });
});
