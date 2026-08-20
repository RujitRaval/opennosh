"use client";

import { useEffect, useRef, useState, type FormEvent, type KeyboardEvent } from "react";

import { ApiError, api } from "@/lib/api";
import type { FoodSearchItem } from "@/lib/types";

type AddFoodDialogProps = {
  day: string;
  onClose: () => void;
  onAdded: (foodName: string) => Promise<void>;
  onExpired: () => void;
};

function localNoon(day: string): string {
  return new Date(`${day}T12:00:00`).toISOString();
}

export function AddFoodDialog({ day, onClose, onAdded, onExpired }: AddFoodDialogProps) {
  const dialogRef = useRef<HTMLElement>(null);
  const searchRef = useRef<HTMLInputElement>(null);
  const closeRef = useRef<HTMLButtonElement>(null);
  const savingRef = useRef(false);
  const [query, setQuery] = useState("");
  const [results, setResults] = useState<FoodSearchItem[]>([]);
  const [selected, setSelected] = useState<FoodSearchItem | null>(null);
  const [mealSlot, setMealSlot] = useState("Lunch");
  const [grams, setGrams] = useState("100");
  const [searching, setSearching] = useState(false);
  const [saving, setSaving] = useState(false);
  const [searchComplete, setSearchComplete] = useState(false);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    const previousFocus = document.activeElement as HTMLElement | null;
    const mobile = window.matchMedia?.("(max-width: 48rem)").matches ?? false;
    const focusTimer = window.setTimeout(
      () => (mobile ? closeRef.current : searchRef.current)?.focus(),
      0,
    );
    return () => {
      window.clearTimeout(focusTimer);
      previousFocus?.focus();
    };
  }, []);

  function trapFocus(event: KeyboardEvent<HTMLElement>) {
    if (event.key === "Escape") {
      event.preventDefault();
      onClose();
      return;
    }
    if (event.key !== "Tab") return;
    const focusable = dialogRef.current?.querySelectorAll<HTMLElement>(
      'button:not(:disabled), input:not(:disabled), [href], [tabindex]:not([tabindex="-1"])',
    );
    if (!focusable?.length) return;
    const first = focusable[0];
    const last = focusable[focusable.length - 1];
    if (event.shiftKey && document.activeElement === first) {
      event.preventDefault();
      last.focus();
    } else if (!event.shiftKey && document.activeElement === last) {
      event.preventDefault();
      first.focus();
    }
  }

  async function search(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const normalizedQuery = query.trim();
    if (normalizedQuery.length < 2) {
      setError("Enter at least two letters or numbers to search.");
      return;
    }
    setSearching(true);
    setError(null);
    setSelected(null);
    setResults([]);
    setSearchComplete(false);
    try {
      const response = await api.searchFoods(normalizedQuery, navigator.language);
      setResults(response.items);
      setSearchComplete(true);
    } catch (caught) {
      if (caught instanceof ApiError && caught.status === 401) {
        onExpired();
        return;
      }
      setError(caught instanceof Error ? caught.message : "Food search could not be completed.");
    } finally {
      setSearching(false);
    }
  }

  async function add(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (!selected || savingRef.current) return;
    const normalizedMealSlot = mealSlot.trim();
    if (!normalizedMealSlot) {
      setError("Enter a meal name before adding this food.");
      return;
    }
    savingRef.current = true;
    setSaving(true);
    setError(null);
    try {
      await api.addLog({
        loggedAt: localNoon(day),
        mealSlot: normalizedMealSlot,
        source: selected.source,
        sourceId: selected.source_id,
        grams,
      });
      await onAdded(selected.name);
    } catch (caught) {
      if (caught instanceof ApiError && caught.status === 401) {
        onExpired();
        return;
      }
      setError(caught instanceof Error ? caught.message : "This food could not be added.");
      savingRef.current = false;
      setSaving(false);
    }
  }

  return (
    <div className="dialog-backdrop">
      <section
        ref={dialogRef}
        className="dialog"
        role="dialog"
        aria-modal="true"
        aria-labelledby="add-food-title"
        onKeyDown={trapFocus}
      >
        <div className="dialog-heading">
          <div>
            <p className="section-kicker">Add an entry</p>
            <h2 id="add-food-title">Find a food</h2>
          </div>
          <button ref={closeRef} className="icon-button" type="button" onClick={onClose} aria-label="Close add food dialog">
            ×
          </button>
        </div>

        {error ? (
          <p className="notice notice-error" role="alert">
            {error}
          </p>
        ) : null}

        <form className="search-form" onSubmit={search} role="search">
          <label htmlFor="food-search">Search the food catalogue</label>
          <div className="inline-field">
            <input
              id="food-search"
              ref={searchRef}
              type="search"
              minLength={2}
              maxLength={100}
              required
              value={query}
              onChange={(event) => setQuery(event.target.value)}
              placeholder="Try chicken breast or dal"
            />
            <button className="button button-secondary" disabled={searching}>
              {searching ? "Searching…" : "Search"}
            </button>
          </div>
        </form>

        <div aria-live="polite" className="search-status">
          {searchComplete ? `${results.length} ${results.length === 1 ? "food" : "foods"} found` : ""}
        </div>

        {searchComplete && results.length === 0 ? (
          <div className="empty-compact">
            <h3>No matching foods</h3>
            <p>Try a shorter or more general name. Custom food entry is coming in the next step.</p>
          </div>
        ) : null}

        {results.length > 0 ? (
          <fieldset className="food-results">
            <legend>Choose a food</legend>
            {results.map((food) => (
              <label className="food-result" key={`${food.source}:${food.source_id}`}>
                <input
                  type="radio"
                  name="food"
                  value={food.id}
                  checked={selected?.id === food.id}
                  onChange={() => setSelected(food)}
                />
                <span>
                  <strong>{food.name}</strong>
                  <small>
                    {food.category ? `${food.category} · ` : ""}
                    {food.source === "usda"
                      ? "USDA"
                      : `${food.attribution.contributed_by ? `Contributed by ${food.attribution.contributed_by} · ` : ""}Community food`}
                  </small>
                </span>
              </label>
            ))}
          </fieldset>
        ) : null}

        {selected ? (
          <form className="entry-form" onSubmit={add}>
            <h3>Log {selected.name}</h3>
            <div className="field-grid">
              <div>
                <label htmlFor="meal-slot">Meal name</label>
                <input
                  id="meal-slot"
                  list="meal-options"
                  required
                  maxLength={64}
                  value={mealSlot}
                  onChange={(event) => setMealSlot(event.target.value)}
                />
                <datalist id="meal-options">
                  <option value="Breakfast" />
                  <option value="Lunch" />
                  <option value="Dinner" />
                  <option value="Snack" />
                  <option value="Post workout" />
                </datalist>
              </div>
              <div>
                <label htmlFor="grams">Amount in grams</label>
                <div className="unit-input">
                  <input
                    id="grams"
                    type="number"
                    inputMode="decimal"
                    min="0.01"
                    max="1000000"
                    step="0.01"
                    required
                    value={grams}
                    onChange={(event) => setGrams(event.target.value)}
                  />
                  <span aria-hidden="true">g</span>
                </div>
              </div>
            </div>
            <button className="button button-primary button-full" disabled={saving}>
              {saving ? "Adding…" : `Add ${selected.name}`}
            </button>
          </form>
        ) : null}
      </section>
    </div>
  );
}
