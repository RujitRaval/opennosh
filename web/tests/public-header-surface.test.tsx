import { cleanup, render } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import { PublicHeader } from "@/components/public/public-header";

const navigationState = vi.hoisted(() => ({ pathname: "/en/build" }));
vi.mock("next/navigation", () => ({
  usePathname: () => navigationState.pathname,
}));

afterEach(() => {
  cleanup();
  navigationState.pathname = "/en/build";
});

describe("public header surface contrast", () => {
  it("uses the approved dark-surface wordmark only on the dark Build hub", () => {
    const build = render(<PublicHeader language="en" />);
    expect(build.container.querySelector(".public-header")).toHaveClass("public-header-dark");
    expect(build.container.querySelector(".public-brand-image")).toHaveAttribute(
      "src",
      "/brand/wordmark-commons-ink.svg",
    );

    cleanup();
    navigationState.pathname = "/en/notices";
    const notices = render(<PublicHeader language="en" />);
    expect(notices.container.querySelector(".public-header")).not.toHaveClass("public-header-dark");
    expect(notices.container.querySelector(".public-brand-image")).toHaveAttribute(
      "src",
      "/brand/wordmark-rice-paper.svg",
    );
  });

  it("keeps both wordmark halves visible on the Tomato contribution journey", () => {
    navigationState.pathname = "/en/contribute/local/evidence";
    const contribution = render(<PublicHeader language="en" />);

    expect(contribution.container.querySelector(".public-brand-image")).toHaveAttribute(
      "src",
      "/brand/wordmark-signal-tomato.svg",
    );
  });
});
