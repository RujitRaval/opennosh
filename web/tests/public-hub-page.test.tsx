import { cleanup, render, screen, within } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import PublicHubPage, {
  dynamic, generateMetadata,
  generateStaticParams,
} from "@/app/(public)/[language]/[hub]/page";

afterEach(() => {
  cleanup();
  vi.unstubAllEnvs();
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
