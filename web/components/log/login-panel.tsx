"use client";

import { useEffect, useRef, useState, type FormEvent } from "react";

import { TrackerWordmark } from "@/components/tracker/tracker-wordmark";

type LoginPanelProps = {
  message?: string;
  onAuthenticate: (mode: "login" | "register", email: string, password: string) => Promise<void>;
  onRecover: (email: string, recoveryCode: string, newPassword: string) => Promise<void>;
};

export function LoginPanel({ message, onAuthenticate, onRecover }: LoginPanelProps) {
  const messageRef = useRef<HTMLParagraphElement>(null);
  const [mode, setMode] = useState<"login" | "register" | "recover">("login");
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [recoveryCode, setRecoveryCode] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [submitting, setSubmitting] = useState(false);

  useEffect(() => {
    if (message) messageRef.current?.focus();
  }, [message]);

  async function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    setSubmitting(true);
    setError(null);
    try {
      if (mode === "recover") await onRecover(email, recoveryCode, password);
      else await onAuthenticate(mode, email, password);
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : "This request could not be completed.");
      setSubmitting(false);
    }
  }

  const title =
    mode === "login" ? "Sign in to your log" :
    mode === "register" ? "Create your account" :
    "Recover your account";

  return (
    <main id="main-content" className="auth-shell">
      <section className="auth-intro" aria-labelledby="auth-title">
        <TrackerWordmark surface="commons-ink" priority />
        <div className="auth-statement">
          <p className="eyebrow">Nutrition, without judgment</p>
          <h1 id="auth-title">A clear view of what fuels you.</h1>
          <p className="lede">
            Your daily food log, macro targets, body records, and strength work—private by default
            and built on open food data.
          </p>
        </div>
        <p className="auth-principles" aria-label="Tracker principles">
          <span>Private by default</span>
          <span>Your data</span>
          <span>Open food records</span>
        </p>
      </section>

      <section className="auth-card" aria-labelledby="sign-in-title">
        <p className="section-kicker">Private tracker / {mode}</p>
        <h2 id="sign-in-title">{title}</h2>
        {mode === "recover" ? (
          <p className="form-help">Use the recovery code you saved when you created the account. A new code will replace it.</p>
        ) : null}
        {message ? (
          <p ref={messageRef} className="notice notice-neutral" role="status" tabIndex={-1}>
            {message}
          </p>
        ) : null}
        {error ? <p className="notice notice-error" role="alert">{error}</p> : null}
        <form onSubmit={submit} className="stack-form">
          <label htmlFor="email">Email address</label>
          <input id="email" name="email" type="email" autoComplete="email" required value={email} onChange={(event) => setEmail(event.target.value)} />
          {mode === "recover" ? (
            <>
              <label htmlFor="recovery-code">Recovery code</label>
              <input id="recovery-code" name="recovery-code" autoComplete="off" required minLength={32} value={recoveryCode} onChange={(event) => setRecoveryCode(event.target.value)} />
            </>
          ) : null}
          <label htmlFor="password">{mode === "recover" ? "New password" : "Password"}</label>
          <input id="password" name="password" type="password" autoComplete={mode === "login" ? "current-password" : "new-password"} required minLength={12} value={password} onChange={(event) => setPassword(event.target.value)} />
          <button className="button button-primary button-full" disabled={submitting}>
            {submitting ? "Working…" : title}
          </button>
        </form>
        <div className="auth-actions">
          <button className="auth-switch" type="button" onClick={() => { setMode(mode === "register" ? "login" : "register"); setError(null); }}>
            {mode === "register" ? "Already have an account? Sign in" : "New to opennosh? Create an account"}
          </button>
          {mode !== "register" ? (
            <button className="auth-switch" type="button" onClick={() => { setMode(mode === "recover" ? "login" : "recover"); setError(null); }}>
              {mode === "recover" ? "Back to sign in" : "Forgot your password?"}
            </button>
          ) : null}
        </div>
      </section>
    </main>
  );
}
