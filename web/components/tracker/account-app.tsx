"use client";

import { useRouter } from "next/navigation";
import { useEffect, useState, type FormEvent } from "react";

import { LoginPanel } from "@/components/log/login-panel";
import { OnboardingPanel } from "@/components/tracker/onboarding-panel";
import { RecoveryCodeGate } from "@/components/tracker/recovery-code-gate";
import { RecoverySetupGate } from "@/components/tracker/recovery-setup-gate";
import { TrackerHeader } from "@/components/tracker/tracker-header";
import { TrackerWordmark } from "@/components/tracker/tracker-wordmark";
import { api } from "@/lib/api";
import type { AuthenticatedUser, PreferredUnits } from "@/lib/types";

export function AccountApp() {
  const router = useRouter();
  const [user, setUser] = useState<AuthenticatedUser | null>(null);
  const [checking, setChecking] = useState(true);
  const [message, setMessage] = useState<string>();
  const [messageIsError, setMessageIsError] = useState(false);
  const [recoveryCode, setRecoveryCode] = useState<string>();
  const [busy, setBusy] = useState(false);

  useEffect(() => {
    api.sessionState()
      .then((state) => setUser(state.user))
      .catch((caught) => { setMessageIsError(true); setMessage(caught instanceof Error ? caught.message : "Your account could not be opened."); })
      .finally(() => setChecking(false));
  }, []);

  if (checking) return <main className="boot-screen" role="status"><TrackerWordmark surface="rice-paper" priority /><p>Opening account settings…</p></main>;
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
    setRecoveryCode(response.recovery_code);
    setUser(response.user);
  }} />;

  if (!user.recovery_configured) return <RecoverySetupGate
    onGenerated={(code) => { setUser({ ...user, recovery_configured: true }); setRecoveryCode(code); }}
    onLogout={logout}
  />;

  if (recoveryCode && user.onboarding_completed) return <RecoveryCodeGate
    recoveryCode={recoveryCode}
    onSaved={() => setRecoveryCode(undefined)}
    onLogout={logout}
  />;

  if (!user.onboarding_completed) return <OnboardingPanel
    user={user}
    recoveryCode={recoveryCode}
    onComplete={(updated) => {
      setRecoveryCode(undefined);
      setUser(updated);
      router.push("/tracker");
    }}
    onLogout={logout}
  />;

  async function logout() {
    await api.logout();
    setUser(null);
    setMessageIsError(false);
    setMessage("You’re signed out.");
  }

  return (
    <>
      <a className="skip-link" href="#main-content">Skip to account settings</a>
      <TrackerHeader active="account" email={user.email} onLogout={() => void logout().catch((caught) => { setMessageIsError(true); setMessage(caught instanceof Error ? caught.message : "You could not be signed out."); })} />
      <main id="main-content" className="settings-shell">
        <header className="settings-hero">
          <p className="eyebrow">Private tracker / Account</p>
          <h1>Your data. Your account.</h1>
          <p>Change the essentials without giving up control of your private records.</p>
        </header>

        {message ? <p className={messageIsError ? "notice notice-error" : "notice notice-neutral"} role={messageIsError ? "alert" : "status"}>{message}</p> : null}
        <div className="settings-grid">
          <UnitsCard user={user} onSaved={(updated) => { setUser(updated); setMessageIsError(false); setMessage("Units updated."); }} />
          <PasswordCard onSaved={() => { setMessageIsError(false); setMessage("Password changed. Other signed-in sessions were ended."); }} />
          <section className="settings-card">
            <p className="section-kicker">Tracker setup</p>
            <h2>Targets and first-run choices</h2>
            <p>Reopen the guided setup to revise units and nutrition targets together.</p>
            <button className="button button-secondary" type="button" disabled={busy} onClick={async () => {
              setBusy(true);
              setMessage(undefined);
              try {
                const updated = await api.updateAccountSettings({ onboarding_completed: false });
                setUser(updated);
                router.push("/tracker");
              } catch (caught) {
                setMessageIsError(true);
                setMessage(caught instanceof Error ? caught.message : "Guided setup could not be reopened.");
              } finally {
                setBusy(false);
              }
            }}>{busy ? "Reopening…" : "Reopen guided setup"}</button>
          </section>
          <RecoveryCard onRotated={(code) => { setRecoveryCode(code); setMessageIsError(false); setMessage("Your previous recovery code no longer works."); }} />
          <DeleteCard email={user.email} onDeleted={() => { setUser(null); setMessageIsError(false); setMessage("Your account and private Tracker records were deleted."); }} />
        </div>
      </main>
    </>
  );
}

