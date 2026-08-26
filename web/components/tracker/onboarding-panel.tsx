"use client";

import { useState, type FormEvent } from "react";

import { api } from "@/lib/api";
import type { AuthenticatedUser, PreferredUnits } from "@/lib/types";

type TargetValues = {
  kcal: string;
  protein: string;
  carbs: string;
  fat: string;
};

const initialTargets: Record<"training" | "rest", TargetValues> = {
  training: { kcal: "2200", protein: "160", carbs: "240", fat: "70" },
  rest: { kcal: "2000", protein: "150", carbs: "200", fat: "65" },
};

function today(): string {
  return new Date().toISOString().slice(0, 10);
}

export function OnboardingPanel({
  user,
  recoveryCode,
  onComplete,
  onLogout,
}: {
  user: AuthenticatedUser;
  recoveryCode?: string;
  onComplete: (user: AuthenticatedUser) => void;
  onLogout: () => void;
}) {
  const [units, setUnits] = useState<PreferredUnits>(user.preferred_units);
  const [targets, setTargets] = useState(initialTargets);
  const [savedRecovery, setSavedRecovery] = useState(!recoveryCode);
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState<string | null>(null);

  function updateTarget(dayType: "training" | "rest", field: keyof TargetValues, value: string) {
    setTargets((current) => ({
      ...current,
      [dayType]: { ...current[dayType], [field]: value },
    }));
  }

  async function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (!savedRecovery) return;
    setSaving(true);
    setError(null);
    try {
      await api.replaceTargets({
        items: (["training", "rest"] as const).map((dayType) => ({
          day_type: dayType,
          kcal: targets[dayType].kcal,
          protein_g: targets[dayType].protein,
          carb_g: targets[dayType].carbs,
          fat_g: targets[dayType].fat,
          active_from: today(),
        })),
      });
      const updated = await api.updateAccountSettings({
        preferred_units: units,
        onboarding_completed: true,
      });
      onComplete(updated);
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : "Setup could not be saved.");
      setSaving(false);
    }
  }

  return (
    <main id="main-content" className="setup-shell">
      <section className="setup-intro">
        <p className="eyebrow">Private tracker / First run</p>
        <h1>Make the tracker yours.</h1>
        <p>
          Choose familiar units and starting targets. You can change all of this later; the
          numbers are reference points, never judgments.
        </p>
      </section>

      <form className="setup-form" onSubmit={submit}>
        {recoveryCode ? (
          <section className="setup-section recovery-card" aria-labelledby="recovery-title">
            <p className="section-kicker">One-time account key</p>
            <h2 id="recovery-title">Save your recovery code</h2>
            <p>This is the only way to reset your password. opennosh cannot email or reveal it later.</p>
            <output className="recovery-code" aria-label="Recovery code">{recoveryCode}</output>
            <label className="confirmation-row">
              <input type="checkbox" checked={savedRecovery} onChange={(event) => setSavedRecovery(event.target.checked)} />
              <span>I saved this code somewhere private.</span>
            </label>
          </section>
        ) : null}

        <section className="setup-section" aria-labelledby="units-title">
          <p className="section-kicker">Step 1</p>
          <h2 id="units-title">Choose your units</h2>
          <div className="choice-grid">
            <label className={units === "metric" ? "choice-card is-selected" : "choice-card"}>
              <input type="radio" name="units" value="metric" checked={units === "metric"} onChange={() => setUnits("metric")} />
              <strong>Metric</strong><span>Kilograms and centimetres</span>
            </label>
            <label className={units === "us" ? "choice-card is-selected" : "choice-card"}>
              <input type="radio" name="units" value="us" checked={units === "us"} onChange={() => setUnits("us")} />
              <strong>US customary</strong><span>Pounds and inches</span>
            </label>
          </div>
        </section>

        <section className="setup-section" aria-labelledby="targets-title">
          <p className="section-kicker">Step 2</p>
          <h2 id="targets-title">Set starting nutrition targets</h2>
          <p className="form-help">These optional guide rails can be adjusted from Account at any time.</p>
          <div className="target-setup-grid">
            {(["training", "rest"] as const).map((dayType) => (
              <fieldset key={dayType} className="target-card">
                <legend>{dayType === "training" ? "Training day" : "Rest day"}</legend>
                {([
                  ["kcal", "Energy", "kcal"],
                  ["protein", "Protein", "g"],
                  ["carbs", "Carbohydrate", "g"],
                  ["fat", "Fat", "g"],
                ] as const).map(([field, label, suffix]) => (
                  <label key={field}>
                    <span>{label}</span>
                    <span className="unit-input">
                      <input type="number" min="0" step="0.01" required value={targets[dayType][field]} onChange={(event) => updateTarget(dayType, field, event.target.value)} />
                      <span>{suffix}</span>
                    </span>
                  </label>
                ))}
              </fieldset>
            ))}
          </div>
        </section>

        {error ? <p className="notice notice-error" role="alert">{error}</p> : null}
        <div className="setup-actions">
          <button className="button button-primary" type="submit" disabled={saving || !savedRecovery}>
            {saving ? "Saving your setup…" : "Open my tracker"}
          </button>
          <button className="text-button" type="button" onClick={onLogout}>Sign out</button>
        </div>
      </form>
    </main>
  );
}
