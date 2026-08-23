import AxeBuilder from "@axe-core/playwright";
import { expect, test } from "@playwright/test";

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
  await expect(page.getByRole("link", { name: /Check related records/ })).toBeVisible();
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

test("the complete record tail keeps evidence, history, and reuse in order", async ({ page }) => {
  await page.goto("/en/explore/foods/community/rajma-masala?food_locale=hi-IN#provenance");

  const tail = await page.locator("[data-record-tail]").evaluateAll((nodes) =>
    nodes.map((node) => node.getAttribute("data-record-tail")),
  );
  expect(tail).toEqual(["1-full-nutrients", "2-evidence", "3-history", "4-reuse"]);
  await expect(page.getByRole("heading", { name: "What this release can prove" })).toBeVisible();
  await expect(page.getByText("No explicitly linked variants are published for this record. It remains source-qualified on its own.")).toBeVisible();
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

test("not-found records never masquerade as nutrition", async ({ page }) => {
  const response = await page.goto("/en/explore/foods/community/missing-food?food_locale=hi-IN");
  expect(response?.status()).toBe(404);
  await expect(page.getByText("This page could not be found.")).toBeVisible();
  await expect(page.getByText("Energy")).toHaveCount(0);
});

test.describe("without JavaScript", () => {
  test.use({ javaScriptEnabled: false });

  test("identity, trust, nutrients, source, license, and provenance remain complete", async ({ page }) => {
    const response = await page.goto("/en/explore/foods/community/rajma-masala?food_locale=hi-IN");
    expect(response?.status()).toBe(200);
    await expect(page.getByRole("heading", { level: 1, name: "Rajma masala" })).toBeVisible();
    await expect(page.getByText("Published with provenance")).toBeVisible();
    await expect(page.getByText("Energy").first()).toBeVisible();
    await expect(page.getByText("CC0-1.0").first()).toBeVisible();
    await expect(page.getByRole("heading", { name: "Where this record comes from" })).toBeVisible();
  });

  test("portion and unit controls submit through the server", async ({ page }) => {
    await page.goto("/en/explore/foods/community/rajma-masala?food_locale=hi-IN");
    await page.getByLabel("Selected portion").selectOption("0");
    await page.getByRole("button", { name: "US" }).click();

    expect(new URL(page.url()).searchParams.get("portion")).toBe("0");
    expect(new URL(page.url()).searchParams.get("units")).toBe("us");
    await expect(page.getByText("3.53 oz")).toBeVisible();
    await expect(page.getByText("Canonical 100 g")).toBeVisible();
    await expect(page.getByText("127 kcal").first()).toBeVisible();
  });
});
