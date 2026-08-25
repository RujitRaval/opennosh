import { cleanup, render, screen } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { NextRequest } from "next/server";

import PublicHome from "@/app/(public)/[language]/page";
import TrackerPage, { metadata } from "@/app/(tracker)/tracker/page";
import manifest from "@/app/manifest";
import { routes } from "@/lib/routes";
import {
  publicReturnPathCookie,
  resolvePublicReturnPath,
  resolveTrackerRootContext,
} from "@/lib/root-topology";
import { proxy } from "@/proxy";

function json(body: unknown, status = 200): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { "Content-Type": "application/json" },
  });
}

beforeEach(() => {
  vi.stubGlobal("fetch", vi.fn(async () => json({ detail: "Not authenticated" }, 401)));
});

afterEach(() => {
  cleanup();
  vi.restoreAllMocks();
  vi.unstubAllEnvs();
});

describe("independent public and tracker roots", () => {
  it("defines stable public and tracker addresses", () => {
    expect(routes.publicHome()).toBe("/en");
    expect(routes.tracker.home).toBe("/tracker");
    expect(routes.tracker.trends).toBe("/tracker/trends");
    expect(manifest()).toMatchObject({
      name: "opennosh tracker",
      start_url: "/tracker",
      display: "standalone",
    });
  });

  it.each([
    ["/", "/en"],
    ["/trends", "/tracker/trends"],
    ["/notices", "/en/notices"],
  ])("redirects the legacy path %s to %s", (source, destination) => {
    const response = proxy(new NextRequest(`https://opennosh.org${source}`));
    expect(response.status).toBe(307);
    expect(response.headers.get("location")).toBe(`https://opennosh.org${destination}`);
  });

  it("can roll the unlocalized entry back to Tracker without rendering a page", async () => {
    vi.stubEnv("OPENNOSH_PUBLIC_ROOT_ENABLED", "off");
    const response = proxy(new NextRequest("https://opennosh.org/"));

    expect(response.status).toBe(307);
    expect(response.headers.get("location")).toBe("https://opennosh.org/tracker");
    expect(await response.text()).toBe("");
  });

  it("remembers only same-origin localized public return paths", () => {
    const response = proxy(new NextRequest("https://opennosh.org/en/explore?food_locale=hi-IN"));

    expect(response.headers.get("set-cookie")).toContain(
      `${publicReturnPathCookie}=%2Fen%2Fexplore%3Ffood_locale%3Dhi-IN`,
    );
    expect(resolvePublicReturnPath("/en/explore?food_locale=hi-IN", "en"))
      .toBe("/en/explore?food_locale=hi-IN");
    expect(resolvePublicReturnPath("//example.com/en", "en")).toBe("/en");
    expect(resolvePublicReturnPath("/tracker", "en")).toBe("/en");
    expect(resolvePublicReturnPath("/en/missing", "en")).toBe("/en");
    expect(resolvePublicReturnPath(`/en/${"x".repeat(2_048)}`, "en")).toBe("/en");
    expect(resolvePublicReturnPath("/en/explore/foods/community/masala-dosa", "en"))
      .toBe("/en/explore/foods/community/masala-dosa");
    expect(resolvePublicReturnPath("/en/contribute/local/status", "en"))
      .toBe("/en/contribute/local/status");
    expect(resolveTrackerRootContext({
      savedLanguage: "unsupported",
      savedPublicPath: "/en/commons",
    })).toEqual({ language: "en", publicReturnPath: "/en/commons" });
  });

  it("renders the public commons with an explicit route into the private tracker", async () => {
    render(await PublicHome({ params: Promise.resolve({ language: "en" }) }));

    expect(screen.getByRole("heading", { name: /Food databelongs toeveryone\./ })).toBeVisible();
    const trackerLinks = screen.getAllByRole("link", { name: /Private tracker/ });
    expect(trackerLinks).toHaveLength(2);
    for (const link of trackerLinks) expect(link).toHaveAttribute("href", "/tracker");
  });

  it("renders the tracker directly at its canonical route", async () => {
    render(<TrackerPage />);

    expect(metadata).toMatchObject({ title: "Daily nutrition log · opennosh" });
    expect(await screen.findByRole("heading", { name: "Sign in to your log" })).toBeVisible();
    const trackerWordmark = screen.getByRole("link", { name: "opennosh tracker" });
    expect(trackerWordmark).toHaveAttribute("href", "/tracker");
    expect(trackerWordmark.querySelector("img")).toHaveAttribute(
      "src",
      "/brand/v1/wordmark-commons-ink.svg",
    );
  });
});
