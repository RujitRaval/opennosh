import { expect, test } from "@playwright/test";

test("public and Tracker roots use a durable full-document handoff", async ({ page }) => {
  await page.goto("/en/contribute/local/evidence");
  const sourceUrl = page.getByLabel("Source URL");
  await sourceUrl.fill("https://example.org/durable-source");
  await expect.poll(() => page.evaluate(() => {
    const value = window.localStorage.getItem("opennosh.contribution.local.v1");
    return value ? JSON.parse(value).fields.source_uri : null;
  })).toBe("https://example.org/durable-source");

  await page.evaluate(() => {
    (window as Window & { __opennoshRootMarker?: boolean }).__opennoshRootMarker = true;
  });
  const menuButton = page.locator(".menu-button");
  if (await menuButton.isVisible()) await menuButton.click();
  await page.locator('a[href="/tracker"]:visible').first().click();

  await expect(page).toHaveURL(/\/tracker$/);
  await expect(page.locator("html")).toHaveAttribute("lang", "en");
  expect(await page.evaluate(() =>
    Boolean((window as Window & { __opennoshRootMarker?: boolean }).__opennoshRootMarker),
  )).toBe(false);
  await expect(page.getByRole("link", { name: "Return to the commons" })).toHaveAttribute(
    "href",
    "/en/contribute/local/evidence",
  );
  expect(await page.evaluate(() => JSON.parse(
    window.localStorage.getItem("opennosh.contribution.local.v1") ?? "null",
  )?.fields.source_uri)).toBe("https://example.org/durable-source");

  await page.evaluate(() => {
    (window as Window & { __opennoshTrackerMarker?: boolean }).__opennoshTrackerMarker = true;
  });
  await page.getByRole("link", { name: "Return to the commons" }).click();
  await expect(page).toHaveURL(/\/en\/contribute\/local\/evidence$/);
  expect(await page.evaluate(() =>
    Boolean((window as Window & { __opennoshTrackerMarker?: boolean }).__opennoshTrackerMarker),
  )).toBe(false);
  await expect(page.getByLabel("Source URL")).toHaveValue("https://example.org/durable-source");

  await page.goBack();
  await expect(page).toHaveURL(/\/tracker$/);
  await page.goBack();
  await expect(page).toHaveURL(/\/en\/contribute\/local\/evidence$/);
  await expect(page.getByLabel("Source URL")).toHaveValue("https://example.org/durable-source");
});

test("root redirect and direct Tracker deep links always label the document", async ({ page }) => {
  await page.goto("/");
  await expect(page).toHaveURL(/\/en$/);
  await expect(page.locator("html")).toHaveAttribute("lang", "en");

  await page.context().addCookies([{
    name: "opennosh_interface_language",
    value: "zz",
    domain: "127.0.0.1",
    path: "/",
  }]);
  await page.goto("/tracker/trends");
  await expect(page).toHaveURL(/\/tracker\/trends$/);
  await expect(page.locator("html")).toHaveAttribute("lang", "en");
  await expect(page.locator("html")).toHaveAttribute("data-interface-language", "en");
  await expect(page.getByRole("heading", { name: "Sign in to your log" })).toBeVisible();
});
