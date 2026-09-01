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
  appeal: vi.fn(),
  pause: vi.fn(),
  queue: vi.fn(),
  recuse: vi.fn(),
  release: vi.fn(),
  respond: vi.fn(),
  resolveAppeal: vi.fn(),
  resolveDispute: vi.fn(),
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
    viewer_role: "steward",
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
    expect(await screen.findByRole("heading", { name: "Open a dispute" })).toBeVisible();
    expect(screen.queryByRole("heading", { name: "Respond with a new exact version" })).not.toBeInTheDocument();
  });

  it("shows contributor follow-up without steward-only controls", async () => {
    apiState.reviewCase.mockResolvedValue({
      ...reviewCase("changes_requested"),
      viewer_role: "contributor",
    });
    render(<GovernanceCase reviewCaseId="55555555-5555-4555-8555-555555555555" />);

    expect(await screen.findByRole("heading", { name: "Respond with a new exact version" })).toBeVisible();
    expect(screen.queryByRole("button", { name: "Record decision" })).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Recuse and release" })).not.toBeInTheDocument();
  });

  it("exposes the complete dispute and appeal action chain", async () => {
    const dispute = {
      dispute_id: "88888888-8888-4888-8888-888888888888",
      decision_id: "77777777-7777-4777-8777-777777777777",
      category: "accuracy",
      public_reason: "The source was read incorrectly.",
      requested_remedy: "Compare the preserved panel again.",
      state: "open" as const,
      revision: 1,
      resolution: null,
    };
    apiState.reviewCase.mockResolvedValue({
      ...reviewCase("disputed"),
      disputes: [dispute],
    });
    apiState.resolveDispute.mockResolvedValue({});
    const first = render(
      <GovernanceCase reviewCaseId="55555555-5555-4555-8555-555555555555" />,
    );
    expect(await screen.findByRole("heading", { name: "Resolve the open dispute" })).toBeVisible();
    fireEvent.change(screen.getByLabelText("Public-safe resolution"), {
      target: { value: "Return the case for a fresh review." },
    });
    fireEvent.click(screen.getByRole("button", { name: "Resolve and reopen review" }));
    await waitFor(() => expect(apiState.resolveDispute).toHaveBeenCalledWith(
      dispute.dispute_id,
      {
        expected_case_revision: 2,
        expected_dispute_revision: 1,
        resolution: "Return the case for a fresh review.",
      },
    ));
    first.unmount();

    apiState.reviewCase.mockReset().mockResolvedValue({
      ...reviewCase("reopened"),
      viewer_role: "contributor",
      disputes: [{ ...dispute, state: "resolved", revision: 2, resolution: "Reopened." }],
    });
    const second = render(
      <GovernanceCase reviewCaseId="55555555-5555-4555-8555-555555555555" />,
    );
    expect(await screen.findByRole("heading", { name: "Appeal the resolved dispute" })).toBeVisible();
    second.unmount();

    apiState.reviewCase.mockReset().mockResolvedValue({
      ...reviewCase("appealed"),
      disputes: [{ ...dispute, state: "resolved", revision: 2, resolution: "Reopened." }],
      appeals: [{
        appeal_id: "99999999-9999-4999-8999-999999999999",
        dispute_id: dispute.dispute_id,
        public_reason: "The wrong panel was compared.",
        requested_remedy: "Use the preserved back panel.",
        state: "open",
        revision: 1,
        resolution: null,
      }],
    });
    render(<GovernanceCase reviewCaseId="55555555-5555-4555-8555-555555555555" />);
    expect(await screen.findByRole("heading", { name: "Decide the independent appeal" })).toBeVisible();
  });
});
