import { expect, test } from "@playwright/test";

import fontManifest from "../../assets/fonts/v2/font-build.v2.json";

const hrefFor = (output: string) =>
  `/fonts/${fontManifest.assetVersion}/${output.split("/").at(-1)}`;
const allFontHrefs = fontManifest.fonts.map((font) => hrefFor(font.output));
const criticalFontHrefs = fontManifest.fonts
  .filter((font) => font.delivery === "critical")
  .map((font) => hrefFor(font.output));

test("public routes preload only the critical Latin faces within transfer budgets", async ({ page }) => {
  await page.goto("/en");
  await page.evaluate(async () => document.fonts.ready);

  const preloads = await page.locator('link[rel="preload"][as="font"]').evaluateAll((links) =>
    links.map((link) => new URL((link as HTMLLinkElement).href).pathname).sort(),
  );
  expect(preloads).toEqual([...criticalFontHrefs].sort());

  const resources = await page.evaluate(() =>
    performance
      .getEntriesByType("resource")
      .map((entry) => entry as PerformanceResourceTiming)
      .filter((entry) => entry.name.includes("/fonts/v2/"))
      .map((entry) => ({
        href: new URL(entry.name).pathname,
        bytes: entry.encodedBodySize,
        initiatorType: entry.initiatorType,
      })),
  );
  const requestedHrefs = [...new Set(resources.map((resource) => resource.href))];
  expect(requestedHrefs.length).toBeLessThanOrEqual(fontManifest.budgets.totalRequests);
  expect(requestedHrefs.every((href) => allFontHrefs.includes(href))).toBe(true);
  expect(criticalFontHrefs.every((href) => requestedHrefs.includes(href))).toBe(true);

  const criticalBytes = fontManifest.fonts
    .filter((font) => font.delivery === "critical")
    .reduce((total, font) => total + font.outputBytes, 0);
  const totalBytes = fontManifest.fonts.reduce((total, font) => total + font.outputBytes, 0);
  expect(criticalBytes).toBeLessThanOrEqual(fontManifest.budgets.criticalBytes);
  expect(totalBytes).toBeLessThanOrEqual(fontManifest.budgets.totalBytes);
});

test("metric-compatible fallbacks keep the public task usable during slow font arrival", async ({
  page,
}) => {
  let releaseFonts = () => {};
  const fontGate = new Promise<void>((resolve) => {
    releaseFonts = resolve;
  });

  await page.addInitScript(() => {
    (window as Window & { __openNoshFontCls?: number }).__openNoshFontCls = 0;
    new PerformanceObserver((list) => {
      for (const entry of list.getEntries()) {
        const shift = entry as PerformanceEntry & { hadRecentInput: boolean; value: number };
        if (!shift.hadRecentInput) {
          const target = window as Window & { __openNoshFontCls?: number };
          target.__openNoshFontCls = (target.__openNoshFontCls ?? 0) + shift.value;
        }
      }
    }).observe({ type: "layout-shift", buffered: true });
  });
  await page.route("**/fonts/v2/*.woff2", async (route) => {
    await fontGate;
    await route.continue();
  });

  await page.goto("/en", { waitUntil: "domcontentloaded" });
  const heading = page.getByRole("heading", { level: 1 });
  const start = page.getByRole("link", { name: "Start" });
  await expect(heading).toBeVisible();
  await expect(start).toBeVisible();
  await start.focus();
  await expect(start).toBeFocused();

  releaseFonts();
  await page.evaluate(async () => {
    await document.fonts.ready;
    await new Promise<void>((resolve) => requestAnimationFrame(() => requestAnimationFrame(() => resolve())));
  });
  const fontCls = await page.evaluate(
    () => (window as Window & { __openNoshFontCls?: number }).__openNoshFontCls ?? 0,
  );
  expect(fontCls).toBeLessThanOrEqual(fontManifest.budgets.fontCls);
  await expect(heading).toBeVisible();
  await expect(start).toBeFocused();
});

test("Tracker documents transfer zero Living Commons font bytes", async ({ page }) => {
  const fontRequests: string[] = [];
  page.on("request", (request) => {
    if (request.url().includes("/fonts/v2/")) fontRequests.push(request.url());
  });

  await page.goto("/tracker");
  await expect(page.locator("html")).toHaveAttribute("data-surface", "tracker");
  await expect(page.locator('link[rel="preload"][as="font"]')).toHaveCount(0);
  expect(fontRequests).toEqual([]);
});
