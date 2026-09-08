import { cleanup, render, screen, within } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import PublicHubPage, {
  dynamic, generateMetadata,
  generateStaticParams,
} from "@/app/(public)/[language]/[hub]/page";

const mocks = vi.hoisted(() => ({
  getFoodCatalogSummary: vi.fn(),
  getPublicCommonsSnapshot: vi.fn(),
}));

vi.mock("@/lib/food-catalog", () => ({
  getFoodCatalogSummary: mocks.getFoodCatalogSummary,
}));
vi.mock("@/lib/public-commons", () => ({
  getPublicCommonsSnapshot: mocks.getPublicCommonsSnapshot,
}));

afterEach(() => {
  cleanup();
  vi.unstubAllEnvs();
});

beforeEach(() => {
  vi.clearAllMocks();
  mocks.getFoodCatalogSummary.mockResolvedValue(null);
  mocks.getPublicCommonsSnapshot.mockResolvedValue(null);
});

describe("public hub pages", () => {
  it("generates one stable route for each task hub", () => {
    expect(generateStaticParams()).toEqual([
      { hub: "explore" },
      { hub: "contribute" },
      { hub: "commons" },
      { hub: "build" },
    ]);
  });

  it("renders its location, visible title, next action, and honest availability state", async () => {
    render(
      await PublicHubPage({
        params: Promise.resolve({ language: "en", hub: "explore" }),
      }),
    );

    expect(screen.getByRole("heading", { level: 1, name: "Explore" })).toBeVisible();
    expect(screen.getByRole("link", { name: /See how records work/ })).toHaveAttribute(
      "href",
      "#principles",
    );
    expect(screen.getByText(/will not advertise unfinished work/)).toBeVisible();

    const breadcrumbs = screen.getByRole("navigation", { name: "Breadcrumb" });
    expect(within(breadcrumbs).getByRole("link", { name: "Home" })).toHaveAttribute("href", "/en");
    expect(within(breadcrumbs).getByText("Explore")).toHaveAttribute("aria-current", "page");
  });

  it("renders only enabled child surfaces", async () => {
    vi.stubEnv("OPENNOSH_PUBLIC_NAV_FEATURES", "explorer-search");
    expect(dynamic).toBe("force-dynamic");
    render(
      await PublicHubPage({
        params: Promise.resolve({ language: "en", hub: "explore" }),
      }),
    );

    expect(screen.getByRole("link", { name: /Search foods/ })).toHaveAttribute(
      "href",
      "/en/explore#search",
    );
    expect(screen.queryByText(/will not advertise unfinished work/)).not.toBeInTheDocument();
  });

  it("keeps signed community proof separate from USDA reference counts", async () => {
    vi.stubEnv("OPENNOSH_PUBLIC_NAV_FEATURES", "explorer-search");
    mocks.getFoodCatalogSummary.mockResolvedValue({
      schema_version: "1.0",
      community_records: 166,
      usda_reference_records: 13_497,
      searchable_records: 13_663,
    });
    mocks.getPublicCommonsSnapshot.mockResolvedValue({
      state: "live",
      release: { release_id: "fixture" },
      verified_record_count: 166,
    });

    render(
      await PublicHubPage({
        params: Promise.resolve({ language: "en", hub: "explore" }),
      }),
    );

    expect(screen.getByText("Signed community records")).toBeVisible();
    expect(screen.getByText("166")).toBeVisible();
    expect(screen.getByText("USDA reference foods")).toBeVisible();
    expect(screen.getByText("13,497")).toBeVisible();

    cleanup();
    mocks.getPublicCommonsSnapshot.mockResolvedValue({
      state: "illustrative",
      release: { release_id: "fixture" },
      verified_record_count: 166,
    });
    render(
      await PublicHubPage({
        params: Promise.resolve({ language: "en", hub: "explore" }),
      }),
    );

    expect(screen.queryByText("Signed community records")).not.toBeInTheDocument();
    expect(screen.getByText("USDA reference foods")).toBeVisible();
    expect(screen.getByText("13,497")).toBeVisible();
  });

  it("provides localized metadata from the same hub registry", async () => {
    await expect(
      generateMetadata({
        params: Promise.resolve({ language: "en", hub: "commons" }),
      }),
    ).resolves.toMatchObject({
      title: "Commons - opennosh",
    });
  });
});
