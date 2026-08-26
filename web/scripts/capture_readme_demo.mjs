import { mkdir } from "node:fs/promises";
import { resolve } from "node:path";

import { chromium } from "playwright";

const baseURL = process.env.README_DEMO_BASE_URL ?? "http://127.0.0.1:3099";
const output = resolve(process.argv[2] ?? "../docs/assets/readme-demo-frames");
await mkdir(output, { recursive: true });

const browser = await chromium.launch();
const page = await browser.newPage({
  viewport: { width: 960, height: 624 },
  deviceScaleFactor: 1,
  reducedMotion: "reduce",
});
let authenticated = false;

const user = {
  id: "4c683fc5-548a-4772-a090-b26ea0951d50",
  email: "alex@example.com",
  onboarding_completed: false,
  preferred_units: "us",
};
const food = {
  id: "community:rajma-masala",
  source: "community",
  source_id: "rajma-masala",
  name: "Rajma masala",
  name_local: "राजमा मसाला",
  category: "Punjabi home-style preparation",
  attribution: {
    source: "community",
    license: "CC0-1.0",
    contributed_by: "Punjab Foods Collective",
    pack_id: "indian-staples-north",
    pack_version: "1.0.0",
    provenance: "Recipe analysis checked against household preparation sources",
  },
};

await page.route("**/api/v1/**", async (route) => {
  const request = route.request();
  const url = new URL(request.url());
  if (url.pathname === "/api/v1/auth/session-state") {
    return route.fulfill({ json: authenticated ? { authenticated: true, user } : { authenticated: false, user: null } });
  }
  if (url.pathname === "/api/v1/auth/register") {
    authenticated = true;
    return route.fulfill({
      status: 201,
      headers: { "Set-Cookie": "opennosh_csrf=demo-csrf; Path=/; SameSite=Strict" },
      json: { user, csrf_token: "demo-csrf", recovery_code: "save-this-private-demo-recovery-code-7sWj2M9qK4" },
    });
  }
  if (url.pathname === "/api/v1/auth/account/settings") {
    user.onboarding_completed = Boolean(request.postDataJSON().onboarding_completed);
    user.preferred_units = request.postDataJSON().preferred_units ?? user.preferred_units;
    return route.fulfill({ json: user });
  }
  if (url.pathname === "/api/v1/targets" && request.method() === "PUT") {
    return route.fulfill({ json: { items: [], target_kcal_floor: "1200.00", safety_copy: "Reference targets only." } });
  }
  if (url.pathname === "/api/v1/foods/search") {
    return route.fulfill({
      json: {
        schema_version: "2.0",
        items: [food],
        limit: 12,
        has_more: false,
        next_cursor: null,
        snapshot_id: "00000000-0000-4000-8000-000000000031",
        snapshot_expires_at: "2026-08-26T23:59:00Z",
      },
    });
  }
  if (url.pathname === "/api/v1/logs") {
    return route.fulfill({ json: { day: "2026-08-26", timezone: "America/New_York", items: [], limit: 100, offset: 0, has_more: false } });
  }
  if (url.pathname === "/api/v1/logs/daily-totals") {
    return route.fulfill({ json: { day: "2026-08-26", timezone: "America/New_York", entry_count: 0, grams: "0.00", nutrients: {} } });
  }
  if (url.pathname === "/api/v1/targets/resolve-optional") return route.fulfill({ json: null });
  if (url.pathname === "/api/v1/auth/logout") return route.fulfill({ status: 204, body: "" });
  return route.fulfill({ status: 404, json: { detail: `Unhandled demo route: ${url.pathname}` } });
});

async function frame(number, name) {
  await page.screenshot({ path: resolve(output, `${String(number).padStart(2, "0")}-${name}.png`) });
}

await page.goto(`${baseURL}/en`);
await page.waitForLoadState("networkidle");
await frame(1, "commons-home");

await page.goto(`${baseURL}/en/explore`);
await page.getByRole("heading", { name: "Search starter food records." }).scrollIntoViewIfNeeded();
await frame(2, "explore-search");
await page.getByLabel("Food name").fill("rajma");
await page.getByRole("button", { name: "Search records" }).click();
await page.getByText("Rajma masala").waitFor();
await frame(3, "explore-result");

await page.goto(`${baseURL}/tracker`);
await page.getByRole("button", { name: /create an account/i }).click();
await frame(4, "tracker-sign-up");
await page.getByLabel("Email address").fill(user.email);
await page.getByLabel("Password").fill("a-long-private-demo-password");
await page.getByRole("button", { name: "Create account" }).click();
await page.getByRole("heading", { name: "Save your recovery code" }).waitFor();
await frame(5, "recovery-code");
await page.getByLabel("I saved this code somewhere private.").check();
await page.getByRole("button", { name: "Open my tracker" }).click();
await page.getByRole("heading", { name: /nutrition at a glance/i }).waitFor();
await frame(6, "tracker-ready");

await browser.close();
