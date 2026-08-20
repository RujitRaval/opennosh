import type {
  AuthenticatedUser,
  DailyTotals,
  FoodSearchResponse,
  LogEntry,
  LogEntryListResponse,
  SessionResponse,
  Target,
} from "./types";

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
    if (typeof body.detail === "string") return body.detail;
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
  target: (day: string, dayType: "training" | "rest") =>
    request<Target>(
      `/api/v1/targets/resolve?${new URLSearchParams({ day, day_type: dayType })}`,
    ),
  searchFoods: (query: string, locale: string) =>
    request<FoodSearchResponse>(
      `/api/v1/foods/search?${new URLSearchParams({ q: query, locale, limit: "12" })}`,
    ),
  addLog: (input: {
    loggedAt: string;
    mealSlot: string;
    source: string;
    sourceId: string;
    grams: string;
  }) =>
    request<LogEntry>("/api/v1/logs", {
      method: "POST",
      body: JSON.stringify({
        logged_at: input.loggedAt,
        meal_slot: input.mealSlot,
        food: { source: input.source, source_id: input.sourceId },
        quantity: { amount: input.grams, unit: "g", portion_name: null },
      }),
    }),
  deleteLog: (entryId: string) =>
    request<void>(`/api/v1/logs/${entryId}`, { method: "DELETE" }),
};
