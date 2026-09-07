import { describe, expect, it, vi } from "vitest";

import { resolveFoodCatalogSummary } from "@/lib/food-catalog";

describe("food catalog server adapter", () => {
  it("accepts exact, internally consistent source counts", async () => {
    const fetcher = vi.fn<typeof fetch>().mockResolvedValue(Response.json({
      schema_version: "1.0",
      community_records: 166,
      usda_reference_records: 8_073,
      searchable_records: 8_239,
    }));

    await expect(resolveFoodCatalogSummary(fetcher)).resolves.toEqual({
      schema_version: "1.0",
      community_records: 166,
      usda_reference_records: 8_073,
      searchable_records: 8_239,
    });
    expect(fetcher).toHaveBeenCalledWith(
      new URL("/api/v1/foods/catalog-summary", "http://localhost:8000").toString(),
      expect.objectContaining({ next: { revalidate: 300, tags: ["food-catalog"] } }),
    );
  });

  it.each([
    { schema_version: "2.0", community_records: 166, usda_reference_records: 8_073, searchable_records: 8_239 },
    { schema_version: "1.0", community_records: -1, usda_reference_records: 8_073, searchable_records: 8_072 },
    { schema_version: "1.0", community_records: 166, usda_reference_records: 8_073, searchable_records: 9_999 },
  ])("fails closed for malformed counts", async (payload) => {
    const fetcher = vi.fn<typeof fetch>().mockResolvedValue(Response.json(payload));
    await expect(resolveFoodCatalogSummary(fetcher)).resolves.toBeNull();
  });

  it("fails closed when the catalog cannot be reached", async () => {
    const fetcher = vi.fn<typeof fetch>().mockRejectedValue(new Error("offline"));
    await expect(resolveFoodCatalogSummary(fetcher)).resolves.toBeNull();
  });
});
