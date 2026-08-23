import { expect, test, type Page, type Route } from "@playwright/test";

import detail from "../fixtures/contracts/foods/v1-detail-community.json";

const emptySearch = {
  schema_version: "2.0",
  items: [],
  limit: 12,
  has_more: false,
  next_cursor: null,
  snapshot_id: "018f5316-4f4e-7d79-b9f6-88c11a68a497",
  snapshot_expires_at: "2026-08-23T14:30:00Z",
};

async function routePrimary(page: Page) {
  await page.route("**/api/v1/foods/community/rajma-masala", (route) =>
    route.fulfill({ status: 200, json: detail }),
  );
}

async function fail(route: Route) {
  await route.fulfill({ status: 503, json: { detail: "Temporarily unavailable" } });
}

test("global, malformed, and repeated locale preferences remain honest at the API boundary", async ({ page }) => {
  await routePrimary(page);
  const locales: Array<string | null> = [];
  await page.route("**/api/v1/foods/search?**", (route) => {
    locales.push(new URL(route.request().url()).searchParams.get("locale"));
    return route.fulfill({ status: 200, json: emptySearch });
  });

  await page.goto("/en/explore/foods/community/rajma-masala");
  await expect(page.getByText("Global / no preference")).toBeVisible();
  await expect.poll(() => locales.at(-1)).toBeNull();

  await page.goto("/en/explore/foods/community/rajma-masala?food_locale=../../etc");
  await expect(page.getByText("Global / no preference")).toBeVisible();
  await expect.poll(() => locales.at(-1)).toBeNull();

  await page.goto("/en/explore/foods/community/rajma-masala?food_locale=hi-IN&food_locale=fr-FR");
  await expect(page.getByText(/Hindi.*\(hi-IN\)/)).toBeVisible();
  await expect.poll(() => locales.at(-1)).toBe("hi-IN");
});

test("a failed related-record search never hides the trusted primary record", async ({ page }) => {
  await routePrimary(page);
  await page.route("**/api/v1/foods/search?**", fail);

  await page.goto("/en/explore/foods/community/rajma-masala?food_locale=hi-IN");

  await expect(page.getByRole("heading", { level: 1, name: "Rajma masala" })).toBeVisible();
  await expect(page.getByText("No related published variants were returned for this food locale.")).toBeVisible();
});

test("a transient primary-record failure exposes retry and recovers cleanly", async ({ page }) => {
  let attempts = 0;
  let recover = false;
  await page.route("**/api/v1/foods/community/rajma-masala", (route) => {
    attempts += 1;
    return recover
      ? route.fulfill({ status: 200, json: detail })
      : fail(route);
  });
  await page.route("**/api/v1/foods/search?**", (route) =>
    route.fulfill({ status: 200, json: emptySearch }),
  );

  await page.goto("/en/explore/foods/community/rajma-masala?food_locale=hi-IN");
  await expect(page.getByRole("heading", { name: "We cannot verify this record right now." })).toBeVisible();
  recover = true;
  await page.getByRole("button", { name: "Try again" }).click();
  await expect(page.getByRole("heading", { level: 1, name: "Rajma masala" })).toBeVisible();
  expect(attempts).toBeGreaterThanOrEqual(2);
});

test("invalid public record identifiers return a real 404", async ({ page }) => {
  const response = await page.goto("/en/explore/foods/community/RAJMA-MASALA");
  expect(response?.status()).toBe(404);
  await expect(page.getByText("This page could not be found.")).toBeVisible();
});
