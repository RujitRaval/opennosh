import type { FoodDetail, HouseholdPortion } from "@/lib/types";

export type FoodRecordNutrient = {
  code: string;
  label: string;
  amountPer100g: number;
  unit: string;
};

export type FoodRecordPortion = HouseholdPortion & {
  gramsValue: number;
};

export type FoodRecordView = {
  id: string;
  source: FoodDetail["source"];
  sourceId: string;
  name: string;
  localName: string | null;
  preparation: string;
  recordLocale: string | null;
  foodLocalePreference: string;
  packId: string | null;
  trust: {
    status: string;
    explanation: string;
    sourceClass: string;
    version: string | null;
    lastVerified: string | null;
  };
  portions: FoodRecordPortion[];
  nutrients: FoodRecordNutrient[];
  sourceSummary: string;
  sourceUri: string | null;
  license: string;
  sourceLicense: string | null;
  provenance: string | null;
  contributor: string | null;
  uncertainty: string;
  immutableUrl: string | null;
  provenanceUrl: string | null;
};

const nutrientNames: Record<string, string> = {
  energy_kcal: "Energy",
  energy_kj: "Energy",
  protein_g: "Protein",
  carbohydrate_g: "Carbohydrate",
  fat_g: "Fat",
  fibre_g: "Fibre",
  fiber_g: "Fibre",
  sugar_g: "Sugars",
  sodium_mg: "Sodium",
  calcium_mg: "Calcium",
  iron_mg: "Iron",
  potassium_mg: "Potassium",
};

function titleCase(value: string): string {
  return value
    .replace(/_(?:kcal|kj|mcg|mg|g|iu)$/i, "")
    .split("_")
    .filter(Boolean)
    .map((part) => `${part[0]?.toUpperCase() ?? ""}${part.slice(1)}`)
    .join(" ");
}

function nutrientUnit(code: string): string {
  return code.match(/_(kcal|kj|mcg|mg|g|iu)$/i)?.[1] ?? "";
}

function numberValue(value: unknown): number | null {
  if (typeof value !== "string" && typeof value !== "number") return null;
  const parsed = Number(value);
  return Number.isFinite(parsed) && parsed >= 0 ? parsed : null;
}

function nutrientsFrom(detail: FoodDetail): FoodRecordNutrient[] {
  const envelope = detail.nutrients;
  const values =
    typeof envelope.nutrients === "object" &&
    envelope.nutrients !== null &&
    !Array.isArray(envelope.nutrients)
      ? (envelope.nutrients as Record<string, unknown>)
      : envelope;

  return Object.entries(values)
    .flatMap(([code, raw]) => {
      const amount = numberValue(raw);
      if (amount === null || code === "basis") return [];
      return [
        {
          code,
          label: nutrientNames[code] ?? titleCase(code),
          amountPer100g: amount,
          unit: nutrientUnit(code),
        },
      ];
    })
    .sort((left, right) => {
      const priority = ["energy_kcal", "protein_g", "carbohydrate_g", "fat_g"];
      const leftIndex = priority.indexOf(left.code);
      const rightIndex = priority.indexOf(right.code);
      if (leftIndex >= 0 || rightIndex >= 0) {
        return (leftIndex < 0 ? priority.length : leftIndex) -
          (rightIndex < 0 ? priority.length : rightIndex);
      }
      return left.label.localeCompare(right.label);
    });
}

function portionsFrom(detail: FoodDetail): FoodRecordPortion[] {
  const portions = detail.portions.flatMap((portion) => {
    const gramsValue = numberValue(portion.grams);
    return gramsValue !== null && gramsValue > 0
      ? [{ ...portion, gramsValue }]
      : [];
  });
  return [
    { name: "100 g reference", grams: "100", gramsValue: 100 },
    ...portions.filter(
      (portion) =>
        portion.gramsValue !== 100 || portion.name.toLocaleLowerCase() !== "100 g reference",
    ),
  ];
}

function trustFor(detail: FoodDetail): FoodRecordView["trust"] {
  const attribution = detail.attribution;
  if (detail.source === "community") {
    const hasRelease = Boolean(attribution.pack_id && attribution.pack_version);
    const hasProvenance = Boolean(attribution.provenance);
    return {
      status:
        hasRelease && hasProvenance
          ? "Published with provenance"
          : "Published record, verification details incomplete",
      explanation:
        hasRelease && hasProvenance
          ? "This community record keeps its evidence description and pack release attached."
          : "This record is public, but this release does not provide every verification field.",
      sourceClass: "Community-maintained food pack",
      version: attribution.pack_version ?? null,
      lastVerified: null,
    };
  }

  return {
    status: "Source-qualified reference",
    explanation:
      "This record comes from the USDA reference collection and keeps the source license attached.",
    sourceClass: "Government food reference",
    version: null,
    lastVerified: null,
  };
}

