import { cache } from "react";

import {
  publicMissionActivityMap,
  publicMissionCatalog,
} from "@/lib/api/adapters/public-missions";
import type {
  PublicMissionActivityMap,
  PublicMissionCatalog,
  PublicMissionsSnapshot,
} from "@/lib/api/domain/public-missions";

const requestTimeoutMs = 1_500;

function unavailableCatalog(): PublicMissionCatalog {
  return { schema_version: "1.0", state: "unavailable", reason: "proof_unavailable", missions: [] };
}

function unavailableActivity(): PublicMissionActivityMap {
  return {
    schema_version: "1.0",
    state: "unavailable",
    reason: "proof_unavailable",
    minimum_cohort: 10,
    regions: [],
  };
}

async function resolveEndpoint<T>(
  fetcher: typeof fetch,
  url: string,
  tag: string,
  adapter: (value: unknown) => T,
  fallback: () => T,
): Promise<T> {
  try {
    const visualFixtures = process.env.OPENNOSH_VISUAL_FIXTURES === "1";
    const response = await fetcher(url, {
      ...(visualFixtures
        ? { cache: "no-store" as const }
        : { next: { revalidate: 60, tags: [tag] } }),
      headers: { Accept: "application/json" },
      signal: AbortSignal.timeout(requestTimeoutMs),
    });
    if (!response.ok) return fallback();
    return adapter(await response.json());
  } catch {
    return fallback();
  }
}

export async function resolvePublicMissionsSnapshot(
  fetcher: typeof fetch = fetch,
): Promise<PublicMissionsSnapshot> {
  const apiOrigin = (process.env.API_URL ?? "http://localhost:8000").replace(/\/$/, "");
  const catalog = resolveEndpoint(
    fetcher,
    `${apiOrigin}/api/v1/public/missions`,
    "public-missions",
    publicMissionCatalog,
    unavailableCatalog,
  );
  const activity = resolveEndpoint(
    fetcher,
    `${apiOrigin}/api/v1/public/missions/activity`,
    "public-mission-activity",
    publicMissionActivityMap,
    unavailableActivity,
  );
  const [resolvedCatalog, resolvedActivity] = await Promise.all([catalog, activity]);
  return { catalog: resolvedCatalog, activity: resolvedActivity };
}

export const getPublicMissionsSnapshot = cache(resolvePublicMissionsSnapshot);
