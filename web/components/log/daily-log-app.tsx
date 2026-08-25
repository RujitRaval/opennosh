"use client";

import { Fragment, useCallback, useEffect, useMemo, useRef, useState } from "react";

import { TrackerHeader } from "@/components/tracker/tracker-header";
import { TrackerWordmark } from "@/components/tracker/tracker-wordmark";
import { ApiError, api } from "@/lib/api";
import type { AuthenticatedUser, DailyTotals, LogEntry, Target } from "@/lib/types";

import { AddFoodDialog } from "./add-food-dialog";
import { LoginPanel } from "./login-panel";
import { NutritionSummary } from "./nutrition-summary";

const emptyTotals = (day: string, timezone: string): DailyTotals => ({
  day,
  timezone,
  entry_count: 0,
  grams: "0.00",
  nutrients: {},
});

function localDate(date = new Date()): string {
  const year = date.getFullYear();
  const month = String(date.getMonth() + 1).padStart(2, "0");
  const day = String(date.getDate()).padStart(2, "0");
  return `${year}-${month}-${day}`;
}

function moveDay(day: string, offset: number): string {
  const value = new Date(`${day}T12:00:00`);
  value.setDate(value.getDate() + offset);
  return localDate(value);
}

function readableDay(day: string): string {
  return new Intl.DateTimeFormat(undefined, {
    weekday: "long",
    month: "long",
    day: "numeric",
    year: "numeric",
  }).format(new Date(`${day}T12:00:00`));
}

function quantityLabel(entry: LogEntry): string {
  if (entry.quantity.unit === "portion") {
    return `${entry.quantity.amount} × ${entry.quantity.portion_name ?? "portion"}`;
  }
  return `${Number(entry.quantity.amount).toLocaleString()} ${entry.quantity.unit}`;
}

function nutrient(entry: LogEntry, code: string, unit: string): string {
  return `${Number(entry.snapshot.nutrients[code] ?? 0).toLocaleString(undefined, {
    maximumFractionDigits: 1,
  })}${unit}`;
}

function EntryRow({
  entry,
  deleting,
  onDelete,
}: {
  entry: LogEntry;
  deleting: boolean;
  onDelete: () => void;
}) {
  return (
    <li className="entry-row">
      <div className="entry-main">
        <strong>{entry.food.name}</strong>
        <span>{quantityLabel(entry)}</span>
      </div>
      <dl className="entry-nutrients" aria-label={`Nutrition for ${entry.food.name}`}>
        <div>
          <dt>Energy</dt>
          <dd>{nutrient(entry, "energy_kcal", " kcal")}</dd>
        </div>
        <div>
          <dt>Protein</dt>
          <dd>{nutrient(entry, "protein_g", "g")}</dd>
        </div>
        <div>
          <dt>Carbs</dt>
          <dd>{nutrient(entry, "carbohydrate_g", "g")}</dd>
        </div>
        <div>
          <dt>Fat</dt>
          <dd>{nutrient(entry, "fat_g", "g")}</dd>
        </div>
      </dl>
      <button
        className="text-button"
        type="button"
        disabled={deleting}
        onClick={onDelete}
        aria-label={`Delete ${entry.food.name} from ${entry.meal_slot}`}
      >
        {deleting ? "Deleting…" : "Delete"}
      </button>
    </li>
  );
}

