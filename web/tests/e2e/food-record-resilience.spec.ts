import { expect, test } from "@playwright/test";

import detail from "../fixtures/contracts/foods/v1-detail-community.json";

const publicDetail = {
  schema_version: "1.0",
  record: detail,
  release: {
    release_version: "0.52.0.0",
    published_at: "2026-08-25T12:00:00Z",
    state: "verified",
    stale_age_seconds: 0,
  },
  immutable_url: "/api/v1/public/releases/0.52.0.0/foods/community/rajma-masala",
  provenance_url: "/api/v1/public/releases/0.52.0.0/foods/community/rajma-masala/provenance",
};

test("global, malformed, and repeated locale preferences remain honest", async ({ page }) => {
  await page.goto("/en/explore/foods/community/rajma-masala");
  await expect(page.getByText("Global / no preference")).toBeVisible();

  await page.goto("/en/explore/foods/community/rajma-masala?food_locale=../../etc");
  await expect(page.getByText("Global / no preference")).toBeVisible();

  await page.goto("/en/explore/foods/community/rajma-masala?food_locale=hi-IN&food_locale=fr-FR");
  await expect(page.getByText(/Hindi.*\(hi-IN\)/)).toBeVisible();
});

test("the page does not infer same-food variants from fuzzy search", async ({ page }) => {
  let searchRequests = 0;
  await page.route("**/api/v1/foods/search?**", (route) => {
    searchRequests += 1;
    return route.abort();
  });

  await page.goto("/en/explore/foods/community/rajma-masala?food_locale=hi-IN");

  await expect(page.getByRole("heading", { level: 1, name: "Rajma masala" })).toBeVisible();
  await expect(page.getByText("No explicitly linked variants are published for this record. It remains source-qualified on its own.")).toBeVisible();
  expect(searchRequests).toBe(0);
});

test("a server-side primary-record failure exposes retry and recovers cleanly", async ({ page }) => {
  await page.route("**/api/v1/public/foods/community/unavailable-food", (route) =>
    route.fulfill({ status: 200, json: publicDetail }),
  );

  await page.goto("/en/explore/foods/community/unavailable-food?food_locale=hi-IN");
  await expect(page.getByRole("heading", { name: "We cannot verify this record right now." })).toBeVisible();
  await page.getByRole("link", { name: "Try again" }).click();
  await expect(page.getByRole("heading", { level: 1, name: "Rajma masala" })).toBeVisible();
});

test("a stalled browser retry times out and remains safely retryable", async ({ page }) => {
  await page.route("**/api/v1/public/foods/community/unavailable-food", () => new Promise(() => {}));

  await page.goto("/en/explore/foods/community/unavailable-food?food_locale=hi-IN");
  await page.getByRole("link", { name: "Try again" }).click();
  await expect(page.getByRole("heading", { name: "We cannot verify this record right now." })).toBeVisible({
    timeout: 7_000,
  });
  await expect(page.getByRole("link", { name: "Try again" })).toBeEnabled();
});

test.describe("retry without JavaScript", () => {
  test.use({ javaScriptEnabled: false });

  test("the unavailable state offers a real reload link", async ({ page }) => {
    await page.goto("/en/explore/foods/community/unavailable-food?food_locale=hi-IN");
    const retry = page.getByRole("link", { name: "Try again" });
    await expect(retry).toHaveAttribute("href", "");
    await retry.click();
    await expect(page.getByRole("heading", { name: "We cannot verify this record right now." })).toBeVisible();
  });
});

test("invalid public record identifiers return a real 404", async ({ page }) => {
  const response = await page.goto("/en/explore/foods/community/RAJMA-MASALA");
  expect(response?.status()).toBe(404);
  await expect(page.getByText("This page could not be found.")).toBeVisible();
});
