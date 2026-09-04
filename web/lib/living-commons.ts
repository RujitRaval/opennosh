import { cache } from "react";

import {
  publicImpactSnapshot,
  publicOperationsSnapshot,
  publicReuseSnapshot,
} from "@/lib/api/adapters/living-commons";
import type {
  PublicImpactSnapshot,
  PublicOperationsSnapshot,
  PublicReuseSnapshot,
} from "@/lib/api/domain/living-commons";

const timeoutMs = 1_500;

async function read(fetcher: typeof fetch, path: string): Promise<unknown> {
  const origin = (process.env.API_URL ?? "http://localhost:8000").replace(/\/$/, "");
  const response = await fetcher(`${origin}${path}`, {
    cache: "no-store",
    headers: { Accept: "application/json" },
    signal: AbortSignal.timeout(timeoutMs),
  });
  if (!response.ok) throw new Error("Living Commons proof unavailable");
  return response.json();
}

export async function resolvePublicReuse(fetcher: typeof fetch = fetch): Promise<PublicReuseSnapshot> {
  try {
    const [registry, dependencies] = await Promise.all([
      read(fetcher, "/api/v1/public/reuse"),
      read(fetcher, "/api/v1/public/reuse/dependencies"),
    ]);
    return publicReuseSnapshot(registry, dependencies);
  } catch {
    return { state: "unavailable", declarations: [], dependencies: [] };
  }
}

export async function resolvePublicImpact(fetcher: typeof fetch = fetch): Promise<PublicImpactSnapshot> {
  try {
    return publicImpactSnapshot(await read(fetcher, "/api/v1/public/impact"));
  } catch {
    return {
      schema_version: "1.0",
      state: "unavailable",
      reason: "proof_unavailable",
      metric_definition_version: "1.0",
      observed_at: new Date(0).toISOString(),
      source_checkpoint_id: null,
      minimum_cohort: 10,
      global: {
        verified_adopters: 0,
        community_declarations: 0,
        accepted_contributions: 0,
        pack_installs: 0,
        api_reads: 0,
        artifact_downloads: 0,
      },
      regions: [],
      digest: "0".repeat(64),
    };
  }
}

export async function resolvePublicOperations(
  fetcher: typeof fetch = fetch,
): Promise<PublicOperationsSnapshot> {
  try {
    const [status, incidents] = await Promise.all([
      read(fetcher, "/api/v1/public/status"),
      read(fetcher, "/api/v1/public/incidents"),
    ]);
    return publicOperationsSnapshot(status, incidents);
  } catch {
    return { state: "unavailable", configuration_digest: null, components: [], incidents: [] };
  }
}

export const getPublicReuse = cache(resolvePublicReuse);
export const getPublicImpact = cache(resolvePublicImpact);
export const getPublicOperations = cache(resolvePublicOperations);
