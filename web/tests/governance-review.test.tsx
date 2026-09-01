import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import { GovernanceCase } from "@/components/governance/governance-case";
import { GovernanceQueue } from "@/components/governance/governance-queue";
import type { GovernanceReviewCase } from "@/lib/api/domain/governance";

const apiState = vi.hoisted(() => ({
  approve: vi.fn(),
  claim: vi.fn(),
  decide: vi.fn(),
  dispute: vi.fn(),
  pause: vi.fn(),
  queue: vi.fn(),
  recuse: vi.fn(),
  respond: vi.fn(),
  resume: vi.fn(),
  reviewCase: vi.fn(),
}));

vi.mock("@/lib/api/governance", () => ({ governanceApi: apiState }));

function reviewCase(state: GovernanceReviewCase["state"] = "in_review"): GovernanceReviewCase {
  return {
    review_case_id: "55555555-5555-4555-8555-555555555555",
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
    revision: 2,
    assigned_steward_actor_id: state === "pending" ? null : "22222222-2222-4222-8222-222222222222",
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

afterEach(() => {
  cleanup();
  for (const mock of Object.values(apiState)) mock.mockReset();
});

describe("accountable governance browser surface", () => {
  it("renders deterministic queue facts without a hidden score", async () => {
    apiState.queue.mockResolvedValue({ pack_id: "global-core", cases: [reviewCase("pending")] });
    render(<GovernanceQueue />);

    expect(await screen.findByRole("heading", { name: "Oldest unacknowledged work comes first." })).toBeVisible();
    expect(screen.getByText("Red lentils")).toBeVisible();
    expect(screen.getByText("Needs acknowledgement")).toBeVisible();
    expect(screen.getByRole("link", { name: /Red lentils/ })).toHaveAttribute(
      "href",
      "/governance/cases/55555555-5555-4555-8555-555555555555",
    );
  });

  it("shows exact-version truth, public reasons, and no provider material", async () => {
    apiState.reviewCase.mockResolvedValue(reviewCase());
    render(<GovernanceCase reviewCaseId="55555555-5555-4555-8555-555555555555" />);

    expect(await screen.findByRole("heading", { name: "Red lentils" })).toBeVisible();
    expect(screen.getByText(/This contribution is not published/)).toBeVisible();
    expect(screen.getByText("Submitted for steward review.")).toBeVisible();
    expect(screen.getByText(/Evidence bytes, object keys, provider revisions/)).toBeVisible();
    expect(document.body).not.toHaveTextContent("secret_access_key");
    expect(screen.getByRole("button", { name: "Record decision" })).toBeEnabled();
    expect(screen.getByRole("button", { name: "Recuse and release" })).toBeEnabled();
  });

  it("refreshes after a reasoned changes request", async () => {
    apiState.reviewCase
      .mockResolvedValueOnce(reviewCase())
      .mockResolvedValueOnce(reviewCase("changes_requested"));
    apiState.decide.mockResolvedValue({});
    render(<GovernanceCase reviewCaseId="55555555-5555-4555-8555-555555555555" />);

    await screen.findByRole("button", { name: "Record decision" });
    fireEvent.change(screen.getAllByLabelText("Public-safe reason", { selector: "textarea" })[0]!, {
      target: { value: "Clarify the serving size." },
    });
    fireEvent.click(screen.getByRole("button", { name: "Record decision" }));

    await waitFor(() => expect(apiState.decide).toHaveBeenCalledWith(
      "55555555-5555-4555-8555-555555555555",
      { expected_revision: 2, outcome: "changes_requested", reason: "Clarify the serving size." },
    ));
    expect(await screen.findByRole("heading", { name: "Respond with a new exact version" })).toBeVisible();
  });
});
