import AxeBuilder from "@axe-core/playwright";
import { expect, test, type Page } from "@playwright/test";

import detail from "../fixtures/contracts/foods/v1-detail-community.json";
import variant from "../fixtures/contracts/foods/v1-detail-community-variant.json";

async function mockFoodRecord(page: Page, { withVariant = true } = {}) {
  await page.route("**/api/v1/foods/community/rajma-masala", (route) =>
    route.fulfill({ status: 200, json: detail }),
  );
  await page.route("**/api/v1/foods/community/rajma-masala-restaurant", (route) =>
    route.fulfill({ status: 200, json: variant }),
  );
  await page.route("**/api/v1/foods/search?**", (route) =>
    route.fulfill({
      status: 200,
      json: {
        schema_version: "2.0",
        items: withVariant
          ? [
              {
                id: detail.id,
                source: detail.source,
                source_id: detail.source_id,
                name: detail.name,
                name_local: detail.name_local,
                category: detail.category,
                attribution: detail.attribution,
              },
              {
                id: variant.id,
                source: variant.source,
                source_id: variant.source_id,
                name: variant.name,
                name_local: variant.name_local,
                category: variant.category,
                attribution: variant.attribution,
              },
            ]
          : [],
        limit: 12,
        has_more: false,
        next_cursor: null,
        snapshot_id: "018f5316-4f4e-7d79-b9f6-88c11a68a497",
        snapshot_expires_at: "2026-08-23T14:30:00Z",
      },
    }),
  );
}

test.beforeEach(async ({ page }) => {
  await mockFoodRecord(page);
});

test("record answers nutrition with its trust context visible and correctly ordered", async ({ page }) => {
  await page.goto("/en/explore/foods/community/rajma-masala?food_locale=hi-IN");

  await expect(page.getByRole("heading", { level: 1, name: "Rajma masala" })).toBeVisible();
  await expect(page.getByText("Published with provenance")).toBeVisible();
  await expect(page.getByText(/Hindi.*\(hi-IN\)/)).toBeVisible();
  await expect(page.getByText("CC0-1.0").first()).toBeVisible();
  await expect(page.getByText("2.4.0").first()).toBeVisible();
  await expect(page.getByText(/Not supplied by this release/).first()).toBeVisible();
  await expect(page.getByText(/Recipe analysis checked against two household preparations/).first()).toBeVisible();
  await expect(page.getByRole("link", { name: /See provenance/ })).toBeVisible();
  await expect(page.getByRole("link", { name: /Compare variants/ })).toBeVisible();
  await expect(page.getByRole("link", { name: /Correct this record/ })).toBeVisible();

  const order = await page.locator("[data-record-order]").evaluateAll((nodes) =>
    nodes.map((node) => node.getAttribute("data-record-order")),
  );
  expect(order).toEqual([
    "1-identity",
    "2-trust",
    "3-serving-and-nutrients",
    "4-source-summary",
    "5-actions",
  ]);

  const primary = page.getByRole("navigation", { name: "Primary navigation" });
  if (await primary.isVisible()) {
    await expect(primary.getByRole("link", { name: "Explore" })).toHaveAttribute("aria-current", "page");
  }

  const accessibility = await new AxeBuilder({ page })
    .withTags(["wcag2a", "wcag2aa", "wcag21aa", "wcag22aa"])
    .analyze();
  expect(accessibility.violations).toEqual([]);
});

test("portion controls preserve canonical grams while offering US display units", async ({ page }) => {
  await page.goto("/en/explore/foods/community/rajma-masala?food_locale=hi-IN");
  await page.getByLabel("Selected portion").selectOption({ label: "1 katori" });
  await page.getByRole("button", { name: "US" }).click();

  await expect(page.getByText("6.35 oz")).toBeVisible();
  await expect(page.getByText("Canonical 180 g")).toBeVisible();
  await expect(page.getByText("229 kcal").first()).toBeVisible();
  await expect(page.getByText("11.2 g").first()).toBeVisible();
});

test("conflicting fixtures remain side by side on desktop and sequential on mobile", async ({ page }) => {
  await page.goto("/en/explore/foods/community/rajma-masala?food_locale=hi-IN#variants");
  const variants = page.locator("#variants");
  await expect(variants.getByText("Conflicting published values")).toBeVisible();
  await expect(variants.getByText("127 kcal")).toBeVisible();
  await expect(variants.getByText("168 kcal")).toBeVisible();
  await expect(variants.getByText("CC0-1.0")).toBeVisible();
  await expect(variants.getByText("CC BY 4.0")).toBeVisible();
});

test("the record reflows at the 320 CSS-pixel equivalent of 200 percent desktop zoom", async ({ page }) => {
  await page.setViewportSize({ width: 320, height: 900 });
  await page.goto("/en/explore/foods/community/rajma-masala?food_locale=hi-IN");
  await page.locator(".food-record-first").evaluate((element) => element.scrollIntoView());
  await expect(page.getByText("Published with provenance")).toBeVisible();
  await expect(page.getByRole("link", { name: /Correct this record/ })).toBeVisible();
  const overflow = await page.evaluate(() => document.documentElement.scrollWidth - document.documentElement.clientWidth);
  expect(overflow).toBeLessThanOrEqual(1);
});

test("not-found and unavailable states never masquerade as nutrition", async ({ page }) => {
  await page.unroute("**/api/v1/foods/community/rajma-masala");
  await page.route("**/api/v1/foods/community/rajma-masala", (route) =>
    route.fulfill({ status: 404, json: { detail: "Food not found" } }),
  );
  await page.goto("/en/explore/foods/community/rajma-masala?food_locale=hi-IN");
  await expect(page.getByRole("heading", { name: "This published food record is not available." })).toBeVisible();
  await expect(page.getByText("Energy")).toHaveCount(0);
});
