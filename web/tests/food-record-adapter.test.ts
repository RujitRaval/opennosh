import { describe, expect, it } from "vitest";

import {
  foodDetail,
  foodDetailResponse,
  publicFoodDetailResponse,
} from "@/lib/api/adapters/foods";
import {
  foodRecordsConflict,
  formatPortionMass,
  scaledNutrientAmount,
  toFoodRecordView,
} from "@/lib/food-record";
import detailFixture from "@/tests/fixtures/contracts/foods/v1-detail-community.json";
import variantFixture from "@/tests/fixtures/contracts/foods/v1-detail-community-variant.json";

describe("public food record adapter", () => {
  it("keeps generated transport details behind the adapter boundary", () => {
    expect(foodDetailResponse(detailFixture)).toMatchObject({
      id: "community:rajma-masala",
      source: "community",
      source_id: "rajma-masala",
    });
  });

  it("binds public records to immutable release and provenance URLs", () => {
    const record = publicFoodDetailResponse(
      {
        schema_version: "1.0",
        record: detailFixture,
        release: {
          release_version: "0.52.0.0",
          published_at: "2026-08-25T12:00:00Z",
          state: "verified",
          stale_age_seconds: 0,
        },
        immutable_url: "/api/v1/public/releases/0.52.0.0/foods/community/rajma-masala",
        provenance_url: "/api/v1/public/releases/0.52.0.0/foods/community/rajma-masala/provenance",
      },
      "hi-IN",
    );

    expect(record.trust).toMatchObject({
      status: "Published with provenance",
      version: "2.4.0",
      lastVerified: null,
    });
    expect(record.immutableUrl).toContain("/releases/0.52.0.0/");
    expect(record.provenanceUrl?.endsWith("/provenance")).toBe(true);
  });

  it("makes a stale latest alias explicit without weakening its verified bytes", () => {
    const record = publicFoodDetailResponse({
      schema_version: "1.0",
      record: detailFixture,
      release: {
        release_version: "0.52.0.0",
        published_at: "2026-08-25T12:00:00Z",
        state: "stale",
        stale_age_seconds: 7200,
      },
      immutable_url: "/immutable-record",
      provenance_url: "/immutable-provenance",
    });

    expect(record.trust.status).toContain("latest alias is 2h stale");
    expect(record.trust.explanation).toContain("last cryptographically verified release");
  });

  it("keeps identity, trust, source, version, license, portions, and locale preference explicit", () => {
    const record = toFoodRecordView(
      foodDetail(detailFixture as unknown as Parameters<typeof foodDetail>[0]),
      "hi-IN",
    );

    expect(record).toMatchObject({
      id: "community:rajma-masala",
      name: "Rajma masala",
      localName: "राजमा मसाला",
      preparation: "Punjabi home-style preparation",
      recordLocale: null,
      foodLocalePreference: "hi-IN",
      packId: "north-india-home-foods",
      trust: {
        status: "Published with provenance",
        sourceClass: "Community-maintained food pack",
        version: "2.4.0",
        lastVerified: null,
      },
      license: "CC0-1.0",
      sourceLicense: "CC BY 4.0",
      provenance: "Recipe analysis checked against two household preparations",
    });
    expect(record.portions.map((portion) => portion.name)).toEqual([
      "100 g reference",
      "1 katori",
      "1 cup",
    ]);
    expect(record.nutrients.slice(0, 4).map((nutrient) => nutrient.code)).toEqual([
      "energy_kcal",
      "protein_g",
      "carbohydrate_g",
      "fat_g",
    ]);
  });

  it("scales from canonical grams and changes only the portion mass display system", () => {
    const record = toFoodRecordView(
      foodDetail(detailFixture as unknown as Parameters<typeof foodDetail>[0]),
    );
    const energy = record.nutrients.find((nutrient) => nutrient.code === "energy_kcal");
    expect(energy && scaledNutrientAmount(energy, 180)).toBeCloseTo(228.6);
    expect(formatPortionMass(180, "metric")).toBe("180 g");
    expect(formatPortionMass(180, "us")).toBe("6.35 oz");
    expect(formatPortionMass(907.184, "us")).toBe("2 lb");
  });

  it("preserves conflicting records instead of averaging them", () => {
    const records = [detailFixture, variantFixture].map((fixture) =>
      toFoodRecordView(foodDetail(fixture as unknown as Parameters<typeof foodDetail>[0])),
    );
    expect(foodRecordsConflict(records)).toBe(true);
    expect(records.map((record) => record.nutrients[0]?.amountPer100g)).toEqual([127, 168]);
    expect(records.map((record) => record.license)).toEqual(["CC0-1.0", "CC BY 4.0"]);
  });

  it("does not invent missing publication proof", () => {
    const record = toFoodRecordView(
      foodDetail({
        ...detailFixture,
        attribution: {
          ...detailFixture.attribution,
          pack_version: null,
          provenance: null,
        },
      } as unknown as Parameters<typeof foodDetail>[0]),
    );
    expect(record.trust).toMatchObject({
      status: "Published record, verification details incomplete",
      version: null,
      lastVerified: null,
    });
    expect(record.sourceSummary).toContain("does not include a separate evidence note");
  });
});
