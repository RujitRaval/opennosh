import type { FoodAttribution, FoodSource } from "@/lib/types";

type FoodAttributionProps = {
  source: FoodSource;
  attribution?: FoodAttribution;
};

function safeSourceUrl(value: string | null | undefined): string | null {
  if (!value) return null;
  try {
    const url = new URL(value);
    return url.protocol === "https:" || url.protocol === "http:" ? url.toString() : null;
  } catch {
    return null;
  }
}

export function FoodAttributionLine({ source, attribution }: FoodAttributionProps) {
  if (source === "custom") {
    return <small>Private to your account</small>;
  }

  if (source === "openfoodfacts") {
    const sourceUrl = safeSourceUrl(attribution?.source_url);
    return (
      <small>
        {attribution?.attribution_text || "Open Food Facts contributors"} · ODbL 1.0 / DbCL 1.0
        {sourceUrl ? (
          <>
            {" · "}
            <a href={sourceUrl} target="_blank" rel="noreferrer">
              Source
            </a>
          </>
        ) : null}
      </small>
    );
  }

  if (source === "usda") {
    return <small>USDA · {attribution?.license || "CC0 1.0"}</small>;
  }

  const sourceUrl = safeSourceUrl(attribution?.source_uri);
  return (
    <small>
      {attribution?.contributed_by ? `Contributed by ${attribution.contributed_by} · ` : ""}
      Community food · {attribution?.license || attribution?.source_license || "visible source license"}
      {sourceUrl ? (
        <>
          {" · "}
          <a href={sourceUrl} target="_blank" rel="noreferrer">
            Source
          </a>
        </>
      ) : null}
    </small>
  );
}
