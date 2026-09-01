import { afterEach, describe, expect, it, vi } from "vitest";

import { api } from "@/lib/api";
import { uploadEvidenceBytes } from "@/lib/api/evidence-upload";
import { governanceApi } from "@/lib/api/governance";
import foodDetailFixture from "@/tests/fixtures/contracts/foods/v1-detail-community.json";

afterEach(() => {
  vi.restoreAllMocks();
  document.cookie = "__Host-opennosh-csrf=; Max-Age=0; Secure; Path=/";
  document.cookie = "opennosh_csrf=; Max-Age=0; Path=/";
});

describe("browser API client", () => {
  it("binds every governance action to its exact encoded resource", async () => {
    const fetchMock = vi.fn<typeof fetch>();
    fetchMock.mockImplementation(async () => Response.json({}));
    vi.stubGlobal("fetch", fetchMock);
    vi.spyOn(crypto, "randomUUID").mockReturnValue("11111111-1111-4111-8111-111111111111");

    await governanceApi.queue("starter us");
    await governanceApi.reviewCase("case/id");
    await governanceApi.contributorCase("draft/id");
    await governanceApi.claim("case/id", 2);
    await governanceApi.release("case/id", { expected_revision: 2, reason: "Release." });
    await governanceApi.pause("case/id", {
      expected_revision: 2,
      reason: "Wait.",
      next_review_at: "2026-09-02T20:00:00Z",
    });
    await governanceApi.resume("case/id", { expected_revision: 2, reason: "Resume." });
    await governanceApi.recuse("case/id", { expected_revision: 2, reason: "Conflict." });
    await governanceApi.decide("case/id", {
      expected_revision: 2,
      outcome: "changes_requested",
      reason: "Clarify.",
    });
    await governanceApi.approve("case/id", {
      expected_revision: 2,
      pack_id: "starter-us",
      record_id: "soup",
      expected_base_commit: "a".repeat(40),
      files: [{ path: "packs/starter-us/soup.json", content: "{}" }],
      reason: "Verified.",
    });
    await governanceApi.respond("case/id", {
      expected_revision: 2,
      expected_draft_version: 1,
      patches: [{ field: "name", value: "Tomato soup", base_value: null, base_version: 1 }],
      public_reason: "Updated.",
    });
    await governanceApi.dispute("case/id", {
      expected_revision: 2,
      category: "accuracy",
      public_reason: "Incorrect.",
      requested_remedy: "Review it.",
    });
    await governanceApi.resolveDispute("dispute/id", {
      expected_case_revision: 2,
      expected_dispute_revision: 1,
      resolution: "Reopened.",
    });
    await governanceApi.appeal("dispute/id", {
      expected_case_revision: 2,
      expected_dispute_revision: 2,
      public_reason: "Missed evidence.",
      requested_remedy: "Independent review.",
    });
    await governanceApi.resolveAppeal("appeal/id", {
      expected_case_revision: 3,
      expected_appeal_revision: 1,
      resolution: "Upheld.",
    });

    expect(fetchMock.mock.calls.map(([path]) => path)).toEqual([
      "/api/v1/governance/review-cases?pack_id=starter%20us",
      "/api/v1/governance/review-cases/case%2Fid",
      "/api/v1/governance/contributor/review-case?draft_id=draft%2Fid",
      "/api/v1/governance/review-cases/case%2Fid/claim",
      "/api/v1/governance/review-cases/case%2Fid/release",
      "/api/v1/governance/review-cases/case%2Fid/pause",
      "/api/v1/governance/review-cases/case%2Fid/resume",
      "/api/v1/governance/review-cases/case%2Fid/recuse",
      "/api/v1/governance/review-cases/case%2Fid/decision",
      "/api/v1/governance/review-cases/case%2Fid/approve",
      "/api/v1/governance/review-cases/case%2Fid/response",
      "/api/v1/governance/review-cases/case%2Fid/disputes",
      "/api/v1/governance/disputes/dispute%2Fid/resolve",
      "/api/v1/governance/disputes/dispute%2Fid/appeal",
      "/api/v1/governance/appeals/appeal%2Fid/resolve",
    ]);
    for (const [, init] of fetchMock.mock.calls.slice(3)) {
      expect(new Headers(init?.headers).get("Idempotency-Key")).toBe(
        "11111111-1111-4111-8111-111111111111",
      );
    }
  });

  it("uses a stable message for non-JSON server failures", async () => {
    vi.stubGlobal("fetch", vi.fn(async () => new Response("upstream unavailable", { status: 503 })));

    await expect(api.session()).rejects.toMatchObject({
      name: "ApiError",
      status: 503,
      kind: "unexpected",
      message: "opennosh could not reach the server. Please try again.",
    });
  });

  it("replaces prohibited API detail with reviewed neutral copy", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn(
        async () =>
          new Response(JSON.stringify({ detail: "You " + "should eat less" }), {
            status: 422,
            headers: { "Content-Type": "application/json" },
          }),
      ),
    );

    await expect(api.session()).rejects.toMatchObject({
      name: "ApiError",
      status: 422,
      kind: "invalid-field",
      message: "That request could not be completed. Please try again.",
    });
  });

  it("preserves neutral API detail that passes the health-safety review", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn(
        async () =>
          new Response(JSON.stringify({ detail: "Email or password is incorrect" }), {
            status: 401,
            headers: { "Content-Type": "application/json" },
          }),
      ),
    );

    await expect(api.session()).rejects.toMatchObject({
      name: "ApiError",
      status: 401,
      kind: "authentication-required",
      message: "Email or password is incorrect",
    });
  });

  it("turns browser network failures into a retryable API error", async () => {
    vi.stubGlobal("fetch", vi.fn(async () => {
      throw new TypeError("Failed to fetch");
    }));

    await expect(api.session()).rejects.toMatchObject({
      name: "ApiError",
      status: undefined,
      kind: "network",
      message: "opennosh could not reach the server. Check your connection and retry.",
    });
  });

  it("forwards opaque food-search cursors with the bound request inputs", async () => {
    const fetchMock = vi.fn<typeof fetch>();
    fetchMock.mockResolvedValue(
      Response.json({
        schema_version: "2.0",
        items: [],
        limit: 12,
        has_more: false,
        next_cursor: null,
        snapshot_id: "018f5316-4f4e-7d79-b9f6-88c11a68a497",
        snapshot_expires_at: "2026-08-23T14:30:00Z",
      }),
    );
    vi.stubGlobal("fetch", fetchMock);

    await api.searchFoods(
      "green beans",
      "en-US",
      "community",
      "signed.cursor+/=",
    );

    const url = new URL(String(fetchMock.mock.calls[0][0]), "https://opennosh.test");
    expect(url.pathname).toBe("/api/v1/foods/search");
    expect(Object.fromEntries(url.searchParams)).toEqual({
      q: "green beans",
      locale: "en-US",
      limit: "12",
      source: "community",
      cursor: "signed.cursor+/=",
    });
    expect(url.searchParams.has("offset")).toBe(false);
  });

  it("omits the optional locale when a public food search has no preference", async () => {
    const fetchMock = vi.fn<typeof fetch>();
    fetchMock.mockResolvedValue(
      Response.json({
        schema_version: "2.0",
        items: [],
        limit: 12,
        has_more: false,
        next_cursor: null,
        snapshot_id: "018f5316-4f4e-7d79-b9f6-88c11a68a497",
        snapshot_expires_at: "2026-08-23T14:30:00Z",
      }),
    );
    vi.stubGlobal("fetch", fetchMock);

    await api.searchFoods("green beans");

    const url = new URL(String(fetchMock.mock.calls[0][0]), "https://opennosh.test");
    expect(url.searchParams.get("q")).toBe("green beans");
    expect(url.searchParams.has("locale")).toBe(false);
  });

  it("forwards a cancellation signal when loading a public food detail", async () => {
    const fetchMock = vi.fn<typeof fetch>();
    fetchMock.mockResolvedValue(Response.json(foodDetailFixture));
    vi.stubGlobal("fetch", fetchMock);
    const controller = new AbortController();

    await api.foodDetail("community", "rajma-masala", controller.signal);

    expect(fetchMock.mock.calls[0][1]?.signal).toBe(controller.signal);
  });

  it("uses the artifact-backed public endpoint and preserves a pinned version", async () => {
    const fetchMock = vi.fn<typeof fetch>();
    fetchMock.mockResolvedValue(Response.json({
      schema_version: "1.0",
      record: foodDetailFixture,
      release: {
        release_version: "0.52.0.0",
        published_at: "2026-08-25T12:00:00Z",
        state: "verified",
        stale_age_seconds: 0,
      },
      immutable_url: "/immutable-record",
      provenance_url: "/immutable-provenance",
    }));
    vi.stubGlobal("fetch", fetchMock);
    const controller = new AbortController();

    const record = await api.publicFoodDetail(
      "community",
      "rajma-masala",
      "hi-IN",
      controller.signal,
      "0.52.0.0",
    );

    expect(String(fetchMock.mock.calls[0][0])).toBe(
      "/api/v1/public/foods/community/rajma-masala?version=0.52.0.0",
    );
    expect(fetchMock.mock.calls[0][1]?.signal).toBe(controller.signal);
    expect(record.foodLocalePreference).toBe("hi-IN");
    expect(record.immutableUrl).toBe("/immutable-record");
  });

  it("prefers the production CSRF cookie and accepts empty success responses", async () => {
    document.cookie = "opennosh_csrf=development-token; Path=/";
    document.cookie = "__Host-opennosh-csrf=production-token; Secure; Path=/";
    const fetchMock = vi.fn<typeof fetch>();
    fetchMock.mockResolvedValue(new Response(null, { status: 204 }));
    vi.stubGlobal("fetch", fetchMock);

    await expect(api.logout()).resolves.toBeUndefined();

    const headers = new Headers(fetchMock.mock.calls[0][1]?.headers);
    expect(headers.get("X-CSRF-Token")).toBe("production-token");
    expect(headers.get("Accept")).toBe("application/json, application/problem+json");
  });

  it("uses the generated contribution-draft route and carries the requested stage", async () => {
    const fetchMock = vi.fn<typeof fetch>();
    fetchMock.mockResolvedValue(Response.json({
      schema_version: "1", workflow_version: "1",
      draft_id: "018f5316-4f4e-7d79-b9f6-88c11a68a497", draft_version: 1,
      review_state: "draft", completed_stages: [], accessible_stages: ["evidence"],
      blockers: [], next_safe_stage: "evidence", requested_stage: "evidence",
      resolved_stage: "evidence", repair_reason: null, saved_at: "2026-08-24T12:00:00Z",
      fields: { rights_acknowledged: false, duplicates_resolved: false, review_acknowledged: false },
      duplicate_candidates: [], receipt: null,
    }));
    vi.stubGlobal("fetch", fetchMock);

    await api.contributionDraft("018f5316-4f4e-7d79-b9f6-88c11a68a497", "evidence");

    const url = new URL(String(fetchMock.mock.calls[0][0]), "https://opennosh.test");
    expect(url.pathname).toBe("/api/v1/contribution-drafts/018f5316-4f4e-7d79-b9f6-88c11a68a497");
    expect(url.searchParams.get("requested_stage")).toBe("evidence");
  });

  it("uses the private evidence create, complete, status, and attach routes", async () => {
    const uploadId = "018f5316-4f4e-7d79-b9f6-88c11a68a498";
    const session = {
      upload_id: uploadId, state: "sanitized", source_draft_version: 3,
      media_type: "image/png", declared_byte_length: 8, observed_byte_length: 8,
      observed_sha256: "a".repeat(64), expires_at: "2026-09-01T12:00:00Z",
      uploaded_at: "2026-09-01T11:55:00Z", sanitized_at: "2026-09-01T11:56:00Z",
      attached_at: null, preserved_at: null, evidence_id: null, failure_code: null,
    };
    const fetchMock = vi.fn<typeof fetch>()
      .mockResolvedValueOnce(Response.json({
        upload_id: uploadId, state: "initiated", expires_at: "2026-09-01T12:00:00Z",
        max_byte_length: 10_485_760, completion_capability: "a".repeat(43),
        upload: { method: "PUT", url: "https://uploads.example.test/private", headers: { "Content-Type": "image/png" } },
      }, { status: 201 }))
      .mockResolvedValueOnce(Response.json(session))
      .mockResolvedValueOnce(Response.json(session))
      .mockResolvedValueOnce(Response.json({ ...session, state: "attached", attached_at: "2026-09-01T11:57:00Z", evidence_id: uploadId }));
    vi.stubGlobal("fetch", fetchMock);
    const draftId = "018f5316-4f4e-7d79-b9f6-88c11a68a497";

    await api.createEvidenceUpload(draftId, { source_draft_version: 3, media_type: "image/png", byte_length: 8 }, "attempt-1");
    await api.completeEvidenceUpload(draftId, uploadId, { completion_capability: "a".repeat(43) });
    await api.evidenceUpload(draftId, uploadId);
    await api.attachEvidenceUpload(draftId, uploadId, {
      source_draft_version: 3, source_description: "Front label", rights_acknowledged: true, redaction_state: "reviewed",
    });

    expect(fetchMock.mock.calls.map(([url]) => String(url))).toEqual([
      `/api/v1/contribution-drafts/${draftId}/evidence-uploads`,
      `/api/v1/contribution-drafts/${draftId}/evidence-uploads/${uploadId}/complete`,
      `/api/v1/contribution-drafts/${draftId}/evidence-uploads/${uploadId}`,
      `/api/v1/contribution-drafts/${draftId}/evidence-uploads/${uploadId}/attach`,
    ]);
    expect(new Headers(fetchMock.mock.calls[0][1]?.headers).get("Idempotency-Key")).toBe("attempt-1");
  });

  it("accepts an older safe upload-session response without newer lifecycle fields", async () => {
    const uploadId = "018f5316-4f4e-7d79-b9f6-88c11a68a498";
    const draftId = "018f5316-4f4e-7d79-b9f6-88c11a68a497";
    const fetchMock = vi.fn<typeof fetch>().mockResolvedValue(Response.json({
      upload_id: uploadId, state: "uploaded", source_draft_version: 3,
      media_type: "image/png", declared_byte_length: 8, observed_byte_length: 8,
      observed_sha256: "a".repeat(64), expires_at: "2026-09-01T12:00:00Z",
      uploaded_at: "2026-09-01T11:55:00Z", failure_code: null,
    }));
    vi.stubGlobal("fetch", fetchMock);

    await expect(api.evidenceUpload(draftId, uploadId)).resolves.toMatchObject({
      upload_id: uploadId,
      state: "uploaded",
    });
  });

  it("reads the safe contribution evidence status", async () => {
    const draftId = "018f5316-4f4e-7d79-b9f6-88c11a68a497";
    const fetchMock = vi.fn<typeof fetch>().mockResolvedValue(Response.json({
      evidence_id: "018f5316-4f4e-7d79-b9f6-88c11a68a498",
      evidence_class: "sanitized_media", source_draft_version: 3,
      public_state: null, preservation_pending: true, preservation_failed: false,
      preservation_failure_code: null,
    }));
    vi.stubGlobal("fetch", fetchMock);

    await expect(api.contributionEvidence(draftId)).resolves.toMatchObject({
      evidence_class: "sanitized_media", preservation_pending: true,
    });
    expect(fetchMock).toHaveBeenCalledWith(
      `/api/v1/contribution-drafts/${draftId}/evidence`,
      expect.any(Object),
    );
  });

  it("uploads bytes without ambient credentials or a browser-forbidden length header", async () => {
    const fetchMock = vi.fn<typeof fetch>().mockResolvedValue(new Response(null, { status: 200 }));
    vi.stubGlobal("fetch", fetchMock);
    const file = new File(["evidence"], "private-label.png", { type: "image/png" });

    await uploadEvidenceBytes({
      method: "PUT",
      url: "https://uploads.example.test/opaque-capability",
      headers: { "Content-Type": "image/png", "Content-Length": String(file.size), "If-None-Match": "*" },
    }, file);

    const options = fetchMock.mock.calls[0][1];
    const headers = new Headers(options?.headers);
    expect(options).toMatchObject({ method: "PUT", credentials: "omit", redirect: "error", cache: "no-store" });
    expect(headers.get("Content-Length")).toBeNull();
    expect(headers.get("Content-Type")).toBe("image/png");
    expect(headers.get("If-None-Match")).toBe("*");
  });
});
