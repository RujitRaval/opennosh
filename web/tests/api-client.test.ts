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
      message: "opennosh could not reach the server. Please try again.",
    });
  });

  it("turns browser network failures into a retryable API error", async () => {
    vi.stubGlobal("fetch", vi.fn(async () => {
      throw new TypeError("Failed to fetch");
    }));

    await expect(api.session()).rejects.toMatchObject({
      name: "ApiError",
      status: 0,
      message: "opennosh could not reach the server. Check your connection and retry.",
    });
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
  });
});
