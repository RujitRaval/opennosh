import { afterEach, describe, expect, it, vi } from "vitest";

import { dynamic, GET } from "@/app/healthz/route";

afterEach(() => {
  vi.restoreAllMocks();
});

describe("web liveness", () => {
  it("stays healthy without calling the database-aware API readiness route", async () => {
    const fetchMock = vi.fn<typeof fetch>().mockRejectedValue(
      new Error("the API and database must not be consulted"),
    );
    vi.stubGlobal("fetch", fetchMock);

    const response = GET();

    expect(dynamic).toBe("force-dynamic");
    expect(response.status).toBe(200);
    expect(response.headers.get("Cache-Control")).toBe("no-store");
    await expect(response.json()).resolves.toEqual({ status: "ok" });
    expect(fetchMock).not.toHaveBeenCalled();
  });
});
