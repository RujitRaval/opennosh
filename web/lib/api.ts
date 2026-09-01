import type {
  AuthenticatedUser as TransportUser,
  BodyMetricResponse as TransportMetric,
  BodyMetricListResponse as TransportMetricList,
  BodyMetricTrendResponse as TransportMetricTrend,
  ContributionCapability as TransportContributionCapability,
  ContributionDraftCreate,
  ContributionDraftPatch,
  ContributionEvidenceStatus,
  ContributionSubmit,
  EvidenceUploadAttachRequest,
  EvidenceUploadCompleteRequest,
  EvidenceUploadCreateRequest,
  EvidenceUploadCreateResponse as TransportEvidenceUploadCreateResponse,
  EvidenceUploadSessionResponse as TransportEvidenceUploadSessionResponse,
  CustomFoodResponse as TransportCustomFood,
  DailyTotalsRangeResponse as TransportTotalsRange,
  DailyTotalsResponse as TransportTotals,
  FoodCapabilities as TransportCapabilities,
  FoodDetail as TransportFoodDetail,
  ExerciseSearchResponse as TransportExerciseSearch,
  FoodSearchResponse as TransportFoodSearch,
  LogEntryListResponse as TransportLogList,
  LogEntryResponse as TransportLogEntry,
  OpenFoodFactsFood as TransportBarcodeFood,
  RecoveryCodeResponse as TransportRecoveryCode,
  RegistrationResponse as TransportRegistration,
  SessionResponse as TransportSession,
  SessionState as TransportSessionState,
  TargetResponse as TransportTarget,
  WorkoutListResponse as TransportWorkoutList,
  WorkoutResponse as TransportWorkout,
  WorkoutTrendResponse as TransportWorkoutTrend,
} from "./generated/client/types.gen";
import {
  authenticatedUser,
  registrationResponse,
  sessionResponse,
  sessionState,
} from "./api/adapters/auth";
import {
  contributionCapability,
  evidenceUploadCreation,
  evidenceUploadSession,
} from "./api/adapters/contributions";
import {
  barcodeFood,
  customFood,
  foodCapabilities,
  foodDetail,
  foodSearch,
  publicFoodDetailResponse,
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
  sessionState: async () => {
    try {
      return await request<TransportSessionState>("/api/v1/auth/session-state").then(sessionState);
    } catch (caught) {
      if (caught instanceof ApiProblem && caught.status === 401) {
        return { authenticated: false, user: null };
      }
      if (!(caught instanceof ApiProblem) || caught.kind !== "network") throw caught;
      try {
        const user = await request<TransportUser>("/api/v1/auth/session").then(authenticatedUser);
        return { authenticated: true, user };
      } catch (fallback) {
        if (fallback instanceof ApiProblem && fallback.status === 401) {
          return { authenticated: false, user: null };
        }
        throw fallback;
      }
    }
  },
  login: (email: string, password: string) =>
    request<TransportSession>("/api/v1/auth/login", {
      method: "POST",
      body: JSON.stringify({ email, password }),
    }).then(sessionResponse),
  register: (email: string, password: string) =>
    request<TransportRegistration>("/api/v1/auth/register", {
      method: "POST",
      body: JSON.stringify({ email, password }),
    }).then(registrationResponse),
  recover: (email: string, recoveryCode: string, newPassword: string) =>
    request<TransportRegistration>("/api/v1/auth/recover", {
      method: "POST",
      body: JSON.stringify({ email, recovery_code: recoveryCode, new_password: newPassword }),
    }).then(registrationResponse),
  updateAccountSettings: (input: {
    onboarding_completed?: boolean;
    preferred_units?: "metric" | "us";
  }) => request<TransportUser>("/api/v1/auth/account/settings", {
    method: "PATCH",
    body: JSON.stringify(input),
  }).then(authenticatedUser),
  changePassword: (currentPassword: string, newPassword: string) =>
    request<void>("/api/v1/auth/account/password", {
      method: "PUT",
      body: JSON.stringify({ current_password: currentPassword, new_password: newPassword }),
    }),
  rotateRecoveryCode: (password: string) =>
    request<TransportRecoveryCode>("/api/v1/auth/account/recovery-code", {
      method: "POST",
      body: JSON.stringify({ password }),
    }),
  deleteAccount: (password: string) =>
    request<void>("/api/v1/auth/account", {
      method: "DELETE",
      body: JSON.stringify({ password }),
    }),
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
  createEvidenceUpload: (
    draftId: string,
    input: EvidenceUploadCreateRequest,
    idempotencyKey: string,
  ) => request<TransportEvidenceUploadCreateResponse>(
    `/api/v1/contribution-drafts/${encodeURIComponent(draftId)}/evidence-uploads`,
    {
      method: "POST",
      headers: { "Idempotency-Key": idempotencyKey },
      body: JSON.stringify(input),
    },
  ).then(evidenceUploadCreation),
  completeEvidenceUpload: (
    draftId: string,
    uploadId: string,
    input: EvidenceUploadCompleteRequest,
  ) => request<TransportEvidenceUploadSessionResponse>(
    `/api/v1/contribution-drafts/${encodeURIComponent(draftId)}/evidence-uploads/${encodeURIComponent(uploadId)}/complete`,
    { method: "POST", body: JSON.stringify(input) },
  ).then(evidenceUploadSession),
  evidenceUpload: (draftId: string, uploadId: string, signal?: AbortSignal) =>
    request<TransportEvidenceUploadSessionResponse>(
      `/api/v1/contribution-drafts/${encodeURIComponent(draftId)}/evidence-uploads/${encodeURIComponent(uploadId)}`,
      { signal },
    ).then(evidenceUploadSession),
  attachEvidenceUpload: (
    draftId: string,
    uploadId: string,
    input: EvidenceUploadAttachRequest,
  ) => request<TransportEvidenceUploadSessionResponse>(
    `/api/v1/contribution-drafts/${encodeURIComponent(draftId)}/evidence-uploads/${encodeURIComponent(uploadId)}/attach`,
    { method: "POST", body: JSON.stringify(input) },
  ).then(evidenceUploadSession),
  contributionEvidence: (draftId: string) => request<ContributionEvidenceStatus>(
    `/api/v1/contribution-drafts/${encodeURIComponent(draftId)}/evidence`,
  ),
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
  optionalTarget: async (day: string, dayType: "training" | "rest") => {
    const query = new URLSearchParams({ day, day_type: dayType });
    try {
      return await request<TransportTarget | null>(
        `/api/v1/targets/resolve-optional?${query}`,
      ).then((value) => value ? target(value) : null);
    } catch (caught) {
      if (!(caught instanceof ApiProblem) || caught.kind !== "network") throw caught;
      try {
        return await request<TransportTarget>(`/api/v1/targets/resolve?${query}`).then(target);
      } catch (fallback) {
        if (fallback instanceof ApiProblem && fallback.status === 404) return null;
        throw fallback;
      }
    }
  },
  replaceTargets: (input: {
    items: Array<{
      day_type: "training" | "rest";
      kcal: string;
      protein_g: string;
      carb_g: string;
      fat_g: string;
      active_from: string;
      confirm_below_floor?: boolean;
    }>;
  }) => request<unknown>("/api/v1/targets", {
    method: "PUT",
    body: JSON.stringify(input),
  }),
  createBodyMetric: (input: {
    recorded_at: string;
    metric_type: string;
    value: string;
    unit: string;
  }) => request<TransportMetric>("/api/v1/body-metrics", {
    method: "POST",
    body: JSON.stringify(input),
  }),
  searchExercises: (query: string) => request<TransportExerciseSearch>(
    `/api/v1/exercises/search?${new URLSearchParams({ q: query, limit: "8" })}`,
  ),
  createWorkout: (input: {
    performed_at: string;
    notes?: string;
    sets: Array<{
      exercise_id: string;
      reps: number;
      load_value?: string;
      load_unit: string;
    }>;
  }) => request<TransportWorkout>("/api/v1/workouts", {
    method: "POST",
    body: JSON.stringify(input),
  }),
  foodCapabilities: () =>
    request<TransportCapabilities>("/api/v1/foods/capabilities").then(foodCapabilities),
  searchFoods: (
    query: string,
    locale?: string,
    source?: "usda" | "community",
    cursor?: string,
  ) =>
    request<TransportFoodSearch>(
      `/api/v1/foods/search?${new URLSearchParams({
        q: query,
        ...(locale ? { locale } : {}),
        limit: "12",
        ...(source ? { source } : {}),
        ...(cursor ? { cursor } : {}),
      })}`,
    ).then(foodSearch),
  foodDetail: (source: "usda" | "community", sourceId: string, signal?: AbortSignal) =>
    request<TransportFoodDetail>(
      `/api/v1/foods/${source}/${encodeURIComponent(sourceId)}`,
      { signal },
    ).then(foodDetail),
  publicFoodDetail: (
    source: "usda" | "community",
    sourceId: string,
    foodLocale: string,
    signal?: AbortSignal,
    version?: string,
  ) =>
    request<unknown>(
      `/api/v1/public/foods/${source}/${encodeURIComponent(sourceId)}${
        version ? `?${new URLSearchParams({ version })}` : ""
      }`,
      { signal },
    ).then((value) => publicFoodDetailResponse(value, foodLocale)),
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
