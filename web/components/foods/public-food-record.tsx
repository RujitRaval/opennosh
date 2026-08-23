"use client";

import Link from "next/link";
import { useCallback, useEffect, useState } from "react";

import { FoodRecord } from "@/components/foods/food-record";
import { api, ApiProblem } from "@/lib/api";
import { toFoodRecordView, type FoodRecordView } from "@/lib/food-record";
import { routes, type InterfaceLanguage } from "@/lib/routes";
import type { CatalogueFoodSource } from "@/lib/types";

type RecordState =
  | { kind: "loading" }
  | { kind: "ready"; record: FoodRecordView; variants: FoodRecordView[] }
  | { kind: "not-found" }
  | { kind: "unavailable"; reference: string };

export function PublicFoodRecord({
  language,
  source,
  sourceId,
  foodLocale,
}: {
  language: InterfaceLanguage;
  source: CatalogueFoodSource;
  sourceId: string;
  foodLocale: string;
}) {
  const [state, setState] = useState<RecordState>({ kind: "loading" });

  const load = useCallback(async (): Promise<RecordState> => {
    try {
      const detail = await api.foodDetail(source, sourceId);
      const record = toFoodRecordView(detail, foodLocale);
      let variants: FoodRecordView[] = [];
      try {
        const matches = await api.searchFoods(detail.name, foodLocale);
        const candidates = matches.items
          .filter((item) => item.id !== detail.id)
          .slice(0, 3);
        const settled = await Promise.allSettled(
          candidates.map((item) => api.foodDetail(item.source, item.source_id)),
        );
        variants = settled.flatMap((result) =>
          result.status === "fulfilled" ? [toFoodRecordView(result.value, foodLocale)] : [],
        );
      } catch {
        // Related records enrich the page but never hide a valid primary record.
      }
      return { kind: "ready", record, variants };
    } catch (error) {
      if (error instanceof ApiProblem && error.kind === "not-found") {
        return { kind: "not-found" };
      }
      return {
        kind: "unavailable",
        reference: error instanceof ApiProblem ? error.reference : "unavailable",
      };
    }
  }, [foodLocale, source, sourceId]);

  useEffect(() => {
    let active = true;
    void load().then((nextState) => {
      if (active) setState(nextState);
    });
    return () => {
      active = false;
    };
  }, [load]);

  if (state.kind === "loading") {
    return (
      <section className="record-state record-skeleton" aria-busy="true" aria-live="polite">
        <p className="mono">Checking the published record</p>
        <h1>Loading food data and its source…</h1>
        <div aria-hidden="true"><span /><span /><span /><span /></div>
      </section>
    );
  }

  if (state.kind === "not-found") {
    return (
      <section className="record-state">
        <p className="mono">Record not found / {source}:{sourceId}</p>
        <h1>This published food record is not available.</h1>
        <p>The source or pack may have changed. Search Explore before beginning a correction.</p>
        <Link href={`${routes.publicHub("explore", language)}?${new URLSearchParams({ food_locale: foodLocale })}`}>Return to Explore <span aria-hidden="true">→</span></Link>
      </section>
    );
  }

  if (state.kind === "unavailable") {
    return (
      <section className="record-state" role="alert">
        <p className="mono">Verified read unavailable</p>
        <h1>We cannot verify this record right now.</h1>
        <p>The page will not show cached or invented nutrition without its trust context.</p>
        <button
          type="button"
          onClick={() => {
            setState({ kind: "loading" });
            void load().then(setState);
          }}
        >
          Try again
        </button>
        <small className="mono">Reference / {state.reference}</small>
      </section>
    );
  }

  return <FoodRecord record={state.record} variants={state.variants} />;
}
