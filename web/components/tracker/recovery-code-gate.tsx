"use client";

import { useState } from "react";

import { TrackerWordmark } from "@/components/tracker/tracker-wordmark";

export function RecoveryCodeGate({
  recoveryCode,
  onSaved,
  onLogout,
}: {
  recoveryCode: string;
  onSaved: () => void;
  onLogout: () => void;
}) {
  const [confirmed, setConfirmed] = useState(false);
  const [signingOut, setSigningOut] = useState(false);
  const [error, setError] = useState<string>();

  async function signOut() {
    setSigningOut(true);
    setError(undefined);
    try {
      await onLogout();
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : "You could not be signed out.");
      setSigningOut(false);
    }
  }

  return (
    <main id="main-content" className="setup-shell">
      <section className="setup-intro">
        <TrackerWordmark surface="rice-paper" priority />
        <p className="eyebrow">Private tracker / Account recovered</p>
        <h1>Save your new recovery code.</h1>
        <p>Your previous code no longer works. This replacement is shown once and cannot be emailed or revealed later.</p>
      </section>
      <section className="setup-form recovery-card" aria-labelledby="recovered-code-title">
        <p className="section-kicker">One-time account key</p>
        <h2 id="recovered-code-title">Store this code somewhere private</h2>
        <output className="recovery-code" aria-label="Recovery code">{recoveryCode}</output>
        <label className="confirmation-row">
          <input type="checkbox" checked={confirmed} onChange={(event) => setConfirmed(event.target.checked)} />
          <span>I saved this code somewhere private.</span>
        </label>
        {error ? <p className="notice notice-error" role="alert">{error}</p> : null}
        <div className="setup-actions">
          <button className="button button-primary" type="button" disabled={!confirmed} onClick={onSaved}>Continue to my tracker</button>
          <button className="text-button" type="button" disabled={signingOut} onClick={() => void signOut()}>{signingOut ? "Signing out…" : "Sign out"}</button>
        </div>
      </section>
    </main>
  );
}
