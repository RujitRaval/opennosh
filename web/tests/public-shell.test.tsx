import { cleanup, fireEvent, render, screen, within } from "@testing-library/react";
import { readFileSync } from "node:fs";
import path from "node:path";
import { afterEach, describe, expect, it } from "vitest";

import { BrandLogo } from "@/components/public/brand-logo";
import { PublicHeader } from "@/components/public/public-header";
import { brandSurfaces, brandWordmarks } from "@/lib/brand-assets";

const expectedColors = {
  "rice-paper": ["#12120F", "#F04E35"],
  "commons-ink": ["#F4F0E6", "#D7F34C"],
  "signal-tomato": ["#12120F", "#F4F0E6"],
  "field-acid": ["#12120F", "#5848E8"],
  "one-light": ["#F4F0E6", "#F4F0E6"],
  "one-dark": ["#12120F", "#12120F"],
} as const;

afterEach(cleanup);

describe("public brand assets", () => {
  it.each(brandSurfaces)("ships an outlined, surface-safe %s wordmark", (surface) => {
    const asset = brandWordmarks[surface];
    const svg = readFileSync(path.join(process.cwd(), "public", asset.src), "utf8");

    expect(svg).toContain("<path");
    expect(svg).not.toContain("<text");
    for (const color of expectedColors[surface]) expect(svg).toContain(color);
  });

  it("uses the selected production asset in the shared logo component", () => {
    render(<BrandLogo surface="commons-ink" />);
    expect(screen.getByRole("img", { name: "opennosh" })).toHaveAttribute(
      "src",
      "/brand/wordmark-commons-ink.svg",
    );
  });
});

describe("public navigation", () => {
  it("exposes four stable hubs and the independent tracker", () => {
    render(<PublicHeader language="en" />);
    const primary = screen.getByRole("navigation", { name: "Primary navigation" });

    for (const label of ["Explore", "Contribute", "Commons", "Build"]) {
      expect(within(primary).getByRole("link", { name: label })).toHaveAttribute(
        "href",
        `/en#${label.toLowerCase()}`,
      );
    }
    expect(screen.getByRole("link", { name: /Tracker/ })).toHaveAttribute("href", "/tracker");
  });

  it("closes the mobile menu with Escape and restores focus", () => {
    render(<PublicHeader language="en" />);
    const menuButton = screen.getByRole("button", { name: "Menu" });
    menuButton.focus();
    fireEvent.click(menuButton);

    expect(screen.getByRole("navigation", { name: "Mobile navigation" })).toBeVisible();
    fireEvent.keyDown(window, { key: "Escape" });

    expect(screen.queryByRole("navigation", { name: "Mobile navigation" })).not.toBeInTheDocument();
    expect(menuButton).toHaveFocus();
  });
});
