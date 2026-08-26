import { expect, test } from "@playwright/test";

import {
  completeContributionDraft,
  emptyContributionDraft,
  expectNoHorizontalOverflow,
  installFrozenClock,
  mockTrackerApi,
  mockTrackerOnboarding,
  mockTrackerSignIn,
  repairContributionDraft,
  seedContribution,
  setCommonsState,
  settleVisualPage,
} from "./visual-fixtures";

test.beforeEach(async ({ page }) => {
  await installFrozenClock(page);
});

test("Living Commons first viewport reflows @reflow @mobile @tablet @wide", async ({ page, request }, testInfo) => {
  await setCommonsState(request, "live");
  await page.goto(`/en?visual=${testInfo.project.name}`);
  await expect(page.getByRole("heading", { level: 1 })).toContainText("Food data");
  await settleVisualPage(page);
  await expectNoHorizontalOverflow(page);
  await expect(page).toHaveScreenshot("homepage-first-viewport.png");
});

test("food trust hierarchy reflows at 200-percent zoom equivalent @reflow @desktop", async ({ page }) => {
  await page.goto("/en/explore/foods/community/rajma-masala?food_locale=hi-IN");
  await expect(page.getByRole("heading", { level: 1, name: "Rajma masala" })).toBeVisible();
  await settleVisualPage(page);
  await expectNoHorizontalOverflow(page);
  await expect(page).toHaveScreenshot("food-record-first-viewport.png");
});

test("all accepted-activity proof states @desktop", async ({ page, request }) => {
  for (const state of ["live", "quiet", "partial", "stale", "unavailable"] as const) {
    await setCommonsState(request, state);
    await page.goto(`/en?visual-state=${state}`);
    await expect(page.locator("[data-activity-state]")).toHaveAttribute("data-activity-state", state);
    await settleVisualPage(page);
    await expect(page.locator(".commons-stage")).toHaveScreenshot(`commons-${state}.png`);
  }
});

test("public Explore hub and enabled search entry @desktop", async ({ page }) => {
  await page.goto("/en/explore");
  await expect(page.getByRole("heading", { level: 1, name: "Explore" })).toBeVisible();
  await expect(page.getByRole("link", { name: /Search foods/ })).toBeVisible();
  await settleVisualPage(page);
  await expect(page.locator("main")).toHaveScreenshot("explore-hub.png");
});

test("every contribution stage @desktop", async ({ page }) => {
  await seedContribution(page, completeContributionDraft);
  for (const stage of ["evidence", "details", "duplicates", "provenance", "review"] as const) {
    await page.goto(`/en/contribute/local/${stage}`);
    await expect(page.locator(".contribution-stage-heading h1")).toBeVisible();
    await settleVisualPage(page);
    await expect(page.locator(".contribution-page")).toHaveScreenshot(`contribution-${stage}.png`);
  }
});

test("contribution validation summary @desktop", async ({ page }) => {
  await seedContribution(page, emptyContributionDraft);
  await page.goto("/en/contribute/local/evidence");
  await page.getByRole("button", { name: /Continue/ }).last().click();
  await expect(page.locator(".contribution-errors")).toBeFocused();
  await settleVisualPage(page);
  await expect(page.locator(".contribution-workspace")).toHaveScreenshot("contribution-validation.png");
});

test("contribution duplicate repair @desktop", async ({ page }) => {
  await seedContribution(page, repairContributionDraft);
  await page.goto("/en/contribute/local/duplicates");
  await expect(page.getByText("Punjabi rajma")).toBeVisible();
  await settleVisualPage(page);
  await expect(page.locator(".contribution-page")).toHaveScreenshot("contribution-repair.png");
});

