import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import { RouteError } from "@/components/errors/route-error";
import { ApiProblem } from "@/lib/api/domain/problem";

afterEach(cleanup);

describe("RouteError", () => {
  it("shows a safe API message and request reference, then retries", () => {
    const reset = vi.fn();
    const error = new ApiProblem(
      "Review the highlighted fields.",
      "invalid-field",
      "request-123",
      422,
      "validation_failed",
    );

    render(<RouteError error={error} reset={reset} />);

    expect(screen.getByText("Review the highlighted fields.")).toBeInTheDocument();
    expect(screen.getByText("Reference: request-123")).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "Try again" }));
    expect(reset).toHaveBeenCalledOnce();
  });

  it("does not expose an unexpected error message and uses its digest", () => {
    const error = Object.assign(new Error("database password leaked"), {
      digest: "digest-456",
    });

    render(<RouteError error={error} reset={() => undefined} />);

    expect(screen.queryByText("database password leaked")).not.toBeInTheDocument();
    expect(screen.getByText("Please try the page again.")).toBeInTheDocument();
    expect(screen.getByText("Reference: digest-456")).toBeInTheDocument();
  });

  it("omits unavailable references", () => {
    const error = new ApiProblem(
      "Connection unavailable.",
      "network",
      "unavailable",
    );

    render(<RouteError error={error} reset={() => undefined} />);

    expect(screen.queryByText(/Reference:/)).not.toBeInTheDocument();
  });
});
