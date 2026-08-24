import AxeBuilder from "@axe-core/playwright";
import { expect, test } from "@playwright/test";

test("public root redirects into the localized movement site", async ({ page }) => {
  await page.goto("/?food_locale=fr-FR");

  await expect(page).toHaveURL(/\/en\?food_locale=fr-FR$/);
  await expect(page.locator("html")).toHaveAttribute("lang", "en");
  await expect(page.getByRole("heading", { level: 1 })).toContainText("Food data");
  await expect(page.getByRole("heading", { level: 1 })).toContainText("everyone");
  await expect(page.getByText("Accepted activity is unavailable.")).toBeVisible();
  await expect(page.getByText("18,429")).toHaveCount(0);
  await expect(page.locator('link[href*="fonts.googleapis.com"], link[href*="fonts.gstatic.com"]')).toHaveCount(0);

  const accessibility = await new AxeBuilder({ page })
    .withTags(["wcag2a", "wcag2aa", "wcag21aa", "wcag22aa"])
    .analyze();
  expect(accessibility.violations).toEqual([]);
});

test("desktop trunk identifies the current hub, page, and next action", async ({ page }, testInfo) => {
  test.skip(testInfo.project.name === "mobile", "Desktop trunk is exercised in a desktop project.");
  await page.goto("/en/explore?food_locale=fr-FR");

  const primary = page.getByRole("navigation", { name: "Primary navigation" });
  await expect(primary.getByRole("link", { name: "Explore" })).toHaveAttribute(
    "aria-current",
    "page",
  );
  await expect(page.getByRole("heading", { level: 1, name: "Explore" })).toBeVisible();
  await expect(page.getByRole("link", { name: /See how records work/ })).toHaveAttribute(
    "href",
    "#principles",
  );
  await expect(page.locator("html")).toHaveAttribute("lang", "en");
  await expect(page).toHaveURL(/food_locale=fr-FR/);

  await primary.getByRole("link", { name: "Build" }).click();
  await expect(page).toHaveURL(/\/en\/build$/);
  await expect(primary.getByRole("link", { name: "Build" })).toHaveAttribute(
    "aria-current",
    "page",
  );
  await expect(page.getByRole("heading", { level: 1, name: "Build" })).toBeVisible();
});

test("deep public pages expose their full breadcrumb and owning hub", async ({ page }, testInfo) => {
  await page.goto("/en/notices");

  const breadcrumb = page.getByRole("navigation", { name: "Breadcrumb" });
  await expect(breadcrumb.getByRole("link", { name: "Home" })).toHaveAttribute("href", "/en");
  await expect(breadcrumb.getByRole("link", { name: "Build" })).toHaveAttribute(
    "href",
    "/en/build",
  );
  await expect(breadcrumb.getByText("Licenses + notices")).toHaveAttribute(
    "aria-current",
    "page",
  );

  if (testInfo.project.name === "mobile") {
    await page.getByRole("button", { name: "Menu" }).click();
    await expect(
      page.getByRole("navigation", { name: "Mobile navigation" }).getByRole("link", { name: "Build" }),
    ).toHaveAttribute("aria-current", "page");
  } else {
    const primary = page.getByRole("navigation", { name: "Primary navigation" });
    await expect(primary.getByRole("link", { name: "Build" })).toHaveAttribute(
      "aria-current",
      "page",
    );
  }
});

test("unsupported interface languages fail visibly", async ({ page }) => {
  const response = await page.goto("/zz/explore");

  expect(response?.status()).toBe(404);
  await expect(page.getByText("This page could not be found.")).toBeVisible();
});

test("mobile menu exposes task hubs and utilities with deterministic focus", async ({ page }, testInfo) => {
  test.skip(testInfo.project.name !== "mobile", "Mobile navigation is exercised in the mobile project.");
  await page.goto("/en/commons");
  const menu = page.getByRole("button", { name: "Menu" });
  await menu.click();

  const mobileNavigation = page.getByRole("navigation", { name: "Mobile navigation" });
  await expect(mobileNavigation).toBeVisible();
  const explore = mobileNavigation.getByRole("link", { name: /Explore/ });
  await expect(explore).toHaveAttribute("href", "/en/explore");
  await expect(explore).toBeFocused();
  await expect(mobileNavigation.getByRole("link", { name: /Open private tracker/ })).toHaveAttribute(
    "href",
    "/tracker",
  );
  await expect(mobileNavigation.getByLabel("Interface language: English")).toBeVisible();

  await page.keyboard.press("Escape");
  await expect(mobileNavigation).toBeHidden();
  await expect(menu).toBeFocused();
});

