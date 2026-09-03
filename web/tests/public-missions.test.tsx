import { cleanup, render, screen, within } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import { PublicMissions, PublicMissionsLoading } from "@/components/public/public-missions";
import {
  createPublicMissionsSnapshotResolver,
  resolvePublicMissionsSnapshot,
} from "@/lib/public-missions";
import {
  publicMissionActivityFixture,
  publicMissionCatalogFixture,
} from "@/tests/fixtures/public-missions";

afterEach(() => {
  cleanup();
  vi.unstubAllEnvs();
});

function responseFor(url: string | URL | Request) {
  const path = new URL(String(url)).pathname;
  return Promise.resolve(Response.json(
    path.endsWith("/activity") ? publicMissionActivityFixture() : publicMissionCatalogFixture(),
  ));
}

describe("public missions server adapter", () => {
  it("resolves the catalog and privacy-thresholded activity through uncached network reads", async () => {
    const fetcher = vi.fn<typeof fetch>().mockImplementation(responseFor);

    const result = await resolvePublicMissionsSnapshot(fetcher);

    expect(result.catalog.state).toBe("live");
    expect(result.activity.state).toBe("live");
    expect(fetcher).toHaveBeenCalledTimes(2);
    expect(fetcher).toHaveBeenCalledWith(
      "http://localhost:8000/api/v1/public/missions",
      expect.objectContaining({ cache: "no-store" }),
    );
    expect(fetcher).toHaveBeenCalledWith(
      "http://localhost:8000/api/v1/public/missions/activity",
      expect.objectContaining({ cache: "no-store" }),
    );
  });

  it("fails malformed catalog proof closed without hiding valid regional proof", async () => {
    const malformedCatalog = {
      ...publicMissionCatalogFixture(),
      missions: [{ ...publicMissionCatalogFixture().missions[0], progress_state: "live", accepted_count: 2 }],
    };
    const fetcher = vi.fn<typeof fetch>().mockImplementation((url) => Promise.resolve(Response.json(
      String(url).endsWith("/activity") ? publicMissionActivityFixture() : malformedCatalog,
    )));

    const result = await resolvePublicMissionsSnapshot(fetcher);

    expect(result.catalog).toMatchObject({ state: "unavailable", reason: "proof_unavailable", missions: [] });
    expect(result.activity.state).toBe("live");
  });

  it.each([
    ["sub-threshold count", { ...publicMissionActivityFixture().regions[0], accepted_count: 9 }],
    ["precise location field", { ...publicMissionActivityFixture().regions[0], latitude: 18.1 }],
    ["contributor dimension", { ...publicMissionActivityFixture().regions[0], contributor_id: "person-1" }],
  ])("fails a %s closed without discarding a valid catalog", async (_label, region) => {
    const malformedActivity = { ...publicMissionActivityFixture(), regions: [region] };
    const fetcher = vi.fn<typeof fetch>().mockImplementation((url) => Promise.resolve(Response.json(
      String(url).endsWith("/activity") ? malformedActivity : publicMissionCatalogFixture(),
    )));

    const result = await resolvePublicMissionsSnapshot(fetcher);

    expect(result.catalog.state).toBe("live");
    expect(result.activity).toMatchObject({ state: "unavailable", reason: "proof_unavailable", regions: [] });
  });

  it("fails each non-success response closed independently", async () => {
    const fetcher = vi.fn<typeof fetch>().mockImplementation((url) => Promise.resolve(
      String(url).endsWith("/activity")
        ? Response.json(publicMissionActivityFixture("zero"))
        : new Response("unavailable", { status: 503 }),
    ));

    await expect(resolvePublicMissionsSnapshot(fetcher)).resolves.toMatchObject({
      catalog: { state: "unavailable", missions: [] },
      activity: { state: "zero", regions: [] },
    });
  });

  it("caches only validated live or zero proof and never serves it after expiry", async () => {
    let currentTime = 1_000;
    let catalogProofAvailable = true;
    const fetcher = vi.fn<typeof fetch>().mockImplementation((url) => {
      if (String(url).endsWith("/activity")) {
        return Promise.resolve(Response.json(publicMissionActivityFixture("zero")));
      }
      return Promise.resolve(Response.json(
        catalogProofAvailable
          ? publicMissionCatalogFixture()
          : { ...publicMissionCatalogFixture("unavailable"), reason: "proof_unavailable" },
      ));
    });
    const resolve = createPublicMissionsSnapshotResolver(fetcher, () => currentTime, 60_000);

    await expect(resolve()).resolves.toMatchObject({
      catalog: { state: "live" },
      activity: { state: "zero" },
    });
    await resolve();
    expect(fetcher).toHaveBeenCalledTimes(2);

    catalogProofAvailable = false;
    currentTime += 60_001;
    await expect(resolve()).resolves.toMatchObject({
      catalog: { state: "unavailable", reason: "proof_unavailable" },
      activity: { state: "zero" },
    });
    expect(fetcher).toHaveBeenCalledTimes(4);

    await resolve();
    expect(fetcher).toHaveBeenCalledTimes(5);
  });

  it.each([
    ["inverted checkpoint counts", {
      ...publicMissionCatalogFixture(),
      missions: [{
        ...publicMissionCatalogFixture().missions[0],
        accepted_count: 5,
        matched_event_count: 4,
      }],
    }],
    ["partial checkpoint proof", {
      ...publicMissionCatalogFixture(),
      missions: [{
        ...publicMissionCatalogFixture().missions[0],
        checkpoint_id: null,
      }],
    }],
    ["live-empty catalog", { ...publicMissionCatalogFixture(), missions: [] }],
    ["zero-nonempty catalog", { ...publicMissionCatalogFixture(), state: "zero" }],
    ["duplicate mission identity", {
      ...publicMissionCatalogFixture(),
      missions: [
        publicMissionCatalogFixture().missions[0],
        publicMissionCatalogFixture().missions[0],
      ],
    }],
  ])("fails a catalog with %s closed independently", async (_label, catalog) => {
    const fetcher = vi.fn<typeof fetch>().mockImplementation((url) => Promise.resolve(Response.json(
      String(url).endsWith("/activity") ? publicMissionActivityFixture() : catalog,
    )));

    await expect(resolvePublicMissionsSnapshot(fetcher)).resolves.toMatchObject({
      catalog: { state: "unavailable", reason: "proof_unavailable", missions: [] },
      activity: { state: "live" },
    });
  });

  it.each([
    ["live-empty activity", { ...publicMissionActivityFixture(), regions: [] }],
    ["zero-nonempty activity", { ...publicMissionActivityFixture(), state: "zero" }],
    ["duplicate region identities", {
      ...publicMissionActivityFixture(),
      regions: [
        publicMissionActivityFixture().regions[0],
        publicMissionActivityFixture().regions[0],
      ],
    }],
  ])("fails %s closed independently", async (_label, activity) => {
    const fetcher = vi.fn<typeof fetch>().mockImplementation((url) => Promise.resolve(Response.json(
      String(url).endsWith("/activity") ? activity : publicMissionCatalogFixture(),
    )));

    await expect(resolvePublicMissionsSnapshot(fetcher)).resolves.toMatchObject({
      catalog: { state: "live" },
      activity: { state: "unavailable", reason: "proof_unavailable", regions: [] },
    });
  });

  it("fails an oversized regional presentation closed independently", async () => {
    const regions = Array.from({ length: 301 }, (_, index) => ({
      region_code: `${String.fromCharCode(65 + Math.floor(index / 26))}${String.fromCharCode(65 + (index % 26))}`,
      level: "country" as const,
      accepted_count: 10,
    }));
    const fetcher = vi.fn<typeof fetch>().mockImplementation((url) => Promise.resolve(Response.json(
      String(url).endsWith("/activity")
        ? { ...publicMissionActivityFixture(), regions }
        : publicMissionCatalogFixture(),
    )));

    await expect(resolvePublicMissionsSnapshot(fetcher)).resolves.toMatchObject({
      catalog: { state: "live" },
      activity: { state: "unavailable", reason: "proof_unavailable", regions: [] },
    });
  });

  it("coalesces concurrent cache misses without racing proof results", async () => {
    let releaseRequest: (() => void) | undefined;
    const gate = new Promise<void>((resolve) => {
      releaseRequest = resolve;
    });
    const fetcher = vi.fn<typeof fetch>().mockImplementation(async (url) => {
      await gate;
      return Response.json(
        String(url).endsWith("/activity")
          ? publicMissionActivityFixture()
          : publicMissionCatalogFixture(),
      );
    });
    const resolve = createPublicMissionsSnapshotResolver(fetcher);

    const first = resolve();
    const second = resolve();
    releaseRequest?.();

    await expect(Promise.all([first, second])).resolves.toHaveLength(2);
    expect(fetcher).toHaveBeenCalledTimes(2);
  });
});

