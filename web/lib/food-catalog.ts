import { cache } from "react";

export type FoodCatalogSummary = {
  schema_version: "1.0";
  community_records: number;
  usda_reference_records: number;
  searchable_records: number;
};

const requestTimeoutMs = 1_500;

function count(value: unknown): number | null {
  return Number.isSafeInteger(value) && Number(value) >= 0 ? Number(value) : null;
}

export async function resolveFoodCatalogSummary(
  fetcher: typeof fetch = fetch,
): Promise<FoodCatalogSummary | null> {
  const apiOrigin = (process.env.API_URL ?? "http://localhost:8000").replace(/\/$/, "");
  try {
    const response = await fetcher(`${apiOrigin}/api/v1/foods/catalog-summary`, {
      next: { revalidate: 300, tags: ["food-catalog"] },
      headers: { Accept: "application/json" },
      signal: AbortSignal.timeout(requestTimeoutMs),
    });
    if (!response.ok) return null;
    const payload: unknown = await response.json();
    if (!payload || typeof payload !== "object") return null;
    const candidate = payload as Record<string, unknown>;
    const communityRecords = count(candidate.community_records);
    const usdaReferenceRecords = count(candidate.usda_reference_records);
    const searchableRecords = count(candidate.searchable_records);
    if (
      candidate.schema_version !== "1.0"
      || communityRecords === null
      || usdaReferenceRecords === null
      || searchableRecords === null
      || searchableRecords !== communityRecords + usdaReferenceRecords
    ) return null;
    return {
      schema_version: "1.0",
      community_records: communityRecords,
      usda_reference_records: usdaReferenceRecords,
      searchable_records: searchableRecords,
    };
  } catch {
    return null;
  }
}

export const getFoodCatalogSummary = cache(resolveFoodCatalogSummary);
