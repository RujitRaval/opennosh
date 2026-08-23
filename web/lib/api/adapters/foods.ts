import type {
  CustomFoodResponse as TransportCustomFood,
  FoodAttribution as TransportAttribution,
  FoodCapabilities as TransportCapabilities,
  FoodDetail as TransportDetail,
  FoodSearchItem as TransportSearchItem,
  FoodSearchResponse as TransportSearchResponse,
  OpenFoodFactsFood as TransportBarcodeFood,
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

function schemaVersion(value: "1.0" | undefined): void {
  if (value !== undefined && value !== "1.0") {
    throw new Error("Unsupported food contract version");
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

export function foodSearch(value: TransportSearchResponse): FoodSearchResponse {
  schemaVersion(value.schema_version);
  return {
    items: value.items.map(searchItem),
    limit: value.limit,
    offset: value.offset,
    has_more: value.has_more,
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
