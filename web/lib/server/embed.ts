import { publicFoodDetailResponse } from "@/lib/api/adapters/foods";
import type { FoodRecordView } from "@/lib/food-record";
import { getCatalog, type MessageCatalog } from "@/lib/i18n/catalog";
import { resolveInterfaceLanguage } from "@/lib/routes";
import type { CatalogueFoodSource } from "@/lib/types";

export const EMBED_PROTOCOL_VERSION = "1.0";
export const EMBED_MAX_RESPONSE_BYTES = 512 * 1024;

const sources: readonly CatalogueFoodSource[] = ["usda", "community"];
const sourceIdPattern = /^[a-z0-9]+(?:-[a-z0-9]+)*$/;
const releaseVersionPattern = /^[0-9]+\.[0-9]+\.[0-9]+\.[0-9]+$/;
const loopbackHosts = new Set(["localhost", "127.0.0.1", "[::1]"]);

type EmbedKind = "food" | "provenance";

type EmbedRequest = {
  kind: EmbedKind;
  source: string;
  sourceId: string;
  releaseVersion?: string;
};

type LoadedRecord = {
  record: FoodRecordView;
  releaseState: "verified" | "stale";
};

function escapeHtml(value: string): string {
  return value
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;")
    .replaceAll('"', "&quot;")
    .replaceAll("'", "&#39;");
}

function safeFrameAncestorOrigin(token: string): boolean {
  try {
    const parsed = new URL(token);
    if (parsed.origin !== token || parsed.username || parsed.password) return false;
    return parsed.protocol === "https:"
      || (parsed.protocol === "http:" && loopbackHosts.has(parsed.hostname));
  } catch {
    return false;
  }
}

function safeFrameAncestors(value: string | undefined): string {
  if (!value?.trim()) return "'none'";
  const tokens = value.trim().split(/\s+/);
  if (tokens.includes("'none'") && tokens.length !== 1) return "'none'";
  if (
    tokens.some(
      (token) =>
        token !== "'none'" &&
        token !== "'self'" &&
        token !== "https:" &&
        !safeFrameAncestorOrigin(token),
    )
  ) {
    return "'none'";
  }
  return tokens.join(" ");
}

export function embedContentSecurityPolicy(frameAncestors?: string): string {
  return [
    "default-src 'none'",
    "style-src 'self'",
    "script-src 'self'",
    "img-src 'self' data:",
    "connect-src 'self'",
    "base-uri 'none'",
    "form-action 'none'",
    `frame-ancestors ${safeFrameAncestors(frameAncestors)}`,
  ].join("; ");
}

export function embedHeaders(frameAncestors = process.env.OPENNOSH_EMBED_FRAME_ANCESTORS): Headers {
  return new Headers({
    "Cache-Control": "public, max-age=60, stale-while-revalidate=300",
    "Content-Security-Policy": embedContentSecurityPolicy(frameAncestors),
    "Content-Type": "text/html; charset=utf-8",
    "Permissions-Policy":
      "accelerometer=(), attribution-reporting=(), browsing-topics=(), camera=(), geolocation=(), gyroscope=(), microphone=(), payment=(), usb=()",
    "Referrer-Policy": "no-referrer",
    Vary: "Accept-Language",
    "X-Content-Type-Options": "nosniff",
  });
}

function responseBytes(response: Response): number | null {
  const value = response.headers.get("content-length");
  if (value === null || !/^[0-9]+$/.test(value)) return null;
  const parsed = Number(value);
  return Number.isSafeInteger(parsed) ? parsed : EMBED_MAX_RESPONSE_BYTES + 1;
}

