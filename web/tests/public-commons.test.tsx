import { cleanup, render, screen, within } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import { PublicHomeView } from "@/app/(public)/[language]/page";
import {
  AcceptedActivity,
  AcceptedActivityLoading,
} from "@/components/public/public-truth-signals";
import { resolvePublicCommonsSnapshot } from "@/lib/public-commons";
import { publicCommonsFixture } from "@/tests/fixtures/public-commons";

afterEach(() => {
  cleanup();
  vi.useRealTimers();
});

describe("public commons server adapter", () => {
  it("resolves exactly one five-minute revalidated snapshot request", async () => {
    const snapshot = publicCommonsFixture("live");
    const fetcher = vi.fn<typeof fetch>().mockResolvedValue(
      new Response(JSON.stringify(snapshot), {
        status: 200,
        headers: { "Content-Type": "application/json" },
      }),
    );

    const result = await resolvePublicCommonsSnapshot(fetcher);

    expect(result).toEqual(snapshot);
    expect(fetcher).toHaveBeenCalledTimes(1);
    expect(fetcher).toHaveBeenCalledWith(
      "http://localhost:8000/api/v1/public/commons-snapshot",
      expect.objectContaining({ next: { revalidate: 300 } }),
    );
  });


  it.each([
    [
      "unsupported event type",
      {
        ...publicCommonsFixture("live"),
        activity: {
          ...publicCommonsFixture("live").activity,
          events: [
            {
              ...publicCommonsFixture("live").activity.events[0],
              event_type: "review",
            },
          ],
        },
      },
    ],
    [
      "unsafe recent-record link",
      {
        ...publicCommonsFixture("quiet"),
        activity: {
          ...publicCommonsFixture("quiet").activity,
          most_recent_verified_record: {
            ...publicCommonsFixture("quiet").activity.most_recent_verified_record,
            href: "//attacker.example",
          },
        },
      },
    ],
    [
      "invalid timestamp",
      {
        ...publicCommonsFixture("live"),
        activity: { ...publicCommonsFixture("live").activity, ends_at: "not-a-date" },
      },
    ],
    [
      "partial unverified proof",
      {
        ...publicCommonsFixture("unavailable"),
        release: publicCommonsFixture("live").release,
      },
    ],
    [
      "contradictory freshness",
      {
        ...publicCommonsFixture("live"),
        freshness: { ...publicCommonsFixture("live").freshness, activity: "stale" },
      },
    ],
  ])("fails closed for a malformed %s", async (_label, payload) => {
    const fetcher = vi.fn<typeof fetch>().mockResolvedValue(Response.json(payload));

    const result = await resolvePublicCommonsSnapshot(fetcher);

    expect(result.state).toBe("unavailable");
    expect(result.release).toBeNull();
    expect(result.verified_record_count).toBeNull();
  });

  it("fails closed without exposing a number or fabricated event", async () => {
    const fetcher = vi.fn<typeof fetch>().mockRejectedValue(new Error("API unavailable"));

    const result = await resolvePublicCommonsSnapshot(fetcher);

    expect(result.state).toBe("unavailable");
    expect(result.release).toBeNull();
    expect(result.verified_record_count).toBeNull();
    expect(result.activity.events).toEqual([]);
  });
});

describe("public truth signal states", () => {
  it("uses the same verified count and release in hero and footer", () => {
    render(<PublicHomeView language="en" snapshot={publicCommonsFixture("live")} />);

    const proofs = screen.getAllByText("18,429");
    expect(proofs).toHaveLength(2);
    expect(screen.getAllByText(/release 0\.30\.0\.0/i)).toHaveLength(3);
    expect(screen.getByText("1 accepted change")).toBeVisible();
    expect(screen.getByText("Accepted Dhokla as a verified food record.")).toBeVisible();
    expect(screen.getByText("Gujarat, India")).toBeVisible();
    expect(screen.getByText("Portion")).toBeVisible();
    expect(screen.getByRole("link", { name: /view source commit/i })).toHaveAttribute(
      "href",
      "https://github.com/RujitRaval/opennosh/commit/abcdef1234567890",
    );
  });

  it("never changes a verified count on a timer", () => {
    vi.useFakeTimers();
    render(<PublicHomeView language="en" snapshot={publicCommonsFixture("live")} />);

    vi.advanceTimersByTime(86_400_000);

    expect(screen.getAllByText("18,429")).toHaveLength(2);
  });

  it("shows the exact quiet state, last verified record, and useful actions", () => {
    render(<AcceptedActivity language="en" snapshot={publicCommonsFixture("quiet")} />);

    expect(screen.getByText("No accepted changes in the last 24 hours.")).toBeVisible();
    expect(screen.getByRole("link", { name: "Khichdi" })).toHaveAttribute(
      "href",
      "/en/explore/khichdi-gujarati",
    );
    expect(screen.getByText(/August 20, 2026/)).toBeVisible();
    expect(screen.getByRole("link", { name: "Search verified records" })).toBeVisible();
    expect(screen.getByRole("link", { name: "Contribute a food" })).toBeVisible();
  });

  it("freezes stale activity while retaining the last verified proof", () => {
    render(<PublicHomeView language="en" snapshot={publicCommonsFixture("stale")} />);

    const field = screen.getByText("Activity is temporarily stale.").closest(".activity-field");
    expect(field).toHaveAttribute("data-motion-state", "paused");
    expect(field).toHaveAttribute("data-activity-state", "stale");
    expect(screen.getByText(/Frozen at the last verified release 0\.30\.0\.0/)).toBeVisible();
    expect(screen.getByText(/Stale since Aug 23, 2026, 6:05 PM UTC/)).toBeVisible();
    expect(screen.getAllByText(/release 0\.30\.0\.0 · stale/i)).toHaveLength(2);
  });

  it("labels partial and illustrative activity without upgrading either to fact", () => {
    const { rerender } = render(
      <AcceptedActivity language="en" snapshot={publicCommonsFixture("partial")} />,
    );
    expect(screen.getByText("Accepted activity is still catching up.")).toBeVisible();
    expect(screen.getByText(/event list may be incomplete/i)).toBeVisible();

    rerender(<AcceptedActivity language="en" snapshot={publicCommonsFixture("illustrative")} />);
    expect(screen.getAllByText("Illustrative data")[0]).toBeVisible();
    expect(screen.getByText("This preview is not production activity.")).toBeVisible();
  });

  it("keeps search and contribution available when activity is unavailable", () => {
    render(<AcceptedActivity language="en" snapshot={publicCommonsFixture("unavailable")} />);

    expect(screen.getByText("Accepted activity is unavailable.")).toBeVisible();
    expect(screen.queryByText("18,429")).not.toBeInTheDocument();
    const actions = screen.getByRole("navigation", { name: "Commons activity actions" });
    expect(within(actions).getAllByRole("link")).toHaveLength(2);
  });

  it("shows a truthful loading state with no speculative pulse or count", () => {
    render(<AcceptedActivityLoading />);

    expect(screen.getByText("Checking the latest accepted events.")).toBeVisible();
    expect(screen.getByText(/No speculative pulses or counts/)).toBeVisible();
    expect(screen.queryByText("18,429")).not.toBeInTheDocument();
  });
});
