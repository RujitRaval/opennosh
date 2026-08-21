"use client";

import {
  useCallback,
  useEffect,
  useRef,
  useState,
  type FormEvent,
  type KeyboardEvent,
} from "react";

import { FoodAttributionLine } from "@/components/foods/food-attribution";
import { ApiError, api } from "@/lib/api";
import type {
  BarcodeFood,
  CustomFood,
  FoodAttribution,
  FoodDetail,
  FoodSearchItem,
  FoodSource,
  HouseholdPortion,
} from "@/lib/types";

type AddFoodDialogProps = {
  day: string;
  onClose: () => void;
  onAdded: (foodName: string) => Promise<void>;
  onExpired: () => void;
};

type SearchSource = "all" | "usda" | "community";
type EntryMode = "search" | "barcode" | "custom";

type SelectedFood = {
  id: string;
  source: FoodSource;
  source_id: string;
  name: string;
  portions: HouseholdPortion[];
  attribution?: FoodAttribution;
};

function localNoon(day: string): string {
  return new Date(`${day}T12:00:00`).toISOString();
}

function selectedFromSearch(food: FoodSearchItem, portions: HouseholdPortion[] = []): SelectedFood {
  return { ...food, portions };
}

function selectedFromDetail(food: FoodDetail | BarcodeFood | CustomFood): SelectedFood {
  return {
    id: food.id,
    source: food.source,
    source_id: food.source_id,
    name: food.name,
    portions: food.portions,
    attribution: "attribution" in food ? food.attribution : undefined,
  };
}

function errorText(caught: unknown, fallback: string): string {
  return caught instanceof Error ? caught.message : fallback;
}

