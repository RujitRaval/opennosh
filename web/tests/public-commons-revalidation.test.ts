import { afterEach, describe, expect, it, vi } from "vitest";

const revalidateTag = vi.hoisted(() => vi.fn());

vi.mock("next/cache", () => ({ revalidateTag }));

import { POST } from "@/app/api/internal/public-commons/revalidate/route";

afterEach(() => {
  revalidateTag.mockReset();
  vi.unstubAllEnvs();
});

describe("public commons edge invalidation", () => {
  it("invalidates the complete snapshot tag after an authenticated publication", async () => {
    vi.stubEnv(
      "PUBLIC_COMMONS_REVALIDATION_TOKEN",
      "test-public-commons-operations-token",
    );
    const response = await POST(
      new Request("http://localhost/api/internal/public-commons/revalidate", {
        method: "POST",
        headers: {
          "x-opennosh-proxy-token": "test-public-commons-operations-token",
        },
      }),
    );

    expect(response.status).toBe(200);
    expect(response.headers.get("cache-control")).toBe("no-store");
    expect(revalidateTag).toHaveBeenCalledOnce();
    expect(revalidateTag).toHaveBeenCalledWith("public-commons", "max");
  });

  it("hides the route and never invalidates for a wrong token", async () => {
    vi.stubEnv(
      "PUBLIC_COMMONS_REVALIDATION_TOKEN",
      "test-public-commons-operations-token",
    );
    const response = await POST(
      new Request("http://localhost/api/internal/public-commons/revalidate", {
        method: "POST",
        headers: { "x-opennosh-proxy-token": "wrong-token" },
      }),
    );

    expect(response.status).toBe(404);
    expect(revalidateTag).not.toHaveBeenCalled();
  });

  it.each([
    ["missing configured token", undefined, "test-public-commons-operations-token"],
    ["missing supplied token", "test-public-commons-operations-token", undefined],
    ["equal-length wrong token", "test-public-commons-operations-token", "wrong-public-commons-operations-token"],
  ])("hides the route for a %s", async (_label, configured, supplied) => {
    if (configured) vi.stubEnv("PUBLIC_COMMONS_REVALIDATION_TOKEN", configured);
    const headers = supplied ? { "x-opennosh-proxy-token": supplied } : undefined;

    const response = await POST(
      new Request("http://localhost/api/internal/public-commons/revalidate", {
        method: "POST",
        headers,
      }),
    );

    expect(response.status).toBe(404);
    expect(revalidateTag).not.toHaveBeenCalled();
  });
});
