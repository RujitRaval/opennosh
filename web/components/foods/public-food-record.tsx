"use client";

import Link from "next/link";
import { useState } from "react";

import { FoodRecord } from "@/components/foods/food-record";
import { api, ApiProblem } from "@/lib/api";
import { toFoodRecordView, type FoodRecordView } from "@/lib/food-record";
import { routes, type InterfaceLanguage } from "@/lib/routes";
import type { CatalogueFoodSource } from "@/lib/types";

export type PublicFoodRecordState =
  | { kind: "ready"; record: FoodRecordView }
  | { kind: "not-found" }
  | { kind: "unavailable"; reference: string };

export function PublicFoodRecord({
  initialState,
  language,
  source,
  sourceId,
  foodLocale,
}: {
  initialState: PublicFoodRecordState;
  language: InterfaceLanguage;
  source: CatalogueFoodSource;
  sourceId: string;
  foodLocale: string;
}) {
  const [state, setState] = useState<PublicFoodRecordState | { kind: "loading" }>(initialState);

  async function retry(): Promise<void> {
    setState({ kind: "loading" });
    try {
      const detail = await api.foodDetail(source, sourceId, AbortSignal.timeout(5_000));
      setState({ kind: "ready", record: toFoodRecordView(detail, foodLocale) });
    } catch (error) {
      if (error instanceof ApiProblem && error.kind === "not-found") {
        setState({ kind: "not-found" });
        return;
      }
      setState({
        kind: "unavailable",
        reference: error instanceof ApiProblem ? error.reference : "unavailable",
      });
    }
  }

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
        <button type="button" onClick={() => void retry()}>Try again</button>
        <small className="mono">Reference / {state.reference}</small>
      </section>
    );
  }

  return <FoodRecord record={state.record} />;
}
