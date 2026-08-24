import { cleanup, render, screen, within } from "@testing-library/react";
import { afterEach, describe, expect, it } from "vitest";

import { FoodRecord } from "@/components/foods/food-record";
import { foodDetail } from "@/lib/api/adapters/foods";
import { toFoodRecordView } from "@/lib/food-record";
import detailFixture from "@/tests/fixtures/contracts/foods/v1-detail-community.json";

afterEach(cleanup);

function communityRecord() {
  return toFoodRecordView(
    foodDetail(detailFixture as unknown as Parameters<typeof foodDetail>[0]),
    "hi-IN",
  );
}

describe("FoodRecord edge cases", () => {
  it("keeps valid flat nutrients while rejecting basis and invalid amounts", () => {
    const record = toFoodRecordView({
      ...foodDetail(detailFixture as unknown as Parameters<typeof foodDetail>[0]),
      nutrients: {
        basis: "per_100g",
        energy_kcal: "101",
        resistant_starch_g: "2.5",
        negative_g: "-3",
        malformed_g: "many",
      },
    });

    expect(record.nutrients.map((nutrient) => nutrient.code)).toEqual([
      "energy_kcal",
      "resistant_starch_g",
    ]);
    expect(record.nutrients[1]).toMatchObject({
      label: "Resistant Starch",
      amountPer100g: 2.5,
      unit: "g",
    });
  });

  it("uses the source-qualified USDA trust branch without inventing release proof", () => {
    const detail = foodDetail(detailFixture as unknown as Parameters<typeof foodDetail>[0]);
    const record = toFoodRecordView({
      ...detail,
      id: "usda:169910",
      source: "usda",
      source_id: "169910",
      attribution: {
        source: "usda",
        license: "CC0-1.0",
        source_uri: "https://fdc.nal.usda.gov/fdc-app.html#/food-details/169910/nutrients",
      },
    });

    expect(record.trust).toMatchObject({
      status: "Source-qualified reference",
      sourceClass: "Government food reference",
      version: null,
      lastVerified: null,
    });
    expect(record.sourceSummary).toContain("USDA reference");
  });

  it("shows honest missing-evidence fallbacks and an explicit record history", () => {
    const record = {
      ...communityRecord(),
      localName: null,
      sourceUri: null,
      provenance: null,
      contributor: null,
    };

    render(<FoodRecord record={record} />);

    expect(screen.getByText("No public source URL supplied")).toBeVisible();
    expect(screen.getByText("No separate provenance note supplied")).toBeVisible();
    expect(screen.getByText("No public contributor credit supplied")).toBeVisible();
    const history = screen.getByRole("heading", { name: "What this release can prove" }).closest("section");
    expect(history).not.toBeNull();
    expect(within(history as HTMLElement).getByText("Earlier revisions")).toBeVisible();
    expect(within(history as HTMLElement).getAllByText("Not supplied by this release").length).toBeGreaterThan(0);
  });

  it("labels aligned variants without collapsing their separate records", () => {
    const record = communityRecord();
    const aligned = { ...record, id: "community:rajma-masala-copy", sourceId: "rajma-masala-copy" };

    render(<FoodRecord record={record} variants={[aligned]} />);

    expect(screen.getByText("Values align across these records")).toBeVisible();
    const variants = screen.getByRole("heading", { name: "Same food, attached context" }).closest("section");
    expect(variants).not.toBeNull();
    expect(within(variants as HTMLElement).getAllByRole("article")).toHaveLength(2);
  });
});
