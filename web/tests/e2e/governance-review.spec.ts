import AxeBuilder from "@axe-core/playwright";
import { expect, test } from "@playwright/test";

const caseId = "55555555-5555-4555-8555-555555555555";

function reviewCase(state: "pending" | "in_review" = "pending") {
  return {
    review_case_id: caseId,
    source_draft_id: "44444444-4444-4444-8444-444444444444",
    source_draft_version: 3,
    pack_id: "global-core",
    submitted_fields: {
      name: "Red lentils",
      evidence_type: "public_document",
      source_uri: "https://example.test/source",
      attribution: "Fixture publisher",
      source_license: "CC-BY-4.0",
    },
    state,
    revision: state === "pending" ? 1 : 2,
    assigned_steward_actor_id: state === "pending"
      ? null
      : "22222222-2222-4222-8222-222222222222",
    acknowledged_at: state === "pending" ? null : "2026-09-01T21:00:00Z",
    pause_reason: null,
    next_review_at: null,
    opened_at: "2026-09-01T20:00:00Z",
    updated_at: "2026-09-01T21:00:00Z",
    closed_at: null,
    events: [{
      sequence: 1,
      event_type: "opened",
      actor_id: "11111111-1111-4111-8111-111111111111",
      public_reason: "Submitted for steward review.",
      occurred_at: "2026-09-01T20:00:00Z",
    }],
    disputes: [],
    appeals: [],
  };
}

test("steward can acknowledge an exact-version case without hidden priority", async ({ page }) => {
  let claimed = false;
  await page.route("**/api/v1/governance/**", async (route) => {
    const request = route.request();
    const url = new URL(request.url());
    if (request.method() === "POST" && url.pathname.endsWith("/claim")) {
      expect(request.headers()["idempotency-key"]).toMatch(/^[0-9a-f-]{36}$/);
      expect(request.postDataJSON()).toEqual({ expected_revision: 1 });
      claimed = true;
      return route.fulfill({ json: reviewCase("in_review") });
    }
    if (url.pathname.endsWith(`/review-cases/${caseId}`)) {
      return route.fulfill({ json: reviewCase(claimed ? "in_review" : "pending") });
    }
    return route.fulfill({ json: { pack_id: "global-core", cases: [reviewCase()] } });
  });

  await page.goto("/governance");
  await expect(page.getByRole("heading", { name: "Oldest unacknowledged work comes first." })).toBeVisible();
  await expect(page.getByText("There is no hidden score. Ownership, pauses, and next-review dates stay visible.")).toBeVisible();
  await page.getByRole("link", { name: /Red lentils/ }).click();

  await expect(page.getByRole("heading", { name: "Red lentils" })).toBeVisible();
  await expect(page.getByText(/This contribution is not published/)).toBeVisible();
  await page.getByRole("button", { name: "Acknowledge this case" }).click();
  await expect(page.getByText("in review", { exact: true })).toBeVisible();
  await expect(page.getByRole("heading", { name: "Request changes or reject" })).toBeVisible();

  const accessibility = await new AxeBuilder({ page }).analyze();
  expect(accessibility.violations).toEqual([]);
});
