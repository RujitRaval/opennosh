import { cleanup, render, screen } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { NextRequest } from "next/server";

import PublicHome from "@/app/(public)/[language]/page";
import TrackerPage, { metadata } from "@/app/(tracker)/tracker/page";
import manifest from "@/app/manifest";
import { routes } from "@/lib/routes";
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
    expect(screen.getByRole("link", { name: "opennosh tracker" })).toHaveAttribute(
      "href",
      "/tracker",
    );
  });
});
