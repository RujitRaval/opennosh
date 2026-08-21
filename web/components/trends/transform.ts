import type { BodyMetric, DailyTotals, WorkoutTrendPoint } from "@/lib/types";

export type TrendPoint = { date: string; value: number; count?: number; key?: string };
export type TrendOption = { key: string; label: string; unit: string };

export const nutritionMetrics = {
  energy_kcal: { label: "Energy", unit: "kcal" },
  protein_g: { label: "Protein", unit: "g" },
  carbohydrate_g: { label: "Carbohydrate", unit: "g" },
  fat_g: { label: "Fat", unit: "g" },
} as const;

export type NutritionMetric = keyof typeof nutritionMetrics;

export function calendarDate(date: Date, timezone: string): string {
  const parts = new Intl.DateTimeFormat("en-US", {
    year: "numeric",
    month: "2-digit",
    day: "2-digit",
    timeZone: timezone,
  }).formatToParts(date);
  const values = Object.fromEntries(parts.map((part) => [part.type, part.value]));
  return `${values.year}-${values.month}-${values.day}`;
}

export function rangeStart(to: string, days: number): string {
  const value = new Date(`${to}T00:00:00Z`);
  value.setUTCDate(value.getUTCDate() - days + 1);
  return value.toISOString().slice(0, 10);
}

const bodyMetricLabels: Record<string, string> = {
  body_weight: "Body weight",
  body_fat_percentage: "Body fat percentage",
  height: "Height",
  waist_circumference: "Waist circumference",
  hip_circumference: "Hip circumference",
  chest_circumference: "Chest circumference",
  neck_circumference: "Neck circumference",
  upper_arm_circumference: "Upper arm circumference",
  thigh_circumference: "Thigh circumference",
};

const unitLabels: Record<string, string> = {
  kg: "kg",
  lb: "lb",
  percent: "%",
  cm: "cm",
  in: "in",
  machine_units: "machine units",
};

export function nutritionSeries(items: DailyTotals[], metric: NutritionMetric): TrendPoint[] {
  return items.map((item) => ({
    date: item.day,
    value: Number(item.nutrients[metric] ?? 0),
    count: item.entry_count,
  }));
}

export function bodyMetricOptions(items: BodyMetric[]): TrendOption[] {
  const options = new Map<string, TrendOption>();
  for (const item of items) {
    const key = `${item.metric_type}:${item.unit}`;
    options.set(key, {
      key,
      label: `${bodyMetricLabels[item.metric_type] ?? item.metric_type.replaceAll("_", " ")} (${unitLabels[item.unit] ?? item.unit})`,
      unit: unitLabels[item.unit] ?? item.unit,
    });
  }
  return [...options.values()].sort((a, b) => a.label.localeCompare(b.label));
}

export function bodyMetricSeries(items: BodyMetric[], key: string): TrendPoint[] {
  const [metricType, unit] = key.split(":");
  return items
    .filter((item) => item.metric_type === metricType && item.unit === unit)
    .map((item) => ({ date: item.recorded_at, value: Number(item.value), key: item.id }))
    .sort((a, b) => a.date.localeCompare(b.date));
}

export function strengthOptions(items: WorkoutTrendPoint[]): TrendOption[] {
  const options = new Map<string, TrendOption>();
  for (const item of items) {
    const key = `${item.exercise_id}:${item.load_unit}`;
    options.set(key, {
      key,
      label: `${item.exercise_name} (${unitLabels[item.load_unit] ?? item.load_unit})`,
      unit: unitLabels[item.load_unit] ?? item.load_unit,
    });
  }
  return [...options.values()].sort((a, b) => a.label.localeCompare(b.label));
}

export function strengthSeries(items: WorkoutTrendPoint[], key: string): TrendPoint[] {
  const splitAt = key.lastIndexOf(":");
  const exerciseId = key.slice(0, splitAt);
  const loadUnit = key.slice(splitAt + 1);
  return items
    .filter((item) => item.exercise_id === exerciseId && item.load_unit === loadUnit)
    .map((item) => ({ date: item.day, value: Number(item.volume) }))
    .sort((a, b) => a.date.localeCompare(b.date));
}
