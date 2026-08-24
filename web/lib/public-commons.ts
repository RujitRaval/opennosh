import { cache } from "react";

import { publicCommonsSnapshot } from "@/lib/api/adapters/public-commons";
import type { PublicCommonsSnapshot } from "@/lib/api/domain/public-commons";

const requestTimeoutMs = 1_500;

function unavailableSnapshot(now = new Date()): PublicCommonsSnapshot {
  return {
    schema_version: "1",
    snapshot_id: `web-unavailable-${now.toISOString().slice(0, 16)}`,
    as_of: now.toISOString(),
    state: "unavailable",
    release: null,
    verified_record_count: null,
    activity: {
      starts_at: new Date(now.getTime() - 86_400_000).toISOString(),
      ends_at: now.toISOString(),
      accepted_count: 0,
      events: [],
      most_recent_verified_record: null,
    },
    freshness: {
      release: "unavailable",
      activity: "unavailable",
      checked_at: now.toISOString(),
      stale_since: null,
    },
    reasons: ["latest_release_unavailable"],
  };
}
export async function resolvePublicCommonsSnapshot(
  fetcher: typeof fetch = fetch,
): Promise<PublicCommonsSnapshot> {
  const apiOrigin = (process.env.API_URL ?? "http://localhost:8000").replace(/\/$/, "");
  const visualFixtures = process.env.OPENNOSH_VISUAL_FIXTURES === "1";
  try {
    const response = await fetcher(`${apiOrigin}/api/v1/public/commons-snapshot`, {
      ...(visualFixtures ? { cache: "no-store" as const } : { next: { revalidate: 300 } }),
      headers: { Accept: "application/json" },
      signal: AbortSignal.timeout(requestTimeoutMs),
    });
    if (!response.ok) return unavailableSnapshot();
    const payload: unknown = await response.json();
    return publicCommonsSnapshot(payload);
  } catch {
    return unavailableSnapshot();
  }
}

// React scopes this memoization to one server render. Hero, activity, and footer
// therefore receive the same immutable object without client-side re-fetching.
export const getPublicCommonsSnapshot = cache(resolvePublicCommonsSnapshot);
