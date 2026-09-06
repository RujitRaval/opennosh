import { expect, test } from "@playwright/test";

// Regression: ISSUE-001 — the public search button inherited ink-colored text on an ink background
// Found by /qa on 2026-09-06
// Report: .gstack/qa-reports/qa-report-opennosh-org-2026-09-06.md
test("public search submit text contrasts with its background", async ({ page }) => {
  await page.goto("/en/explore");

  const submit = page.getByRole("button", { name: "Search records" });
  await expect(submit).toBeVisible();

  const colors = await submit.evaluate((element) => {
    const styles = getComputedStyle(element);
    return { foreground: styles.color, background: styles.backgroundColor };
  });

  expect(colors).toEqual({
    foreground: "rgb(244, 240, 230)",
    background: "rgb(18, 18, 15)",
  });
});
