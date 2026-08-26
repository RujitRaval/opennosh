import fs from "node:fs";
import path from "node:path";
import ts from "typescript";

const webRoot = path.resolve(import.meta.dirname, "..");
const localizedFiles = [
  "app/(public)/[language]/layout.tsx",
  "app/(public)/[language]/page.tsx",
  "app/(public)/[language]/[hub]/page.tsx",
  "app/(public)/[language]/notices/page.tsx",
  "app/(public)/[language]/explore/foods/[source]/[sourceId]/page.tsx",
  "app/(public)/[language]/contribute/[draft]/status/page.tsx",
  "components/public/public-header.tsx",
  "components/public/public-footer.tsx",
  "components/public/public-truth-signals.tsx",
  "components/contributions/contribution-journey.tsx",
  "components/contributions/contribution-status.tsx",
  "components/foods/public-food-record.tsx",
  "components/foods/public-food-search.tsx",
  "components/foods/food-record.tsx",
];
const userFacingAttributes = new Set(["aria-label", "title", "placeholder", "alt"]);
const violations = [];

for (const relativePath of localizedFiles) {
  const filename = path.join(webRoot, relativePath);
  const sourceText = fs.readFileSync(filename, "utf8");
  const source = ts.createSourceFile(filename, sourceText, ts.ScriptTarget.Latest, true, ts.ScriptKind.TSX);

  function report(node, message) {
    const position = source.getLineAndCharacterOfPosition(node.getStart(source));
    violations.push(relativePath + ":" + (position.line + 1) + " " + message);
  }

  function visit(node) {
    if (ts.isJsxText(node)) {
      const text = node.getText(source).replace(/\s+/g, " ").trim();
      if (/[A-Za-z]{2}/.test(text)) report(node, "raw JSX interface copy: " + JSON.stringify(text));
    }
    if (
      ts.isJsxAttribute(node)
      && userFacingAttributes.has(node.name.getText(source))
      && node.initializer
      && ts.isStringLiteral(node.initializer)
      && /[A-Za-z]{2}/.test(node.initializer.text)
    ) {
      report(node, "raw " + node.name.getText(source) + " copy: " + JSON.stringify(node.initializer.text));
    }
    ts.forEachChild(node, visit);
  }

  visit(source);
}

if (violations.length > 0) {
  console.error("Localized public surfaces contain direct interface copy:\n" + violations.join("\n"));
  process.exit(1);
}

console.log("Localization copy gate passed for " + localizedFiles.length + " public interface files.");
