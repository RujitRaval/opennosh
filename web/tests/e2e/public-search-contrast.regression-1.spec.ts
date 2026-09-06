import { expect, test } from "@playwright/test";

// Regression: ISSUE-001 — the public search button inherited ink-colored text on an ink background
// Found by /qa on 2026-09-06
// Report: .gstack/qa-reports/qa-report-opennosh-org-2026-09-06.md
test("public search controls use the approved foreground and background tokens", async ({ page }) => {
  await page.goto("/en/explore");

  const input = page.getByRole("searchbox", { name: "Food name" });
  const submit = page.getByRole("button", { name: "Search records" });
  await expect(input).toBeVisible();
  await expect(submit).toBeVisible();

  const inputColors = await input.evaluate((element) => {
    const styles = getComputedStyle(element);
    return { foreground: styles.color, background: styles.backgroundColor };
  });
  const submitColors = await submit.evaluate((element) => {
    const styles = getComputedStyle(element);
    return { foreground: styles.color, background: styles.backgroundColor };
  });

  expect(inputColors).toEqual({
    foreground: "rgb(18, 18, 15)",
    background: "rgb(244, 240, 230)",
  });
  expect(submitColors).toEqual({
    foreground: "rgb(244, 240, 230)",
    background: "rgb(18, 18, 15)",
  });
});
