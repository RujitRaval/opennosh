import { expect, test, type Page } from "@playwright/test";

const completeDraft = {
  schemaVersion: "1",
  clientDraftId: "local",
  fields: {
    evidence_type: "public_document",
    source_uri: "https://example.org/food",
    rights_acknowledged: true,
    name: "Test food",
    name_local: "",
    locale: "en-US",
    category: "Prepared food",
    portion_description: "1 cup",
    portion_amount: "1",
    portion_unit: "serving",
    portion_grams: "200",
    energy_kcal: "250",
    protein_g: "10",
    fat_g: "8",
    carbohydrate_g: "35",
    ingredients: "",
    duplicates_resolved: true,
    pack_id: "global-core",
    source_date: "2026-08-24",
    attribution: "Test contributor",
    source_license: "CC0-1.0",
    review_acknowledged: true,
  },
  duplicateCandidates: [],
  duplicateQuery: "Test food|en-US",
  savedAt: "2026-08-24T12:00:00.000Z",
  saveState: "saved_on_device",
};

async function seedContribution(page: Page) {
  await page.addInitScript((draft) => {
    window.localStorage.setItem("opennosh.contribution.local.v1", JSON.stringify(draft));
  }, completeDraft);
}

test("all shipped-language routes and contribution stages agree on English @shipped", async ({ page }) => {
  const consoleErrors: string[] = [];
  page.on("console", (message) => {
    if (message.type() === "error") consoleErrors.push(message.text());
  });
  await seedContribution(page);

  for (const route of ["/en", "/en/explore?food_locale=hi-IN", "/en/commons", "/en/build", "/en/notices"]) {
    await page.goto(route);
    await expect(page.locator("html")).toHaveAttribute("lang", "en");
    await expect(page.locator("body")).not.toContainText(/\{[a-zA-Z0-9_]+\}/);
  }
  await page.goto("/en");
  await expect(page).toHaveTitle("Food data belongs to everyone - opennosh");
  await expect(page.locator('meta[name="description"]')).toHaveAttribute(
    "content",
    "Search, verify, improve, and reuse an open, versioned food-data commons.",
  );
  await page.goto("/en/notices");
  await expect(page).toHaveTitle("Licenses and data notices - opennosh");

  const stages = {
    evidence: "Start with the source",
    details: "Describe what the source says",
    duplicates: "Check what already exists",
    provenance: "Keep its origin attached",
    review: "Review the exact proposal",
  };
  for (const [stage, heading] of Object.entries(stages)) {
    await page.goto("/en/contribute/local/" + stage);
    await expect(page.getByRole("heading", { level: 1, name: heading })).toBeVisible();
    await expect(page.locator("html")).toHaveAttribute("lang", "en");
  }

  expect(consoleErrors).toEqual([]);
});

test("expanded pseudo-copy survives every contribution stage without overflow @pseudo", async ({ page }, testInfo) => {
  const consoleErrors: string[] = [];
  page.on("console", (message) => {
    if (message.type() === "error") consoleErrors.push(message.text());
  });
  await seedContribution(page);

  await page.goto("/en-XA?food_locale=hi-IN");
  await expect(page.locator("html")).toHaveAttribute("lang", "en-XA");
  await expect(page.locator("html")).toHaveAttribute("data-pseudo-locale", "true");
  await expect(page).toHaveTitle(/^［/);
  await expect(page.locator('meta[name="description"]')).toHaveAttribute("content", /^［/);
  await expect(page.getByRole("heading", { level: 1 })).toContainText("［");
  await expect(page).toHaveURL(/food_locale=hi-IN/);
  if (testInfo.project.name.includes("mobile")) {
    await page.locator(".menu-button").click();
  }
  const languageSelect = testInfo.project.name.includes("mobile")
    ? page.locator("#mobile-menu select")
    : page.locator("select.language-label");
  await languageSelect.selectOption("en");
  await expect(page).toHaveURL(/\/en\?food_locale=hi-IN$/);
  await page.goto("/en-XA?food_locale=hi-IN");

  for (const stage of ["evidence", "details", "duplicates", "provenance", "review"]) {
    await page.goto("/en-XA/contribute/local/" + stage);
    await expect(page.locator("html")).toHaveAttribute("lang", "en-XA");
    await expect(page.locator(".contribution-stage-heading h1")).toContainText("［");
    const viewportOverflow = await page.evaluate(() => document.documentElement.scrollWidth - window.innerWidth);
    expect(viewportOverflow).toBeLessThanOrEqual(1);
  }

  expect(consoleErrors).toEqual([]);
});
