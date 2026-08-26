import { readFile, readdir } from "node:fs/promises";

const verticalRoot = new URL("../tests/vertical/", import.meta.url);
const forbidden = [
  ["request interception", /\.(?:route|routeFromHAR)\s*\(/],
  ["mock fulfillment", /\.fulfill\s*\(/],
  ["mock API fixture", /public-food-api/],
  ["mocked UI helper", /tests\/e2e/],
];
const issues = [];

async function TypeScriptFiles(directory, prefix = "") {
  const entries = await readdir(directory, { withFileTypes: true });
  const files = [];
  for (const entry of entries) {
    const relative = prefix ? `${prefix}/${entry.name}` : entry.name;
    if (entry.isDirectory()) {
      files.push(...await TypeScriptFiles(new URL(`${entry.name}/`, directory), relative));
    } else if (entry.isFile() && /\.(?:[cm]?[jt]s|[jt]sx)$/.test(entry.name)) {
      files.push(relative);
    }
  }
  return files.sort();
}

const verticalFiles = await TypeScriptFiles(verticalRoot);
for (const name of verticalFiles) {
  const source = await readFile(new URL(name, verticalRoot), "utf8");
  for (const [label, pattern] of forbidden) {
    if (pattern.test(source)) issues.push(`${name}: vertical acceptance cannot use ${label}`);
  }
}

const verticalConfig = await readFile(
  new URL("../playwright.vertical.config.ts", import.meta.url),
  "utf8",
);
if (!/testDir:\s*["\x27]\.\/tests\/vertical["\x27]/.test(verticalConfig)) {
  issues.push("playwright.vertical.config.ts: vertical acceptance test directory must stay explicit");
}
if (!/retries:\s*0/.test(verticalConfig)) {
  issues.push("playwright.vertical.config.ts: vertical acceptance must disable retries");
}
if (!/vertical-trust-chromium/.test(verticalConfig)) {
  issues.push("playwright.vertical.config.ts: vertical acceptance project must stay explicit");
}

if (issues.length > 0) {
  console.error(issues.join("\n"));
  process.exit(1);
}

console.log(`Validated ${verticalFiles.length} non-intercepted vertical acceptance files.`);