test("tracker document excludes public navigation and public font variables", async ({ page }) => {
  const requestedResources: string[] = [];
  page.on("response", (response) => requestedResources.push(response.url()));
  await page.route("**/api/v1/**", (route) =>
    route.fulfill({ status: 401, json: { detail: "Not authenticated" } }),
  );
  await page.goto("/tracker");

  await expect(page.locator("html")).toHaveAttribute("data-surface", "tracker");
  await expect(page.getByRole("navigation", { name: "Primary navigation" })).toHaveCount(0);
  await expect(page.locator("body")).not.toHaveClass(/font-archivo/);
  expect(
    requestedResources.some((url) =>
      /archivo-latin-variable|source-sans-3-latin-variable|ibm-plex-mono-latin/.test(url),
    ),
  ).toBe(false);
});

test("the public home remains complete when JavaScript is unavailable", async ({ browser }) => {
  const context = await browser.newContext({ javaScriptEnabled: false });
  const page = await context.newPage();

  await page.goto("/en");
  await expect(page.getByRole("heading", { level: 1 })).toContainText("Food data");
  await expect(page.getByRole("link", { name: "Start" })).toBeVisible();
  await expect(page.getByRole("link", { name: /Read the contribution guide/ })).toBeVisible();
  await expect(page.getByText("Accepted activity is unavailable.")).toBeVisible();
  const actions = page.getByRole("navigation", { name: "Commons activity actions" });
  await expect(actions.getByRole("link", { name: "Search verified records" })).toBeVisible();
  await expect(actions.getByRole("link", { name: "Contribute a food" })).toBeVisible();
  await expect(page.getByText("18,429")).toHaveCount(0);
  await expect(page.locator(".footer-release-proof")).toHaveCount(0);
  await expect(page.locator("html")).toHaveAttribute("data-motion", "off");

  await context.close();
});

test("eligible motion activates sparingly and pauses away from the viewport", async ({ page }) => {
  await page.goto("/en");

  await expect(page.locator("html")).toHaveAttribute(
    "data-motion-runtime",
    "opennosh:motion-runtime:v1",
  );
  await expect(page.locator('html[data-motion-state="running"]')).toHaveCount(1);
  expect(
    await page.locator('[data-motion-region][data-motion-visible="true"]').count(),
  ).toBeLessThanOrEqual(2);

  await page.locator('[data-motion-region="contribute"]').scrollIntoViewIfNeeded();
  await expect(page.locator('[data-motion-region="hero"]')).toHaveAttribute(
    "data-motion-visible",
    "false",
  );
  expect(
    await page.locator('[data-motion-region][data-motion-visible="true"]').count(),
  ).toBeLessThanOrEqual(2);

  await page.evaluate(() => {
    Object.defineProperty(document, "visibilityState", { configurable: true, value: "hidden" });
    document.dispatchEvent(new Event("visibilitychange"));
  });
  await expect(page.locator("html")).toHaveAttribute("data-motion-state", "paused");
});

test("the runtime disables decoration after a long-task budget breach", async ({ page }) => {
  await page.goto("/en");
  await expect(page.locator("html")).toHaveAttribute(
    "data-motion-runtime",
    "opennosh:motion-runtime:v1",
  );

  await page.evaluate(() => {
    setTimeout(() => {
      const end = performance.now() + 70;
      while (performance.now() < end) {
        // Intentionally occupy the main thread to exercise the runtime kill switch.
      }
    }, 0);
  });

  await expect(page.locator("html")).toHaveAttribute("data-motion", "limited");
  await expect(page.locator("html")).toHaveAttribute("data-motion-reason", "long-task-budget");
});

test("reduced-motion visitors never download the optional runtime", async ({ browser }) => {
  const context = await browser.newContext({ reducedMotion: "reduce" });
  const page = await context.newPage();

  await page.goto("/en");
  await page.waitForTimeout(1_300);

  await expect(page.locator("html")).toHaveAttribute("data-motion", "off");
  await expect(page.locator("html")).toHaveAttribute("data-motion-reason", "reduced-motion");
  await expect(page.locator("html")).not.toHaveAttribute("data-motion-runtime", /.+/);

  await context.close();
});

test("data-saver and low-power visitors keep the static experience", async ({ page }) => {
  await page.addInitScript(() => {
    Object.defineProperty(navigator, "connection", {
      configurable: true,
      value: { saveData: true, effectiveType: "4g" },
    });
    Object.defineProperty(navigator, "hardwareConcurrency", {
      configurable: true,
      value: 2,
    });
  });

  await page.goto("/en");
  await page.waitForTimeout(1_300);

  await expect(page.locator("html")).toHaveAttribute("data-motion", "off");
  await expect(page.locator("html")).toHaveAttribute("data-motion-reason", "data-saver");
  await expect(page.locator("html")).not.toHaveAttribute("data-motion-runtime", /.+/);
});
