"use client";

import { useCallback, useEffect, useMemo, useRef, useState } from "react";

import { LoginPanel } from "@/components/log/login-panel";
import { TrackerHeader } from "@/components/tracker/tracker-header";
import { OnboardingPanel } from "@/components/tracker/onboarding-panel";
import { RecoveryCodeGate } from "@/components/tracker/recovery-code-gate";
import { RecoverySetupGate } from "@/components/tracker/recovery-setup-gate";
import { TrackerWordmark } from "@/components/tracker/tracker-wordmark";
import { ApiError, api } from "@/lib/api";
import type { AuthenticatedUser, BodyMetric, WorkoutTrendPoint } from "@/lib/types";

import { TrendPanel } from "./trend-panel";
import {
  bodyMetricOptions,
  bodyMetricSeries,
  calendarDate,
  nutritionMetrics,
  nutritionSeries,
  rangeStart,
  strengthOptions,
  strengthSeries,
  type NutritionMetric,
} from "./transform";

type RangeDays = 7 | 30 | 90;

function Trends({
  user,
  onExpired,
  onLogout,
}: {
  user: AuthenticatedUser;
  onExpired: () => void;
  onLogout: () => void;
}) {
  const [rangeDays, setRangeDays] = useState<RangeDays>(30);
  const [nutritionMetric, setNutritionMetric] = useState<NutritionMetric>("energy_kcal");
  const [bodyKey, setBodyKey] = useState("");
  const [strengthKey, setStrengthKey] = useState("");
  const [nutrition, setNutrition] = useState<Awaited<ReturnType<typeof api.totalsRange>> | null>(null);
  const [bodyMetrics, setBodyMetrics] = useState<BodyMetric[]>([]);
  const [workouts, setWorkouts] = useState<WorkoutTrendPoint[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const loadSequence = useRef(0);
  const timezone = useMemo(() => Intl.DateTimeFormat().resolvedOptions().timeZone || "UTC", []);

  const load = useCallback(async () => {
    const sequence = ++loadSequence.current;
    setLoading(true);
    setError(null);
    try {
      const now = new Date();
      const nutritionTo = calendarDate(now, timezone);
      const nutritionFrom = rangeStart(nutritionTo, rangeDays);
      const utcTo = calendarDate(now, "UTC");
      const utcFrom = rangeStart(utcTo, rangeDays);
      const [nutritionResponse, bodyResponse, workoutResponse] = await Promise.all([
        api.totalsRange(nutritionFrom, nutritionTo, timezone),
        api.bodyMetricTrends(utcFrom, utcTo),
        api.workoutTrends(utcFrom, utcTo),
      ]);
      if (sequence !== loadSequence.current) return;
      setNutrition(nutritionResponse);
      setBodyMetrics(bodyResponse.items);
      setWorkouts(workoutResponse.items);
    } catch (caught) {
      if (sequence !== loadSequence.current) return;
      if (caught instanceof ApiError && caught.status === 401) {
        onExpired();
        return;
      }
      setError(caught instanceof Error ? caught.message : "Trend history could not be loaded.");
    } finally {
      if (sequence === loadSequence.current) setLoading(false);
    }
  }, [onExpired, rangeDays, timezone]);

  useEffect(() => {
    const timeout = window.setTimeout(() => void load(), 0);
    return () => window.clearTimeout(timeout);
  }, [load]);

  const bodyOptions = useMemo(() => bodyMetricOptions(bodyMetrics), [bodyMetrics]);
  const liftOptions = useMemo(() => strengthOptions(workouts), [workouts]);

  const effectiveBodyKey = bodyOptions.some((option) => option.key === bodyKey)
    ? bodyKey
    : (bodyOptions[0]?.key ?? "");
  const effectiveStrengthKey = liftOptions.some((option) => option.key === strengthKey)
    ? strengthKey
    : (liftOptions[0]?.key ?? "");
  const nutritionDefinition = nutritionMetrics[nutritionMetric];
  const selectedBody = bodyOptions.find((option) => option.key === effectiveBodyKey);
  const selectedStrength = liftOptions.find((option) => option.key === effectiveStrengthKey);

  async function logout() {
    try {
      await api.logout();
      onLogout();
    } catch (caught) {
      if (caught instanceof ApiError && caught.status === 401) return onExpired();
      setError(caught instanceof Error ? caught.message : "Sign out could not be completed.");
    }
  }

  return (
    <>
      <a className="skip-link" href="#main-content">Skip to trends</a>
      <TrackerHeader active="trends" email={user.email} onLogout={() => void logout()} />

      <main id="main-content" className="log-shell trends-shell">
        <section className="day-header" aria-labelledby="trends-heading">
          <div>
            <p className="eyebrow">Your recorded history</p>
            <h1 id="trends-heading">Trends</h1>
            <p className="trends-lede">Review nutrition, body measurements, and strength records without scores or coaching.</p>
          </div>
          <fieldset className="range-control">
            <legend>Date range</legend>
            {([7, 30, 90] as const).map((days) => (
              <label key={days}>
                <input
                  type="radio"
                  name="trend-range"
                  value={days}
                  checked={rangeDays === days}
                  onChange={() => setRangeDays(days)}
                />
                <span>{days} days</span>
              </label>
            ))}
          </fieldset>
        </section>

        <p className="range-note">
          Nutrition days use {timezone}. Body and strength records use the API’s UTC date boundaries.
        </p>

        {error ? (
          <section className="error-panel" role="alert">
            <div><h2>We couldn’t load trends</h2><p>{error}</p></div>
            <button className="button button-secondary" type="button" onClick={() => void load()}>Retry</button>
          </section>
        ) : null}

        {loading ? (
          <div className="loading-panel" role="status"><p>Loading your recorded history…</p></div>
        ) : !error && nutrition ? (
          <div className="trends-grid">
            <TrendPanel
              title="Nutrition"
              description={`${nutritionDefinition.label} recorded per local calendar day.`}
              points={nutritionSeries(nutrition.items, nutritionMetric)}
              unit={nutritionDefinition.unit}
              emptyMessage="Food entries in this date range will appear here."
            >
              <label className="trend-filter">
                <span>Nutrition measure</span>
                <select value={nutritionMetric} onChange={(event) => setNutritionMetric(event.target.value as NutritionMetric)}>
                  {Object.entries(nutritionMetrics).map(([key, value]) => <option key={key} value={key}>{value.label}</option>)}
                </select>
              </label>
            </TrendPanel>

            <TrendPanel
              title="Body metrics"
              description={selectedBody ? `${selectedBody.label}; different units remain separate.` : "Measurements remain separated by type and unit."}
              points={effectiveBodyKey ? bodyMetricSeries(bodyMetrics, effectiveBodyKey) : []}
              unit={selectedBody?.unit ?? ""}
              emptyMessage="Body measurements in this date range will appear here, separated by type and unit."
            >
              {bodyOptions.length ? (
                <label className="trend-filter">
                  <span>Body measure</span>
                  <select value={effectiveBodyKey} onChange={(event) => setBodyKey(event.target.value)}>
                    {bodyOptions.map((option) => <option key={option.key} value={option.key}>{option.label}</option>)}
                  </select>
                </label>
              ) : null}
            </TrendPanel>

            <TrendPanel
              title="Strength volume"
              description={selectedStrength ? `${selectedStrength.label}; incompatible load units are never combined.` : "Volume is shown only for numeric loads with a single compatible unit."}
              points={effectiveStrengthKey ? strengthSeries(workouts, effectiveStrengthKey) : []}
              unit={selectedStrength?.unit ?? ""}
              emptyMessage="Numeric workout volume in this date range will appear here by exercise and load unit. Bodyweight, band, and RPE-only sets do not produce volume."
            >
              {liftOptions.length ? (
                <label className="trend-filter">
                  <span>Exercise and load unit</span>
                  <select value={effectiveStrengthKey} onChange={(event) => setStrengthKey(event.target.value)}>
                    {liftOptions.map((option) => <option key={option.key} value={option.key}>{option.label}</option>)}
                  </select>
                </label>
              ) : null}
            </TrendPanel>
          </div>
        ) : null}
      </main>
    </>
  );
}

export function TrendsApp() {
  const [user, setUser] = useState<AuthenticatedUser | null>(null);
  const [recoveryCode, setRecoveryCode] = useState<string>();
  const [checking, setChecking] = useState(true);
  const [authMessage, setAuthMessage] = useState<string>();

  useEffect(() => {
    api.sessionState()
      .then((state) => setUser(state.user))
      .catch((caught) => {
        setAuthMessage(caught instanceof Error ? caught.message : "Your session could not be checked.");
      })
      .finally(() => setChecking(false));
  }, []);

  if (checking) return <main id="main-content" className="boot-screen" role="status"><TrackerWordmark surface="rice-paper" priority /><p>Opening trends…</p></main>;
  if (!user) {
    return <LoginPanel message={authMessage} onAuthenticate={async (mode, email, password) => {
      if (mode === "login") {
        const response = await api.login(email, password);
        setAuthMessage(undefined);
        setRecoveryCode(undefined);
        setUser(response.user);
        return;
      }
      const response = await api.register(email, password);
      setAuthMessage(undefined);
      setRecoveryCode(response.recovery_code);
      setUser(response.user);
    }} onRecover={async (email, code, password) => {
      const response = await api.recover(email, code, password);
      setAuthMessage("Password reset. Save the new recovery code before continuing.");
      setRecoveryCode(response.recovery_code);
      setUser(response.user);
    }} />;
  }
  if (!user.recovery_configured) {
    return <RecoverySetupGate onGenerated={(code) => {
      setUser({ ...user, recovery_configured: true });
      setRecoveryCode(code);
    }} onLogout={async () => {
      await api.logout();
      setUser(null);
      setAuthMessage("You’re signed out.");
    }} />;
  }
  if (recoveryCode && user.onboarding_completed) {
    return <RecoveryCodeGate recoveryCode={recoveryCode} onSaved={() => setRecoveryCode(undefined)} onLogout={async () => {
      await api.logout();
      setRecoveryCode(undefined);
      setUser(null);
      setAuthMessage("You’re signed out.");
    }} />;
  }
  if (!user.onboarding_completed) {
    return <OnboardingPanel user={user} recoveryCode={recoveryCode} onComplete={(updated) => {
      setRecoveryCode(undefined);
      setUser(updated);
    }} onLogout={async () => {
      await api.logout();
      setRecoveryCode(undefined);
      setUser(null);
      setAuthMessage("You’re signed out.");
    }} />;
  }
  return <Trends user={user} onExpired={() => { setUser(null); setAuthMessage("Your session ended. Sign in again to continue."); }} onLogout={() => { setUser(null); setAuthMessage("You’re signed out."); }} />;
}
