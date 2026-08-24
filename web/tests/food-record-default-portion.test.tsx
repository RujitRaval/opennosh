import { cleanup, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it } from "vitest";

import { FoodRecord } from "@/components/foods/food-record";
import { foodDetail } from "@/lib/api/adapters/foods";
import { toFoodRecordView } from "@/lib/food-record";
import detailFixture from "@/tests/fixtures/contracts/foods/v1-detail-community.json";

afterEach(cleanup);

describe("FoodRecord default portion", () => {
  it("starts with the first published household portion while preserving canonical grams", () => {
    const record = toFoodRecordView(
      foodDetail(detailFixture as unknown as Parameters<typeof foodDetail>[0]),
      "hi-IN",
    );

    render(<FoodRecord record={record} />);

    expect(screen.getByLabelText("Selected portion")).toHaveValue("1");
    expect(screen.getAllByText("1 katori").length).toBeGreaterThan(0);
    expect(screen.getByText("Canonical 180 g")).toBeVisible();
    expect(screen.getAllByText("229 kcal").length).toBeGreaterThan(0);
  });

  it("falls back to the canonical reference when no household portion is published", () => {
    const detail = foodDetail(detailFixture as unknown as Parameters<typeof foodDetail>[0]);
    const record = toFoodRecordView({ ...detail, portions: [] }, "hi-IN");

    render(<FoodRecord record={record} />);

    expect(screen.getByLabelText("Selected portion")).toHaveValue("0");
    expect(screen.getAllByText("100 g reference").length).toBeGreaterThan(0);
    expect(screen.getByText("Canonical 100 g")).toBeVisible();
  });
});
