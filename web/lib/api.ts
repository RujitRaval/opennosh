import type {
  AuthenticatedUser,
  BodyMetricListResponse,
  BodyMetricTrendResponse,
  DailyTotals,
  DailyTotalsRange,
  BarcodeFood,
  CustomFood,
  FoodCapabilities,
  FoodDetail,
  FoodSource,
  FoodSearchResponse,
  LogEntry,
  LogEntryListResponse,
  SessionResponse,
  Target,
  WorkoutListResponse,
  WorkoutTrendResponse,
} from "./types";
import { reviewedApiErrorMessage } from "./health-safety";

export class ApiError extends Error {
  constructor(
    public readonly status: number,
    message: string,
  ) {
    super(message);
    this.name = "ApiError";
  }
}

function csrfTokenFromCookie(): string | null {
  if (typeof document === "undefined") return null;

  for (const name of ["__Host-opennosh-csrf", "opennosh_csrf"]) {
    const prefix = `${name}=`;
    const match = document.cookie.split("; ").find((cookie) => cookie.startsWith(prefix));
    if (match) return match.slice(prefix.length);
  }
  return null;
}

async function errorMessage(response: Response): Promise<string> {
  try {
    const body = (await response.json()) as { detail?: unknown };
    if (typeof body.detail === "string") return reviewedApiErrorMessage(body.detail);
  } catch {
    // Use the stable fallback below for non-JSON upstream failures.
  }
  return response.status >= 500
    ? "opennosh could not reach the server. Please try again."
    : "That request could not be completed. Please try again.";
}

async function request<T>(path: `/api/v1/${string}`, init: RequestInit = {}): Promise<T> {
  const headers = new Headers(init.headers);
  headers.set("Accept", "application/json");
  if (init.body) headers.set("Content-Type", "application/json");

  if (init.method && !["GET", "HEAD"].includes(init.method.toUpperCase())) {
    const csrfToken = csrfTokenFromCookie();
    if (csrfToken) headers.set("X-CSRF-Token", csrfToken);
  }

  let response: Response;
  try {
    response = await fetch(path, {
      ...init,
      headers,
      credentials: "same-origin",
      cache: "no-store",
    });
  } catch {
    throw new ApiError(0, "opennosh could not reach the server. Check your connection and retry.");
  }

  if (!response.ok) throw new ApiError(response.status, await errorMessage(response));
  if (response.status === 204) return undefined as T;
  return (await response.json()) as T;
}

export const api = {
  session: () => request<AuthenticatedUser>("/api/v1/auth/session"),
  login: (email: string, password: string) =>
    request<SessionResponse>("/api/v1/auth/login", {
      method: "POST",
      body: JSON.stringify({ email, password }),
    }),
  register: (email: string, password: string) =>
    request<SessionResponse>("/api/v1/auth/register", {
      method: "POST",
      body: JSON.stringify({ email, password }),
    }),
  logout: () => request<void>("/api/v1/auth/logout", { method: "POST" }),
  logs: (day: string, timezone: string) =>
    request<LogEntryListResponse>(
      `/api/v1/logs?${new URLSearchParams({ day, timezone, limit: "100" })}`,
    ),
  totals: (day: string, timezone: string) =>
    request<DailyTotals>(
      `/api/v1/logs/daily-totals?${new URLSearchParams({ day, timezone })}`,
    ),
  totalsRange: (from: string, to: string, timezone: string) =>
    request<DailyTotalsRange>(
      `/api/v1/logs/daily-totals/range?${new URLSearchParams({ from, to, timezone })}`,
    ),
  bodyMetrics: (from: string, to: string, offset = 0) =>
    request<BodyMetricListResponse>(
      `/api/v1/body-metrics?${new URLSearchParams({
        from,
        to,
        limit: "100",
        offset: String(offset),
      })}`,
    ),
  bodyMetricTrends: (from: string, to: string) =>
    request<BodyMetricTrendResponse>(
      `/api/v1/body-metrics/trends?${new URLSearchParams({ from, to })}`,
    ),
  workouts: (from: string, to: string, offset = 0) =>
    request<WorkoutListResponse>(
      `/api/v1/workouts?${new URLSearchParams({
        from,
        to,
        limit: "100",
        offset: String(offset),
      })}`,
    ),
  workoutTrends: (from: string, to: string) =>
    request<WorkoutTrendResponse>(
      `/api/v1/workouts/trends?${new URLSearchParams({ from, to })}`,
    ),
  target: (day: string, dayType: "training" | "rest") =>
    request<Target>(
      `/api/v1/targets/resolve?${new URLSearchParams({ day, day_type: dayType })}`,
    ),
  foodCapabilities: () => request<FoodCapabilities>("/api/v1/foods/capabilities"),
  searchFoods: (query: string, locale: string, source?: "usda" | "community") =>
    request<FoodSearchResponse>(
      `/api/v1/foods/search?${new URLSearchParams({
        q: query,
        locale,
        limit: "12",
        ...(source ? { source } : {}),
      })}`,
    ),
  foodDetail: (source: "usda" | "community", sourceId: string) =>
    request<FoodDetail>(`/api/v1/foods/${source}/${encodeURIComponent(sourceId)}`),
  lookupBarcode: (barcode: string) =>
    request<BarcodeFood>(`/api/v1/foods/barcode/${encodeURIComponent(barcode)}`),
  createCustomFood: (input: {
    name: string;
    energyKcal: string;
    proteinG: string;
    carbohydrateG: string;
    fatG: string;
    portion?: { name: string; grams: string };
  }) =>
    request<CustomFood>("/api/v1/foods/custom", {
      method: "POST",
      body: JSON.stringify({
        name: input.name,
        nutrients: {
          basis: "per_100g",
          nutrients: {
            energy_kcal: input.energyKcal,
            protein_g: input.proteinG,
            carbohydrate_g: input.carbohydrateG,
            fat_g: input.fatG,
          },
        },
        portions: input.portion ? [input.portion] : [],
      }),
    }),
  addLog: (input: {
    loggedAt: string;
    mealSlot: string;
    source: FoodSource;
    sourceId: string;
    amount: string;
    unit: "g" | "portion";
    portionName: string | null;
  }) =>
    request<LogEntry>("/api/v1/logs", {
      method: "POST",
      body: JSON.stringify({
        logged_at: input.loggedAt,
        meal_slot: input.mealSlot,
        food: { source: input.source, source_id: input.sourceId },
        quantity: {
          amount: input.amount,
          unit: input.unit,
          portion_name: input.portionName,
        },
      }),
    }),
  deleteLog: (entryId: string) =>
    request<void>(`/api/v1/logs/${entryId}`, { method: "DELETE" }),
};
