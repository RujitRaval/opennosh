import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import {
  EMBED_MAX_RESPONSE_BYTES,
  embedContentSecurityPolicy,
  embedHeaders,
  renderEmbed,
} from "@/lib/server/embed";
import detailFixture from "@/tests/fixtures/contracts/foods/v1-detail-community.json";

const provenanceUrl =
  "/api/v1/public/releases/0.86.0.0/foods/community/rajma-masala/provenance";

function publicFood(state: "verified" | "stale" = "verified") {
  return {
    schema_version: "1.0",
    record: detailFixture,
    release: {
      release_version: "0.86.0.0",
      published_at: "2026-09-03T20:00:00Z",
      state,
      stale_age_seconds: state === "stale" ? 120 : 0,
    },
    immutable_url:
      "/api/v1/public/releases/0.86.0.0/foods/community/rajma-masala",
    provenance_url: provenanceUrl,
  };
}

function apiResponse(body: unknown, init: ResponseInit = {}) {
  return new Response(JSON.stringify(body), {
    ...init,
    headers: { "content-type": "application/json", ...init.headers },
  });
}

describe("tracking-free embed contract", () => {
  beforeEach(() => {
    vi.stubEnv("API_URL", "https://api.internal");
    vi.stubEnv("OPENNOSH_EMBED_FRAME_ANCESTORS", "https: http://localhost:3000");
  });

  afterEach(() => {
    vi.unstubAllEnvs();
    vi.restoreAllMocks();
  });

  it("renders a proof-bearing food card without ambient credentials or trackers", async () => {
    const fetchMock = vi.spyOn(globalThis, "fetch").mockResolvedValue(apiResponse(publicFood()));

    const response = await renderEmbed(
      new Request("https://opennosh.org/embed/v1/foods/community/rajma-masala", {
        headers: { "accept-language": "en-US,en;q=0.9" },
      }),
      { kind: "food", source: "community", sourceId: "rajma-masala" },
    );
    const html = await response.text();

    expect(response.status).toBe(200);
    expect(response.headers.get("x-opennosh-embed")).toBe("1.0; food");
    expect(response.headers.get("set-cookie")).toBeNull();
    expect(response.headers.get("x-frame-options")).toBeNull();
    expect(response.headers.get("content-security-policy")).toContain(
      "frame-ancestors https: http://localhost:3000",
    );
    expect(html).toContain('data-embed-state="verified"');
    expect(html).toContain("Rajma masala");
    expect(html).toContain("CC0-1.0");
    expect(html).toContain("Punjab Foods Collective");
    expect(html).toContain("0.86.0.0");
    expect(html).toContain(provenanceUrl);
    expect(html).not.toMatch(/analytics|beacon|localStorage|sessionStorage/i);
    expect(fetchMock).toHaveBeenCalledWith(
      "https://api.internal/api/v1/public/foods/community/rajma-masala",
      expect.objectContaining({
        cache: "no-store",
        headers: { Accept: "application/json, application/problem+json" },
        redirect: "manual",
      }),
    );
  });

  it("renders an exact-release stale proof without upgrading its state", async () => {
    vi.spyOn(globalThis, "fetch").mockResolvedValue(apiResponse(publicFood("stale")));

    const response = await renderEmbed(
      new Request(
        "https://opennosh.org/embed/v1/releases/0.86.0.0/foods/community/rajma-masala/provenance",
      ),
      {
        kind: "provenance",
        releaseVersion: "0.86.0.0",
        source: "community",
        sourceId: "rajma-masala",
      },
    );
    const html = await response.text();

    expect(response.status).toBe(200);
    expect(response.headers.get("x-opennosh-embed")).toBe("1.0; provenance");
    expect(html).toContain('data-embed-state="stale-verified"');
    expect(html).toContain("Stale, cryptographically verified");
    expect(html).toContain("Recipe analysis checked against two household preparations");
    expect(globalThis.fetch).toHaveBeenCalledWith(
      "https://api.internal/api/v1/public/releases/0.86.0.0/foods/community/rajma-masala",
      expect.any(Object),
    );
  });

  it("fails closed for invalid inputs, unbound proof, redirects, and oversized bodies", async () => {
    const fetchMock = vi.spyOn(globalThis, "fetch");
    const invalid = await renderEmbed(
      new Request("https://opennosh.org/embed/v1/foods/community/not%20safe"),
      { kind: "food", source: "community", sourceId: "not safe" },
    );
    expect(invalid.status).toBe(404);
    expect(fetchMock).not.toHaveBeenCalled();

    fetchMock.mockResolvedValueOnce(
      apiResponse({ ...publicFood(), provenance_url: "https://attacker.example/proof" }),
    );
    const unproven = await renderEmbed(new Request("https://opennosh.org/embed"), {
      kind: "food",
      source: "community",
      sourceId: "rajma-masala",
    });
    expect(unproven.status).toBe(503);
    expect(await unproven.text()).toContain("embed is withheld");

    fetchMock.mockResolvedValueOnce(
      apiResponse({
        ...publicFood(),
        immutable_url: "/api/v1/public/releases/0.86.0.0/foods/community/another-food",
        provenance_url:
          "/api/v1/public/releases/0.86.0.0/foods/community/another-food/provenance",
      }),
    );
    const misbound = await renderEmbed(new Request("https://opennosh.org/embed"), {
      kind: "food",
      source: "community",
      sourceId: "rajma-masala",
    });
    expect(misbound.status).toBe(503);

    fetchMock.mockResolvedValueOnce(apiResponse(publicFood()));
    const mismatchedRelease = await renderEmbed(new Request("https://opennosh.org/embed"), {
      kind: "provenance",
      releaseVersion: "0.87.0.0",
      source: "community",
      sourceId: "rajma-masala",
    });
    expect(mismatchedRelease.status).toBe(503);

    fetchMock.mockResolvedValueOnce(new Response(null, { status: 302 }));
    const redirected = await renderEmbed(new Request("https://opennosh.org/embed"), {
      kind: "food",
      source: "community",
      sourceId: "rajma-masala",
    });
    expect(redirected.status).toBe(503);

    fetchMock.mockResolvedValueOnce(
      new Response(JSON.stringify(publicFood()), { headers: { "content-type": "text/plain" } }),
    );
    const wrongMediaType = await renderEmbed(new Request("https://opennosh.org/embed"), {
      kind: "food",
      source: "community",
      sourceId: "rajma-masala",
    });
    expect(wrongMediaType.status).toBe(503);

    fetchMock.mockResolvedValueOnce(
      new Response("{}", { headers: { "content-length": String(EMBED_MAX_RESPONSE_BYTES + 1) } }),
    );
    const oversized = await renderEmbed(new Request("https://opennosh.org/embed"), {
      kind: "food",
      source: "community",
      sourceId: "rajma-masala",
    });
    expect(oversized.status).toBe(503);
  });

  it("fails closed when a frame-ancestor setting could inject CSP", () => {
    expect(embedContentSecurityPolicy("https://good.example; script-src *")).toContain(
      "frame-ancestors 'none'",
    );
    expect(embedContentSecurityPolicy("'none' https:")).toContain("frame-ancestors 'none'");
    expect(embedContentSecurityPolicy("https://partner.example/path")).toContain(
      "frame-ancestors 'none'",
    );
    expect(embedContentSecurityPolicy("https://partner.example:99999")).toContain(
      "frame-ancestors 'none'",
    );
    expect(embedContentSecurityPolicy("https://partner.example")).toContain(
      "frame-ancestors https://partner.example",
    );
    expect(embedHeaders("https:").has("x-frame-options")).toBe(false);
  });
});