function DailyLog({ user, onExpired, onLogout }: { user: AuthenticatedUser; onExpired: () => void; onLogout: () => void }) {
  const [day, setDay] = useState(() => localDate());
  const [dayType, setDayType] = useState<"training" | "rest">("training");
  const [entries, setEntries] = useState<LogEntry[]>([]);
  const [totals, setTotals] = useState<DailyTotals>(() => emptyTotals(day, "UTC"));
  const [target, setTarget] = useState<Target | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [notice, setNotice] = useState<string | null>(null);
  const [addOpen, setAddOpen] = useState(false);
  const [deleting, setDeleting] = useState<string | null>(null);
  const [confirmingDelete, setConfirmingDelete] = useState<string | null>(null);
  const [focusMealsRequest, setFocusMealsRequest] = useState(0);
  const loadSequence = useRef(0);
  const selectedDay = useRef(day);
  const selectedDayType = useRef(dayType);
  const deletingEntry = useRef<string | null>(null);
  const mealsTitleRef = useRef<HTMLHeadingElement>(null);
  const timezone = useMemo(
    () => Intl.DateTimeFormat().resolvedOptions().timeZone || "UTC",
    [],
  );

  const loadDay = useCallback(async (requestedDay: string, requestedDayType: "training" | "rest") => {
    const sequence = ++loadSequence.current;
    setLoading(true);
    setError(null);
    try {
      const [logResponse, totalResponse, targetResponse] = await Promise.all([
        api.logs(requestedDay, timezone),
        api.totals(requestedDay, timezone),
        api.target(requestedDay, requestedDayType).catch((caught) => {
          if (caught instanceof ApiError && caught.status === 404) return null;
          throw caught;
        }),
      ]);
      if (sequence !== loadSequence.current) return;
      setEntries(logResponse.items);
      setTotals(totalResponse);
      setTarget(targetResponse);
      if (logResponse.has_more) {
        setNotice("This view shows the first 100 entries for the day.");
      }
    } catch (caught) {
      if (sequence !== loadSequence.current) return;
      if (caught instanceof ApiError && caught.status === 401) {
        onExpired();
        return;
      }
      setError(caught instanceof Error ? caught.message : "The daily log could not be loaded.");
    } finally {
      if (sequence === loadSequence.current) setLoading(false);
    }
  }, [onExpired, timezone]);

  useEffect(() => {
    const timeout = window.setTimeout(() => void loadDay(day, dayType), 0);
    return () => window.clearTimeout(timeout);
  }, [day, dayType, loadDay]);

  useEffect(() => {
    if (focusMealsRequest > 0) mealsTitleRef.current?.focus();
  }, [focusMealsRequest]);

  const groups = useMemo(() => {
    const grouped = new Map<string, LogEntry[]>();
    for (const entry of entries) {
      grouped.set(entry.meal_slot, [...(grouped.get(entry.meal_slot) ?? []), entry]);
    }
    return [...grouped.entries()];
  }, [entries]);

  async function deleteEntry(entry: LogEntry) {
    if (deletingEntry.current) return;
    deletingEntry.current = entry.id;
    const deletedDay = day;
    setDeleting(entry.id);
    setError(null);
    try {
      await api.deleteLog(entry.id);
      setConfirmingDelete(null);
      setNotice(`${entry.food.name} was removed from ${entry.meal_slot}.`);
      if (selectedDay.current === deletedDay) {
        await loadDay(selectedDay.current, selectedDayType.current);
        setFocusMealsRequest((request) => request + 1);
      }
    } catch (caught) {
      if (caught instanceof ApiError && caught.status === 401) return onExpired();
      setError(caught instanceof Error ? caught.message : "The entry could not be deleted.");
    } finally {
      deletingEntry.current = null;
      setDeleting(null);
    }
  }

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
      <a className="skip-link" href="#main-content">Skip to daily log</a>
      <TrackerHeader active="daily" email={user.email} onLogout={() => void logout()} />

      <main id="main-content" className="log-shell">
        <section className="day-header" aria-labelledby="day-heading">
          <div>
            <p className="eyebrow">Daily log / {dayType} day</p>
            <h1 id="day-heading">{readableDay(day)}</h1>
          </div>
          <button className="button button-primary" type="button" onClick={() => setAddOpen(true)}>
            <span aria-hidden="true">＋</span> Add food
          </button>
        </section>

        <div className="day-controls">
          <div className="date-nav" aria-label="Choose log date">
            <button type="button" onClick={() => { const value = moveDay(day, -1); selectedDay.current = value; setNotice(null); setDay(value); }} aria-label="Previous day">←</button>
            <label htmlFor="log-date" className="visually-hidden">Log date</label>
            <input
              id="log-date"
              type="date"
              required
              value={day}
              onChange={(event) => {
                if (!event.target.value) return;
                selectedDay.current = event.target.value;
                setNotice(null);
                setDay(event.target.value);
              }}
            />
            <button type="button" onClick={() => { const value = moveDay(day, 1); selectedDay.current = value; setNotice(null); setDay(value); }} aria-label="Next day">→</button>
            <button className="today-button" type="button" onClick={() => { const value = localDate(); selectedDay.current = value; setNotice(null); setDay(value); }}>Today</button>
          </div>
          <fieldset className="day-type">
            <legend>Target day type</legend>
            {(["training", "rest"] as const).map((type) => (
              <label key={type}>
                <input
                  type="radio"
                  name="day-type"
                  value={type}
                  checked={dayType === type}
                  onChange={() => { selectedDayType.current = type; setNotice(null); setDayType(type); }}
                />
                <span>{type === "training" ? "Training day" : "Rest day"}</span>
              </label>
            ))}
          </fieldset>
        </div>

        <div aria-live="polite" aria-atomic="true" className="live-region">
          {notice ? <p className="notice notice-neutral">{notice}</p> : null}
        </div>
        {error ? (
          <section className="error-panel" role="alert">
            <div>
              <h2>We couldn’t load this view</h2>
              <p>{error}</p>
            </div>
            <button className="button button-secondary" type="button" onClick={() => void loadDay(day, dayType)}>Retry</button>
          </section>
        ) : null}

        {loading ? (
          <div className="loading-panel" role="status">
            <p>Loading your daily log…</p>
            <div className="skeleton-grid" aria-hidden="true">
              <span /><span /><span /><span />
            </div>
          </div>
        ) : !error ? (
          <div className="log-content">
            <NutritionSummary totals={totals} target={target} />

            <section className="meals-section" aria-labelledby="meals-title">
              <div className="section-heading">
                <div>
                  <p className="section-kicker">Your entries</p>
                  <h2 ref={mealsTitleRef} id="meals-title" tabIndex={-1}>Meals</h2>
                </div>
              </div>
              {groups.length === 0 ? (
                <div className="empty-state">
                  <h3>Nothing logged for this day</h3>
                  <p>Add a food when you’re ready. This log is a record, not a scorecard.</p>
                  <button className="button button-secondary" type="button" onClick={() => setAddOpen(true)}>Add your first food</button>
                </div>
              ) : (
                <div className="meal-groups">
                  {groups.map(([meal, mealEntries]) => (
                    <section className="meal-card" key={meal} aria-labelledby={`meal-${mealEntries[0].id}`}>
                      <div className="meal-heading">
                        <h3 id={`meal-${mealEntries[0].id}`}>{meal}</h3>
                        <span>{mealEntries.length} {mealEntries.length === 1 ? "item" : "items"}</span>
                      </div>
                      <ul>
                        {mealEntries.map((entry) => (
                          <Fragment key={entry.id}>
                            <EntryRow
                              entry={entry}
                              deleting={deleting === entry.id}
                              onDelete={() => setConfirmingDelete(entry.id)}
                            />
                            {confirmingDelete === entry.id ? (
                              <li className="delete-confirmation">
                                <div role="alert">
                                  <span>Remove {entry.food.name} from this log?</span>
                                  <button className="button button-danger" type="button" disabled={deleting === entry.id} onClick={() => void deleteEntry(entry)}>{deleting === entry.id ? "Deleting…" : "Delete entry"}</button>
                                  <button className="button button-secondary" type="button" disabled={deleting === entry.id} onClick={() => setConfirmingDelete(null)}>Cancel</button>
                                </div>
                              </li>
                            ) : null}
                          </Fragment>
                        ))}
                      </ul>
                    </section>
                  ))}
                </div>
              )}
            </section>

            {Object.keys(totals.nutrients).length > 4 ? (
              <section className="nutrient-table-section" aria-labelledby="nutrient-table-title">
                <p className="section-kicker">Full detail</p>
                <h2 id="nutrient-table-title">Nutrients</h2>
                <div className="table-scroll" tabIndex={0} aria-label="Scrollable nutrient totals">
                  <table>
                    <thead><tr><th scope="col">Nutrient</th><th scope="col">Amount</th></tr></thead>
                    <tbody>
                      {Object.entries(totals.nutrients).map(([code, value]) => (
                        <tr key={code}>
                          <th scope="row">{code.replaceAll("_", " ")}</th>
                          <td>{Number(value).toLocaleString(undefined, { maximumFractionDigits: 2 })}</td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </div>
              </section>
            ) : null}
          </div>
        ) : null}
      </main>

      {addOpen ? (
        <AddFoodDialog
          day={day}
          onClose={() => setAddOpen(false)}
          onAdded={async (foodName) => {
            setAddOpen(false);
            setNotice(`${foodName} was added to the log.`);
            await loadDay(day, dayType);
            setFocusMealsRequest((request) => request + 1);
          }}
          onExpired={onExpired}
        />
      ) : null}
    </>
  );
}

export function DailyLogApp() {
  const [user, setUser] = useState<AuthenticatedUser | null>(null);
  const [checking, setChecking] = useState(true);
  const [authMessage, setAuthMessage] = useState<string | undefined>();

  useEffect(() => {
    api.session()
      .then(setUser)
      .catch((caught) => {
        if (!(caught instanceof ApiError) || caught.status !== 401) {
          setAuthMessage(caught instanceof Error ? caught.message : "Your session could not be checked.");
        }
      })
      .finally(() => setChecking(false));
  }, []);

  if (checking) {
    return (
      <main id="main-content" className="boot-screen" role="status">
        <TrackerWordmark surface="rice-paper" priority />
        <p>Opening your log…</p>
      </main>
    );
  }

  if (!user) {
    return (
      <LoginPanel
        message={authMessage}
        onAuthenticate={async (mode, email, password) => {
          const response = mode === "login"
            ? await api.login(email, password)
            : await api.register(email, password);
          setAuthMessage(undefined);
          setUser(response.user);
        }}
      />
    );
  }

  return (
    <DailyLog
      user={user}
      onExpired={() => {
        setUser(null);
        setAuthMessage("Your session ended. Sign in again to continue.");
      }}
      onLogout={() => {
        setUser(null);
        setAuthMessage("You’re signed out.");
      }}
    />
  );
}
