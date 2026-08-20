import AxeBuilder from "@axe-core/playwright";
import { expect, test, type Page } from "@playwright/test";

const food = {
  id: "usda:171077",
  source: "usda",
  source_id: "171077",
  name: "Chicken breast",
  name_local: null,
  category: "Poultry",
  attribution: { license: "CC0-1.0", contributed_by: null },
};

const entry = {
  id: "3fd6633d-c6fa-446d-a0e2-89fc3ef69b9d",
  logged_at: "2026-08-20T16:00:00Z",
  meal_slot: "Lunch",
  food: { source: "usda", source_id: "171077", name: "Chicken breast" },
  quantity: { amount: "150", unit: "g", portion_name: null },
  snapshot: {
    basis: "computed",
    grams: "150.00",
    nutrients: {
      energy_kcal: "248.00",
      protein_g: "46.50",
      carbohydrate_g: "0.00",
      fat_g: "5.40",
    },
  },
};

async function mockApi(page: Page) {
  let authenticated = false;
  let entries: typeof entry[] = [];

  await page.route("**/api/v1/**", async (route) => {
    const request = route.request();
    const url = new URL(request.url());
    const path = url.pathname;

    if (path === "/api/v1/auth/session") {
      return route.fulfill({
        status: authenticated ? 200 : 401,
        json: authenticated
          ? { id: "4c683fc5-548a-4772-a090-b26ea0951d50", email: "alex@example.com" }
          : { detail: "Not authenticated" },
      });
    }
    if (path === "/api/v1/auth/login") {
      authenticated = true;
      return route.fulfill({
        headers: { "Set-Cookie": "opennosh_csrf=journey-csrf; Path=/; SameSite=Strict" },
        json: {
          user: { id: "4c683fc5-548a-4772-a090-b26ea0951d50", email: "alex@example.com" },
          csrf_token: "journey-csrf",
        },
      });
    }
    if (path === "/api/v1/foods/search") {
      return route.fulfill({ json: { items: [food], limit: 12, offset: 0, has_more: false } });
    }
    if (path === "/api/v1/logs" && request.method() === "POST") {
      expect(request.headers()["x-csrf-token"]).toBe("journey-csrf");
      entries = [entry];
      return route.fulfill({ status: 201, json: entry });
    }
    if (path === `/api/v1/logs/${entry.id}` && request.method() === "DELETE") {
      expect(request.headers()["x-csrf-token"]).toBe("journey-csrf");
      entries = [];
      return route.fulfill({ status: 204, body: "" });
    }
    if (path === "/api/v1/logs/daily-totals") {
      return route.fulfill({
        json: {
          day: url.searchParams.get("day"),
          timezone: url.searchParams.get("timezone"),
          entry_count: entries.length,
          grams: entries.length ? "150.00" : "0.00",
          nutrients: entries.length ? entry.snapshot.nutrients : {},
        },
      });
    }
    if (path === "/api/v1/logs") {
      return route.fulfill({
        json: {
          day: url.searchParams.get("day"),
          timezone: url.searchParams.get("timezone"),
          items: entries,
          limit: 100,
          offset: 0,
          has_more: false,
        },
      });
    }
    if (path === "/api/v1/targets/resolve") {
      return route.fulfill({
        json: {
          id: "5ff7c942-62d1-43df-8809-a76303d9a889",
          day_type: "training",
          kcal: "2200.00",
          protein_g: "160.00",
          carb_g: "240.00",
          fat_g: "70.00",
          active_from: "2026-01-01",
          active_until: null,
        },
      });
    }
    return route.fulfill({ status: 404, json: { detail: `Unhandled ${path}` } });
  });
}

async function expectNoWcagViolations(page: Page) {
  const results = await new AxeBuilder({ page })
    .withTags(["wcag2a", "wcag2aa", "wcag21aa", "wcag22aa"])
    .analyze();
  expect(results.violations).toEqual([]);
}

test("login, add food, view totals, and delete the entry", async ({ page }, testInfo) => {
  await mockApi(page);
  await page.goto("/");

  await expect(page.getByRole("heading", { name: /sign in to your log/i })).toBeVisible();
  await expectNoWcagViolations(page);
  await page.getByLabel("Email address").fill("alex@example.com");
  await page.getByLabel("Password").fill("a-long-test-password");
  await page.getByRole("button", { name: "Sign in" }).click();

  await expect(page.getByRole("heading", { name: /nutrition at a glance/i })).toBeVisible();
  await expectNoWcagViolations(page);

  await page.getByRole("button", { name: "Add food" }).click();
  await expectNoWcagViolations(page);
  await page.getByLabel("Search the food catalogue").fill("chicken");
  await page.getByRole("button", { name: "Search" }).click();
  await page.getByRole("radio", { name: /chicken breast/i }).check();
  await page.getByLabel("Amount in grams").fill("150");
  await page.getByRole("button", { name: "Add Chicken breast" }).click();

  await expect(page.getByText(/chicken breast was added to the log/i)).toBeVisible();
  await expect(page.getByRole("heading", { name: "Meals" })).toBeFocused();
  await expect(page.getByRole("progressbar", { name: /energy: 248 of 2,200 kcal/i })).toBeVisible();
  await expect(page.getByText("248 kcal")).toBeVisible();
  await expectNoWcagViolations(page);
  await page.screenshot({ path: testInfo.outputPath("daily-log.png"), fullPage: true });

  await page.getByRole("button", { name: /delete chicken breast from lunch/i }).click();
  await expectNoWcagViolations(page);
  await page.getByRole("button", { name: "Delete entry" }).click();
  await expect(page.getByText(/chicken breast was removed from lunch/i)).toBeVisible();
  await expect(page.getByRole("heading", { name: /nothing logged for this day/i })).toBeVisible();
  await expect(page.getByRole("heading", { name: "Meals" })).toBeFocused();
  await expectNoWcagViolations(page);
});
