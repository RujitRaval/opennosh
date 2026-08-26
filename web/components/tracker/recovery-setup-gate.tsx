"use client";

import { useState } from "react";

import { TrackerWordmark } from "@/components/tracker/tracker-wordmark";
import { api } from "@/lib/api";

export function RecoverySetupGate({
  onGenerated,
  onLogout,
}: {
  onGenerated: (recoveryCode: string) => void;
  onLogout: () => Promise<void> | void;
}) {
  const [password, setPassword] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string>();

  async function generate() {
    setBusy(true);
    setError(undefined);
    try {
      const response = await api.rotateRecoveryCode(password);
      setPassword("");
      onGenerated(response.recovery_code);
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : "Recovery protection could not be added.");
      setBusy(false);
    }
  }

  async function signOut() {
    setBusy(true);
    setError(undefined);
    try { await onLogout(); }
    catch (caught) {
      setError(caught instanceof Error ? caught.message : "You could not be signed out.");
      setBusy(false);
    }
  }

  return (
    <main id="main-content" className="setup-shell">
      <section className="setup-intro">
        <TrackerWordmark surface="rice-paper" priority />
        <p className="eyebrow">Private tracker / Account protection</p>
        <h1>Add account recovery before continuing.</h1>
        <p>Confirm your password to create a one-time recovery code. This protects older accounts that were created before recovery codes existed.</p>
      </section>
      <section className="setup-form recovery-card" aria-labelledby="recovery-setup-title">
        <p className="section-kicker">Required once</p>
        <h2 id="recovery-setup-title">Create your recovery code</h2>
        {error ? <p className="notice notice-error" role="alert">{error}</p> : null}
        <label htmlFor="setup-recovery-password">Confirm password</label>
        <input id="setup-recovery-password" type="password" autoComplete="current-password" minLength={12} required value={password} onChange={(event) => setPassword(event.target.value)} />
        <div className="setup-actions">
          <button className="button button-primary" type="button" disabled={busy || password.length < 12} onClick={() => void generate()}>{busy ? "Creating…" : "Create recovery code"}</button>
          <button className="text-button" type="button" disabled={busy} onClick={() => void signOut()}>Sign out</button>
        </div>
      </section>
    </main>
  );
}
