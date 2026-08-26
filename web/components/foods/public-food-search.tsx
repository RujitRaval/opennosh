"use client";

import Link from "next/link";
import { useState, type FormEvent } from "react";

import { api } from "@/lib/api";
import { routes, type InterfaceLanguage } from "@/lib/routes";
import type { FoodSearchItem } from "@/lib/types";

export function PublicFoodSearch({ language }: { language: InterfaceLanguage }) {
  const [query, setQuery] = useState("");
  const [items, setItems] = useState<FoodSearchItem[]>([]);
  const [searched, setSearched] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string>();

  async function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    setBusy(true);
    setError(undefined);
    try {
      const result = await api.searchFoods(query, language, "community");
      setItems(result.items);
      setSearched(true);
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : "Search could not be completed.");
    } finally {
      setBusy(false);
    }
  }

  return (
    <section id="search" className="public-search-tool" aria-labelledby="public-search-title">
      <div className="public-search-heading">
        <p className="mono">OPEN FOOD RECORDS / LIVE</p>
        <h2 id="public-search-title">Search the starter Commons.</h2>
        <p>165 validated community records are available now. Every result keeps its source, license, pack, and provenance visible.</p>
      </div>
      <form className="public-search-form" role="search" onSubmit={(event) => void submit(event)}>
        <label htmlFor="public-food-query">Food name</label>
        <div>
          <input id="public-food-query" name="q" type="search" required minLength={2} maxLength={120} placeholder="Try dal, paneer, tofu…" value={query} onChange={(event) => setQuery(event.target.value)} />
          <button type="submit" disabled={busy}>{busy ? "Searching…" : "Search records"}</button>
        </div>
      </form>
      {error ? <p className="public-search-error" role="alert">{error}</p> : null}
      <div aria-live="polite">
        {searched && items.length === 0 ? (
          <p className="public-search-empty">No matching starter record yet. You can help add one through Contribute.</p>
        ) : null}
        {items.length ? (
          <ol className="public-search-results">
            {items.map((item) => (
              <li key={item.id}>
                <Link href={routes.publicFoodRecord(item.source, item.source_id, language)}>
                  <span>
                    <strong>{item.name}</strong>
                    {item.name_local ? <small lang={item.attribution.pack_id?.includes("gujarati") ? "gu" : undefined}>{item.name_local}</small> : null}
                  </span>
                  <span className="public-record-proof">
                    <small>{item.category ?? "Community food"}</small>
                    <small>{item.attribution.license} · {item.attribution.pack_id ?? "community"}</small>
                  </span>
                  <i aria-hidden="true">→</i>
                </Link>
              </li>
            ))}
          </ol>
        ) : null}
      </div>
    </section>
  );
}
