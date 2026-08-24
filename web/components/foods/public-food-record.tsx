"use client";

import Link from "next/link";
import { useState } from "react";

import { FoodRecord } from "@/components/foods/food-record";
import { api, ApiProblem } from "@/lib/api";
import { toFoodRecordView, type FoodRecordView } from "@/lib/food-record";
import { routes, type InterfaceLanguage } from "@/lib/routes";
import type { CatalogueFoodSource } from "@/lib/types";
import { formatMessage, getCatalog } from "@/lib/i18n/catalog";

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
  initialPortionIndex,
  initialMeasurement,
}: {
  initialState: PublicFoodRecordState;
  language: InterfaceLanguage;
  source: CatalogueFoodSource;
  sourceId: string;
  foodLocale: string;
  initialPortionIndex?: number;
  initialMeasurement?: "metric" | "us";
}) {
  const copy = getCatalog(language).food;
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
        <p className="mono">{copy.loadingLabel}</p>
        <h1>{copy.loadingTitle}</h1>
        <div aria-hidden="true"><span /><span /><span /><span /></div>
      </section>
    );
  }

  if (state.kind === "not-found") {
    return (
      <section className="record-state">
        <p className="mono">{formatMessage(copy.notFoundLabel, { source, sourceId })}</p>
        <h1>{copy.notFoundTitle}</h1>
        <p>{copy.notFoundBody}</p>
        <Link href={`${routes.publicHub("explore", language)}?${new URLSearchParams({ food_locale: foodLocale })}`}>{copy.returnExplore} <span aria-hidden="true">→</span></Link>
      </section>
    );
  }

  if (state.kind === "unavailable") {
    return (
      <section className="record-state" role="alert">
        <p className="mono">{copy.unavailableLabel}</p>
        <h1>{copy.unavailableTitle}</h1>
        <p>{copy.unavailableBody}</p>
        <a href="" onClick={(event) => { event.preventDefault(); void retry(); }}>{copy.tryAgain}</a>
        <small className="mono">{formatMessage(copy.reference, { reference: state.reference })}</small>
      </section>
    );
  }

  return (
    <FoodRecord
      record={state.record}
      initialPortionIndex={initialPortionIndex}
      initialMeasurement={initialMeasurement}
      foodLocale={foodLocale}
      language={language}
    />
  );
}
