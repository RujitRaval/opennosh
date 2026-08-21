import { readFileSync, readdirSync } from "node:fs";
import { join } from "node:path";

import { cleanup, render } from "@testing-library/react";
import ts from "typescript";
import { afterEach, describe, expect, it } from "vitest";

import { NutritionSummary } from "@/components/log/nutrition-summary";
import { prohibitedHealthCopy } from "@/lib/health-safety";
import type { DailyTotals, Target } from "@/lib/types";

function sourceFiles(directory: string): string[] {
  return readdirSync(directory, { recursive: true, withFileTypes: true })
    .filter(
      (entry) =>
        entry.isFile() &&
        /\.(?:ts|tsx)$/.test(entry.name) &&
        entry.name !== "health-safety.ts",
    )
    .map((entry) => `${entry.parentPath}/${entry.name}`)
    .sort();
}

const productionSources = [
  ...sourceFiles(join(process.cwd(), "app")),
  ...sourceFiles(join(process.cwd(), "components")),
  ...sourceFiles(join(process.cwd(), "lib")),
];

function staticText(expression: ts.Expression): string | null {
  if (ts.isStringLiteralLike(expression)) return expression.text;
  if (ts.isTemplateExpression(expression)) {
    return [expression.head.text, ...expression.templateSpans.map((span) => span.literal.text)]
      .join(" ")
      .replace(/\s+/g, " ");
  }
  if (ts.isParenthesizedExpression(expression)) return staticText(expression.expression);
  if (ts.isBinaryExpression(expression) && expression.operatorToken.kind === ts.SyntaxKind.PlusToken) {
    const left = staticText(expression.left);
    const right = staticText(expression.right);
    return left === null || right === null ? null : left + right;
  }
  return null;
}

function sourceCopyCandidates(path: string, source: string): string[] {
  const sourceFile = ts.createSourceFile(
    path,
    source,
    ts.ScriptTarget.Latest,
    true,
    path.endsWith(".tsx") ? ts.ScriptKind.TSX : ts.ScriptKind.TS,
  );
  const candidates: string[] = [];

  function visit(node: ts.Node): void {
    if (ts.isStringLiteralLike(node) || ts.isJsxText(node)) candidates.push(node.text);
    if (ts.isBinaryExpression(node) || ts.isTemplateExpression(node)) {
      const composed = staticText(node);
      if (composed !== null) candidates.push(composed);
    }
    ts.forEachChild(node, visit);
  }

  visit(sourceFile);
  return candidates;
}

function copyCandidates(path: string): string[] {
  return sourceCopyCandidates(path, readFileSync(path, "utf8"));
}

function totals(energy: string): DailyTotals {
  return {
    day: "2026-08-21",
    timezone: "UTC",
    entry_count: 1,
    grams: "100.00",
    nutrients: {
      energy_kcal: energy,
      protein_g: "100",
      carbohydrate_g: "100",
      fat_g: "50",
    },
  };
}

const target: Target = {
  id: "target-1",
  day_type: "training",
  kcal: "2000",
  protein_g: "150",
  carb_g: "250",
  fat_g: "70",
  active_from: "2026-08-01",
  active_until: null,
};

afterEach(cleanup);

describe("health-safety copy gate", () => {
  it.each([
    ["habit mechanics", "Keep your streak", "View your recorded history"],
    ["evaluative feedback", "Great job", "Entry saved"],
    ["shame framing", "Enjoy a cheat meal", "Meal recorded"],
    ["food moralising", "Choose clean foods", "Food details"],
    ["target judgement", "Calories remaining", "900 of 2,000 kcal"],
    ["target judgement", "You went under", "You recorded 900 kcal"],
    ["exercise compensation", "Burn it off", "Workout recorded"],
    ["automatic coaching", "You should eat more", "Choose your own target"],
    ["medical interpretation", "Your BMI is high", "Body metric history"],
    ["social comparison", "Ranked against other users", "Compare date ranges"],
    ["fasting optimisation", "Start a fast", "Logging window"],
  ])("detects %s while allowing neutral copy", (label, violation, neutralCopy) => {
    const rule = prohibitedHealthCopy.find((candidate) => candidate.label === label);
    expect(rule?.pattern.test(violation)).toBe(true);
    expect(rule?.pattern.test(neutralCopy)).toBe(false);
  });

  it("normalizes composed static copy before applying the gate", () => {
    const candidates = sourceCopyCandidates(
      "composed-copy.tsx",
      `export const Copy = () => <p>{"You " + "should eat less"}</p>;`,
    );

    expect(candidates).toContain("You should eat less");
    expect(
      prohibitedHealthCopy.some(({ pattern }) =>
        candidates.some((candidate) => pattern.test(candidate)),
      ),
    ).toBe(true);
  });

  it("inspects the static text around template interpolations", () => {
    const candidates = sourceCopyCandidates(
      "template-copy.tsx",
      "export const Copy = ({ amount }: { amount: number }) => <p>{`${amount} calories remaining`}</p>;",
    );

    expect(
      prohibitedHealthCopy.some(({ pattern }) =>
        candidates.some((candidate) => pattern.test(candidate)),
      ),
    ).toBe(true);
  });

  it("keeps prohibited patterns out of production user-facing source", () => {
    const violations = productionSources.flatMap((path) => {
      const candidates = copyCandidates(path);
      return prohibitedHealthCopy
        .filter(({ pattern }) => candidates.some((candidate) => pattern.test(candidate)))
        .map(({ label }) => `${path}: ${label}`);
    });

    expect(violations).toEqual([]);
  });

  it("reports intake below and above a target with the same neutral structure", () => {
    const snapshots = ["900", "2300"].map((energy) => {
      const view = render(<NutritionSummary totals={totals(energy)} target={target} />);
      const cards = Array.from(view.container.querySelectorAll(".progress-card"));
      const text = cards.map((card) => card.textContent?.replace(/\s+/g, " ").trim());
      const structure = cards.map((card) =>
        card.outerHTML.replace(/[\d,.]+/g, "#").replace(/\s+/g, " "),
      );
      view.unmount();
      return { structure, text };
    });

    expect(snapshots[0].structure).toEqual(snapshots[1].structure);
    expect(snapshots.map(({ text }) => text)).toMatchInlineSnapshot(`
      [
        [
          "Energy900 of 2,000 kcal",
          "Protein100 of 150 g",
          "Carbohydrate100 of 250 g",
          "Fat50 of 70 g",
        ],
        [
          "Energy2,300 of 2,000 kcal",
          "Protein100 of 150 g",
          "Carbohydrate100 of 250 g",
          "Fat50 of 70 g",
        ],
      ]
    `);
  });
});
