"use client";

import Link from "next/link";
import { useMemo, useState } from "react";

import {
  foodRecordsConflict,
  formatNutrientAmount,
  formatPortionMass,
  scaledNutrientAmount,
  type FoodRecordView,
} from "@/lib/food-record";
import { routes } from "@/lib/routes";

const coreNutrients = ["energy_kcal", "protein_g", "carbohydrate_g", "fat_g"];

function displayLocale(value: string): string {
  if (value === "global") return "Global / no preference";
  try {
    return `${new Intl.DisplayNames(["en"], { type: "language" }).of(value) ?? value} (${value})`;
  } catch {
    return value;
  }
}

function versionLabel(record: FoodRecordView): string {
  return record.trust.version ?? "Not supplied by this release";
}

function VariantLedger({ records }: { records: readonly FoodRecordView[] }) {
  const conflict = foodRecordsConflict(records);
  const hasLinkedRecords = records.length > 1;
  return (
    <section id="variants" className="record-chapter variant-chapter" aria-labelledby="variants-title">
      <div className="chapter-heading">
        <p className="mono">Related records</p>
        <div>
          <h2 id="variants-title">
            {hasLinkedRecords ? "Same food, attached context" : "No related records are linked"}
          </h2>
          <p>
            {hasLinkedRecords
              ? "Variants remain separate when preparation, evidence, values, or licensing differ."
              : "opennosh waits for an explicit source relationship instead of guessing from similar names."}
          </p>
        </div>
      </div>
      {hasLinkedRecords ? (
        <>
          <p className={`variant-status${conflict ? " variant-status-conflict" : ""}`} role="status">
            <strong>{conflict ? "Conflicting published values" : "Values align across these records"}</strong>
            <span>
              {conflict
                ? "Compare the source and license before choosing a record. opennosh does not average disagreements into one score."
                : "The records remain independently sourced even where their key values agree."}
            </span>
          </p>
          <div className="variant-ledger">
            {records.map((record) => (
              <article key={record.id} aria-labelledby={`variant-${record.id.replaceAll(":", "-")}`}>
                <p className="mono">{record.trust.sourceClass}</p>
                <h3 id={`variant-${record.id.replaceAll(":", "-")}`}>{record.name}</h3>
                <dl>
                  <div><dt>Preparation</dt><dd>{record.preparation}</dd></div>
                  <div><dt>Energy / 100 g</dt><dd>{formatNutrientAmount(record.nutrients.find((item) => item.code === "energy_kcal")?.amountPer100g ?? 0, "kcal")}</dd></div>
                  <div><dt>Version</dt><dd>{versionLabel(record)}</dd></div>
                  <div><dt>License</dt><dd>{record.license}</dd></div>
                  <div><dt>Source</dt><dd>{record.sourceSummary}</dd></div>
                </dl>
              </article>
            ))}
          </div>
        </>
      ) : (
        <p className="record-empty-state">
          No explicitly linked variants are published for this record. It remains source-qualified on its own.
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
}: {
  record: FoodRecordView;
  variants?: readonly FoodRecordView[];
  initialPortionIndex?: number;
  initialMeasurement?: "metric" | "us";
  foodLocale?: string;
}) {
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
    title: `Correction: ${record.name}`,
    body: `Food record: ${record.id}\n\nWhat should be corrected?\n`,
  })}`;

  return (
    <>
      <section className="food-record-first" aria-labelledby="food-record-title">
        <div className="food-identity" data-record-order="1-identity">
          <p className="mono">Food record / {record.source}:{record.sourceId}</p>
          <h1 id="food-record-title">{record.name}</h1>
          {record.localName ? <p className="food-local-name" lang="und">{record.localName}</p> : null}
          <dl className="identity-ledger">
            <div><dt>Preparation</dt><dd>{record.preparation}</dd></div>
            <div><dt>Record locale</dt><dd>{record.recordLocale ?? "Not specified in this release"}</dd></div>
            <div><dt>Pack</dt><dd>{record.packId ?? "Source collection"}</dd></div>
            <div><dt>Food locale preference</dt><dd>{displayLocale(record.foodLocalePreference)}</dd></div>
          </dl>
        </div>

        <aside className="trust-panel" data-record-order="2-trust" aria-labelledby="trust-title">
          <p className="mono">Verification state</p>
          <h2 id="trust-title">{record.trust.status}</h2>
          <p>{record.trust.explanation}</p>
          <dl>
            <div><dt>Source class</dt><dd>{record.trust.sourceClass}</dd></div>
            <div><dt>Release version</dt><dd>{versionLabel(record)}</dd></div>
            <div><dt>Last verified</dt><dd>{record.trust.lastVerified ?? "Not supplied by this release"}</dd></div>
            <div><dt>License</dt><dd>{record.license}</dd></div>
          </dl>
        </aside>

        <div className="nutrition-panel" data-record-order="3-serving-and-nutrients">
          <form method="get" className="nutrition-controls">
            {foodLocale ? <input type="hidden" name="food_locale" value={foodLocale} /> : null}
            <div>
              <label className="mono" htmlFor="record-portion">Selected portion</label>
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
              <legend className="mono">Portion units</legend>
              <button type="submit" name="units" value="metric" aria-pressed={measurement === "metric"} onClick={(event) => { event.preventDefault(); setMeasurement("metric"); }}>Metric</button>
              <button type="submit" name="units" value="us" aria-pressed={measurement === "us"} onClick={(event) => { event.preventDefault(); setMeasurement("us"); }}>US</button>
            </fieldset>
          </form>
          <p className="portion-mass">
            <strong>{portion.name}</strong>
            <span>{formatPortionMass(portion.gramsValue, measurement)}</span>
            <small className="mono">Canonical {formatPortionMass(portion.gramsValue, "metric")}</small>
          </p>
          <dl className="key-nutrients" aria-label={`Key nutrients for ${portion.name}`}>
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
            <p className="mono">Source beside the values</p>
            <p className="source-statement">{record.sourceSummary}</p>
            <p className="uncertainty-note"><strong>Uncertainty:</strong> {record.uncertainty}</p>
          </div>
          <dl>
            <div><dt>Version</dt><dd>{versionLabel(record)}</dd></div>
            <div><dt>Record license</dt><dd>{record.license}</dd></div>
            {record.sourceLicense && record.sourceLicense !== record.license ? (
              <div><dt>Source license</dt><dd>{record.sourceLicense}</dd></div>
            ) : null}
          </dl>
        </div>

        <nav className="record-actions" data-record-order="5-actions" aria-label="Food record actions">
          <a href="#provenance">See provenance <span aria-hidden="true">↓</span></a>
          <a href="#variants">
            {visibleRecords.length > 1 ? "Compare variants" : "Check related records"} <span aria-hidden="true">↓</span>
          </a>
          <a href={correctionHref}>Correct this record <span aria-hidden="true">↗</span></a>
          <Link className="secondary-record-action" href={routes.tracker.home}>
            Open tracker <span aria-hidden="true">↗</span>
          </Link>
        </nav>
      </section>

      <section className="record-chapter full-nutrients" data-record-tail="1-full-nutrients" aria-labelledby="full-nutrients-title">
        <div className="chapter-heading">
          <p className="mono">Full nutrients</p>
          <div>
            <h2 id="full-nutrients-title">The complete published profile</h2>
            <p>Values recalculate for the selected portion. Macronutrients stay in grams in both unit modes.</p>
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
          <p className="mono">Evidence ledger</p>
          <div><h2 id="provenance-title">Where this record comes from</h2><p>Missing details stay visible. They are not converted into a confidence score.</p></div>
        </div>
        <ol className="provenance-ledger">
          <li><span className="mono">01 / Source</span><strong>{record.sourceSummary}</strong>{record.sourceUri ? <a href={record.sourceUri}>Open source ↗</a> : <small>No public source URL supplied</small>}</li>
          <li><span className="mono">02 / Provenance</span><strong>{record.provenance ?? "No separate provenance note supplied"}</strong></li>
          <li><span className="mono">03 / Publication</span><strong>{record.packId ? `${record.packId} / ${versionLabel(record)}` : "Source collection / version not supplied"}</strong></li>
          <li><span className="mono">04 / Credit</span><strong>{record.contributor ?? "No public contributor credit supplied"}</strong></li>
        </ol>
      </section>

      <VariantLedger records={visibleRecords} />

      <section className="record-chapter history-chapter" data-record-tail="3-history" aria-labelledby="history-title">
        <div className="chapter-heading">
          <p className="mono">Record history</p>
          <div>
            <h2 id="history-title">What this release can prove</h2>
            <p>Publication facts stay explicit even when a full revision feed is not part of the current contract.</p>
          </div>
        </div>
        <dl className="history-ledger">
          <div><dt>Current release</dt><dd>{versionLabel(record)}</dd></div>
          <div><dt>Last verified</dt><dd>{record.trust.lastVerified ?? "Not supplied by this release"}</dd></div>
          <div><dt>Earlier revisions</dt><dd>Not supplied by this release</dd></div>
          <div><dt>Stable record ID</dt><dd>{record.id}</dd></div>
        </dl>
      </section>

      <section className="record-reuse" data-record-tail="4-reuse" aria-labelledby="reuse-title">
        <p className="mono">Reuse this record</p>
        <h2 id="reuse-title">The data stays attached to its terms.</h2>
        <p>Record license: <strong>{record.license}</strong>. Use the public API response to preserve the source identifier and attribution.</p>
        <a href={`/api/v1/foods/${record.source}/${encodeURIComponent(record.sourceId)}`}>View API response <span aria-hidden="true">↗</span></a>
      </section>
    </>
  );
}
