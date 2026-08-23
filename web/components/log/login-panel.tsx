"use client";

import { useEffect, useRef, useState, type FormEvent } from "react";
import Link from "next/link";

import { routes } from "@/lib/routes";

type LoginPanelProps = {
  message?: string;
  onAuthenticate: (mode: "login" | "register", email: string, password: string) => Promise<void>;
};

export function LoginPanel({ message, onAuthenticate }: LoginPanelProps) {
  const messageRef = useRef<HTMLParagraphElement>(null);
  const [mode, setMode] = useState<"login" | "register">("login");
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
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
      await onAuthenticate(mode, email, password);
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : "Sign in could not be completed.");
      setSubmitting(false);
    }
  }

  return (
    <main id="main-content" className="auth-shell">
      <section className="auth-intro" aria-labelledby="auth-title">
        <Link className="wordmark" href={routes.tracker.home} aria-label="opennosh tracker">
          open<span>nosh</span>
        </Link>
        <p className="eyebrow">Nutrition, without judgment</p>
        <h1 id="auth-title">A clear view of what fuels you.</h1>
        <p className="lede">
          Your daily food log, macro targets, and strength work—private, self-hosted, and built on
          open food data.
        </p>
      </section>

      <section className="auth-card" aria-labelledby="sign-in-title">
        <p className="section-kicker">{mode === "login" ? "Welcome back" : "Your private log"}</p>
        <h2 id="sign-in-title">{mode === "login" ? "Sign in to your log" : "Create your account"}</h2>
        {message ? (
          <p ref={messageRef} className="notice notice-neutral" role="status" tabIndex={-1}>
            {message}
          </p>
        ) : null}
        {error ? (
          <p className="notice notice-error" role="alert">
            {error}
          </p>
        ) : null}
        <form onSubmit={submit} className="stack-form">
          <label htmlFor="email">Email address</label>
          <input
            id="email"
            name="email"
            type="email"
            autoComplete="email"
            required
            value={email}
            onChange={(event) => setEmail(event.target.value)}
          />
          <label htmlFor="password">Password</label>
          <input
            id="password"
            name="password"
            type="password"
            autoComplete={mode === "login" ? "current-password" : "new-password"}
            required
            minLength={12}
            value={password}
            onChange={(event) => setPassword(event.target.value)}
          />
          <button className="button button-primary button-full" disabled={submitting}>
            {submitting ? (mode === "login" ? "Signing in…" : "Creating account…") : (mode === "login" ? "Sign in" : "Create account")}
          </button>
        </form>
        <button
          className="auth-switch"
          type="button"
          onClick={() => {
            setMode(mode === "login" ? "register" : "login");
            setError(null);
          }}
        >
          {mode === "login" ? "New to opennosh? Create an account" : "Already have an account? Sign in"}
        </button>
      </section>
    </main>
  );
}
