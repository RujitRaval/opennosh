"use client";

import { useMemo, useState } from "react";

import { CrossRootLink } from "@/components/shell/cross-root-link";
import {
  foodRecordsConflict,
  formatNutrientAmount,
  formatPortionMass,
  scaledNutrientAmount,
  type FoodRecordView,
} from "@/lib/food-record";
import { routes } from "@/lib/routes";
import type { InterfaceLanguage } from "@/lib/routes";
import { fallbackLanguage, formatMessage, getCatalog, pseudoLanguage } from "@/lib/i18n/catalog";

const coreNutrients = ["energy_kcal", "protein_g", "carbohydrate_g", "fat_g"];

function displayLocale(value: string, language: InterfaceLanguage, globalLabel: string): string {
  if (value === "global") return globalLabel;
  try {
    const locale = language === pseudoLanguage ? fallbackLanguage : language;
    return `${new Intl.DisplayNames([locale], { type: "language" }).of(value) ?? value} (${value})`;
  } catch {
    return value;
  }
}

function versionLabel(record: FoodRecordView, fallback: string): string {
  return record.trust.version ?? fallback;
}

function VariantLedger({
  records,
  language,
}: {
  records: readonly FoodRecordView[];
  language: InterfaceLanguage;
}) {
  const copy = getCatalog(language).food;
  const conflict = foodRecordsConflict(records);
  const hasLinkedRecords = records.length > 1;
  return (
    <section id="variants" className="record-chapter variant-chapter" aria-labelledby="variants-title">
      <div className="chapter-heading">
        <p className="mono">{copy.relatedRecords}</p>
        <div>
          <h2 id="variants-title">
            {hasLinkedRecords ? copy.sameFood : copy.noRelated}
          </h2>
          <p>
            {hasLinkedRecords
              ? copy.variantsSeparate
              : copy.variantsNoGuess}
          </p>
        </div>
      </div>
      {hasLinkedRecords ? (
        <>
          <p className={`variant-status${conflict ? " variant-status-conflict" : ""}`} role="status">
            <strong>{conflict ? copy.conflicting : copy.aligned}</strong>
            <span>
              {conflict
                ? copy.conflictBody
                : copy.alignedBody}
            </span>
          </p>
          <div className="variant-ledger">
            {records.map((record) => (
              <article key={record.id} aria-labelledby={`variant-${record.id.replaceAll(":", "-")}`}>
                <p className="mono">{record.trust.sourceClass}</p>
                <h3 id={`variant-${record.id.replaceAll(":", "-")}`}>{record.name}</h3>
                <dl>
                  <div><dt>{copy.preparation}</dt><dd>{record.preparation}</dd></div>
                  <div><dt>{copy.energy100}</dt><dd>{formatNutrientAmount(record.nutrients.find((item) => item.code === "energy_kcal")?.amountPer100g ?? 0, "kcal")}</dd></div>
                  <div><dt>{copy.version}</dt><dd>{versionLabel(record, copy.notSupplied)}</dd></div>
                  <div><dt>{copy.license}</dt><dd>{record.license}</dd></div>
                  <div><dt>{copy.source}</dt><dd>{record.sourceSummary}</dd></div>
                </dl>
              </article>
            ))}
          </div>
        </>
      ) : (
        <p className="record-empty-state">
          {copy.noVariants}
        </p>
      )}
    </section>
  );
}

