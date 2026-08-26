import type {
  CustomFoodResponse as TransportCustomFood,
  FoodAttribution as TransportAttribution,
  FoodCapabilities as TransportCapabilities,
  FoodDetail as TransportDetail,
  FoodSearchItem as TransportSearchItem,
  FoodSearchResponse as TransportSearchResponse,
  OpenFoodFactsFood as TransportBarcodeFood,
  PublicFoodRecordResponse as TransportPublicFood,
} from "@/lib/generated/client/types.gen";
import type {
  BarcodeFood,
  CustomFood,
  FoodAttribution,
  FoodCapabilities,
  FoodDetail,
  FoodSearchItem,
  FoodSearchResponse,
  HouseholdPortion,
} from "@/lib/types";
import { toPublishedFoodRecordView, type FoodRecordView } from "@/lib/food-record";

// The vertical reference client validates the generated wire contract through this boundary.
export type PublicFoodRecordContract = TransportPublicFood;
export type FoodSearchContract = TransportSearchResponse;
export type FoodSearchItemContract = TransportSearchItem;

type LegacySearchResponse = Omit<
  TransportSearchResponse,
  "schema_version" | "next_cursor" | "snapshot_id" | "snapshot_expires_at"
> & {
  schema_version?: "1.0";
  offset?: number;
  next_cursor?: never;
  snapshot_id?: never;
  snapshot_expires_at?: never;
};

function schemaVersion(value: "1.0" | undefined): void {
  if (value !== undefined && value !== "1.0") {
    throw new Error("Unsupported food contract version");
  }
}

function searchSchemaVersion(value: "1.0" | "2.0" | undefined): void {
  if (value !== undefined && value !== "1.0" && value !== "2.0") {
    throw new Error("Unsupported food search contract version");
  }
}

function catalogueAttribution(value: TransportAttribution): FoodAttribution {
  return {
    source: value.source,
    license: value.license,
    source_uri: value.source_uri ?? null,
    source_license: value.source_license ?? null,
    contributed_by: value.contributed_by ?? null,
    pack_id: value.pack_id ?? null,
    pack_version: value.pack_version ?? null,
    provenance: value.provenance ?? null,
  };
}

function portions(values: Array<Record<string, unknown>> | undefined): HouseholdPortion[] {
  return (values ?? []).map((value) => {
    if (
      typeof value.name !== "string" ||
      (typeof value.grams !== "string" && typeof value.grams !== "number")
    ) {
      throw new Error("Malformed household portion contract");
    }
    return { name: value.name, grams: String(value.grams) };
  });
}

function searchItem(value: TransportSearchItem): FoodSearchItem {
  return {
    id: value.id,
    source: value.source,
    source_id: value.source_id,
    name: value.name,
    name_local: value.name_local ?? null,
    category: value.category ?? null,
    attribution: catalogueAttribution(value.attribution),
  };
}

export function foodSearch(
  value: TransportSearchResponse | LegacySearchResponse,
): FoodSearchResponse {
  searchSchemaVersion(value.schema_version);
  const current = value.schema_version === "2.0" ? value : null;
  return {
    items: value.items.map(searchItem),
    limit: value.limit,
    has_more: value.has_more,
    next_cursor: current?.next_cursor ?? null,
    snapshot_id: current?.snapshot_id ?? null,
    snapshot_expires_at: current?.snapshot_expires_at ?? null,
  };
}

export function foodDetail(value: TransportDetail): FoodDetail {
  schemaVersion(value.schema_version);
  return {
    ...searchItem(value),
    nutrients: { ...value.nutrients },
    portions: portions(value.portions),
  };
}

export function foodDetailResponse(value: unknown): FoodDetail {
  return foodDetail(value as TransportDetail);
}

export function publicFoodDetailResponse(
  value: unknown,
  foodLocale = "global",
): FoodRecordView {
  const envelope = value as Partial<TransportPublicFood>;
  if (
    envelope.schema_version !== "1.0" ||
    !envelope.record ||
    !envelope.release ||
    typeof envelope.release.release_version !== "string" ||
    typeof envelope.release.published_at !== "string" ||
    !["verified", "stale"].includes(envelope.release.state ?? "") ||
    typeof envelope.release.stale_age_seconds !== "number" ||
    typeof envelope.immutable_url !== "string" ||
    typeof envelope.provenance_url !== "string"
  ) {
    throw new Error("Malformed public food artifact contract");
  }
  return toPublishedFoodRecordView(
    foodDetail(envelope.record),
    {
      release_version: envelope.release.release_version,
      published_at: envelope.release.published_at,
      state: envelope.release.state as "verified" | "stale",
      stale_age_seconds: envelope.release.stale_age_seconds,
    },
    {
      immutable_url: envelope.immutable_url,
      provenance_url: envelope.provenance_url,
    },
    foodLocale,
  );
}

export function foodCapabilities(value: TransportCapabilities): FoodCapabilities {
  schemaVersion(value.schema_version);
  return { barcode_lookup_enabled: value.barcode_lookup_enabled };
}

export function barcodeFood(value: TransportBarcodeFood): BarcodeFood {
  schemaVersion(value.schema_version);
  return {
    id: value.id,
    source: "openfoodfacts",
    source_id: value.source_id,
    barcode: value.barcode,
    name: value.name,
    brand: value.brand ?? null,
    nutrients: { ...value.nutrients },
    portions: portions(value.portions),
    attribution: {
      source: "openfoodfacts",
      source_url: value.attribution.source_url,
      database_license: value.attribution.database_license ?? "ODbL-1.0",
      contents_license: value.attribution.contents_license ?? "DbCL-1.0",
      attribution_text: value.attribution.attribution_text,
    },
    cached: value.cached,
  };
}

export function customFood(value: TransportCustomFood): CustomFood {
  schemaVersion(value.schema_version);
  return {
    id: value.id,
    source: "custom",
    source_id: value.source_id,
    name: value.name,
    nutrients: { ...value.nutrients },
    portions: portions(value.portions),
    private: true,
  };
}
