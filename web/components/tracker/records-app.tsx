"use client";

import { useEffect, useState, type FormEvent } from "react";

import { LoginPanel } from "@/components/log/login-panel";
import { OnboardingPanel } from "@/components/tracker/onboarding-panel";
import { TrackerHeader } from "@/components/tracker/tracker-header";
import { TrackerWordmark } from "@/components/tracker/tracker-wordmark";
import { api } from "@/lib/api";
import type { AuthenticatedUser } from "@/lib/types";

type ExerciseChoice = { id: string; name: string; attribution_text: string };

function localDateTimeValue(): string {
  const date = new Date();
  date.setMinutes(date.getMinutes() - date.getTimezoneOffset());
  return date.toISOString().slice(0, 16);
}

export function RecordsApp() {
  const [user, setUser] = useState<AuthenticatedUser | null>(null);
  const [checking, setChecking] = useState(true);
  const [message, setMessage] = useState<string>();
  const [recoveryCode, setRecoveryCode] = useState<string>();

  useEffect(() => {
    api.sessionState().then((state) => setUser(state.user)).catch((caught) => {
      setMessage(caught instanceof Error ? caught.message : "Your records could not be opened.");
    }).finally(() => setChecking(false));
  }, []);

  if (checking) return <main className="boot-screen" role="status"><TrackerWordmark surface="rice-paper" priority /><p>Opening records…</p></main>;
  if (!user) return <LoginPanel message={message} onAuthenticate={async (mode, email, password) => {
    if (mode === "login") {
      const response = await api.login(email, password);
      setRecoveryCode(undefined);
      setUser(response.user);
      return;
    }
    const response = await api.register(email, password);
    setRecoveryCode(response.recovery_code);
    setUser(response.user);
  }} onRecover={async (email, code, password) => {
    const response = await api.recover(email, code, password);
    setUser(response.user);
  }} />;

  if (!user.onboarding_completed) return <OnboardingPanel
    user={user}
    recoveryCode={recoveryCode}
    onComplete={(updated) => { setRecoveryCode(undefined); setUser(updated); }}
    onLogout={() => void api.logout().finally(() => setUser(null))}
  />;

  return (
    <>
      <a className="skip-link" href="#main-content">Skip to records</a>
      <TrackerHeader active="records" email={user.email} onLogout={() => void api.logout().finally(() => setUser(null))} />
      <main id="main-content" className="records-shell">
        <header className="settings-hero">
          <p className="eyebrow">Private tracker / Records</p>
          <h1>Body and strength, in context.</h1>
          <p>Record only what helps you. Weight and load units follow your account preference.</p>
        </header>
        <div className="records-grid">
          <BodyMetricForm user={user} />
          <WorkoutForm user={user} />
        </div>
      </main>
    </>
  );
}

function BodyMetricForm({ user }: { user: AuthenticatedUser }) {
  const [metricType, setMetricType] = useState("body_weight");
  const [value, setValue] = useState("");
  const [recordedAt, setRecordedAt] = useState(localDateTimeValue);
  const [notice, setNotice] = useState<string>();
  const unit = metricType === "body_fat_percentage" ? "percent" :
    metricType === "body_weight" ? (user.preferred_units === "us" ? "lb" : "kg") :
    (user.preferred_units === "us" ? "in" : "cm");

  async function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    setNotice(undefined);
    await api.createBodyMetric({
      recorded_at: new Date(recordedAt).toISOString(),
      metric_type: metricType,
      value,
      unit,
    });
    setValue("");
    setNotice("Body record saved.");
  }

  return (
    <section className="record-card">
      <p className="section-kicker">Body record</p><h2>Add a measurement</h2>
      {notice ? <p className="notice notice-neutral" role="status">{notice}</p> : null}
      <form className="stack-form compact-form" onSubmit={(event) => void submit(event)}>
        <label htmlFor="metric-type">Measurement</label>
        <select id="metric-type" value={metricType} onChange={(event) => setMetricType(event.target.value)}>
          <option value="body_weight">Body weight</option>
          <option value="body_fat_percentage">Body fat percentage</option>
          <option value="waist_circumference">Waist circumference</option>
          <option value="hip_circumference">Hip circumference</option>
          <option value="chest_circumference">Chest circumference</option>
        </select>
        <label htmlFor="metric-value">Value ({unit})</label>
        <input id="metric-value" type="number" min="0.0001" step="0.0001" required value={value} onChange={(event) => setValue(event.target.value)} />
        <label htmlFor="metric-time">Recorded at</label>
        <input id="metric-time" type="datetime-local" required value={recordedAt} onChange={(event) => setRecordedAt(event.target.value)} />
        <button className="button button-primary">Save body record</button>
      </form>
    </section>
  );
}

function WorkoutForm({ user }: { user: AuthenticatedUser }) {
  const [query, setQuery] = useState("");
  const [choices, setChoices] = useState<ExerciseChoice[]>([]);
  const [selected, setSelected] = useState<ExerciseChoice>();
  const [reps, setReps] = useState("8");
  const [load, setLoad] = useState("");
  const [performedAt, setPerformedAt] = useState(localDateTimeValue);
  const [notice, setNotice] = useState<string>();
  const loadUnit = user.preferred_units === "us" ? "lb" : "kg";

  async function search(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const response = await api.searchExercises(query);
    setChoices(response.items.map((item) => ({
      id: item.id,
      name: item.name,
      attribution_text: item.attribution.attribution_text,
    })));
  }

  async function save(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (!selected) return;
    await api.createWorkout({
      performed_at: new Date(performedAt).toISOString(),
      sets: [{
        exercise_id: selected.id,
        reps: Number(reps),
        load_value: load,
        load_unit: loadUnit,
      }],
    });
    setNotice("Strength set saved.");
    setLoad("");
  }

  return (
    <section className="record-card">
      <p className="section-kicker">Strength record</p><h2>Add a working set</h2>
      {notice ? <p className="notice notice-neutral" role="status">{notice}</p> : null}
      <form className="inline-search" onSubmit={(event) => void search(event)}>
        <label htmlFor="exercise-search">Find exercise</label>
        <div><input id="exercise-search" required minLength={2} value={query} onChange={(event) => setQuery(event.target.value)} /><button className="button button-secondary">Search</button></div>
      </form>
      {choices.length ? (
        <fieldset className="exercise-results">
          <legend>Choose exercise</legend>
          {choices.map((choice) => (
            <label key={choice.id} className={selected?.id === choice.id ? "exercise-choice is-selected" : "exercise-choice"}>
              <input type="radio" name="exercise" checked={selected?.id === choice.id} onChange={() => setSelected(choice)} />
              <span><strong>{choice.name}</strong><small>{choice.attribution_text}</small></span>
            </label>
          ))}
        </fieldset>
      ) : null}
      <form className="stack-form compact-form" onSubmit={(event) => void save(event)}>
        <p className="selected-record">{selected ? `Selected: ${selected.name}` : "Search and choose an exercise first."}</p>
        <label htmlFor="workout-reps">Repetitions</label><input id="workout-reps" type="number" min="1" max="1000" required value={reps} onChange={(event) => setReps(event.target.value)} />
        <label htmlFor="workout-load">Load ({loadUnit})</label><input id="workout-load" type="number" min="0" step="0.001" required value={load} onChange={(event) => setLoad(event.target.value)} />
        <label htmlFor="workout-time">Performed at</label><input id="workout-time" type="datetime-local" required value={performedAt} onChange={(event) => setPerformedAt(event.target.value)} />
        <button className="button button-primary" disabled={!selected}>Save strength set</button>
      </form>
    </section>
  );
}
