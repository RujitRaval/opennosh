import "server-only";

import { foodDetailResponse } from "@/lib/api/adapters/foods";
import { toFoodRecordView } from "@/lib/food-record";
import type { CatalogueFoodSource } from "@/lib/types";

const requestIdPattern =
  /^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/;

function safeReference(value: string | null): string {
  return value && requestIdPattern.test(value) ? value : "unavailable";
}

export async function loadPublicFoodRecord({
  source,
  sourceId,
  foodLocale,
  clientAddress,
}: {
  source: CatalogueFoodSource;
  sourceId: string;
  foodLocale: string;
  clientAddress: string | null;
}) {
  const apiOrigin = (process.env.API_URL ?? "http://localhost:8000").replace(/\/$/, "");
  const requestHeaders = new Headers({ Accept: "application/json, application/problem+json" });
  const proxyToken = process.env.WEB_PROXY_TOKEN;
  if (proxyToken && clientAddress) {
    requestHeaders.set("x-opennosh-client-address", clientAddress);
    requestHeaders.set("x-opennosh-proxy-token", proxyToken);
  }
  try {
    const response = await fetch(
      `${apiOrigin}/api/v1/foods/${source}/${encodeURIComponent(sourceId)}`,
      {
        headers: requestHeaders,
        cache: "no-store",
        signal: AbortSignal.timeout(5_000),
      },
    );
    if (response.status === 404) return { kind: "not-found" } as const;
    if (!response.ok) {
      return {
        kind: "unavailable",
        reference: safeReference(response.headers.get("X-Request-ID")),
      } as const;
    }
    const detail = foodDetailResponse(await response.json());
    return { kind: "ready", record: toFoodRecordView(detail, foodLocale) } as const;
  } catch {
    return { kind: "unavailable", reference: "unavailable" } as const;
  }
}
