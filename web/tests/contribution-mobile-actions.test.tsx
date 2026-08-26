import { act, cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import { ContributionJourney } from "@/components/contributions/contribution-journey";
import { localContributionStorageKey, newLocalContributionDraft } from "@/lib/contributions/local-draft";

const router = { push: vi.fn(), replace: vi.fn() };
vi.mock("next/navigation", () => ({ useRouter: () => router }));

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

type ObserverCallback = (entries: IntersectionObserverEntry[]) => void;
const observers: ObserverCallback[] = [];

class TestIntersectionObserver {
  constructor(callback: ObserverCallback) { observers.push(callback); }
  observe() {}
  disconnect() {}
  unobserve() {}
  takeRecords() { return []; }
  root = null;
  rootMargin = "0px";
  thresholds = [0];
}

afterEach(() => {
  cleanup();
  observers.length = 0;
  window.localStorage.clear();
  vi.restoreAllMocks();
});

describe("contribution mobile actions", () => {
  it("explains the exact public trust state for every evidence class", async () => {
    vi.stubGlobal("IntersectionObserver", TestIntersectionObserver);
    render(
      <ContributionJourney language="en" routeDraftId="local" requestedStage="evidence" />,
    );

    expect(
      await screen.findByText(/choose a source class to see the proof required/i),
    ).toBeVisible();
    fireEvent.click(screen.getByRole("radio", { name: "Packaging label" }));
    expect(screen.getByText(/independently durable sanitized copy/i)).toBeVisible();
    fireEvent.click(screen.getByRole("radio", { name: "Government database" }));
    expect(screen.getByText(/exact dataset release and record identity/i)).toBeVisible();
    fireEvent.click(screen.getByRole("radio", { name: "Public document" }));
    expect(screen.getByText(/otherwise it preserves only the citation manifest/i)).toBeVisible();
    fireEvent.click(screen.getByRole("radio", { name: "Maintainer attestation" }));
    expect(screen.getByText(/never presented as preserved primary evidence/i)).toBeVisible();
  });

  it("shows the safe-area actions mid-flow and hides them beside in-flow actions", async () => {
    vi.stubGlobal("IntersectionObserver", TestIntersectionObserver);
    const draft = newLocalContributionDraft("mobile-actions");
    draft.fields.evidence_type = "public_document";
    draft.fields.source_uri = "https://example.test/source";
    draft.fields.rights_acknowledged = true;
    window.localStorage.setItem(localContributionStorageKey, JSON.stringify(draft));

    const { container } = render(
      <ContributionJourney language="en" routeDraftId="local" requestedStage="details" />,
    );
    await waitFor(() => expect(observers).toHaveLength(1));
    const heading = container.querySelector(".contribution-stage-heading");
    const inline = container.querySelector(".contribution-actions-inline");
    const mobile = container.querySelector(".contribution-actions-mobile");
    expect(heading).not.toBeNull();
    expect(inline).not.toBeNull();

    act(() => observers[0]?.([
      { target: heading, isIntersecting: false } as IntersectionObserverEntry,
      { target: inline, isIntersecting: false } as IntersectionObserverEntry,
    ]));
    expect(mobile).toHaveClass("is-visible");

    act(() => observers[0]?.([
      { target: inline, isIntersecting: true } as IntersectionObserverEntry,
    ]));
    expect(mobile).not.toHaveClass("is-visible");
  });

  it("visibly gates review handoff until secure evidence preservation is active", async () => {
    vi.stubGlobal("IntersectionObserver", TestIntersectionObserver);
    const draft = newLocalContributionDraft("review-gate");
    Object.assign(draft.fields, {
      evidence_type: "public_document",
      source_uri: "https://example.test/source",
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
      source_date: "2026-08-26",
      attribution: "Fixture contributor",
      source_license: "contributor-original",
    });
    draft.duplicateQuery = "Test food|en-US";
    window.localStorage.setItem(localContributionStorageKey, JSON.stringify(draft));

    render(<ContributionJourney language="en" routeDraftId="local" requestedStage="review" />);

    expect(await screen.findByText("Review handoff is not open yet")).toBeVisible();
    for (const button of screen.getAllByRole("button", { name: "Evidence handoff coming next" })) {
      expect(button).toBeDisabled();
    }
  });
});
