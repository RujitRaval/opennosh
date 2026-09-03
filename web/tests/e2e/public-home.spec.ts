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
  test.skip(testInfo.project.name.includes("mobile"), "Desktop trunk is exercised in a desktop project.");
  await page.goto("/en/explore?food_locale=fr-FR");

  const primary = page.getByRole("navigation", { name: "Primary navigation" });
  await expect(primary.getByRole("link", { name: "Explore" })).toHaveAttribute(
    "aria-current",
    "page",
  );
  await expect(page.getByRole("heading", { level: 1, name: "Explore" })).toBeVisible();
  await expect(page.getByRole("link", { name: /See how records work/ })).toHaveAttribute(
    "href",
    "#search",
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

test("Commons missions expose verified progress and privacy-thresholded regions", async ({ page }) => {
  await page.goto("/en/commons");

  await expect(page.getByRole("heading", { level: 2, name: "Fill a gap the commons can measure." })).toBeVisible();
  await expect(page.getByRole("heading", { level: 3, name: "Document Caribbean breakfast staples" })).toBeVisible();
  await expect(page.getByText("4 of 10 accepted")).toBeVisible();

  const activity = page.locator('[aria-label="Mission activity by broad pack locale"]');
  await expect(activity.getByText("Jamaica")).toBeVisible();
  await expect(activity.getByText("Latin America")).toBeVisible();
  await expect(activity.getByText("14 accepted records")).toBeVisible();
  await expect(activity.getByRole("time")).toHaveCount(0);
  await expect(activity.getByText(/total|ranking|streak/i)).toHaveCount(0);

  const overflow = await page.evaluate(
    () => document.documentElement.scrollWidth - document.documentElement.clientWidth,
  );
  expect(overflow).toBeLessThanOrEqual(1);

  const accessibility = await new AxeBuilder({ page })
    .withTags(["wcag2a", "wcag2aa", "wcag21aa", "wcag22aa"])
    .analyze();
  expect(accessibility.violations).toEqual([]);
});

test("Commons missions fail closed across lifecycle and proof states", async ({ page, request }, testInfo) => {
  test.skip(testInfo.project.name.includes("mobile"), "The state matrix runs once; mobile reflow is covered visually.");
  const apiPort = process.env.E2E_API_PORT || "8001";
  const setState = async (state: string) => {
    const response = await request.post(`http://127.0.0.1:${apiPort}/__visual/mission-state?state=${state}`);
    expect(response.ok()).toBe(true);
  };

  try {
    await setState("disabled");
    await page.goto("/en/commons?mission-state=disabled");
    await expect(page.getByText("Public missions are not open yet.")).toBeVisible();
    await expect(page.getByText("The geographic activity surface is not open yet.")).toBeVisible();
    await expect(page.getByRole("heading", { level: 3 })).toHaveCount(0);

    await setState("zero");
    await page.goto("/en/commons?mission-state=zero");
    await expect(page.getByText("No moderated missions are public yet.")).toBeVisible();
    await expect(page.getByText("No region meets the privacy threshold yet.")).toBeVisible();

    for (const state of ["paused", "stale", "released"] as const) {
      await setState(state);
      await page.goto(`/en/commons?mission-state=${state}`);
      await expect(page.getByText({
        paused: "Paused · 4 accepted",
        stale: "Stale · 4 accepted at the last verified checkpoint",
        released: "Released · 4 accepted",
      }[state])).toBeVisible();
    }

    await setState("slow-activity");
    const slowActivityNavigation = page.goto("/en/commons?mission-state=slow-activity");
    await expect(page.getByRole("heading", { level: 3, name: "Document Caribbean breakfast staples" })).toBeVisible({ timeout: 800 });
    await expect(page.getByText("Checking regional proof.")).toBeVisible({ timeout: 800 });
    await slowActivityNavigation;
    await expect(page.getByText("Jamaica")).toBeVisible();

    await setState("slow-catalog");
    const slowCatalogNavigation = page.goto("/en/commons?mission-state=slow-catalog");
    await expect(page.getByText("Checking mission proof.")).toBeVisible({ timeout: 800 });
    await expect(page.getByText("Jamaica")).toBeVisible({ timeout: 800 });
    await slowCatalogNavigation;
    await expect(page.getByRole("heading", { level: 3, name: "Document Caribbean breakfast staples" })).toBeVisible();

    await setState("narrow");
    await page.goto("/en/commons?mission-state=narrow");
    await expect(page.getByRole("heading", { level: 3, name: "Document Caribbean breakfast staples" })).toBeVisible();
    await expect(page.getByText("Regional proof is unavailable.")).toBeVisible();
    await expect(page.getByText("Jamaica")).toHaveCount(0);

    for (const state of ["malformed", "error"] as const) {
      await setState(state);
      await page.goto(`/en/commons?mission-state=${state}`);
      await expect(page.getByText("Mission proof is unavailable.")).toBeVisible();
      await expect(page.getByText("Regional proof is unavailable.")).toBeVisible();
      await expect(page.getByRole("heading", { level: 3 })).toHaveCount(0);
      await expect(page.getByText(/accepted records$/)).toHaveCount(0);
    }

    const accessibility = await new AxeBuilder({ page })
      .withTags(["wcag2a", "wcag2aa", "wcag21aa", "wcag22aa"])
      .analyze();
    expect(accessibility.violations).toEqual([]);
  } finally {
    await setState("live");
  }
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

  if (testInfo.project.name.includes("mobile")) {
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
  test.skip(!testInfo.project.name.includes("mobile"), "Mobile navigation is exercised in the mobile project.");
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
  await expect(page.locator("body")).toHaveCSS("font-family", /Trebuchet MS/);
  expect(
    requestedResources.some((url) =>
      /\/fonts\/v2\/opennosh-(display|sans|mono)-latin/.test(url),
    ),
  ).toBe(false);
});

test("public tokens provide visible focus on light, Tomato, and Ink surfaces", async ({ page }) => {
  await page.goto("/en");
  await page.evaluate(async () => {
    await document.fonts.ready;
  });

  await expect(page.locator("body")).toHaveCSS("font-family", /opennosh Sans/);
  await expect(page.getByRole("heading", { level: 1 })).toHaveCSS("font-family", /opennosh Display/);
  await expect(page.locator(".mono").first()).toHaveCSS("font-family", /opennosh Mono/);

  const lightTokens = await page.locator("html").evaluate((element) => {
    const styles = getComputedStyle(element);
    return {
      text: styles.getPropertyValue("--color-text").trim(),
      surface: styles.getPropertyValue("--color-surface").trim(),
      focus: styles.getPropertyValue("--focus-ring").trim(),
    };
  });
  expect(lightTokens).toEqual({
    text: "#12120f",
    surface: "#f4f0e6",
    focus: "#5848e8",
  });

  const start = page.getByRole("link", { name: "Start" });
  await start.focus();
  await expect(start).toHaveCSS("outline-style", "solid");
  await expect(start).toHaveCSS("outline-color", "rgb(88, 72, 232)");

  await page.goto("/en/contribute/local/evidence");
  const contributionHome = page
    .getByRole("banner")
    .getByRole("link", { name: "opennosh home" });
  await contributionHome.focus();
  await expect(contributionHome).toHaveCSS("outline-color", "rgb(18, 18, 15)");

  await page.goto("/en/build");
  const darkHeaderHome = page
    .getByRole("banner")
    .getByRole("link", { name: "opennosh home" });
  await darkHeaderHome.focus();
  await expect(darkHeaderHome).toHaveCSS("outline-color", "rgb(215, 243, 76)");
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
