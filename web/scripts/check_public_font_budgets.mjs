import { createHash } from "node:crypto";
import { existsSync, readFileSync, statSync } from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";

import {
  publicFontAssets,
  publicFontAssetVersion,
  publicFontScripts,
} from "../lib/public-font-assets.ts";
import { pseudoLanguage, supportedLanguages } from "../lib/routes.ts";

const webRoot = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "..");
const manifestFile = path.join(webRoot, "assets/fonts/v2/font-build.v2.json");
const manifest = JSON.parse(readFileSync(manifestFile, "utf8"));
const publicLayout = readFileSync(path.join(webRoot, "app/(public)/[language]/layout.tsx"), "utf8");
const publicFonts = readFileSync(path.join(webRoot, "app/(public)/[language]/fonts.ts"), "utf8");
const publicFontCss = readFileSync(path.join(webRoot, "app/(public)/[language]/fonts.css"), "utf8");
const trackerLayout = readFileSync(path.join(webRoot, "app/(tracker)/tracker/layout.tsx"), "utf8");
const trackerCss = readFileSync(path.join(webRoot, "app/(tracker)/tracker/tracker.css"), "utf8");
const failures = [];
const fail = (message) => failures.push(message);

if (publicFontAssetVersion !== manifest.assetVersion || manifest.schemaVersion !== 2) {
  fail("Runtime and build manifests must agree on the v2 font contract.");
}
if (manifest.tool.version !== "4.60.1" || manifest.tool.sourceDateEpoch !== 0) {
  fail("Font generation must stay pinned to FontTools 4.60.1 with SOURCE_DATE_EPOCH=0.");
}

const runtimeAssets = Object.values(publicFontAssets);
const buildAssets = new Map(manifest.fonts.map((font) => [font.id, font]));
if (runtimeAssets.length !== manifest.budgets.totalRequests) {
  fail(`Expected exactly ${manifest.budgets.totalRequests} production font requests.`);
}

for (const asset of runtimeAssets) {
  const id = path.basename(asset.href, ".woff2");
  const built = buildAssets.get(id);
  if (!built) {
    fail(`${asset.href} is missing from the reproducible build manifest.`);
    continue;
  }
  const file = path.join(webRoot, "public", asset.href);
  if (!existsSync(file)) {
    fail(`${asset.href} is missing from the public font directory.`);
    continue;
  }
  const bytes = statSync(file).size;
  const sha256 = createHash("sha256").update(readFileSync(file)).digest("hex");
  for (const [label, actual, expected] of [
    ["bytes", bytes, asset.bytes],
    ["runtime SHA-256", sha256, asset.sha256],
    ["build bytes", bytes, built.outputBytes],
    ["build SHA-256", sha256, built.outputSha256],
    ["delivery", asset.delivery, built.delivery],
    ["script", asset.script, built.script],
    ["derivative family", asset.family, built.outputNames.family],
  ]) {
    if (actual !== expected) fail(`${asset.href} ${label} drifted: ${actual} != ${expected}.`);
  }
  if (!publicFontCss.includes(asset.href)) fail(`${asset.href} is not declared in fonts.css.`);
  if (
    built.reservedNames.some((reserved) =>
      Object.values(built.outputNames).some((name) =>
        name.toLowerCase().includes(reserved.toLowerCase()),
      ),
    )
  ) {
    fail(`${asset.href} reuses a Reserved Font Name in its derivative identity.`);
  }
}

const critical = runtimeAssets.filter((asset) => asset.delivery === "critical");
const criticalBytes = critical.reduce((total, asset) => total + asset.bytes, 0);
const totalBytes = runtimeAssets.reduce((total, asset) => total + asset.bytes, 0);
if (critical.length !== manifest.budgets.criticalRequests) {
  fail(`Critical font request count ${critical.length} exceeds ${manifest.budgets.criticalRequests}.`);
}
if (criticalBytes > manifest.budgets.criticalBytes) {
  fail(`Critical font transfer ${criticalBytes} exceeds ${manifest.budgets.criticalBytes} bytes.`);
}
if (totalBytes > manifest.budgets.totalBytes) {
  fail(`Total font transfer ${totalBytes} exceeds ${manifest.budgets.totalBytes} bytes.`);
}

if (!publicFonts.includes('asset.delivery === "critical"')) {
if (manifest.budgets.trackerBytes !== 0) {
  fail("Tracker Living Commons font budget must remain exactly zero bytes.");
}
  fail("The public preload list must be derived from the typed critical-delivery contract.");
}
if (!publicLayout.includes('import "./fonts.css";') || !publicLayout.includes("preload(href")) {
  fail("The public root must own both the font stylesheet and explicit critical preloads.");
}
for (const asset of runtimeAssets.filter((font) => font.delivery === "deferred")) {
  if (publicLayout.includes(asset.href)) fail(`${asset.href} must not be preloaded.`);
}
if (/fonts\/v2|fonts\.css|public-font/i.test(`${trackerLayout}\n${trackerCss}`)) {
  fail("Tracker source references a Living Commons font resource.");
}

const interfaceLanguages = [...supportedLanguages, pseudoLanguage];
for (const language of interfaceLanguages) {
  if (publicFontScripts[language] !== "latin") {
    fail(`Interface language ${language} lacks an explicit Latin font-script mapping.`);
  }
}
if (Object.values(publicFontScripts).some((script) => !manifest.scripts[script])) {
  fail("A runtime interface-language mapping references an undeclared font script.");
}

if (!publicFontCss.includes("font-display: swap") || !publicFontCss.includes("size-adjust:")) {
  fail("Public faces require non-blocking display and reviewed metric-compatible fallbacks.");
}

if (failures.length > 0) {
  console.error("Living Commons font budget validation failed:\n");
  for (const failure of failures) console.error(`- ${failure}`);
  process.exit(1);
}

console.log(
  `Living Commons font contract valid: ${criticalBytes}/${manifest.budgets.criticalBytes} critical bytes, ${totalBytes}/${manifest.budgets.totalBytes} total bytes, ${critical.length}/${manifest.budgets.criticalRequests} preloads, zero Tracker source references.`,
);