test("all permitted logo colorways @desktop", async ({ page, baseURL }) => {
  const surfaces = [
    ["rice-paper", "#f4f0e6"],
    ["commons-ink", "#12120f"],
    ["signal-tomato", "#f05237"],
    ["field-acid", "#d7f34c"],
    ["one-light", "#5848e8"],
    ["one-dark", "#f4f0e6"],
  ] as const;
  await page.setContent(`<!doctype html><html><head><style>
    *{box-sizing:border-box}body{margin:0;background:#8c897f;color:#12120f;font:14px Arial,sans-serif}
    main{display:grid;grid-template-columns:repeat(2,1fr);min-height:100vh}
    figure{display:grid;place-items:center;gap:24px;margin:0;padding:64px;border:1px solid rgba(0,0,0,.25)}
    img{display:block;width:min(100%,548px);height:auto}figcaption{text-transform:uppercase;letter-spacing:.12em}
  </style></head><body><main>${surfaces.map(([name, background]) => `<figure style="background:${background}"><img src="${baseURL}/brand/v1/wordmark-${name}.svg" alt="${name} wordmark"><figcaption>${name}</figcaption></figure>`).join("")}</main></body></html>`);
  await page.waitForFunction(() => [...document.images].every((image) => image.complete));
  await expect(page).toHaveScreenshot("logo-colorways.png", { fullPage: true });
});

test("public record unavailable and loading boundaries @desktop", async ({ page }) => {
  await page.goto("/en/explore/foods/community/unavailable-food?food_locale=hi-IN");
  await expect(page.getByRole("heading", { name: "We cannot verify this record right now." })).toBeVisible();
  await settleVisualPage(page);
  await expect(page.locator(".food-record-page")).toHaveScreenshot("public-record-error.png");

  await page.route("**/api/v1/public/foods/community/unavailable-food", () => new Promise(() => {}));
  await page.getByRole("link", { name: "Try again" }).click();
  await expect(page.locator(".record-skeleton")).toBeVisible();
  await expect(page.locator(".food-record-page")).toHaveScreenshot("public-record-loading.png");
});

test("Living Commons Tracker daily log and catalogue results @desktop @mobile", async ({ page }) => {
  await mockTrackerApi(page);
  await page.goto("/tracker");
  await expect(page.getByRole("heading", { name: /nutrition at a glance/i })).toBeVisible();
  await settleVisualPage(page, "tracker");
  await expect(page).toHaveScreenshot("tracker-daily-log.png", { fullPage: true });

  await page.getByRole("button", { name: "Add food" }).click();
  await page.getByLabel("Search the food catalogue").fill("rajma");
  await page.getByRole("button", { name: "Search", exact: true }).click();
  await expect(page.getByText("1 food found")).toBeVisible();
  await expect(page.getByRole("dialog")).toHaveScreenshot("tracker-catalogue-results.png");
});

test("Living Commons Tracker sign-in reflows @desktop @mobile", async ({ page }) => {
  await mockTrackerSignIn(page);
  await page.goto("/tracker");
  await expect(page.getByRole("heading", { name: "A clear view of what fuels you." })).toBeVisible();
  await settleVisualPage(page, "tracker");
  await expectNoHorizontalOverflow(page);
  await expect(page).toHaveScreenshot("tracker-sign-in.png", { fullPage: true });
});

test("Living Commons Tracker trends stay calm and legible @desktop @mobile", async ({ page }) => {
  await mockTrackerApi(page);
  await page.goto("/tracker/trends");
  await expect(page.getByRole("heading", { name: "Trends", exact: true })).toBeVisible();
  await expect(page.getByRole("table")).toHaveCount(3);
  await settleVisualPage(page, "tracker");
  await expectNoHorizontalOverflow(page);
  await expect(page).toHaveScreenshot("tracker-trends.png", { fullPage: true });
});

