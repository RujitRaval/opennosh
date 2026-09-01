import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

const state = vi.hoisted(() => {
  process.env.NEXT_PUBLIC_OPENNOSH_EVIDENCE_UPLOADS_ENABLED = "true";
  return {
    session: vi.fn(),
    createContributionDraft: vi.fn(),
    contributionDraft: vi.fn(),
    patchContributionDraft: vi.fn(),
    contributionEvidence: vi.fn(),
    submitContributionDraft: vi.fn(),
  };
});

const router = { push: vi.fn(), replace: vi.fn() };
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

vi.mock("next/navigation", () => ({ useRouter: () => router }));
vi.mock("@/lib/api", () => {
  class TestApiError extends Error {
    status?: number;

    constructor(message: string, _kind: string, _reference: string, status?: number) {
      super(message);
      this.status = status;
    }
  }
  return { api: state, ApiError: TestApiError };
});

import { ContributionJourney } from "@/components/contributions/contribution-journey";
import type { ContributionFields } from "@/lib/contributions/domain";
import {
  contributionDraftStorageKey,
  emptyContributionFields,
  localContributionStorageKey,
  newLocalContributionDraft,
} from "@/lib/contributions/local-draft";

class TestIntersectionObserver {
  observe() {}
  disconnect() {}
  unobserve() {}
  takeRecords() { return []; }
  root = null;
  rootMargin = "0px";
  thresholds = [0];
}

const completeFields: ContributionFields = {
  ...emptyContributionFields,
  evidence_type: "packaging_label" as const,
  source_uri: "https://example.test/packaging-label",
  rights_acknowledged: true,
  name: "Test food",
  locale: "en-US",
  category: "prepared meal",
  portion_description: "one serving",
  portion_amount: "1",
  portion_unit: "serving",
  portion_grams: "100",
  energy_kcal: "100",
  protein_g: "5",
  fat_g: "2",
  carbohydrate_g: "15",
  duplicates_resolved: true,
  pack_id: "global-core",
  source_date: "2026-09-01",
  attribution: "Fixture contributor",
  source_license: "contributor-original" as const,
  review_acknowledged: true,
};

function capability(draftVersion = 3) {
  return {
    draftId: "server-draft",
    draftVersion,
    reviewState: "draft" as const,
    completedStages: ["evidence", "details", "duplicates", "provenance", "review"] as const,
    accessibleStages: ["evidence", "details", "duplicates", "provenance", "review"] as const,
    blockers: [],
    nextSafeStage: "review" as const,
    requestedStage: "review" as const,
    resolvedStage: "review" as const,
    repairReason: null,
    savedAt: "2026-09-01T18:00:00Z",
    fields: completeFields,
    duplicateCandidates: [],
    receipt: null,
  };
}

afterEach(() => {
  cleanup();
  window.localStorage.clear();
  router.push.mockReset();
  router.replace.mockReset();
  for (const mock of Object.values(state)) mock.mockReset();
  vi.unstubAllGlobals();
});

describe("enabled private evidence handoff", () => {
  it("moves a local draft to its server evidence stage when no attachment exists", async () => {
    vi.stubGlobal("IntersectionObserver", TestIntersectionObserver);
    state.session.mockResolvedValue({ id: "user" });
    state.createContributionDraft.mockResolvedValue(capability(1));
    state.patchContributionDraft.mockResolvedValue(capability(2));
    const { ApiError } = await import("@/lib/api");
    state.contributionEvidence.mockRejectedValue(
      new ApiError("Missing evidence", "not-found", "test-evidence", 404),
    );
    const draft = newLocalContributionDraft("local-evidence-handoff");
    draft.fields = { ...completeFields };
    draft.duplicateQuery = "Test food|en-US";
    window.localStorage.setItem(localContributionStorageKey, JSON.stringify(draft));

    render(<ContributionJourney language="en" routeDraftId="local" requestedStage="review" />);
    fireEvent.click((await screen.findAllByRole("button", { name: /Hand to review/ }))[0]!);

    await waitFor(() => {
      expect(router.replace).toHaveBeenCalledWith("/en/contribute/server-draft/evidence");
    });
    expect(state.submitContributionDraft).not.toHaveBeenCalled();
    expect(window.localStorage.getItem(localContributionStorageKey)).toBeNull();
  });

  it("submits only after exact-version sanitized evidence is attached", async () => {
    vi.stubGlobal("IntersectionObserver", TestIntersectionObserver);
    state.session.mockResolvedValue({ id: "user" });
    state.contributionDraft.mockResolvedValue(capability());
    state.contributionEvidence.mockResolvedValue({
      evidence_id: "018f5316-4f4e-7d79-b9f6-88c11a68a498",
      evidence_class: "sanitized_media",
      source_draft_version: 3,
      public_state: null,
      preservation_pending: true,
      preservation_failed: false,
      preservation_failure_code: null,
    });
    state.submitContributionDraft.mockResolvedValue({
      ...capability(4),
      reviewState: "in_review",
      receipt: {
        submissionId: "submission-42",
        submittedAt: "2026-09-01T18:05:00Z",
        acknowledgementDueAt: "2026-09-03T18:05:00Z",
        attribution: "Fixture contributor",
        statusHref: "/en/contribute/server-draft/status",
      },
    });
    const stored = newLocalContributionDraft("server-draft");
    stored.fields = { ...completeFields };
    stored.duplicateQuery = "Test food|en-US";
    stored.serverDraftId = "server-draft";
    stored.serverVersion = 3;
    stored.serverFields = { ...completeFields };
    window.localStorage.setItem(
      contributionDraftStorageKey("server-draft"),
      JSON.stringify(stored),
    );

    render(
      <ContributionJourney language="en" routeDraftId="server-draft" requestedStage="review" />,
    );
    fireEvent.click((await screen.findAllByRole("button", { name: /Hand to review/ }))[0]!);

    await waitFor(() => {
      expect(state.submitContributionDraft).toHaveBeenCalledWith("server-draft", {
        expected_draft_version: 3,
        idempotency_key: expect.any(String),
      });
    });
    expect(router.replace).toHaveBeenCalledWith("/en/contribute/server-draft/status");
  });
});
