import AxeBuilder from "@axe-core/playwright";
import { expect, test } from "@playwright/test";

test("public root redirects into the localized movement site", async ({ page }) => {
  await page.goto("/");

  await expect(page).toHaveURL(/\/en$/);
  await expect(page.locator("html")).toHaveAttribute("lang", "en");
  await expect(page.getByRole("heading", { level: 1 })).toContainText("Food data");
  await expect(page.getByRole("heading", { level: 1 })).toContainText("everyone");
  await expect(page.getByText("No accepted changes to report yet.")).toBeVisible();
  await expect(page.getByText("18,429")).toHaveCount(0);
  await expect(page.locator('link[href*="fonts.googleapis.com"], link[href*="fonts.gstatic.com"]')).toHaveCount(0);

  const accessibility = await new AxeBuilder({ page })
    .withTags(["wcag2a", "wcag2aa", "wcag21aa", "wcag22aa"])
    .analyze();
  expect(accessibility.violations).toEqual([]);
});

test("mobile menu reaches every public hub and returns focus on Escape", async ({ page }, testInfo) => {
  test.skip(testInfo.project.name !== "mobile", "Mobile navigation is exercised in the mobile project.");
  await page.goto("/en");
  const menu = page.getByRole("button", { name: "Menu" });
  await menu.click();

  const mobileNavigation = page.getByRole("navigation", { name: "Mobile navigation" });
  await expect(mobileNavigation).toBeVisible();
  await expect(mobileNavigation.getByRole("link", { name: /Explore/ })).toHaveAttribute("href", "/en#explore");
  await page.keyboard.press("Escape");
  await expect(mobileNavigation).toBeHidden();
  await expect(menu).toBeFocused();
});

test("tracker document excludes public navigation and public font variables", async ({ page }) => {
  const requestedResources: string[] = [];
  page.on("response", (response) => requestedResources.push(response.url()));
  await page.route("**/api/v1/**", (route) => route.fulfill({ status: 401, json: { detail: "Not authenticated" } }));
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