test("Tracker onboarding, account, and records are launch-complete @desktop @mobile", async ({ page }) => {
  await mockTrackerOnboarding(page);
  await page.goto("/tracker");
  await expect(page.getByRole("heading", { name: "Make the tracker yours." })).toBeVisible();
  await settleVisualPage(page, "tracker");
  await expectNoHorizontalOverflow(page);
  await expect(page).toHaveScreenshot("tracker-onboarding.png", { fullPage: true });

  await page.unrouteAll();
  await mockTrackerApi(page);
  await page.goto("/tracker/account");
  await expect(page.getByRole("heading", { name: "Your data. Your account." })).toBeVisible();
  await settleVisualPage(page, "tracker");
  await expectNoHorizontalOverflow(page);
  await expect(page).toHaveScreenshot("tracker-account.png", { fullPage: true });

  await page.goto("/tracker/records");
  await expect(page.getByRole("heading", { name: "Body and strength, in context." })).toBeVisible();
  await settleVisualPage(page, "tracker");
  await expectNoHorizontalOverflow(page);
  await expect(page).toHaveScreenshot("tracker-records.png", { fullPage: true });
});

test("reduced motion remains complete @focused", async ({ page, request }) => {
  await page.emulateMedia({ reducedMotion: "reduce" });
  await setCommonsState(request, "live");
  await page.goto("/en?visual=reduced-motion");
  expect(await page.evaluate(() => matchMedia("(prefers-reduced-motion: reduce)").matches)).toBe(true);
  await expect(page.locator("html")).toHaveAttribute("data-motion", "off");
  await expect(page.locator("html")).toHaveAttribute("data-motion-reason", "kill-switch");
  await page.evaluate(async () => document.fonts.ready);
  await expect(page.locator(".hero")).toHaveScreenshot("reduced-motion-hero.png");
});

test("state-bearing activity never animates non-live proof @focused", async ({ page, request }) => {
  await setCommonsState(request, "partial");
  await page.goto("/en?visual=motion-acceptance");
  await expect(page.locator("[data-activity-state=partial]")).toHaveAttribute("data-motion-state", "paused");
  await setCommonsState(request, "live");
  await page.goto("/en?visual=motion-acceptance-live");
  await expect(page.locator("[data-activity-state=live]")).toHaveAttribute("data-motion-state", "running");
});

test("forced colors preserve public hierarchy @focused", async ({ page }) => {
  await page.emulateMedia({ forcedColors: "active" });
  await page.goto("/en/contribute/local/evidence");
  await expect(page.locator(".contribution-stage-heading h1")).toBeVisible();
  await page.evaluate(async () => document.fonts.ready);
  await expect(page).toHaveScreenshot("forced-colors-contribution.png");
});

test("pseudo-localized long copy keeps the first viewport readable @focused", async ({ page, request }) => {
  await setCommonsState(request, "live");
  await page.goto("/en-XA?visual=pseudo");
  await expect(page.locator("html")).toHaveAttribute("data-pseudo-locale", "true");
  await settleVisualPage(page);
  await expectNoHorizontalOverflow(page);
  await expect(page).toHaveScreenshot("pseudo-home-first-viewport.png");
});

test("keyboard focus remains visible on the primary action @focused", async ({ page, request }) => {
  await setCommonsState(request, "live");
  await page.goto("/en?visual=keyboard-focus");
  const start = page.getByRole("link", { name: "Start" });
  await start.focus();
  await expect(start).toBeFocused();
  await settleVisualPage(page);
  await expect(page.locator(".hero")).toHaveScreenshot("keyboard-focus-hero.png");
});

test("mobile contribution actions keep safe-area spacing @mobile", async ({ page }) => {
  await seedContribution(page, completeContributionDraft);
  await page.goto("/en/contribute/local/details");
  await page.locator(".contribution-stage-heading").evaluate((element) => element.scrollIntoView(false));
  await page.evaluate(() => window.scrollBy(0, window.innerHeight));
  await expect(page.locator(".contribution-actions-mobile")).toHaveClass(/is-visible/);
  await settleVisualPage(page);
  await expect(page).toHaveScreenshot("mobile-safe-area-actions.png");
});
