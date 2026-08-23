import { NextRequest } from "next/server";
import { afterEach, describe, expect, it, vi } from "vitest";

import { GET, POST } from "@/app/api/v1/[...path]/route";

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

  it("forwards mutation bodies and CSRF headers", async () => {
    const fetchMock = vi.fn<typeof fetch>();
    fetchMock.mockResolvedValue(Response.json({ ok: true }));
    vi.stubGlobal("fetch", fetchMock);
    const request = new NextRequest("http://localhost:3000/api/v1/logs", {
      method: "POST",
      headers: { "Content-Type": "application/json", "X-CSRF-Token": "csrf-token" },
      body: JSON.stringify({ food: "test" }),
    });

    await POST(request, { params: Promise.resolve({ path: ["logs"] }) });

    const upstreamRequest = fetchMock.mock.calls[0][1];
    expect(new Headers(upstreamRequest?.headers).get("x-csrf-token")).toBe("csrf-token");
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
