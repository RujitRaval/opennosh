import { cleanup, render, screen, within } from "@testing-library/react";
import { afterEach, describe, expect, it } from "vitest";

import NoticesPage, { generateMetadata } from "@/app/(public)/[language]/notices/page";
import { TrackerFooter } from "@/components/tracker/tracker-footer";

afterEach(cleanup);

describe("license and data notices", () => {
  it("publishes focused metadata and an accessible notice hierarchy", async () => {
    const metadata = await generateMetadata({ params: Promise.resolve({ language: "en" }) });
    expect(metadata.title).toContain("Licenses and data notices");
    render(await NoticesPage({ params: Promise.resolve({ language: "en" }) }));

    expect(screen.getByRole("heading", { level: 1, name: "Licenses and data notices" })).toBeVisible();
    expect(screen.getByRole("heading", { name: "Software" })).toBeVisible();
    expect(screen.getByRole("heading", { name: "Food data" })).toBeVisible();
    expect(screen.getByRole("heading", { name: "Exercise data" })).toBeVisible();
    expect(screen.getByRole("heading", { name: "Private account data" })).toBeVisible();
  });

  it("enumerates every source and license family without combining private data", async () => {
    render(await NoticesPage({ params: Promise.resolve({ language: "en" }) }));
    const main = screen.getByRole("main");

    expect(within(main).getByText(/MIT License/i)).toBeVisible();
    expect(within(main).getAllByText(/CC0 1\.0 Universal/i)).toHaveLength(2);
    expect(within(main).getByText(/ODbL 1\.0.*DbCL 1\.0/i)).toBeVisible();
    expect(within(main).getByText(/CC BY-SA 3\.0/i)).toBeVisible();
    expect(within(main).getByText(/not included in any public food or exercise dataset export/i)).toBeVisible();
  });

  it("keeps the notice page globally reachable", () => {
    render(<TrackerFooter />);
    expect(screen.getByRole("link", { name: "Licenses & data notices" })).toHaveAttribute(
      "href",
      "/en/notices",
    );
  });

  it("shows the full Home, Build, current-page breadcrumb", async () => {
    render(await NoticesPage({ params: Promise.resolve({ language: "en" }) }));

    const breadcrumb = screen.getByRole("navigation", { name: "Breadcrumb" });
    expect(within(breadcrumb).getByRole("link", { name: "Home" })).toHaveAttribute("href", "/en");
    expect(within(breadcrumb).getByRole("link", { name: "Build" })).toHaveAttribute(
      "href",
      "/en/build",
    );
    expect(within(breadcrumb).getByText("Licenses + notices")).toHaveAttribute(
      "aria-current",
      "page",
    );
    expect(screen.getByRole("link", { name: "MIT License" })).toHaveAttribute(
      "href",
      "https://github.com/RujitRaval/opennosh/blob/main/LICENSE",
    );
    expect(screen.getByRole("link", { name: "complete distribution notice" })).toHaveAttribute(
      "href",
      "https://github.com/RujitRaval/opennosh/blob/main/NOTICE.md",
    );
  });
});
