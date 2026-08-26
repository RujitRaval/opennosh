"use client";

import Link from "next/link";
import { useState, type FormEvent } from "react";

import { api } from "@/lib/api";
import { getCatalog } from "@/lib/i18n/catalog";
import { routes, type InterfaceLanguage } from "@/lib/routes";
import type { FoodSearchItem } from "@/lib/types";

export function PublicFoodSearch({ language }: { language: InterfaceLanguage }) {
  const copy = getCatalog(language).search;
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
      setError(caught instanceof Error ? caught.message : copy.errorFallback);
    } finally {
      setBusy(false);
    }
  }

  return (
    <section id="search" className="public-search-tool" aria-labelledby="public-search-title">
      <div className="public-search-heading">
        <p className="mono">{copy.liveLabel}</p>
        <h2 id="public-search-title">{copy.title}</h2>
        <p>{copy.description}</p>
      </div>
      <form className="public-search-form" role="search" onSubmit={(event) => void submit(event)}>
        <label htmlFor="public-food-query">{copy.foodName}</label>
        <div>
          <input id="public-food-query" name="q" type="search" required minLength={2} maxLength={120} placeholder={copy.placeholder} value={query} onChange={(event) => setQuery(event.target.value)} />
          <button type="submit" disabled={busy}>{busy ? copy.searching : copy.submit}</button>
        </div>
      </form>
      {error ? <p className="public-search-error" role="alert">{error}</p> : null}
      <div aria-live="polite">
        {searched && items.length === 0 ? (
          <p className="public-search-empty">{copy.empty}</p>
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
                    <small>{item.category ?? copy.categoryFallback}</small>
                    <small>{item.attribution.license} · {item.attribution.pack_id ?? copy.packFallback}</small>
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
