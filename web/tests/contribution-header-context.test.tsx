import { cleanup, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import { PublicHeader } from "@/components/public/public-header";

vi.mock("next/navigation", () => ({
  usePathname: () => "/en/contribute/local/evidence",
}));

afterEach(cleanup);

describe("contribution journey header context", () => {
  it("anchors the journey to its hub instead of presenting a backward next action", () => {
    render(<PublicHeader language="en" />);

    expect(screen.getByRole("link", { name: "Contribution" })).toHaveAttribute(
      "href",
      "/en/contribute",
    );
    expect(screen.queryByRole("link", { name: "Next / Start" })).not.toBeInTheDocument();
  });
});
