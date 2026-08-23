import { afterEach, describe, expect, it, vi } from "vitest";

import { api } from "@/lib/api";

afterEach(() => {
  vi.restoreAllMocks();
  document.cookie = "__Host-opennosh-csrf=; Max-Age=0; Secure; Path=/";
  document.cookie = "opennosh_csrf=; Max-Age=0; Path=/";
});

describe("browser API client", () => {
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
});