async function loadRecord(input: EmbedRequest): Promise<LoadedRecord | "not-found" | "unavailable"> {
  if (
    !sources.includes(input.source as CatalogueFoodSource) ||
    !sourceIdPattern.test(input.sourceId) ||
    (input.releaseVersion !== undefined && !releaseVersionPattern.test(input.releaseVersion))
  ) {
    return "not-found";
  }

  const apiOrigin = (process.env.API_URL ?? "http://localhost:8000").replace(/\/$/, "");
  const encodedId = encodeURIComponent(input.sourceId);
  const path = input.releaseVersion
    ? `/api/v1/public/releases/${input.releaseVersion}/foods/${input.source}/${encodedId}`
    : `/api/v1/public/foods/${input.source}/${encodedId}`;
  try {
    const response = await fetch(`${apiOrigin}${path}`, {
      cache: "no-store",
      headers: { Accept: "application/json, application/problem+json" },
      redirect: "manual",
      signal: AbortSignal.timeout(5_000),
    });
    if (response.status === 404) return "not-found";
    if (!response.ok || response.status >= 300) return "unavailable";
    if (!response.headers.get("content-type")?.toLowerCase().startsWith("application/json")) {
      return "unavailable";
    }
    if ((responseBytes(response) ?? 0) > EMBED_MAX_RESPONSE_BYTES) return "unavailable";
    const bytes = await response.arrayBuffer();
    if (bytes.byteLength > EMBED_MAX_RESPONSE_BYTES) return "unavailable";
    const payload: unknown = JSON.parse(new TextDecoder().decode(bytes));
    const record = publicFoodDetailResponse(payload);
    const release = (payload as {
      release?: {
        published_at?: unknown;
        release_version?: unknown;
        stale_age_seconds?: unknown;
        state?: unknown;
      };
    }).release;
    const releaseState = release?.state;
    const expectedImmutableUrl =
      `/api/v1/public/releases/${record.trust.version}/foods/${input.source}/${input.sourceId}`;
    if (
      (releaseState !== "verified" && releaseState !== "stale") ||
      !record.trust.version ||
      !releaseVersionPattern.test(record.trust.version) ||
      (input.releaseVersion !== undefined && record.trust.version !== input.releaseVersion) ||
      record.source !== input.source ||
      record.sourceId !== input.sourceId ||
      record.immutableUrl !== expectedImmutableUrl ||
      record.provenanceUrl !== `${expectedImmutableUrl}/provenance` ||
      typeof release?.published_at !== "string" ||
      Number.isNaN(Date.parse(release.published_at)) ||
      typeof release.stale_age_seconds !== "number" ||
      !Number.isSafeInteger(release.stale_age_seconds) ||
      release.stale_age_seconds < 0 ||
      !record.license.trim() ||
      record.license === "Not supplied"
    ) {
      return "unavailable";
    }
    return { record, releaseState };
  } catch {
    return "unavailable";
  }
}

function documentShell({
  body,
  language,
  title,
}: {
  body: string;
  language: string;
  title: string;
}): string {
  return `<!doctype html>
<html lang="${escapeHtml(language)}">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <meta name="color-scheme" content="light">
  <title>${escapeHtml(title)}</title>
  <link rel="stylesheet" href="/embed/v1/embed.css">
  <script src="/embed/v1/resize.js" defer></script>
</head>
<body>${body}</body>
</html>`;
}

function unavailableDocument(
  language: string,
  status: 404 | 503,
  copy: MessageCatalog["embed"],
): string {
  const heading = status === 404 ? copy.notFoundTitle : copy.unavailableTitle;
  const detail =
    status === 404
      ? copy.notFoundBody
      : copy.unavailableBody;
  return documentShell({
    language,
    title: `${heading} - opennosh`,
    body: `<main class="embed-card embed-unavailable" data-embed-state="unavailable">
  <p class="embed-eyebrow">${escapeHtml(copy.brand)} / ${escapeHtml(copy.proofRequired)}</p>
  <h1>${escapeHtml(heading)}</h1>
  <p>${escapeHtml(detail)}</p>
</main>`,
  });
}

function verificationLabel(
  state: "verified" | "stale",
  copy: MessageCatalog["embed"],
): string {
  return state === "stale" ? copy.staleVerified : copy.verified;
}

function attributionLabel(record: FoodRecordView): string {
  return record.contributor ?? record.packId ?? `${record.source} source`;
}

