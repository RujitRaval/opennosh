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
const cacheTtlMs = 60_000;

type CacheEntry<T> = {
  expiresAt: number;
  value: T;
};

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
  adapter: (value: unknown) => T,
  fallback: () => T,
): Promise<T> {
  try {
    const response = await fetcher(url, {
      cache: "no-store",
      headers: { Accept: "application/json" },
      signal: AbortSignal.timeout(requestTimeoutMs),
    });
    if (!response.ok) return fallback();
    return adapter(await response.json());
  } catch {
    return fallback();
  }
}

function createCachedEndpoint<T extends { state: string }>(
  resolve: () => Promise<T>,
  now: () => number,
  ttlMs: number,
) {
  let entry: CacheEntry<T> | undefined;
  let pending: Promise<T> | undefined;

  return async (): Promise<T> => {
    const currentTime = now();
    if (entry && entry.expiresAt > currentTime) return entry.value;
    if (pending) return pending;

    pending = resolve()
      .then((value) => {
        if (ttlMs > 0 && (value.state === "live" || value.state === "zero")) {
          entry = { value, expiresAt: now() + ttlMs };
        } else {
          entry = undefined;
        }
        return value;
      })
      .finally(() => {
        pending = undefined;
      });
    return pending;
  };
}

export function createPublicMissionResolvers(
  fetcher: typeof fetch = fetch,
  now: () => number = Date.now,
  ttlMs: number = process.env.OPENNOSH_VISUAL_FIXTURES === "1" ? 0 : cacheTtlMs,
) {
  const apiOrigin = (process.env.API_URL ?? "http://localhost:8000").replace(/\/$/, "");
  const catalog = createCachedEndpoint(
    () => resolveEndpoint(
      fetcher,
      `${apiOrigin}/api/v1/public/missions`,
      publicMissionCatalog,
      unavailableCatalog,
    ),
    now,
    ttlMs,
  );
  const activity = createCachedEndpoint(
    () => resolveEndpoint(
      fetcher,
      `${apiOrigin}/api/v1/public/missions/activity`,
      publicMissionActivityMap,
      unavailableActivity,
    ),
    now,
    ttlMs,
  );

  return { catalog, activity };
}

export function createPublicMissionsSnapshotResolver(
  fetcher: typeof fetch = fetch,
  now: () => number = Date.now,
  ttlMs: number = process.env.OPENNOSH_VISUAL_FIXTURES === "1" ? 0 : cacheTtlMs,
): () => Promise<PublicMissionsSnapshot> {
  const { catalog, activity } = createPublicMissionResolvers(fetcher, now, ttlMs);
  return async () => {
    const [resolvedCatalog, resolvedActivity] = await Promise.all([catalog(), activity()]);
    return { catalog: resolvedCatalog, activity: resolvedActivity };
  };
}

export async function resolvePublicMissionsSnapshot(
  fetcher: typeof fetch = fetch,
): Promise<PublicMissionsSnapshot> {
  return createPublicMissionsSnapshotResolver(fetcher)();
}

const publicMissionResolvers = createPublicMissionResolvers();
export const getPublicMissionCatalog = cache(publicMissionResolvers.catalog);
export const getPublicMissionActivity = cache(publicMissionResolvers.activity);

export const getPublicMissionsSnapshot = cache(async () => {
  const [catalog, activity] = await Promise.all([
    getPublicMissionCatalog(),
    getPublicMissionActivity(),
  ]);
  return { catalog, activity };
});
