import { expect, test, type Page } from "@playwright/test";

const user = {
  id: "4c683fc5-548a-4772-a090-b26ea0951d50",
  email: "launch@example.com",
  onboarding_completed: false,
  recovery_configured: true,
  preferred_units: "us",
};

async function mockLaunchApi(page: Page) {
  user.onboarding_completed = false;
  user.preferred_units = "us";
  let authenticated = false;
  await page.route("**/api/v1/**", async (route) => {
    const request = route.request();
    const url = new URL(request.url());
    const path = url.pathname;
    if (path === "/api/v1/auth/session-state") {
      return route.fulfill({ json: authenticated ? { authenticated: true, user } : { authenticated: false, user: null } });
    }
    if (path === "/api/v1/auth/register") {
      authenticated = true;
      return route.fulfill({
        status: 201,
        headers: { "Set-Cookie": "opennosh_csrf=launch-csrf; Path=/; SameSite=Strict" },
        json: { user, csrf_token: "launch-csrf", recovery_code: "launch-recovery-code-that-is-long-enough-2026" },
      });
    }
    if (path === "/api/v1/auth/account/settings") {
      const body = request.postDataJSON();
      user.onboarding_completed = body.onboarding_completed ?? user.onboarding_completed;
      user.preferred_units = body.preferred_units ?? user.preferred_units;
      return route.fulfill({ json: user });
    }
    if (path === "/api/v1/targets" && request.method() === "PUT") {
      expect(request.headers()["x-csrf-token"]).toBe("launch-csrf");
      expect(request.postDataJSON().items).toHaveLength(0);
      return route.fulfill({ json: { items: [], target_kcal_floor: "1200.00", safety_copy: "Reference only." } });
    }
    if (path === "/api/v1/foods/search") {
      expect(url.searchParams.get("source")).toBeNull();
      return route.fulfill({
        json: {
          schema_version: "2.0",
          items: [
            {
              id: "community:rajma-masala",
              source: "community",
              source_id: "rajma-masala",
              name: "Rajma masala",
              name_local: "राजमा मसाला",
              category: "Punjabi home-style preparation",
              attribution: {
                source: "community",
                license: "CC0-1.0",
                pack_id: "indian-staples-north",
                pack_version: "1.0.0",
                contributed_by: "opennosh contributors",
              },
            },
            {
              id: "usda:175200",
              source: "usda",
              source_id: "175200",
              name: "Beans, kidney, red, mature seeds, cooked, boiled",
              name_local: null,
              category: "Legumes and Legume Products",
              attribution: {
                source: "usda",
                license: "CC0",
              },
            },
          ],
          limit: 12,
          has_more: false,
          next_cursor: null,
          snapshot_id: "00000000-0000-4000-8000-000000000031",
          snapshot_expires_at: "2026-08-26T23:59:00Z",
        },
      });
    }
    if (path === "/api/v1/logs") return route.fulfill({ json: { day: "2026-08-26", timezone: "UTC", items: [], limit: 100, offset: 0, has_more: false } });
    if (path === "/api/v1/logs/daily-totals") return route.fulfill({ json: { day: "2026-08-26", timezone: "UTC", entry_count: 0, grams: "0.00", nutrients: {} } });
    if (path === "/api/v1/targets/resolve-optional") return route.fulfill({ json: null });
    return route.fulfill({ status: 404, json: { detail: `Unhandled ${path}` } });
  });
}

test("public Explore searches community and USDA records with separate truthful counts", async ({ page, request }) => {
  await mockLaunchApi(page);
  const apiPort = process.env.E2E_API_PORT || "8001";
  const setCommonsState = (state: string) => request.post(
    `http://127.0.0.1:${apiPort}/__visual/commons-state?state=${state}`,
  );
  expect((await setCommonsState("live")).ok()).toBe(true);
  try {
    await page.goto("/en/explore");

    await expect(page.getByRole("heading", { name: "Search food records." })).toBeVisible();
    const counts = page.getByRole("definition");
    await expect(counts.filter({ hasText: "18,429" })).toBeVisible();
    await expect(counts.filter({ hasText: "13,497" })).toBeVisible();
    await page.getByLabel("Food name").fill("rajma");
    await page.getByRole("button", { name: "Search records" }).click();

    const communityResult = page.getByRole("link", { name: /Rajma masala/ });
    await expect(communityResult).toContainText("CC0-1.0");
    await expect(communityResult).toContainText("indian-staples-north");
    await expect(communityResult).toHaveAttribute("href", "/en/explore/foods/community/rajma-masala");
    const usdaResult = page.getByRole("link", { name: /Beans, kidney/ });
    await expect(usdaResult).toContainText("CC0");
    await expect(usdaResult).toContainText("USDA FoodData Central");
    await expect(usdaResult).toHaveAttribute("href", "/en/explore/foods/usda/175200");
  } finally {
    expect((await setCommonsState("unavailable")).ok()).toBe(true);
  }
});

test("new accounts save recovery proof and resume after setup", async ({ page }) => {
  await mockLaunchApi(page);
  await page.goto("/tracker");

  await page.getByRole("button", { name: /create an account/i }).click();
  await page.getByLabel("Email address").fill(user.email);
  await page.getByLabel("Password").fill("a-private-launch-password");
  await page.getByRole("button", { name: "Create account" }).click();

  await expect(page.getByRole("heading", { name: "Save your recovery code" })).toBeVisible();
  await expect(page.getByRole("status", { name: "Recovery code" })).toContainText("launch-recovery-code");
  await expect(page.getByRole("button", { name: "Open my tracker" })).toBeDisabled();

  await page.getByLabel("I saved this code somewhere private.").check();
  await page.getByRole("button", { name: "Open my tracker" }).click();

  await expect(page.getByRole("heading", { name: /nutrition at a glance/i })).toBeVisible();
  await page.reload();
  await expect(page.getByRole("heading", { name: /nutrition at a glance/i })).toBeVisible();
  await expect(page.getByRole("link", { name: "Records" })).toBeVisible();
  await expect(page.getByRole("link", { name: "Account" })).toBeVisible();
});
