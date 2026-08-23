import { cleanup, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import { PublicHeader } from "@/components/public/public-header";

vi.mock("next/navigation", () => ({
  usePathname: () => "/en/commons",
}));

afterEach(cleanup);

describe("public header rendering", () => {
  it("uses a compact contextual action and renders utility symbols instead of entity names", () => {
    render(<PublicHeader language="en" />);

    expect(screen.getByRole("link", { name: "Next / Notices" })).toHaveAttribute(
      "href",
      "/en/notices",
    );
    const tracker = screen.getByRole("link", { name: "Tracker" });
    expect(tracker).toHaveAttribute("href", "/tracker");
    expect(tracker).toHaveTextContent("\u2197");
    expect(screen.queryByText(/&nearr;/)).not.toBeInTheDocument();
  });
});