function proofLedger(
  record: FoodRecordView,
  state: "verified" | "stale",
  copy: MessageCatalog["embed"],
): string {
  return `<dl class="embed-proof" aria-label="${escapeHtml(copy.publicationProof)}">
    <div><dt>${escapeHtml(copy.source)}</dt><dd>${escapeHtml(`${record.source}:${record.sourceId}`)}</dd></div>
    <div><dt>${escapeHtml(copy.license)}</dt><dd>${escapeHtml(record.license)}</dd></div>
    <div><dt>${escapeHtml(copy.attribution)}</dt><dd>${escapeHtml(attributionLabel(record))}</dd></div>
    <div><dt>${escapeHtml(copy.release)}</dt><dd>${escapeHtml(record.trust.version ?? copy.unavailableTitle)}</dd></div>
    <div><dt>${escapeHtml(copy.verification)}</dt><dd>${escapeHtml(verificationLabel(state, copy))}</dd></div>
  </dl>`;
}

function foodDocument(
  record: FoodRecordView,
  state: "verified" | "stale",
  language: string,
  copy: MessageCatalog["embed"],
): string {
  const nutrients = record.nutrients.slice(0, 4).map(
    (nutrient) => `<li><strong>${escapeHtml(String(nutrient.amountPer100g))}${escapeHtml(nutrient.unit)}</strong><span>${escapeHtml(nutrient.label)}</span></li>`,
  ).join("");
  return documentShell({
    language,
    title: `${record.name} - opennosh`,
    body: `<main class="embed-card" data-embed-state="${state === "stale" ? "stale-verified" : "verified"}">
  <header>
    <p class="embed-eyebrow">${escapeHtml(copy.brand)} / ${escapeHtml(copy.foodRecord)}</p>
    <p class="embed-state">${escapeHtml(verificationLabel(state, copy))}</p>
    <h1>${escapeHtml(record.name)}</h1>
    <p>${escapeHtml(record.preparation)}</p>
  </header>
  ${nutrients ? `<ul class="embed-nutrients" aria-label="${escapeHtml(copy.nutrients)}">${nutrients}</ul>` : ""}
  ${proofLedger(record, state, copy)}
  <footer><a href="${escapeHtml(record.provenanceUrl ?? "")}" target="_blank" rel="noopener noreferrer">${escapeHtml(copy.directProvenance)} <span aria-hidden="true">↗</span></a></footer>
</main>`,
  });
}

function provenanceDocument(
  record: FoodRecordView,
  state: "verified" | "stale",
  language: string,
  copy: MessageCatalog["embed"],
): string {
  return documentShell({
    language,
    title: `${record.name} provenance - opennosh`,
    body: `<main class="embed-card" data-embed-state="${state === "stale" ? "stale-verified" : "verified"}">
  <header>
    <p class="embed-eyebrow">${escapeHtml(copy.brand)} / ${escapeHtml(copy.provenance)}</p>
    <p class="embed-state">${escapeHtml(verificationLabel(state, copy))}</p>
    <h1>${escapeHtml(record.name)}</h1>
    <p>${escapeHtml(record.provenance ?? copy.noProvenance)}</p>
  </header>
  ${proofLedger(record, state, copy)}
  <footer><a href="${escapeHtml(record.provenanceUrl ?? "")}" target="_blank" rel="noopener noreferrer">${escapeHtml(copy.openProvenance)} <span aria-hidden="true">↗</span></a></footer>
</main>`,
  });
}

export async function renderEmbed(request: Request, input: EmbedRequest): Promise<Response> {
  const language = resolveInterfaceLanguage({
    acceptLanguage: request.headers.get("accept-language") ?? undefined,
  });
  const copy = getCatalog(language);
  const loaded = await loadRecord(input);
  const headers = embedHeaders();
  headers.set("Content-Language", language);
  if (loaded === "not-found") {
    return new Response(unavailableDocument(language, 404, copy.embed), { headers, status: 404 });
  }
  if (loaded === "unavailable") {
    return new Response(unavailableDocument(language, 503, copy.embed), { headers, status: 503 });
  }
  const body = input.kind === "food"
    ? foodDocument(loaded.record, loaded.releaseState, language, copy.embed)
    : provenanceDocument(loaded.record, loaded.releaseState, language, copy.embed);
  headers.set("X-OpenNosh-Embed", `${EMBED_PROTOCOL_VERSION}; ${input.kind}`);
  return new Response(body, {
    headers,
    status: 200,
  });
}
