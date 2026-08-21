import { describe, expect, it } from "vitest";

import type { BodyMetric, DailyTotals, WorkoutTrendPoint } from "@/lib/types";
import {
  bodyMetricOptions,
  bodyMetricSeries,
  calendarDate,
  nutritionSeries,
  rangeStart,
  strengthOptions,
  strengthSeries,
} from "@/components/trends/transform";

describe("trend transformations", () => {
  it("keeps zero-entry nutrition days while mapping the selected nutrient", () => {
    const items: DailyTotals[] = [
      { day: "2026-08-19", timezone: "UTC", entry_count: 0, grams: "0.00", nutrients: {} },
      { day: "2026-08-20", timezone: "UTC", entry_count: 2, grams: "300.00", nutrients: { protein_g: "42.25" } },
    ];

    expect(nutritionSeries(items, "protein_g")).toEqual([
      { date: "2026-08-19", value: 0, count: 0 },
      { date: "2026-08-20", value: 42.25, count: 2 },
    ]);
  });

  it("derives calendar ranges in the API's requested timezone", () => {
    const instant = new Date("2026-08-21T00:30:00Z");
    expect(calendarDate(instant, "America/New_York")).toBe("2026-08-20");
    expect(calendarDate(instant, "UTC")).toBe("2026-08-21");
    expect(rangeStart("2026-08-21", 7)).toBe("2026-08-15");
  });

  it("separates body measurements by metric type and unit", () => {
    const items: BodyMetric[] = [
      { id: "kg", recorded_at: "2026-08-19T08:00:00Z", metric_type: "body_weight", value: "80", unit: "kg" },
      { id: "lb", recorded_at: "2026-08-20T08:00:00Z", metric_type: "body_weight", value: "176", unit: "lb" },
    ];

    expect(bodyMetricOptions(items).map((option) => option.key)).toEqual([
      "body_weight:kg",
      "body_weight:lb",
    ]);
    expect(bodyMetricSeries(items, "body_weight:kg")).toEqual([
      { date: "2026-08-19T08:00:00Z", value: 80, key: "kg" },
    ]);
  });

  it("aggregates same-day strength volume only for one exercise and load unit", () => {
    const items: WorkoutTrendPoint[] = [
      { day: "2026-08-20", exercise_id: "squat", exercise_name: "Back squat", load_unit: "kg", volume: "750" },
      { day: "2026-08-20", exercise_id: "squat", exercise_name: "Back squat", load_unit: "lb", volume: "1650" },
    ];

    expect(strengthOptions(items).map((option) => option.key)).toEqual(["squat:kg", "squat:lb"]);
    expect(strengthSeries(items, "squat:kg")).toEqual([{ date: "2026-08-20", value: 750 }]);
    expect(strengthSeries(items, "squat:lb")).toEqual([{ date: "2026-08-20", value: 1650 }]);
  });
});
