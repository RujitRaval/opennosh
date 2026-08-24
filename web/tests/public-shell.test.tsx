import { cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { readFileSync } from "node:fs";
import path from "node:path";
import { afterEach, describe, expect, it, vi } from "vitest";

import { BrandLogo } from "@/components/public/brand-logo";
import { PublicHeader } from "@/components/public/public-header";
import { brandAssetVersion, brandSurfaces, brandWordmarks } from "@/lib/brand-assets";
import { buildPublicNavigation } from "@/lib/public-navigation";

const navigationState = vi.hoisted(() => ({ pathname: "/en" }));
vi.mock("next/navigation", () => ({
  usePathname: () => navigationState.pathname,
}));

const expectedColors = {
  "rice-paper": ["#12120F", "#F04E35"],
  "commons-ink": ["#F4F0E6", "#D7F34C"],
  "signal-tomato": ["#12120F", "#F4F0E6"],
  "field-acid": ["#12120F", "#5848E8"],
  "one-light": ["#F4F0E6", "#F4F0E6"],
  "one-dark": ["#12120F", "#12120F"],
} as const;

afterEach(() => {
  cleanup();
  navigationState.pathname = "/en";
});

describe("public brand assets", () => {
  it.each(brandSurfaces)("ships an outlined, surface-safe %s wordmark", (surface) => {
    const asset = brandWordmarks[surface];
    const svg = readFileSync(path.join(process.cwd(), "public", asset.src), "utf8");

    expect(svg).toContain("<path");
    expect(svg).not.toContain("<text");
    expect(asset.version).toBe(brandAssetVersion);
    expect(asset.src).toContain(`/brand/${brandAssetVersion}/`);
    expect(asset.minimumContrast).toBe(3);
    for (const color of expectedColors[surface]) expect(svg).toContain(color);
  });

  it("uses the selected production asset in the shared logo component", () => {
    render(<BrandLogo surface="commons-ink" />);
    expect(screen.getByRole("img", { name: "opennosh" })).toHaveAttribute(
      "src",
      "/brand/v1/wordmark-commons-ink.svg",
    );
  });
});

describe("public navigation", () => {
  it("exposes four stable localized hubs and the independent tracker utility", () => {
    render(<PublicHeader language="en" />);
    const primary = screen.getByRole("navigation", { name: "Primary navigation" });

    for (const label of ["Explore", "Contribute", "Commons", "Build"]) {
      expect(within(primary).getByRole("link", { name: label })).toHaveAttribute(
        "href",
        `/en/${label.toLowerCase()}`,
      );
    }
    expect(screen.getByRole("link", { name: /Tracker/ })).toHaveAttribute("href", "/tracker");
    expect(screen.getByRole("link", { name: "Next / Explore" })).toHaveAttribute(
      "href",
      "/en/explore",
    );
    expect(screen.getAllByLabelText("Interface language: English")).toHaveLength(2);
  });

  it("identifies the current hub on a deep public page", () => {
    navigationState.pathname = "/en/notices";
    render(<PublicHeader language="en" />);

    expect(screen.getByRole("link", { name: "Build" })).toHaveAttribute("aria-current", "page");
    expect(screen.getByRole("link", { name: "Explore" })).not.toHaveAttribute("aria-current");
  });

  it("shows enabled children inside their hub without changing the four-hub trunk", () => {
    const navigation = buildPublicNavigation("en", ["explorer-search", "api-reference"]);
    render(<PublicHeader language="en" navigation={navigation} />);
    fireEvent.click(screen.getByRole("button", { name: "Menu" }));

    const mobile = screen.getByRole("navigation", { name: "Mobile navigation" });
    expect(within(mobile).getByRole("link", { name: "Search foods" })).toHaveAttribute(
      "href",
      "/en/explore#search",
    );
    expect(within(mobile).getByRole("link", { name: "API reference" })).toHaveAttribute(
      "href",
      "/en/build#api",
    );
    expect(within(mobile).getAllByRole("region")).toHaveLength(4);
  });

  it("moves focus into the mobile menu, then restores it on Escape", async () => {
    render(<PublicHeader language="en" />);
    const menuButton = screen.getByRole("button", { name: "Menu" });
    menuButton.focus();
    fireEvent.click(menuButton);

    const mobile = screen.getByRole("navigation", { name: "Mobile navigation" });
    const firstHub = within(mobile).getByRole("link", { name: /Explore/ });
    await waitFor(() => expect(firstHub).toHaveFocus());

    fireEvent.keyDown(window, { key: "Escape" });

    expect(screen.queryByRole("navigation", { name: "Mobile navigation" })).not.toBeInTheDocument();
    await waitFor(() => expect(menuButton).toHaveFocus());
  });
});
