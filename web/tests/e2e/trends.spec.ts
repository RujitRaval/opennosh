import AxeBuilder from "@axe-core/playwright";
import { expect, test } from "@playwright/test";

test("changes trend ranges and keeps strength load units separate", async ({ page }) => {
  const requestedRanges: string[] = [];
  await page.route("**/api/v1/**", async (route) => {
    const url = new URL(route.request().url());
    if (url.pathname === "/api/v1/auth/session") {
      return route.fulfill({ json: { id: "user", email: "alex@example.com" } });
    }
    if (url.pathname === "/api/v1/logs/daily-totals/range") {
      requestedRanges.push(url.searchParams.get("from") ?? "");
      return route.fulfill({ json: {
        from_date: url.searchParams.get("from"),
        to_date: url.searchParams.get("to"),
        timezone: url.searchParams.get("timezone"),
        items: [
          { day: "2026-08-19", timezone: "UTC", entry_count: 1, grams: "100", nutrients: { energy_kcal: "400", protein_g: "30" } },
          { day: "2026-08-20", timezone: "UTC", entry_count: 2, grams: "200", nutrients: { energy_kcal: "650", protein_g: "48" } },
        ],
      } });
    }
    if (url.pathname === "/api/v1/body-metrics/trends") {
      return route.fulfill({ json: { from_date: url.searchParams.get("from"), to_date: url.searchParams.get("to"), items: [
        { id: "one", recorded_at: "2026-08-19T08:00:00Z", metric_type: "body_weight", value: "80", unit: "kg" },
      ] } });
    }
    if (url.pathname === "/api/v1/workouts/trends") {
      return route.fulfill({ json: { from_date: url.searchParams.get("from"), to_date: url.searchParams.get("to"), items: [
        { day: "2026-08-20", exercise_id: "squat", exercise_name: "Back squat", load_unit: "kg", volume: "500" },
        { day: "2026-08-20", exercise_id: "squat", exercise_name: "Back squat", load_unit: "lb", volume: "1100" },
      ] } });
    }
    return route.fulfill({ status: 404, json: { detail: `Unhandled ${url.pathname}` } });
  });

  await page.goto("/trends");
  await expect(page.getByRole("heading", { name: "Trends" })).toBeVisible();
  await expect(page.getByRole("radio", { name: "30 days" })).toBeChecked();
  await expect(page.getByRole("table")).toHaveCount(3);

  await page.getByLabel("Nutrition measure").selectOption("protein_g");
  await expect(page.getByRole("table", { name: "Nutrition data table" }).getByText("48 g")).toBeVisible();
  await page.getByLabel("Exercise and load unit").selectOption("squat:lb");
  await expect(page.getByRole("table", { name: "Strength volume data table" }).getByText("1,100 lb")).toBeVisible();
  await expect(page.getByRole("table", { name: "Strength volume data table" }).getByText("500 kg")).toHaveCount(0);

  await page.getByRole("radio", { name: "7 days" }).check();
  await expect.poll(() => requestedRanges.length).toBeGreaterThan(1);
  expect(requestedRanges.at(-1)).not.toBe(requestedRanges[0]);

  const results = await new AxeBuilder({ page })
    .withTags(["wcag2a", "wcag2aa", "wcag21aa", "wcag22aa"])
    .analyze();
  expect(results.violations).toEqual([]);
});
