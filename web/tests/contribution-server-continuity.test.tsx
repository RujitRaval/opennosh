import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { ContributionJourney } from "@/components/contributions/contribution-journey";
import { ContributionStatus } from "@/components/contributions/contribution-status";
import type { ContributionCapability } from "@/lib/contributions/domain";
import {
  contributionDraftStorageKey,
  emptyContributionFields,
  localContributionStorageKey,
} from "@/lib/contributions/local-draft";

const router = { push: vi.fn(), replace: vi.fn() };
const apiState = vi.hoisted(() => ({
  contributionDraft: vi.fn(),
  contributorCase: vi.fn(),
  patchContributionDraft: vi.fn(),
}));

vi.mock("next/navigation", () => ({ useRouter: () => router }));
vi.mock("@/lib/api", () => ({
  api: {
    contributionDraft: apiState.contributionDraft,
    patchContributionDraft: apiState.patchContributionDraft,
  },
  ApiError: class ApiError extends Error {},
}));
vi.mock("@/lib/api/governance", () => ({
  governanceApi: { contributorCase: apiState.contributorCase },
}));

const storageValues = new Map<string, string>();
const storage: Storage = {
  get length() { return storageValues.size; },
  clear: () => storageValues.clear(),
  getItem: (key) => storageValues.get(key) ?? null,
  key: (index) => [...storageValues.keys()][index] ?? null,
  removeItem: (key) => { storageValues.delete(key); },
  setItem: (key, value) => { storageValues.set(key, value); },
};
Object.defineProperty(window, "localStorage", { configurable: true, value: storage });

beforeEach(() => {
  apiState.contributorCase.mockRejectedValue(new Error("Governance disabled"));
});

class TestIntersectionObserver {
  constructor() {}
  observe() {}
  disconnect() {}
  unobserve() {}
  takeRecords() { return []; }
  root = null;
  rootMargin = "0px";
  thresholds = [0];
}

function capability(receipt: ContributionCapability["receipt"] = null): ContributionCapability {
  return {
    draftId: "server-draft",
    draftVersion: 3,
    reviewState: receipt ? "in_review" : "draft",
    completedStages: ["evidence"],
    accessibleStages: ["evidence", "details"],
    blockers: [],
    nextSafeStage: "details",
    requestedStage: "evidence",
    resolvedStage: "evidence",
    repairReason: null,
    savedAt: "2026-08-24T08:00:00Z",
    fields: {
      ...emptyContributionFields,
      evidence_type: "public_document",
      source_uri: "https://example.test/source",
      rights_acknowledged: true,
      name: "Server name",
    },
    duplicateCandidates: [],
    receipt,
  };
}

afterEach(() => {
  cleanup();
  apiState.contributionDraft.mockReset();
  apiState.contributorCase.mockReset();
  apiState.patchContributionDraft.mockReset();
  router.push.mockReset();
  router.replace.mockReset();
  window.localStorage.clear();
  vi.unstubAllGlobals();
  vi.useRealTimers();
});

