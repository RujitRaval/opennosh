import { expect, test } from "@playwright/test";

import detail from "../fixtures/contracts/foods/v1-detail-community.json";

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
  await expect(page.getByText("No related published variants were returned for this food locale.")).toBeVisible();
  expect(searchRequests).toBe(0);
});

test("a server-side primary-record failure exposes retry and recovers cleanly", async ({ page }) => {
  await page.route("**/api/v1/foods/community/unavailable-food", (route) =>
    route.fulfill({ status: 200, json: detail }),
  );

  await page.goto("/en/explore/foods/community/unavailable-food?food_locale=hi-IN");
  await expect(page.getByRole("heading", { name: "We cannot verify this record right now." })).toBeVisible();
  await page.getByRole("button", { name: "Try again" }).click();
  await expect(page.getByRole("heading", { level: 1, name: "Rajma masala" })).toBeVisible();
});

test("invalid public record identifiers return a real 404", async ({ page }) => {
  const response = await page.goto("/en/explore/foods/community/RAJMA-MASALA");
  expect(response?.status()).toBe(404);
  await expect(page.getByText("This page could not be found.")).toBeVisible();
});