function UnitsCard({ user, onSaved }: { user: AuthenticatedUser; onSaved: (user: AuthenticatedUser) => void }) {
  const [units, setUnits] = useState<PreferredUnits>(user.preferred_units);
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState<string>();
  return (
    <section className="settings-card">
      <p className="section-kicker">Display units</p>
      <h2>Measurement system</h2>
      {error ? <p className="notice notice-error" role="alert">{error}</p> : null}
      <label htmlFor="account-units">Preferred units</label>
      <select id="account-units" value={units} onChange={(event) => setUnits(event.target.value as PreferredUnits)}>
        <option value="metric">Metric — kg and cm</option>
        <option value="us">US customary — lb and in</option>
      </select>
      <button className="button button-secondary" type="button" disabled={saving || units === user.preferred_units} onClick={async () => {
        setSaving(true);
        setError(undefined);
        try { onSaved(await api.updateAccountSettings({ preferred_units: units })); }
        catch (caught) { setError(caught instanceof Error ? caught.message : "Units could not be saved."); }
        finally { setSaving(false); }
      }}>{saving ? "Saving…" : "Save units"}</button>
    </section>
  );
}

function PasswordCard({ onSaved }: { onSaved: () => void }) {
  const [current, setCurrent] = useState("");
  const [next, setNext] = useState("");
  const [error, setError] = useState<string>();
  const [saving, setSaving] = useState(false);
  async function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    setError(undefined);
    setSaving(true);
    try { await api.changePassword(current, next); setCurrent(""); setNext(""); onSaved(); }
    catch (caught) { setError(caught instanceof Error ? caught.message : "Password could not be changed."); }
    finally { setSaving(false); }
  }
  return (
    <section className="settings-card">
      <p className="section-kicker">Security</p><h2>Change password</h2>
      {error ? <p className="notice notice-error" role="alert">{error}</p> : null}
      <form className="stack-form compact-form" onSubmit={submit}>
        <label htmlFor="current-password">Current password</label><input id="current-password" type="password" autoComplete="current-password" required minLength={12} value={current} onChange={(event) => setCurrent(event.target.value)} />
        <label htmlFor="new-password">New password</label><input id="new-password" type="password" autoComplete="new-password" required minLength={12} value={next} onChange={(event) => setNext(event.target.value)} />
        <button className="button button-secondary" disabled={saving}>{saving ? "Changing…" : "Change password"}</button>
      </form>
    </section>
  );
}

function RecoveryCard({ onRotated }: { onRotated: (code: string) => void }) {
  const [password, setPassword] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string>();
  return (
    <section className="settings-card">
      <p className="section-kicker">Account recovery</p><h2>Replace recovery code</h2>
      <p>Replacing it immediately invalidates the previous code.</p>
      {error ? <p className="notice notice-error" role="alert">{error}</p> : null}
      <label htmlFor="recovery-password">Confirm password</label>
      <input id="recovery-password" type="password" autoComplete="current-password" minLength={12} value={password} onChange={(event) => setPassword(event.target.value)} />
      <button className="button button-secondary" type="button" disabled={busy || password.length < 12} onClick={async () => {
        setBusy(true);
        setError(undefined);
        try {
          const response = await api.rotateRecoveryCode(password);
          setPassword("");
          onRotated(response.recovery_code);
        } catch (caught) {
          setError(caught instanceof Error ? caught.message : "Recovery code could not be replaced.");
        } finally {
          setBusy(false);
        }
      }}>{busy ? "Generating…" : "Generate new code"}</button>
    </section>
  );
}

function DeleteCard({ email, onDeleted }: { email: string; onDeleted: () => void }) {
  const [password, setPassword] = useState("");
  const [confirmation, setConfirmation] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string>();
  return (
    <section className="settings-card danger-card">
      <p className="section-kicker">Danger zone</p><h2>Delete account</h2>
      <p>This permanently removes {email} and all private Tracker data. Public contributions remain part of the Commons record.</p>
      {error ? <p className="notice notice-error" role="alert">{error}</p> : null}
      <label htmlFor="delete-confirmation">Type DELETE</label><input id="delete-confirmation" value={confirmation} onChange={(event) => setConfirmation(event.target.value)} />
      <label htmlFor="delete-password">Confirm password</label><input id="delete-password" type="password" autoComplete="current-password" minLength={12} value={password} onChange={(event) => setPassword(event.target.value)} />
      <button className="button button-danger" type="button" disabled={busy || confirmation !== "DELETE" || password.length < 12} onClick={async () => {
        setBusy(true);
        setError(undefined);
        try {
          await api.deleteAccount(password);
          onDeleted();
        } catch (caught) {
          setError(caught instanceof Error ? caught.message : "Account could not be deleted.");
          setBusy(false);
        }
      }}>{busy ? "Deleting…" : "Delete my account and data"}</button>
    </section>
  );
}
