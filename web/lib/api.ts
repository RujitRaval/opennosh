import type {
  AuthenticatedUser as TransportUser,
  BodyMetricListResponse as TransportMetricList,
  BodyMetricTrendResponse as TransportMetricTrend,
  ContributionCapability as TransportContributionCapability,
  ContributionDraftCreate,
  ContributionDraftPatch,
  ContributionSubmit,
  CustomFoodResponse as TransportCustomFood,
  DailyTotalsRangeResponse as TransportTotalsRange,
  DailyTotalsResponse as TransportTotals,
  FoodCapabilities as TransportCapabilities,
  FoodDetail as TransportFoodDetail,
  FoodSearchResponse as TransportFoodSearch,
  LogEntryListResponse as TransportLogList,
  LogEntryResponse as TransportLogEntry,
  OpenFoodFactsFood as TransportBarcodeFood,
  SessionResponse as TransportSession,
  TargetResponse as TransportTarget,
  WorkoutListResponse as TransportWorkoutList,
  WorkoutTrendResponse as TransportWorkoutTrend,
} from "./generated/client/types.gen";
import { authenticatedUser, sessionResponse } from "./api/adapters/auth";
import { contributionCapability } from "./api/adapters/contributions";
import {
  barcodeFood,
  customFood,
  foodCapabilities,
  foodDetail,
  foodSearch,
} from "./api/adapters/foods";
import { dailyTotals, dailyTotalsRange, logEntries, logEntry } from "./api/adapters/logs";
import { bodyMetricList, bodyMetricTrend } from "./api/adapters/metrics";
import { target } from "./api/adapters/targets";
import { workoutList, workoutTrend } from "./api/adapters/workouts";
import { ApiProblem } from "./api/domain/problem";
import { request } from "./api/transport";
import type { FoodSource } from "./types";

export { ApiProblem, ApiProblem as ApiError };

export const api = {
  session: () => request<TransportUser>("/api/v1/auth/session").then(authenticatedUser),
  login: (email: string, password: string) =>
    request<TransportSession>("/api/v1/auth/login", {
      method: "POST",
      body: JSON.stringify({ email, password }),
    }).then(sessionResponse),
  register: (email: string, password: string) =>
    request<TransportSession>("/api/v1/auth/register", {
      method: "POST",
      body: JSON.stringify({ email, password }),
    }).then(sessionResponse),
  logout: () => request<void>("/api/v1/auth/logout", { method: "POST" }),
  createContributionDraft: (input: ContributionDraftCreate = {}) =>
    request<TransportContributionCapability>("/api/v1/contribution-drafts", {
      method: "POST",
      body: JSON.stringify(input),
    }).then(contributionCapability),
  contributionDraft: (draftId: string, requestedStage?: string) =>
    request<TransportContributionCapability>(
      `/api/v1/contribution-drafts/${encodeURIComponent(draftId)}${
        requestedStage
          ? `?${new URLSearchParams({ requested_stage: requestedStage })}`
          : ""
      }`,
    ).then(contributionCapability),
  patchContributionDraft: (draftId: string, input: ContributionDraftPatch) =>
    request<TransportContributionCapability>(
      `/api/v1/contribution-drafts/${encodeURIComponent(draftId)}`,
      { method: "PATCH", body: JSON.stringify(input) },
    ).then(contributionCapability),
  submitContributionDraft: (draftId: string, input: ContributionSubmit) =>
    request<TransportContributionCapability>(
      `/api/v1/contribution-drafts/${encodeURIComponent(draftId)}/submit`,
      { method: "POST", body: JSON.stringify(input) },
    ).then(contributionCapability),
  logs: (day: string, timezone: string) =>
    request<TransportLogList>(
      `/api/v1/logs?${new URLSearchParams({ day, timezone, limit: "100" })}`,
    ).then(logEntries),
  totals: (day: string, timezone: string) =>
    request<TransportTotals>(
      `/api/v1/logs/daily-totals?${new URLSearchParams({ day, timezone })}`,
    ).then(dailyTotals),
  totalsRange: (from: string, to: string, timezone: string) =>
    request<TransportTotalsRange>(
      `/api/v1/logs/daily-totals/range?${new URLSearchParams({ from, to, timezone })}`,
    ).then(dailyTotalsRange),
  bodyMetrics: (from: string, to: string, offset = 0) =>
    request<TransportMetricList>(
      `/api/v1/body-metrics?${new URLSearchParams({
        from,
        to,
        limit: "100",
        offset: String(offset),
      })}`,
    ).then(bodyMetricList),
  bodyMetricTrends: (from: string, to: string) =>
    request<TransportMetricTrend>(
      `/api/v1/body-metrics/trends?${new URLSearchParams({ from, to })}`,
    ).then(bodyMetricTrend),
  workouts: (from: string, to: string, offset = 0) =>
    request<TransportWorkoutList>(
      `/api/v1/workouts?${new URLSearchParams({
        from,
        to,
        limit: "100",
        offset: String(offset),
      })}`,
    ).then(workoutList),
  workoutTrends: (from: string, to: string) =>
    request<TransportWorkoutTrend>(
      `/api/v1/workouts/trends?${new URLSearchParams({ from, to })}`,
    ).then(workoutTrend),
  target: (day: string, dayType: "training" | "rest") =>
    request<TransportTarget>(
      `/api/v1/targets/resolve?${new URLSearchParams({ day, day_type: dayType })}`,
    ).then(target),
  foodCapabilities: () =>
    request<TransportCapabilities>("/api/v1/foods/capabilities").then(foodCapabilities),
  searchFoods: (
    query: string,
    locale: string,
    source?: "usda" | "community",
    cursor?: string,
  ) =>
    request<TransportFoodSearch>(
      `/api/v1/foods/search?${new URLSearchParams({
        q: query,
        locale,
        limit: "12",
        ...(source ? { source } : {}),
        ...(cursor ? { cursor } : {}),
      })}`,
    ).then(foodSearch),
  foodDetail: (source: "usda" | "community", sourceId: string) =>
    request<TransportFoodDetail>(
      `/api/v1/foods/${source}/${encodeURIComponent(sourceId)}`,
    ).then(foodDetail),
  lookupBarcode: (barcode: string) =>
    request<TransportBarcodeFood>(
      `/api/v1/foods/barcode/${encodeURIComponent(barcode)}`,
    ).then(barcodeFood),
  createCustomFood: (input: {
    name: string;
    energyKcal: string;
    proteinG: string;
    carbohydrateG: string;
    fatG: string;
    portion?: { name: string; grams: string };
  }) =>
    request<TransportCustomFood>("/api/v1/foods/custom", {
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
    }).then(customFood),
  addLog: (input: {
    loggedAt: string;
    mealSlot: string;
    source: FoodSource;
    sourceId: string;
    amount: string;
    unit: "g" | "portion";
    portionName: string | null;
  }) =>
    request<TransportLogEntry>("/api/v1/logs", {
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
    }).then(logEntry),
  deleteLog: (entryId: string) =>
    request<void>(`/api/v1/logs/${entryId}`, { method: "DELETE" }),
};
