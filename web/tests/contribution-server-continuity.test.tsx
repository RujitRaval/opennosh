import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import { ContributionJourney } from "@/components/contributions/contribution-journey";
import { ContributionStatus } from "@/components/contributions/contribution-status";
import type { ContributionCapability } from "@/lib/contributions/domain";
import {
  contributionDraftStorageKey,
  emptyContributionFields,
  localContributionStorageKey,
} from "@/lib/contributions/local-draft";

const router = { push: vi.fn(), replace: vi.fn() };
const apiState = vi.hoisted(() => ({ contributionDraft: vi.fn() }));

vi.mock("next/navigation", () => ({ useRouter: () => router }));
vi.mock("@/lib/api", () => ({
  api: { contributionDraft: apiState.contributionDraft },
  ApiError: class ApiError extends Error {},
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
  router.push.mockReset();
  router.replace.mockReset();
  window.localStorage.clear();
  vi.unstubAllGlobals();
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

  it("renders the complete server-authoritative receipt on the stable status route", async () => {
    apiState.contributionDraft.mockResolvedValue(capability({
      submissionId: "submission-42",
      submittedAt: "2026-08-24T08:30:00Z",
      acknowledgementDueAt: "2026-08-26T08:30:00Z",
      attribution: "Community kitchen",
      statusHref: "/en/contribute/server-draft/status",
    }));

    render(<ContributionStatus language="en" draftId="server-draft" />);

    expect(await screen.findByRole("heading", { name: "Handed to the commons" })).toBeVisible();
    expect(screen.getByText("submission-42")).toBeVisible();
    expect(screen.getByText("Community kitchen")).toBeVisible();
    expect(screen.getByText(/Acknowledgement expected/)).toBeVisible();
    expect(screen.getByText(/Publication is the separate event/)).toBeVisible();
  });
});