export function AddFoodDialog({ day, onClose, onAdded, onExpired }: AddFoodDialogProps) {
  const dialogRef = useRef<HTMLElement>(null);
  const searchRef = useRef<HTMLInputElement>(null);
  const closeRef = useRef<HTMLButtonElement>(null);
  const savingRef = useRef(false);
  const customSavingRef = useRef(false);
  const barcodeLookupRef = useRef(false);
  const barcodeSequence = useRef(0);
  const searchSequence = useRef(0);
  const searchInFlightKey = useRef("");
  const lastSearchKey = useRef("");
  const [mode, setMode] = useState<EntryMode>("search");
  const [barcodeEnabled, setBarcodeEnabled] = useState(false);
  const [query, setQuery] = useState("");
  const [searchSource, setSearchSource] = useState<SearchSource>("all");
  const [results, setResults] = useState<FoodSearchItem[]>([]);
  const [selected, setSelected] = useState<SelectedFood | null>(null);
  const [mealSlot, setMealSlot] = useState("Lunch");
  const [amount, setAmount] = useState("100");
  const [quantityChoice, setQuantityChoice] = useState("g");
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
      if (previousFocus?.isConnected) previousFocus.focus();
    };
  }, []);

  useEffect(() => {
    let active = true;
    void api
      .foodCapabilities()
      .then((capabilities) => {
        if (active) setBarcodeEnabled(capabilities.barcode_lookup_enabled);
      })
      .catch(() => {
        if (active) setBarcodeEnabled(false);
      });
    return () => {
      active = false;
    };
  }, []);

  const runSearch = useCallback(
    async (force = false) => {
      const normalizedQuery = query.trim();
      if (normalizedQuery.length < 2) {
        if (force) setError("Enter at least two letters or numbers to search.");
        return;
      }
      const key = `${searchSource}:${normalizedQuery}`;
      if (!force && lastSearchKey.current === key) return;
      if (searchInFlightKey.current === key) return;
      lastSearchKey.current = key;
      searchInFlightKey.current = key;
      const sequence = ++searchSequence.current;
      setSearching(true);
      setError(null);
      setSelected(null);
      setSearchComplete(false);
      try {
        const response = await api.searchFoods(
          normalizedQuery,
          navigator.language,
          searchSource === "all" ? undefined : searchSource,
        );
        if (sequence !== searchSequence.current) return;
        setResults(response.items);
        setSearchComplete(true);
      } catch (caught) {
        if (sequence !== searchSequence.current) return;
        if (caught instanceof ApiError && caught.status === 401) {
          onExpired();
          return;
        }
        setResults([]);
        setError(errorText(caught, "Food search could not be completed."));
      } finally {
        if (sequence === searchSequence.current) {
          searchInFlightKey.current = "";
          setSearching(false);
        }
      }
    },
    [onExpired, query, searchSource],
  );

  useEffect(() => {
    if (mode !== "search" || query.trim().length < 2) return;
    const timer = window.setTimeout(() => void runSearch(), 350);
    return () => window.clearTimeout(timer);
  }, [mode, query, runSearch, searchSource]);

  function trapFocus(event: KeyboardEvent<HTMLElement>) {
    if (event.key === "Escape") {
      event.preventDefault();
      onClose();
      return;
    }
    if (event.key !== "Tab") return;
    const focusable = dialogRef.current?.querySelectorAll<HTMLElement>(
      'button:not(:disabled), input:not(:disabled), select:not(:disabled), [href], [tabindex]:not([tabindex="-1"])',
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

  function navigateTabs(event: KeyboardEvent<HTMLDivElement>) {
    if (!["ArrowLeft", "ArrowRight", "Home", "End"].includes(event.key)) return;
    const modes: EntryMode[] = barcodeEnabled
      ? ["search", "barcode", "custom"]
      : ["search", "custom"];
    const currentIndex = modes.indexOf(mode);
    const nextMode =
      event.key === "Home"
        ? modes[0]
        : event.key === "End"
          ? modes[modes.length - 1]
          : modes[
              (currentIndex + (event.key === "ArrowRight" ? 1 : -1) + modes.length) %
                modes.length
            ];
    event.preventDefault();
    changeMode(nextMode);
    window.requestAnimationFrame(() => document.getElementById(`food-entry-tab-${nextMode}`)?.focus());
  }

  function changeMode(nextMode: EntryMode) {
    searchSequence.current += 1;
    barcodeSequence.current += 1;
    searchInFlightKey.current = "";
    barcodeLookupRef.current = false;
    setMode(nextMode);
    setSearching(false);
    setSelected(null);
    setError(null);
    setQuantityChoice("g");
    setAmount("100");
  }

  async function chooseSearchResult(food: FoodSearchItem) {
    setSelected(selectedFromSearch(food));
    setQuantityChoice("g");
    setAmount("100");
    try {
      const detail = await api.foodDetail(food.source, food.source_id);
      setSelected((current) =>
        current?.id === food.id ? selectedFromDetail(detail) : current,
      );
    } catch {
      // Grams remain available if detailed portion data is temporarily unavailable.
    }
  }

  async function lookupBarcode(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (barcodeLookupRef.current) return;
    const data = new FormData(event.currentTarget);
    const barcode = String(data.get("barcode") || "").replace(/[\s-]/g, "");
    if (!/^\d{8}$|^\d{12,14}$/.test(barcode)) {
      setError("Enter an 8, 12, 13, or 14 digit barcode.");
      return;
    }
    const sequence = ++barcodeSequence.current;
    barcodeLookupRef.current = true;
    setSearching(true);
    setError(null);
    setSelected(null);
    try {
      const food = await api.lookupBarcode(barcode);
      if (sequence === barcodeSequence.current) setSelected(selectedFromDetail(food));
    } catch (caught) {
      if (sequence === barcodeSequence.current) {
        setError(errorText(caught, "That barcode could not be looked up. Try again."));
      }
    } finally {
      if (sequence === barcodeSequence.current) {
        barcodeLookupRef.current = false;
        setSearching(false);
      }
    }
  }

  async function createCustom(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (customSavingRef.current) return;
    const form = event.currentTarget;
    const data = new FormData(form);
    const energyKcal = String(data.get("energy_kcal") || "");
    const proteinG = String(data.get("protein_g") || "");
    const carbohydrateG = String(data.get("carbohydrate_g") || "");
    const fatG = String(data.get("fat_g") || "");
    const expectedEnergy = Number(proteinG) * 4 + Number(carbohydrateG) * 4 + Number(fatG) * 9;
    const enteredEnergy = Number(energyKcal);
    if (expectedEnergy > 0 && Math.abs(enteredEnergy - expectedEnergy) / expectedEnergy > 0.15) {
      setError("Calories should be within 15% of the protein, carbohydrate, and fat total.");
      return;
    }
    const portionName = String(data.get("portion_name") || "").trim();
    const portionGrams = String(data.get("portion_grams") || "");
    if ((portionName && !portionGrams) || (!portionName && portionGrams)) {
      setError("Enter both a portion name and its gram weight, or leave both blank.");
      return;
    }
    customSavingRef.current = true;
    setSaving(true);
    setError(null);
    try {
      const food = await api.createCustomFood({
        name: String(data.get("name") || ""),
        energyKcal,
        proteinG,
        carbohydrateG,
        fatG,
        portion: portionName ? { name: portionName, grams: portionGrams } : undefined,
      });
      setSelected(selectedFromDetail(food));
      setQuantityChoice(food.portions.length ? food.portions[0].name : "g");
      setAmount(food.portions.length ? "1" : "100");
      form.reset();
    } catch (caught) {
      if (caught instanceof ApiError && caught.status === 401) {
        onExpired();
        return;
      }
      setError(errorText(caught, "This custom food could not be saved. Try again."));
    } finally {
      customSavingRef.current = false;
      setSaving(false);
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
        amount,
        unit: quantityChoice === "g" ? "g" : "portion",
        portionName: quantityChoice === "g" ? null : quantityChoice,
      });
      await onAdded(selected.name);
    } catch (caught) {
      if (caught instanceof ApiError && caught.status === 401) {
        onExpired();
        return;
      }
      setError(errorText(caught, "This food could not be added."));
      savingRef.current = false;
      setSaving(false);
    }
  }

  return (
    <div className="dialog-backdrop">
      <section
        ref={dialogRef}
        className="dialog dialog-food"
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

        <div className="food-entry-tabs" role="tablist" aria-label="Food entry method" onKeyDown={navigateTabs}>
          <button id="food-entry-tab-search" type="button" role="tab" aria-controls="food-entry-panel-search" aria-selected={mode === "search"} tabIndex={mode === "search" ? 0 : -1} disabled={saving} onClick={() => changeMode("search")}>Search</button>
          {barcodeEnabled ? (
            <button id="food-entry-tab-barcode" type="button" role="tab" aria-controls="food-entry-panel-barcode" aria-selected={mode === "barcode"} tabIndex={mode === "barcode" ? 0 : -1} disabled={saving} onClick={() => changeMode("barcode")}>Barcode</button>
          ) : null}
          <button id="food-entry-tab-custom" type="button" role="tab" aria-controls="food-entry-panel-custom" aria-selected={mode === "custom"} tabIndex={mode === "custom" ? 0 : -1} disabled={saving} onClick={() => changeMode("custom")}>Custom food</button>
        </div>

        {error ? <p className="notice notice-error" role="alert">{error}</p> : null}

        {mode === "search" ? (
          <div id="food-entry-panel-search" role="tabpanel" aria-labelledby="food-entry-tab-search">
            <form className="search-form" onSubmit={(event) => { event.preventDefault(); void runSearch(true); }} role="search">
              <label htmlFor="food-search">Search the food catalogue</label>
              <div className="inline-field">
                <input id="food-search" ref={searchRef} type="search" minLength={2} maxLength={100} required value={query} onChange={(event) => setQuery(event.target.value)} placeholder="Try chicken breast or dal" />
                <button className="button button-secondary" disabled={searching}>{searching ? "Searching…" : "Search"}</button>
              </div>
              <fieldset className="source-filter">
                <legend>Source</legend>
                {(["all", "usda", "community"] as const).map((source) => (
                  <label key={source}>
                    <input type="radio" name="source" checked={searchSource === source} onChange={() => { lastSearchKey.current = ""; setSearchSource(source); }} />
                    <span>{source === "all" ? "All" : source === "usda" ? "USDA" : "Community"}</span>
                  </label>
                ))}
              </fieldset>
            </form>
            <div aria-live="polite" className="search-status">
              {searching ? "Searching…" : searchComplete ? `${results.length} ${results.length === 1 ? "food" : "foods"} found` : ""}
            </div>
            {searchComplete && results.length === 0 ? (
              <div className="empty-compact"><h3>No matching foods</h3><p>Try a broader name, scan a barcode, or create a private food.</p></div>
            ) : null}
            {results.length > 0 ? (
              <fieldset className="food-results">
                <legend>Choose a food. Generic foods appear before branded foods.</legend>
                {results.map((food) => (
                  <label className="food-result" key={`${food.source}:${food.source_id}`}>
                    <input type="radio" name="food" value={food.id} checked={selected?.id === food.id} onChange={() => void chooseSearchResult(food)} />
                    <span><strong>{food.name}</strong><FoodAttributionLine source={food.source} attribution={food.attribution} /></span>
                  </label>
                ))}
              </fieldset>
            ) : null}
          </div>
        ) : null}

        {mode === "barcode" && barcodeEnabled ? (
          <form id="food-entry-panel-barcode" className="stack-form compact-form" onSubmit={lookupBarcode} role="tabpanel" aria-labelledby="food-entry-tab-barcode">
            <label htmlFor="barcode">Scan or enter a barcode</label>
            <input id="barcode" name="barcode" inputMode="numeric" autoComplete="off" minLength={8} maxLength={18} required placeholder="e.g. 3017620422003" />
            <button className="button button-secondary" disabled={searching}>{searching ? "Looking up…" : "Look up barcode"}</button>
            <p className="form-note">Lookup uses Open Food Facts and works with a USB or Bluetooth scanner.</p>
          </form>
        ) : null}

        {mode === "custom" ? (
          <form id="food-entry-panel-custom" className="stack-form custom-food-form" onSubmit={createCustom} role="tabpanel" aria-labelledby="food-entry-tab-custom">
            <div className="private-note"><strong>Private food</strong><span>Only your account can use this entry.</span></div>
            <label htmlFor="custom-food-name">Food name</label>
            <input id="custom-food-name" name="name" type="text" maxLength={255} required />
            <fieldset className="macro-fields">
              <legend>Nutrition per 100 g</legend>
              <label>Calories<input name="energy_kcal" type="number" min="0" max="100000" step="0.01" required /></label>
              <label>Protein (g)<input name="protein_g" type="number" min="0" max="10000" step="0.01" required /></label>
              <label>Carbohydrate (g)<input name="carbohydrate_g" type="number" min="0" max="10000" step="0.01" required /></label>
              <label>Fat (g)<input name="fat_g" type="number" min="0" max="10000" step="0.01" required /></label>
            </fieldset>
            <fieldset className="optional-portion">
              <legend>Optional household portion</legend>
              <label>Portion name<input name="portion_name" type="text" maxLength={80} placeholder="e.g. scoop" /></label>
              <label>Weight (g)<input name="portion_grams" type="number" min="0.01" max="10000" step="0.01" /></label>
            </fieldset>
            <button className="button button-secondary" disabled={saving}>{saving ? "Saving…" : "Save private food"}</button>
          </form>
        ) : null}

        {selected ? (
          <form className="entry-form" onSubmit={add}>
            <div className="selected-food-heading">
              <div><p className="section-kicker">Selected</p><h3>Log {selected.name}</h3></div>
              <FoodAttributionLine source={selected.source} attribution={selected.attribution} />
            </div>
            <div className="field-grid">
              <div>
                <label htmlFor="meal-slot">Meal name</label>
                <input id="meal-slot" list="meal-options" required maxLength={64} value={mealSlot} onChange={(event) => setMealSlot(event.target.value)} />
                <datalist id="meal-options"><option value="Breakfast" /><option value="Lunch" /><option value="Dinner" /><option value="Snack" /><option value="Post workout" /></datalist>
              </div>
              <div>
                <label htmlFor="quantity-unit">Measure</label>
                <select id="quantity-unit" value={quantityChoice} onChange={(event) => { setQuantityChoice(event.target.value); setAmount(event.target.value === "g" ? "100" : "1"); }}>
                  <option value="g">Grams</option>
                  {selected.portions.map((portion) => <option key={portion.name} value={portion.name}>{portion.name} ({portion.grams} g)</option>)}
                </select>
              </div>
              <div>
                <label htmlFor="amount">{quantityChoice === "g" ? "Amount in grams" : `Number of ${quantityChoice}`}</label>
                <input id="amount" type="number" inputMode="decimal" min="0.01" max="1000000" step="0.01" required value={amount} onChange={(event) => setAmount(event.target.value)} />
              </div>
            </div>
            <button className="button button-primary button-full" disabled={saving}>{saving ? "Adding…" : `Add ${selected.name}`}</button>
          </form>
        ) : null}
      </section>
    </div>
  );
}
