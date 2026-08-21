import { cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import TrendsPage, { metadata } from "@/app/trends/page";

const user = { id: "4c683fc5-548a-4772-a090-b26ea0951d50", email: "alex@example.com" };

function json(body: unknown, status = 200): Response {
  return new Response(JSON.stringify(body), { status, headers: { "Content-Type": "application/json" } });
}

function trendsFetch() {
  return vi.fn(async (input: RequestInfo | URL) => {
    const url = new URL(String(input), "http://opennosh.test");
    if (url.pathname === "/api/v1/auth/session") return json(user);
    if (url.pathname === "/api/v1/logs/daily-totals/range") {
      return json({
        from_date: url.searchParams.get("from"),
        to_date: url.searchParams.get("to"),
        timezone: url.searchParams.get("timezone"),
        items: [
          { day: "2026-08-19", timezone: "UTC", entry_count: 0, grams: "0.00", nutrients: {} },
          { day: "2026-08-20", timezone: "UTC", entry_count: 2, grams: "200.00", nutrients: { energy_kcal: "600", protein_g: "42" } },
        ],
      });
    }
    if (url.pathname === "/api/v1/body-metrics/trends") {
      return json({ from_date: url.searchParams.get("from"), to_date: url.searchParams.get("to"), items: [
        { id: "kg", recorded_at: "2026-08-19T08:00:00Z", metric_type: "body_weight", value: "80", unit: "kg" },
        { id: "lb", recorded_at: "2026-08-20T08:00:00Z", metric_type: "body_weight", value: "176", unit: "lb" },
      ] });
    }
    if (url.pathname === "/api/v1/workouts/trends") {
      return json({ from_date: url.searchParams.get("from"), to_date: url.searchParams.get("to"), items: [
        { day: "2026-08-20", exercise_id: "squat", exercise_name: "Back squat", load_unit: "kg", volume: "500" },
        { day: "2026-08-20", exercise_id: "squat", exercise_name: "Back squat", load_unit: "lb", volume: "1100" },
      ] });
    }
    throw new Error(`Unexpected request: ${url.pathname}`);
  });
}

afterEach(() => {
  cleanup();
  vi.useRealTimers();
  vi.restoreAllMocks();
});

describe("trends page", () => {
  it("publishes focused metadata", () => {
    expect(metadata).toMatchObject({ title: "Trends · opennosh" });
  });

  it("provides keyboard controls and a visible table alternative for every chart", async () => {
    const fetchMock = trendsFetch();
    vi.stubGlobal("fetch", fetchMock);
    render(<TrendsPage />);

    expect(await screen.findByRole("heading", { name: "Trends" })).toBeVisible();
    expect(screen.getByRole("radio", { name: "30 days" })).toBeChecked();
    expect(await screen.findAllByRole("table")).toHaveLength(3);
    expect(screen.getByRole("navigation", { name: /primary/i })).toContainElement(screen.getByRole("link", { name: "Daily log" }));
    expect(screen.queryByText(/diagnos|streak|failed|over target/i)).not.toBeInTheDocument();

    fireEvent.click(screen.getByRole("radio", { name: "7 days" }));
    await waitFor(() => expect(screen.getByRole("radio", { name: "7 days" })).toBeChecked());
    await waitFor(() => expect(fetchMock.mock.calls.filter(([input]) => String(input).includes("daily-totals/range")).length).toBeGreaterThan(1));
  });

  it("never combines body or strength values with incompatible units", async () => {
    vi.stubGlobal("fetch", trendsFetch());
    render(<TrendsPage />);
    await screen.findByRole("table", { name: "Nutrition data table" });

    const bodySelect = screen.getByLabelText("Body measure");
    expect(within(bodySelect).getByRole("option", { name: "Body weight (kg)" })).toBeVisible();
    expect(within(bodySelect).getByRole("option", { name: "Body weight (lb)" })).toBeVisible();
    fireEvent.change(bodySelect, { target: { value: "body_weight:lb" } });
    const bodyTable = screen.getByRole("table", { name: "Body metrics data table" });
    expect(within(bodyTable).getByText("176 lb")).toBeVisible();
    expect(within(bodyTable).queryByText("80 kg")).not.toBeInTheDocument();

    const strengthSelect = screen.getByLabelText("Exercise and load unit");
    fireEvent.change(strengthSelect, { target: { value: "squat:lb" } });
    const strengthTable = screen.getByRole("table", { name: "Strength volume data table" });
    expect(within(strengthTable).getByText("1,100 lb")).toBeVisible();
    expect(within(strengthTable).queryByText("1,600")).not.toBeInTheDocument();
  });

  it("uses meaningful empty states without health advice", async () => {
    const fetchMock = trendsFetch();
    fetchMock.mockImplementation(async (input) => {
      const url = new URL(String(input), "http://opennosh.test");
      if (url.pathname === "/api/v1/auth/session") return json(user);
      if (url.pathname === "/api/v1/logs/daily-totals/range") return json({ from_date: "2026-08-01", to_date: "2026-08-20", timezone: "UTC", items: [] });
      if (url.pathname === "/api/v1/body-metrics/trends" || url.pathname === "/api/v1/workouts/trends") return json({ from_date: "2026-08-01", to_date: "2026-08-20", items: [] });
      throw new Error(`Unexpected request: ${url.pathname}`);
    });
    vi.stubGlobal("fetch", fetchMock);
    render(<TrendsPage />);

    expect((await screen.findAllByRole("heading", { name: "No data in this range" }))).toHaveLength(3);
    expect(screen.getByText(/bodyweight, band, and RPE-only sets do not produce volume/i)).toBeVisible();
    expect(screen.queryByText(/you should|diagnos|recommend|failed/i)).not.toBeInTheDocument();
  });

  it("uses local nutrition dates and UTC body and strength dates at a timezone boundary", async () => {
    vi.useFakeTimers({ toFake: ["Date"] });
    vi.setSystemTime(new Date("2026-08-21T00:30:00Z"));
    vi.spyOn(Intl.DateTimeFormat.prototype, "resolvedOptions").mockReturnValue({
      locale: "en-US",
      calendar: "gregory",
      numberingSystem: "latn",
      timeZone: "America/New_York",
    });
    const fetchMock = trendsFetch();
    vi.stubGlobal("fetch", fetchMock);
    render(<TrendsPage />);

    await screen.findByRole("heading", { name: "Trends" });
    await waitFor(() => expect(fetchMock).toHaveBeenCalledTimes(4));
    const urls = fetchMock.mock.calls.map(([input]) => new URL(String(input), "http://opennosh.test"));
    expect(urls.find((url) => url.pathname === "/api/v1/logs/daily-totals/range")?.searchParams.get("to")).toBe("2026-08-20");
    expect(urls.find((url) => url.pathname === "/api/v1/body-metrics/trends")?.searchParams.get("to")).toBe("2026-08-21");
    expect(urls.find((url) => url.pathname === "/api/v1/workouts/trends")?.searchParams.get("to")).toBe("2026-08-21");
  });
});