describe("server-backed contribution continuity", () => {
  it("hydrates a remote draft once and preserves device edits while stages change", async () => {
    vi.stubGlobal("IntersectionObserver", TestIntersectionObserver);
    apiState.contributionDraft.mockResolvedValue(capability());
    window.localStorage.setItem(localContributionStorageKey, "anonymous device draft");

    const { rerender } = render(
      <ContributionJourney language="en" routeDraftId="server-draft" requestedStage="details" />,
    );
    const name = await screen.findByLabelText("Food name");
    fireEvent.change(name, { target: { value: "Device edit" } });

    rerender(
      <ContributionJourney language="en" routeDraftId="server-draft" requestedStage="evidence" />,
    );
    rerender(
      <ContributionJourney language="en" routeDraftId="server-draft" requestedStage="details" />,
    );

    await waitFor(() => expect(screen.getByLabelText("Food name")).toHaveValue("Device edit"));
    expect(apiState.contributionDraft).toHaveBeenCalledTimes(1);
    expect(window.localStorage.getItem(localContributionStorageKey)).toBe("anonymous device draft");
    expect(window.localStorage.getItem(contributionDraftStorageKey("server-draft"))).toContain("Device edit");
  });

  it("shows a recoverable error when a remote draft cannot be opened", async () => {
    apiState.contributionDraft.mockRejectedValue(new Error("Contribution draft not found."));

    render(
      <ContributionJourney language="en" routeDraftId="missing-draft" requestedStage="evidence" />,
    );

    expect(await screen.findByRole("heading", { name: "We could not open this contribution" })).toBeVisible();
    expect(screen.getByText("Contribution draft not found.")).toBeVisible();
    expect(screen.getByRole("link", { name: "Return to your device draft" })).toHaveAttribute(
      "href",
      "/en/contribute/local/evidence",
    );
  });

  it("sends only coalesced changed fields and announces server sync after acknowledgement", async () => {
    vi.stubGlobal("IntersectionObserver", TestIntersectionObserver);
    apiState.contributionDraft.mockResolvedValue(capability());
    apiState.patchContributionDraft.mockImplementation(async (_draftId, input) => {
      const updated = capability();
      return {
        ...updated,
        draftVersion: 4,
        fields: {
          ...updated.fields,
          name: input.patches.find((patch: { field: string }) => patch.field === "name")?.value,
          duplicates_resolved: false,
        },
      };
    });

    render(
      <ContributionJourney language="en" routeDraftId="server-draft" requestedStage="details" />,
    );
    const name = await screen.findByLabelText("Food name");
    vi.useFakeTimers();
    fireEvent.change(name, { target: { value: "D" } });
    fireEvent.change(name, { target: { value: "Da" } });
    fireEvent.change(name, { target: { value: "Dal" } });

    expect(screen.getByRole("status")).toHaveTextContent("sync scheduled");
    await vi.advanceTimersByTimeAsync(750);

    expect(apiState.patchContributionDraft).toHaveBeenCalledTimes(1);
    expect(apiState.patchContributionDraft.mock.calls[0]?.[1]).toMatchObject({
      expected_draft_version: 3,
      patches: expect.arrayContaining([
        { field: "name", value: "Dal", base_value: "Server name", base_version: 3 },
        { field: "duplicates_resolved", value: false, base_value: false, base_version: 3 },
      ]),
    });
    expect(apiState.patchContributionDraft.mock.calls[0]?.[1].patches).toHaveLength(2);
    expect(screen.getByRole("status")).toHaveTextContent("Synced");
  });

  it("flushes a pending edit when continuing to the next stage", async () => {
    vi.stubGlobal("IntersectionObserver", TestIntersectionObserver);
    vi.spyOn(window, "scrollTo").mockImplementation(() => undefined);
    apiState.contributionDraft.mockResolvedValue(capability());
    apiState.patchContributionDraft.mockResolvedValue({
      ...capability(),
      draftVersion: 4,
      fields: { ...capability().fields, source_uri: "https://example.test/new-source" },
    });

    render(
      <ContributionJourney language="en" routeDraftId="server-draft" requestedStage="evidence" />,
    );
    fireEvent.change(await screen.findByLabelText("Public source URL"), {
      target: { value: "https://example.test/new-source" },
    });
    fireEvent.click(screen.getAllByRole("button", { name: /Continue/ })[0]!);

    await waitFor(() => expect(apiState.patchContributionDraft).toHaveBeenCalledTimes(1));
    expect(apiState.patchContributionDraft.mock.calls[0]?.[1]).toMatchObject({
      requested_stage: "details",
      patches: expect.arrayContaining([
        expect.objectContaining({
          field: "source_uri", value: "https://example.test/new-source",
        }),
      ]),
    });
    expect(router.push).toHaveBeenCalledWith("/en/contribute/server-draft/details");
  });

  it("renders the complete server-authoritative receipt on the stable status route", async () => {
    apiState.contributionDraft.mockResolvedValue(capability({
      submissionId: "submission-42",
      submittedAt: "2026-08-24T08:30:00Z",
      acknowledgementDueAt: "2026-08-26T08:30:00Z",
      attribution: "Community kitchen",
      statusHref: "/en/contribute/server-draft/status",
    }));
    apiState.contributorCase.mockResolvedValue({ review_case_id: "review-case-42" });

    render(<ContributionStatus language="en" draftId="server-draft" />);

    expect(await screen.findByRole("heading", { name: "Handed to the commons" })).toBeVisible();
    expect(screen.getByText("submission-42")).toBeVisible();
    expect(screen.getByText("Community kitchen")).toBeVisible();
    expect(screen.getByText(/Acknowledgement expected/)).toBeVisible();
    expect(screen.getByText(/Publication is the separate event/)).toBeVisible();
    expect(screen.getByRole("link", { name: "Open accountable review history" })).toHaveAttribute(
      "href",
      "/governance/cases/review-case-42",
    );
  });
});
