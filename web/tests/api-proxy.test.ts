import { NextRequest } from "next/server";
import { afterEach, describe, expect, it, vi } from "vitest";

import { GET, OPTIONS, POST } from "@/app/api/v1/[...path]/route";

afterEach(() => {
  vi.restoreAllMocks();
  delete process.env.API_URL;
  delete process.env.WEB_PROXY_TOKEN;
});

describe("same-origin API proxy", () => {
  it("forwards the API path, query, session cookie, and separate Set-Cookie headers", async () => {
    process.env.API_URL = "http://api:8000/";
    const upstreamHeaders = new Headers({ "Content-Type": "application/json" });
    upstreamHeaders.append("Set-Cookie", "opennosh_session=session-token; Path=/; HttpOnly");
    upstreamHeaders.append("Set-Cookie", "opennosh_csrf=csrf-token; Path=/; SameSite=Strict");
    const fetchMock = vi.fn<typeof fetch>();
    fetchMock.mockResolvedValue(
      new Response(JSON.stringify({ id: "user-id", email: "alex@example.com" }), {
        headers: upstreamHeaders,
      }),
    );
    vi.stubGlobal("fetch", fetchMock);
    const request = new NextRequest("http://localhost:3000/api/v1/auth/session?fresh=true", {
      headers: { Cookie: "opennosh_session=session-token" },
    });

    const response = await GET(request, { params: Promise.resolve({ path: ["auth", "session"] }) });

    expect(fetchMock).toHaveBeenCalledWith(
      new URL("http://api:8000/api/v1/auth/session?fresh=true"),
      expect.objectContaining({ method: "GET", cache: "no-store", redirect: "manual" }),
    );
    const upstreamRequest = fetchMock.mock.calls[0][1];
    expect(new Headers(upstreamRequest?.headers).get("cookie")).toBe("opennosh_session=session-token");
    expect(response.headers.getSetCookie()).toEqual([
      "opennosh_session=session-token; Path=/; HttpOnly",
      "opennosh_csrf=csrf-token; Path=/; SameSite=Strict",
    ]);
    expect(response.headers.get("Cache-Control")).toBe("no-store");
  });

  it("maps the same-origin health URL to the API root health endpoint", async () => {
    process.env.API_URL = "http://api:8000/";
    const fetchMock = vi.fn<typeof fetch>().mockResolvedValue(
      Response.json({ status: "ok", database: "reachable" }),
    );
    vi.stubGlobal("fetch", fetchMock);
    const request = new NextRequest("http://localhost:3000/api/v1/healthz");

    const response = await GET(request, { params: Promise.resolve({ path: ["healthz"] }) });

    expect(fetchMock).toHaveBeenCalledWith(
      new URL("http://api:8000/healthz"),
      expect.objectContaining({ method: "GET" }),
    );
    expect(response.status).toBe(200);
  });

  it("preserves a degraded database health response for Render", async () => {
    const fetchMock = vi.fn<typeof fetch>().mockResolvedValue(
      Response.json(
        { status: "degraded", database: "unreachable" },
        { status: 503 },
      ),
    );
    vi.stubGlobal("fetch", fetchMock);
    const request = new NextRequest("http://localhost:3000/api/v1/healthz");

    const response = await GET(request, { params: Promise.resolve({ path: ["healthz"] }) });

    expect(fetchMock).toHaveBeenCalledWith(
      new URL("http://localhost:8000/healthz"),
      expect.any(Object),
    );
    expect(response.status).toBe(503);
    await expect(response.json()).resolves.toMatchObject({
      status: "degraded",
      database: "unreachable",
    });
  });

  it("preserves conditional caching only for the anonymous commons snapshot", async () => {
    const upstreamHeaders = new Headers({
      "Cache-Control": "public, max-age=0, s-maxage=300, stale-if-error=86400",
      ETag: '"snapshot-etag"',
    });
    const fetchMock = vi.fn<typeof fetch>().mockResolvedValue(
      new Response(null, { status: 304, headers: upstreamHeaders }),
    );
    vi.stubGlobal("fetch", fetchMock);
    const request = new NextRequest(
      "http://localhost:3000/api/v1/public/commons-snapshot",
      {
        headers: {
          Cookie: "opennosh_session=must-not-cross-public-boundary",
          "If-None-Match": '"snapshot-etag"',
        },
      },
    );

    const response = await GET(request, {
      params: Promise.resolve({ path: ["public", "commons-snapshot"] }),
    });

    const upstreamRequest = new Headers(fetchMock.mock.calls[0][1]?.headers);
    expect(upstreamRequest.get("if-none-match")).toBe('"snapshot-etag"');
    expect(upstreamRequest.get("cookie")).toBeNull();
    expect(response.status).toBe(304);
    expect(response.headers.get("etag")).toBe('"snapshot-etag"');
    expect(response.headers.get("cache-control")).toContain("s-maxage=300");
    expect(response.headers.get("access-control-allow-origin")).toBe("*");
    expect(response.headers.get("access-control-expose-headers")).toContain("ETag");
    expect(response.headers.get("access-control-expose-headers")).toContain("Retry-After");
  });

  it("answers SDK preflights only for anonymous read routes and allowed headers", async () => {
    const request = new NextRequest("http://localhost:3000/api/v1/public/commons-snapshot", {
      method: "OPTIONS",
      headers: {
        Origin: "https://consumer.example",
        "Access-Control-Request-Method": "GET",
        "Access-Control-Request-Headers": "If-None-Match",
      },
    });

    const response = await OPTIONS(request, {
      params: Promise.resolve({ path: ["public", "commons-snapshot"] }),
    });

    expect(response.status).toBe(204);
    expect(response.headers.get("access-control-allow-origin")).toBe("*");
    expect(response.headers.get("access-control-allow-methods")).toBe("GET, HEAD, OPTIONS");
    expect(response.headers.get("access-control-allow-headers")).toContain("If-None-Match");
    expect(response.headers.get("vary")).toBeNull();

    const privateResponse = await OPTIONS(request, {
      params: Promise.resolve({ path: ["auth", "session"] }),
    });
    expect(privateResponse.status).toBe(405);

    const rejectedHeader = await OPTIONS(new NextRequest(request.url, {
      method: "OPTIONS",
      headers: {
        Origin: "https://consumer.example",
        "Access-Control-Request-Method": "GET",
        "Access-Control-Request-Headers": "Authorization",
      },
    }), { params: Promise.resolve({ path: ["public", "commons-snapshot"] }) });
    expect(rejectedHeader.status).toBe(403);
  });

  it("strips cookies and forwards Node SDK identity only on SDK read routes", async () => {
    const fetchMock = vi.fn<typeof fetch>().mockResolvedValue(Response.json({ schema_version: "1.0" }));
    vi.stubGlobal("fetch", fetchMock);
    const request = new NextRequest("http://localhost:3000/api/v1/foods/capabilities", {
      headers: {
        Cookie: "opennosh_session=must-not-cross-sdk-boundary",
        "X-OpenNosh-Client": "js/0.84.0",
      },
    });

    const response = await GET(request, {
      params: Promise.resolve({ path: ["foods", "capabilities"] }),
    });

    const upstreamRequest = new Headers(fetchMock.mock.calls[0][1]?.headers);
    expect(upstreamRequest.get("cookie")).toBeNull();
    expect(upstreamRequest.get("x-opennosh-client")).toBe("js/0.84.0");
    expect(response.headers.get("access-control-allow-origin")).toBe("*");
  });

  it("preserves mission catalog cache policy and strips session cookies", async () => {
    const upstreamHeaders = new Headers({
      "Cache-Control": "public, max-age=0, s-maxage=60, stale-if-error=300",
    });
    const fetchMock = vi.fn<typeof fetch>().mockResolvedValue(
      Response.json({ schema_version: "1.0", state: "zero", reason: null, missions: [] }, {
        headers: upstreamHeaders,
      }),
    );
    vi.stubGlobal("fetch", fetchMock);
    const request = new NextRequest("http://localhost:3000/api/v1/public/missions?limit=20", {
      headers: { Cookie: "opennosh_session=must-not-cross-public-boundary" },
    });

    const response = await GET(request, {
      params: Promise.resolve({ path: ["public", "missions"] }),
    });

    expect(new Headers(fetchMock.mock.calls[0][1]?.headers).get("cookie")).toBeNull();
    expect(response.headers.get("cache-control")).toBe(
      "public, max-age=0, s-maxage=60, stale-if-error=300",
    );
  });

  it("preserves signed artifact cache headers and strips session cookies", async () => {
    const upstreamHeaders = new Headers({
      "Cache-Control": "public, max-age=31536000, immutable",
      ETag: "\"sha256-artifact\"",
    });
    const fetchMock = vi.fn<typeof fetch>().mockResolvedValue(
      new Response("{}", { headers: upstreamHeaders }),
    );
    vi.stubGlobal("fetch", fetchMock);
    const request = new NextRequest(
      "http://localhost:3000/api/v1/public/releases/0.52.0.0/foods/community/rajma-masala",
      { headers: { Cookie: "opennosh_session=must-not-cross-public-boundary" } },
    );

    const response = await GET(request, {
      params: Promise.resolve({
        path: ["public", "releases", "0.52.0.0", "foods", "community", "rajma-masala"],
      }),
    });

    expect(new Headers(fetchMock.mock.calls[0][1]?.headers).get("cookie")).toBeNull();
    expect(response.headers.get("cache-control")).toBe(
      "public, max-age=31536000, immutable",
    );
    expect(response.headers.get("etag")).toBe("\"sha256-artifact\"");
  });

  it("forwards mutation bodies, CSRF headers, and idempotency keys", async () => {
    const fetchMock = vi.fn<typeof fetch>();
    fetchMock.mockResolvedValue(Response.json({ ok: true }));
    vi.stubGlobal("fetch", fetchMock);
    const request = new NextRequest("http://localhost:3000/api/v1/logs", {
      method: "POST",
      headers: {
        "Content-Type": "application/json",
        "Idempotency-Key": "66666666-6666-4666-8666-666666666666",
        "X-CSRF-Token": "csrf-token",
      },
      body: JSON.stringify({ food: "test" }),
    });

    await POST(request, { params: Promise.resolve({ path: ["logs"] }) });

    const upstreamRequest = fetchMock.mock.calls[0][1];
    expect(new Headers(upstreamRequest?.headers).get("x-csrf-token")).toBe("csrf-token");
    expect(new Headers(upstreamRequest?.headers).get("idempotency-key")).toBe(
      "66666666-6666-4666-8666-666666666666",
    );
    expect(await new Response(upstreamRequest?.body).text()).toBe('{"food":"test"}');
  });

  it("replaces caller proxy identity with the trusted ingress address", async () => {
    process.env.WEB_PROXY_TOKEN = "a-unique-test-proxy-token-that-is-long-enough";
    const fetchMock = vi.fn<typeof fetch>();
    fetchMock.mockResolvedValue(Response.json({ ok: true }));
    vi.stubGlobal("fetch", fetchMock);
    const request = new NextRequest("http://localhost:3000/api/v1/auth/login", {
      headers: {
        "X-Forwarded-For": "203.0.113.20",
        "X-opennosh-client-address": "198.51.100.44",
        "X-opennosh-proxy-token": "attacker-value",
      },
    });

    await GET(request, { params: Promise.resolve({ path: ["auth", "login"] }) });

    const headers = new Headers(fetchMock.mock.calls[0][1]?.headers);
    expect(headers.get("x-opennosh-client-address")).toBe("203.0.113.20");
    expect(headers.get("x-opennosh-proxy-token")).toBe(process.env.WEB_PROXY_TOKEN);
  });

  it("omits trusted proxy headers unless both the token and ingress address are present", async () => {
    process.env.WEB_PROXY_TOKEN = "a-unique-test-proxy-token-that-is-long-enough";
    const fetchMock = vi.fn<typeof fetch>();
    fetchMock.mockResolvedValue(Response.json({ ok: true }));
    vi.stubGlobal("fetch", fetchMock);
    const request = new NextRequest("http://localhost:3000/api/v1/auth/session", {
      headers: {
        "X-opennosh-client-address": "198.51.100.44",
        "X-opennosh-proxy-token": "attacker-value",
      },
    });

    await GET(request, { params: Promise.resolve({ path: ["auth", "session"] }) });

    const headers = new Headers(fetchMock.mock.calls[0][1]?.headers);
    expect(headers.get("x-opennosh-client-address")).toBeNull();
    expect(headers.get("x-opennosh-proxy-token")).toBeNull();
  });

  it("returns a cache-safe gateway error when the upstream API cannot be reached", async () => {
    const fetchMock = vi.fn<typeof fetch>();
    fetchMock.mockRejectedValue(new Error("connection refused"));
    vi.stubGlobal("fetch", fetchMock);
    const request = new NextRequest("http://localhost:3000/api/v1/auth/session");

    const response = await GET(request, { params: Promise.resolve({ path: ["auth", "session"] }) });

    expect(response.status).toBe(502);
    expect(response.headers.get("Cache-Control")).toBe("no-store");
    expect(response.headers.get("Content-Type")).toContain("application/problem+json");
    expect(response.headers.get("X-Request-ID")).toBeTruthy();
    await expect(response.json()).resolves.toMatchObject({
      type: "https://opennosh.org/problems/upstream-unavailable",
      title: "Upstream service unavailable",
      status: 502,
      detail: "opennosh could not reach the API. Please try again.",
      code: "upstream_unavailable",
      schema_version: "1.0",
      recovery_actions: [{ id: "retry", label: "Try again" }],
    });
  });

  it("adds SDK CORS to public-food gateway failures and strips ambient cookies", async () => {
    const fetchMock = vi.fn<typeof fetch>().mockRejectedValue(new Error("connection refused"));
    vi.stubGlobal("fetch", fetchMock);
    const request = new NextRequest(
      "http://localhost:3000/api/v1/public/foods/community/rajma-masala",
      { headers: { Cookie: "opennosh_session=must-not-cross-sdk-boundary" } },
    );

    const response = await GET(request, {
      params: Promise.resolve({
        path: ["public", "foods", "community", "rajma-masala"],
      }),
    });

    expect(new Headers(fetchMock.mock.calls[0][1]?.headers).get("cookie")).toBeNull();
    expect(response.status).toBe(502);
    expect(response.headers.get("access-control-allow-origin")).toBe("*");
    expect(response.headers.get("access-control-expose-headers")).toContain("X-Request-ID");
  });

  it("rejects path segments that could escape the versioned API", async () => {
    const fetchMock = vi.fn();
    vi.stubGlobal("fetch", fetchMock);
    const request = new NextRequest("http://localhost:3000/api/v1/healthz");

    const response = await GET(request, { params: Promise.resolve({ path: ["..", "healthz"] }) });

    expect(response.status).toBe(400);
    expect(response.headers.get("Content-Type")).toContain("application/problem+json");
    await expect(response.json()).resolves.toMatchObject({
      code: "invalid_request",
      schema_version: "1.0",
      status: 400,
    });
    expect(fetchMock).not.toHaveBeenCalled();
  });
});