export function FoodRecord({
  record,
  variants = [],
  initialPortionIndex,
  initialMeasurement = "metric",
  foodLocale,
  language = "en",
}: {
  record: FoodRecordView;
  variants?: readonly FoodRecordView[];
  initialPortionIndex?: number;
  initialMeasurement?: "metric" | "us";
  foodLocale?: string;
  language?: InterfaceLanguage;
}) {
  const copy = getCatalog(language).food;
  const defaultPortionIndex = record.portions.length > 1 ? 1 : 0;
  const [portionIndex, setPortionIndex] = useState(
    initialPortionIndex !== undefined && record.portions[initialPortionIndex]
      ? initialPortionIndex
      : defaultPortionIndex,
  );
  const [measurement, setMeasurement] = useState<"metric" | "us">(initialMeasurement);
  const portion = record.portions[portionIndex] ?? record.portions[0];
  const visibleRecords = useMemo(
    () => [record, ...variants.filter((variant) => variant.id !== record.id)],
    [record, variants],
  );
  const keyNutrients = coreNutrients.flatMap((code) => {
    const nutrient = record.nutrients.find((item) => item.code === code);
    return nutrient ? [nutrient] : [];
  });
  const correctionHref = `https://github.com/RujitRaval/opennosh/issues/new?${new URLSearchParams({
    title: formatMessage(copy.correctionTitle, { name: record.name }),
    body: formatMessage(copy.correctionBody, { id: record.id }),
  })}`;

  return (
    <>
      <section className="food-record-first" aria-labelledby="food-record-title">
        <div className="food-identity" data-record-order="1-identity">
          <p className="mono">{formatMessage(copy.identity, { source: record.source, sourceId: record.sourceId })}</p>
          <h1 id="food-record-title">{record.name}</h1>
          {record.localName ? <p className="food-local-name" lang="und">{record.localName}</p> : null}
          <dl className="identity-ledger">
            <div><dt>{copy.preparation}</dt><dd>{record.preparation}</dd></div>
            <div><dt>{copy.recordLocale}</dt><dd>{record.recordLocale ?? copy.notSpecified}</dd></div>
            <div><dt>{copy.pack}</dt><dd>{record.packId ?? copy.sourceCollection}</dd></div>
            <div><dt>{copy.foodLocalePreference}</dt><dd>{displayLocale(record.foodLocalePreference, language, copy.globalLocale)}</dd></div>
          </dl>
        </div>

        <aside className="trust-panel" data-record-order="2-trust" aria-labelledby="trust-title">
          <p className="mono">{copy.verificationState}</p>
          <h2 id="trust-title">{record.trust.status}</h2>
          <p>{record.trust.explanation}</p>
          <dl>
            <div><dt>{copy.sourceClass}</dt><dd>{record.trust.sourceClass}</dd></div>
            <div><dt>{copy.releaseVersion}</dt><dd>{versionLabel(record, copy.notSupplied)}</dd></div>
            <div><dt>{copy.lastVerified}</dt><dd>{record.trust.lastVerified ?? copy.notSupplied}</dd></div>
            <div><dt>{copy.license}</dt><dd>{record.license}</dd></div>
          </dl>
        </aside>

        <div className="nutrition-panel" data-record-order="3-serving-and-nutrients">
          <form method="get" className="nutrition-controls">
            {foodLocale ? <input type="hidden" name="food_locale" value={foodLocale} /> : null}
            <div>
              <label className="mono" htmlFor="record-portion">{copy.selectedPortion}</label>
              <select
                id="record-portion"
                name="portion"
                value={portionIndex}
                onChange={(event) => setPortionIndex(Number(event.target.value))}
              >
                {record.portions.map((item, index) => (
                  <option key={`${item.name}-${item.grams}`} value={index}>{item.name}</option>
                ))}
              </select>
            </div>
            <fieldset className="measurement-switch">
              <legend className="mono">{copy.portionUnits}</legend>
              <button type="submit" name="units" value="metric" aria-pressed={measurement === "metric"} onClick={(event) => { event.preventDefault(); setMeasurement("metric"); }}>{copy.metric}</button>
              <button type="submit" name="units" value="us" aria-pressed={measurement === "us"} onClick={(event) => { event.preventDefault(); setMeasurement("us"); }}>{copy.us}</button>
            </fieldset>
          </form>
          <p className="portion-mass">
            <strong>{portion.name}</strong>
            <span>{formatPortionMass(portion.gramsValue, measurement)}</span>
            <small className="mono">{formatMessage(copy.canonical, { mass: formatPortionMass(portion.gramsValue, "metric") })}</small>
          </p>
          <dl className="key-nutrients" aria-label={formatMessage(copy.keyNutrients, { portion: portion.name })}>
            {keyNutrients.map((nutrient) => (
              <div key={nutrient.code}>
                <dt>{nutrient.label}</dt>
                <dd>{formatNutrientAmount(scaledNutrientAmount(nutrient, portion.gramsValue), nutrient.unit)}</dd>
              </div>
            ))}
          </dl>
        </div>

        <div className="source-band" data-record-order="4-source-summary">
          <div>
            <p className="mono">{copy.sourceBesideValues}</p>
            <p className="source-statement">{record.sourceSummary}</p>
            <p className="uncertainty-note"><strong>{copy.uncertainty}</strong> {record.uncertainty}</p>
          </div>
          <dl>
            <div><dt>{copy.version}</dt><dd>{versionLabel(record, copy.notSupplied)}</dd></div>
            <div><dt>{copy.recordLicense}</dt><dd>{record.license}</dd></div>
            {record.sourceLicense && record.sourceLicense !== record.license ? (
              <div><dt>{copy.sourceLicense}</dt><dd>{record.sourceLicense}</dd></div>
            ) : null}
          </dl>
        </div>

        <nav className="record-actions" data-record-order="5-actions" aria-label={copy.actions}>
          <a href={record.provenanceUrl ?? "#provenance"}>
            {copy.provenanceAction} <span aria-hidden="true">{record.provenanceUrl ? "↗" : "↓"}</span>
          </a>
          <a href="#variants">
            {visibleRecords.length > 1 ? copy.compareVariants : copy.relatedAction} <span aria-hidden="true">↓</span>
          </a>
          <a href={correctionHref}>{copy.correct} <span aria-hidden="true">↗</span></a>
          <CrossRootLink className="secondary-record-action" href={routes.tracker.home}>
            {copy.openTracker} <span aria-hidden="true">↗</span>
          </CrossRootLink>
        </nav>
      </section>

      <section className="record-chapter full-nutrients" data-record-tail="1-full-nutrients" aria-labelledby="full-nutrients-title">
        <div className="chapter-heading">
          <p className="mono">{copy.fullNutrients}</p>
          <div>
            <h2 id="full-nutrients-title">{copy.fullTitle}</h2>
            <p>{copy.fullBody}</p>
          </div>
        </div>
        <dl className="nutrient-ledger">
          {record.nutrients.map((nutrient) => (
            <div key={nutrient.code}>
              <dt>{nutrient.label}<small className="mono">{nutrient.code}</small></dt>
              <dd>{formatNutrientAmount(scaledNutrientAmount(nutrient, portion.gramsValue), nutrient.unit)}</dd>
            </div>
          ))}
        </dl>
      </section>

      <section id="provenance" className="record-chapter provenance-chapter" data-record-tail="2-evidence" aria-labelledby="provenance-title">
        <div className="chapter-heading">
          <p className="mono">{copy.evidenceLedger}</p>
          <div><h2 id="provenance-title">{copy.provenanceTitle}</h2><p>{copy.provenanceBody}</p></div>
        </div>
        <ol className="provenance-ledger">
          <li><span className="mono">01 / {copy.source}</span><strong>{record.sourceSummary}</strong>{record.sourceUri ? <a href={record.sourceUri}>{copy.openSource} ↗</a> : <small>{copy.noSourceUrl}</small>}</li>
          <li><span className="mono">02 / {copy.provenance}</span><strong>{record.provenance ?? copy.noProvenance}</strong></li>
          <li><span className="mono">03 / {copy.publication}</span><strong>{record.packId ? `${record.packId} / ${versionLabel(record, copy.notSupplied)}` : copy.sourceVersionMissing}</strong></li>
          <li><span className="mono">04 / {copy.credit}</span><strong>{record.contributor ?? copy.noCredit}</strong></li>
        </ol>
      </section>

      <VariantLedger records={visibleRecords} language={language} />

      <section className="record-chapter history-chapter" data-record-tail="3-history" aria-labelledby="history-title">
        <div className="chapter-heading">
          <p className="mono">{copy.recordHistory}</p>
          <div>
            <h2 id="history-title">{copy.historyTitle}</h2>
            <p>{copy.historyBody}</p>
          </div>
        </div>
        <dl className="history-ledger">
          <div><dt>{copy.currentRelease}</dt><dd>{versionLabel(record, copy.notSupplied)}</dd></div>
          <div><dt>{copy.lastVerified}</dt><dd>{record.trust.lastVerified ?? copy.notSupplied}</dd></div>
          <div><dt>{copy.earlierRevisions}</dt><dd>{copy.notSupplied}</dd></div>
          <div><dt>{copy.stableId}</dt><dd>{record.id}</dd></div>
        </dl>
      </section>

      <section className="record-reuse" data-record-tail="4-reuse" aria-labelledby="reuse-title">
        <p className="mono">{copy.reuse}</p>
        <h2 id="reuse-title">{copy.reuseTitle}</h2>
        <p>{formatMessage(copy.reuseBody, { license: record.license })}</p>
        <a href={record.immutableUrl ?? `/api/v1/foods/${record.source}/${encodeURIComponent(record.sourceId)}`}>{copy.apiResponse} <span aria-hidden="true">↗</span></a>
      </section>
    </>
  );
}