function sourceSummaryFor(detail: FoodDetail): string {
  const attribution = detail.attribution;
  if (detail.source === "community") {
    const pack = attribution.pack_id ? `the ${attribution.pack_id} pack` : "a community pack";
    return attribution.provenance
      ? `Published through ${pack}. Evidence note: ${attribution.provenance}.`
      : `Published through ${pack}. This release does not include a separate evidence note.`;
  }
  return "Nutrition comes from a source-qualified USDA reference. Values retain the source license and record identifier.";
}

export function toFoodRecordView(
  detail: FoodDetail,
  foodLocalePreference = "global",
): FoodRecordView {
  return {
    id: detail.id,
    source: detail.source,
    sourceId: detail.source_id,
    name: detail.name,
    localName: detail.name_local,
    preparation: detail.category ?? "Reference preparation",
    recordLocale: null,
    foodLocalePreference,
    packId: detail.attribution.pack_id ?? null,
    trust: trustFor(detail),
    portions: portionsFrom(detail),
    nutrients: nutrientsFrom(detail),
    sourceSummary: sourceSummaryFor(detail),
    sourceUri: detail.attribution.source_uri ?? null,
    license: detail.attribution.license ?? "Not supplied",
    sourceLicense: detail.attribution.source_license ?? null,
    provenance: detail.attribution.provenance ?? null,
    contributor: detail.attribution.contributed_by ?? null,
    uncertainty:
      "Nutrition describes this reference preparation and selected portion. Ingredients and preparation can change the values.",
    immutableUrl: null,
    provenanceUrl: null,
  };
}

export function toPublishedFoodRecordView(
  detail: FoodDetail,
  release: {
    release_version: string;
    published_at: string;
    state: "verified" | "stale";
    stale_age_seconds: number;
  },
  urls: { immutable_url: string; provenance_url: string },
  foodLocalePreference = "global",
): FoodRecordView {
  const record = toFoodRecordView(detail, foodLocalePreference);
  const staleHours = Math.max(1, Math.ceil(release.stale_age_seconds / 3600));
  return {
    ...record,
    immutableUrl: urls.immutable_url,
    provenanceUrl: urls.provenance_url,
    trust: {
      ...record.trust,
      status:
        release.state === "stale"
          ? `Verified release · latest alias is ${staleHours}h stale`
          : record.trust.status,
      explanation:
        release.state === "stale"
          ? "The newest alias could not be refreshed, so opennosh is showing the last cryptographically verified release."
          : record.trust.explanation,
      version: record.trust.version,
      lastVerified:
        release.state === "stale" ? release.published_at : record.trust.lastVerified,
    },
  };
}

export function scaledNutrientAmount(
  nutrient: FoodRecordNutrient,
  portionGrams: number,
): number {
  return nutrient.amountPer100g * (portionGrams / 100);
}

export function formatNutrientAmount(amount: number, unit: string): string {
  const digits =
    unit === "kcal" || unit === "kj" ? 0 : amount < 1 ? 2 : amount < 100 ? 1 : 0;
  return `${amount.toLocaleString("en-US", {
    maximumFractionDigits: digits,
    minimumFractionDigits: digits,
  })}${unit ? ` ${unit}` : ""}`;
}

export function formatPortionMass(grams: number, system: "metric" | "us"): string {
  if (system === "metric") return `${grams.toLocaleString("en-US", { maximumFractionDigits: 1 })} g`;
  if (grams >= 453.592) {
    return `${(grams / 453.592).toLocaleString("en-US", { maximumFractionDigits: 2 })} lb`;
  }
  return `${(grams / 28.3495).toLocaleString("en-US", { maximumFractionDigits: 2 })} oz`;
}

export function foodRecordsConflict(records: readonly FoodRecordView[]): boolean {
  if (records.length < 2) return false;
  const signatures = records.map((record) => {
    const core = ["energy_kcal", "protein_g", "carbohydrate_g", "fat_g"].map(
      (code) => record.nutrients.find((nutrient) => nutrient.code === code)?.amountPer100g ?? null,
    );
    return JSON.stringify([record.license, ...core]);
  });
  return new Set(signatures).size > 1;
}