describe("public mission states", () => {
  it.each([
    ["unavailable", "Progress proof unavailable"],
    ["zero", "No accepted records yet"],
    ["partial", "4 of 10 accepted"],
    ["live", "Target reached · 12 accepted"],
    ["stale", "Stale · 4 accepted at the last verified checkpoint"],
    ["paused", "Paused · 4 accepted"],
    ["completed", "Completed · 4 accepted"],
    ["released", "Released · 4 accepted"],
    ["closed", "Closed · 4 accepted"],
  ] as const)("renders an honest %s progress state", (state, label) => {
    render(<PublicMissions language="en" catalog={publicMissionCatalogFixture("live", state)} activity={publicMissionActivityFixture("zero")} />);
    expect(screen.getByText(label)).toBeVisible();
  });

  it("shows lifecycle rules, destination, pause review, and release proof", () => {
    const { rerender } = render(<PublicMissions language="en" catalog={publicMissionCatalogFixture("live", "paused")} activity={publicMissionActivityFixture("zero")} />);
    expect(screen.getByText(/signed target pack/)).toBeVisible();
    expect(screen.getByText("caribbean-community / foods")).toBeVisible();
    expect(screen.getByText(/Review scheduled Sep 15, 2026/)).toBeVisible();

    rerender(<PublicMissions language="en" catalog={publicMissionCatalogFixture("live", "released")} activity={publicMissionActivityFixture("zero")} />);
    expect(screen.getByText("Release receipt aaaaaaaaaaaa")).toBeVisible();
  });

  it("distinguishes disabled, unavailable, and empty catalogs", () => {
    const unavailable = publicMissionCatalogFixture("unavailable");
    const { rerender } = render(<PublicMissions language="en" catalog={unavailable} activity={publicMissionActivityFixture("unavailable")} />);
    expect(screen.getByText("Public missions are not open yet.")).toBeVisible();
    expect(screen.getByText("The geographic activity surface is not open yet.")).toBeVisible();

    rerender(<PublicMissions language="en" catalog={{ ...unavailable, reason: "proof_unavailable" }} activity={{ ...publicMissionActivityFixture("unavailable"), reason: "proof_unavailable" }} />);
    expect(screen.getByText("Mission proof is unavailable.")).toBeVisible();
    expect(screen.getByText("Regional proof is unavailable.")).toBeVisible();

    rerender(<PublicMissions language="en" catalog={publicMissionCatalogFixture("zero")} activity={publicMissionActivityFixture("zero")} />);
    expect(screen.getByText("No moderated missions are public yet.")).toBeVisible();
    expect(screen.getByText("No region meets the privacy threshold yet.")).toBeVisible();
  });

  it("renders only broad region cohorts with no total, filter, timestamp, or person", () => {
    render(<PublicMissions language="en" catalog={publicMissionCatalogFixture()} activity={publicMissionActivityFixture()} />);
    const activity = screen.getByLabelText("Mission activity by broad pack locale");
    expect(within(activity).getByText("Jamaica")).toBeVisible();
    expect(within(activity).getByText("Latin America")).toBeVisible();
    expect(within(activity).getByText("14 accepted records")).toBeVisible();
    expect(within(activity).queryByText(/total|filter|@|latitude|longitude/i)).not.toBeInTheDocument();
    expect(within(activity).queryByRole("time")).not.toBeInTheDocument();
  });

  it("keeps static mission progress out of live status regions", () => {
    const mission = publicMissionCatalogFixture().missions[0];
    render(<PublicMissions
      language="en"
      catalog={{ ...publicMissionCatalogFixture(), missions: [mission, { ...mission, mission_id: "44444444-4444-4444-8444-444444444444" }] }}
      activity={publicMissionActivityFixture("zero")}
    />);
    expect(screen.queryAllByRole("status")).toHaveLength(1);
    expect(screen.getByRole("status")).toHaveTextContent("No region meets the privacy threshold yet.");
  });

  it("shows a truthful loading state without speculative missions or counts", () => {
    render(<PublicMissionsLoading />);
    expect(screen.getByText("Checking mission proof.")).toBeVisible();
    expect(screen.getByText("Checking regional proof.")).toBeVisible();
    expect(screen.getByText(/No mission or progress count/)).toBeVisible();
    expect(screen.getByText(/No region appears/)).toBeVisible();
    expect(screen.queryByText("4 of 10 accepted")).not.toBeInTheDocument();
  });
});
