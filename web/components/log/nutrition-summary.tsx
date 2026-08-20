import type { DailyTotals, Target } from "@/lib/types";

const macroCards = [
  { code: "protein_g", target: "protein_g", label: "Protein", unit: "g" },
  { code: "carbohydrate_g", target: "carb_g", label: "Carbohydrate", unit: "g" },
  { code: "fat_g", target: "fat_g", label: "Fat", unit: "g" },
] as const;

function amount(value: string | undefined): number {
  const parsed = Number(value ?? 0);
  return Number.isFinite(parsed) ? parsed : 0;
}
function compact(value: number): string {
  return new Intl.NumberFormat(undefined, { maximumFractionDigits: 1 }).format(value);
}

type ProgressProps = {
  label: string;
  consumed: number;
  target?: number;
  unit: string;
  prominent?: boolean;
};

function Progress({ label, consumed, target, unit, prominent }: ProgressProps) {
  const hasTarget = target !== undefined && target > 0;
  return (
    <article className={prominent ? "progress-card progress-card-primary" : "progress-card"}>
      <div className="progress-heading">
        <h3>{label}</h3>
        <p>
          <strong>{compact(consumed)}</strong>
          {hasTarget ? ` of ${compact(target)}` : ""} {unit}
        </p>
      </div>
      {hasTarget ? (
        <progress
          aria-label={`${label}: ${compact(consumed)} of ${compact(target)} ${unit}`}
          max={target}
          value={Math.min(consumed, target)}
        />
      ) : (
        <p className="target-unset">No target set</p>
      )}
    </article>
  );
}

export function NutritionSummary({ totals, target }: { totals: DailyTotals; target: Target | null }) {
  return (
    <section aria-labelledby="summary-title">
      <div className="section-heading">
        <div>
          <p className="section-kicker">Daily totals</p>
          <h2 id="summary-title">Nutrition at a glance</h2>
        </div>
        <p className="entry-count">
          {totals.entry_count} {totals.entry_count === 1 ? "entry" : "entries"}
        </p>
      </div>
      <div className="progress-grid">
        <Progress
          label="Energy"
          consumed={amount(totals.nutrients.energy_kcal)}
          target={target ? amount(target.kcal) : undefined}
          unit="kcal"
          prominent
        />
        {macroCards.map((macro) => (
          <Progress
            key={macro.code}
            label={macro.label}
            consumed={amount(totals.nutrients[macro.code])}
            target={target ? amount(target[macro.target]) : undefined}
            unit={macro.unit}
          />
        ))}
      </div>
    </section>
  );
}
