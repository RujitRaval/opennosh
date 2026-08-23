import { cleanup, fireEvent, render, screen, within } from "@testing-library/react";
import { afterEach, describe, expect, it } from "vitest";

import { FoodRecord } from "@/components/foods/food-record";
import { foodDetail } from "@/lib/api/adapters/foods";
import { toFoodRecordView } from "@/lib/food-record";
import detailFixture from "@/tests/fixtures/contracts/foods/v1-detail-community.json";
import variantFixture from "@/tests/fixtures/contracts/foods/v1-detail-community-variant.json";

afterEach(cleanup);

function record(fixture: unknown = detailFixture) {
  return toFoodRecordView(
    foodDetail(fixture as Parameters<typeof foodDetail>[0]),
    "hi-IN",
  );
}

describe("FoodRecord", () => {
  it("renders the trust-first hierarchy in semantic DOM order", () => {
    const { container } = render(<FoodRecord record={record()} />);
    const order = [...container.querySelectorAll("[data-record-order]")].map((node) =>
      node.getAttribute("data-record-order"),
    );
    expect(order).toEqual([
      "1-identity",
      "2-trust",
      "3-serving-and-nutrients",
      "4-source-summary",
      "5-actions",
    ]);
    expect(screen.getByText("Published with provenance")).toBeVisible();
    expect(screen.getAllByText("2.4.0").length).toBeGreaterThan(0);
    expect(screen.getAllByText("CC0-1.0").length).toBeGreaterThan(0);
    expect(screen.getByText(/Hindi.*\(hi-IN\)/)).toBeVisible();
    expect(screen.getByRole("link", { name: /Correct this record/ })).toHaveAttribute(
      "href",
      expect.stringContaining("github.com/RujitRaval/opennosh/issues/new"),
    );

    const tail = [...container.querySelectorAll("[data-record-tail]")].map((node) =>
      node.getAttribute("data-record-tail"),
    );
    expect(tail).toEqual([
      "1-full-nutrients",
      "2-evidence",
      "3-history",
      "4-reuse",
    ]);
    expect(screen.getByRole("heading", { name: "What this release can prove" })).toBeVisible();
  });

  it("changes portions and US mass display while keeping canonical grams visible", () => {
    render(<FoodRecord record={record()} />);
    fireEvent.change(screen.getByLabelText("Selected portion"), { target: { value: "1" } });
    fireEvent.click(screen.getByRole("button", { name: "US" }));

    expect(screen.getByText("6.35 oz")).toBeVisible();
    expect(screen.getByText("Canonical 180 g")).toBeVisible();
    expect(screen.getAllByText("229 kcal").length).toBeGreaterThan(0);
    expect(screen.getAllByText("11.2 g").length).toBeGreaterThan(0);
  });

  it("labels conflicting variants and keeps each source and license separate", () => {
    render(<FoodRecord record={record()} variants={[record(variantFixture)]} />);
    const variants = screen
      .getByRole("heading", { name: "Same food, attached context" })
      .closest("section");
    expect(variants).not.toBeNull();
    const scope = within(variants as HTMLElement);
    expect(scope.getByText("Conflicting published values")).toBeVisible();
    expect(scope.getByText("127 kcal")).toBeVisible();
    expect(scope.getByText("168 kcal")).toBeVisible();
    expect(scope.getAllByText("CC0-1.0").length).toBeGreaterThan(0);
    expect(scope.getAllByText("CC BY 4.0").length).toBeGreaterThan(0);
  });
});
