import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import { metadata } from "@/app/layout";
import Home from "@/app/page";

describe("Home", () => {
  it("explains the product foundation", () => {
    render(<Home />);

    expect(screen.getByRole("heading", { name: /know what fuels your next set/i })).toBeVisible();
    expect(screen.getByText(/self-hosted nutrition and strength tracker/i)).toBeVisible();
  });

  it("publishes the root page metadata", () => {
    expect(metadata).toMatchObject({
      title: "opennosh",
      description: "Self-hosted nutrition and strength tracking.",
    });
  });
});
