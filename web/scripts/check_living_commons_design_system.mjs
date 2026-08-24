import { createHash } from "node:crypto";
import { existsSync, readFileSync, readdirSync } from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";

import {
  brandAssetVersion,
  brandColorTokens,
  brandSurfaces,
  brandWordmarks,
} from "../lib/brand-assets.ts";
import { publicFontAssets, publicFontAssetVersion } from "../lib/public-font-assets.ts";

const webRoot = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "..");
const repositoryRoot = path.resolve(webRoot, "..");
const tokenFile = path.join(webRoot, "app/(public)/[language]/tokens.css");
const publicCssFile = path.join(webRoot, "app/(public)/[language]/public.css");
const publicLayoutFile = path.join(webRoot, "app/(public)/[language]/layout.tsx");
const trackerLayoutFile = path.join(webRoot, "app/(tracker)/tracker/layout.tsx");
const trackerRoot = path.join(webRoot, "app/(tracker)");
const designFile = path.join(repositoryRoot, "DESIGN.md");

const expectedColors = {
  "commons-ink": "#12120f",
  "rice-paper": "#f4f0e6",
  "signal-tomato": "#f04e35",
  "field-acid": "#d7f34c",
  "dataset-indigo": "#5848e8",
  success: "#176b43",
  warning: "#9a5b00",
  error: "#b3261e",
  info: "#3157c8",
};

const failures = [];
const fail = (message) => failures.push(message);
const read = (file) => readFileSync(file, "utf8");

function walk(directory) {
  return readdirSync(directory, { withFileTypes: true }).flatMap((entry) => {
    const target = path.join(directory, entry.name);
    return entry.isDirectory() ? walk(target) : [target];
  });
}

function relative(file) {
  return path.relative(repositoryRoot, file);
}

function luminance(hex) {
  const channels = hex
    .slice(1)
    .match(/.{2}/g)
    .map((value) => Number.parseInt(value, 16) / 255)
    .map((value) => (value <= 0.04045 ? value / 12.92 : ((value + 0.055) / 1.055) ** 2.4));
  return 0.2126 * channels[0] + 0.7152 * channels[1] + 0.0722 * channels[2];
}

function contrast(foreground, background) {
  const first = luminance(foreground);
  const second = luminance(background);
  return (Math.max(first, second) + 0.05) / (Math.min(first, second) + 0.05);
}

const tokenCss = read(tokenFile);
const declaredColors = Object.fromEntries(
  [...tokenCss.matchAll(/--color-([a-z-]+):\s*(#[0-9a-f]{6})\s*;/g)].map((match) => [
    match[1],
    match[2],
  ]),
);

for (const [token, expected] of Object.entries(expectedColors)) {
  if (declaredColors[token] !== expected) {
    fail(`${relative(tokenFile)} must define --color-${token} as ${expected}.`);
  }
}
for (const token of brandColorTokens) {
  if (!declaredColors[token]) fail(`Brand color token ${token} is missing from tokens.css.`);
}

if (brandAssetVersion !== "v1") {
  fail("The canonical brand asset manifest must remain explicitly versioned.");
}
if (publicFontAssetVersion !== "v1") {
  fail("The canonical public font manifest must remain explicitly versioned.");
}

for (const surface of brandSurfaces) {
  const asset = brandWordmarks[surface];
  const assetFile = path.join(webRoot, "public", asset.src);
  if (!existsSync(assetFile)) {
    fail(`${surface} wordmark is missing at ${relative(assetFile)}.`);
    continue;
  }
  const svg = read(assetFile);
  if (svg.includes("<text")) {
    fail(`${relative(assetFile)} must contain outlined paths, not live text.`);
  }
  if (!svg.includes("<path")) fail(`${relative(assetFile)} does not contain an outlined path.`);
  for (const color of [asset.open, asset.nosh]) {
    const expected = declaredColors[color]?.toUpperCase();
    if (!expected || !svg.toUpperCase().includes(expected)) {
      fail(`${relative(assetFile)} does not encode its manifest color ${color}.`);
    }
  }
  for (const intendedSurface of asset.intendedSurfaces) {
    for (const foreground of [asset.open, asset.nosh]) {
      const ratio = contrast(declaredColors[foreground], declaredColors[intendedSurface]);
      if (ratio + Number.EPSILON < asset.minimumContrast) {
        fail(
          `${surface} ${foreground} is ${ratio.toFixed(2)}:1 on ${intendedSurface}; expected ${asset.minimumContrast}:1.`,
        );
      }
    }
  }
}

const publicFontSource = read(path.join(webRoot, "lib/public-fonts.ts"));
for (const asset of Object.values(publicFontAssets)) {
  const assetFile = path.resolve(webRoot, "lib", asset.path);
  if (!existsSync(assetFile)) {
    fail(`Font asset is missing at ${relative(assetFile)}.`);
    continue;
  }
  const digest = createHash("sha256").update(readFileSync(assetFile)).digest("hex");
  if (digest !== asset.sha256) {
    fail(`${relative(assetFile)} does not match its approved SHA-256.`);
  }
  if (!publicFontSource.includes(asset.path)) {
    fail(`${relative(assetFile)} is not wired through next/font/local.`);
  }
}

const publicSourceRoots = [
  path.join(webRoot, "app/(public)"),
  path.join(webRoot, "components/public"),
  path.join(webRoot, "lib"),
];
const sourceFiles = publicSourceRoots
  .flatMap(walk)
  .filter((file) => /\.(css|ts|tsx)$/.test(file));
const canonicalHexes = new Set(Object.values(expectedColors).map((value) => value.toLowerCase()));
for (const file of sourceFiles) {
  if (file === tokenFile) continue;
  const source = read(file);
  for (const match of source.matchAll(/#[0-9a-f]{6}\b/gi)) {
    if (canonicalHexes.has(match[0].toLowerCase())) {
      fail(`Raw Living Commons color ${match[0]} escaped the token source into ${relative(file)}.`);
    }
  }
  if (/fonts\.(googleapis|gstatic)\.com|@import\s+url\(\s*["']?https?:/i.test(source)) {
    fail(`${relative(file)} loads an external font or stylesheet; a clean offline clone must be complete.`);
  }
}

const publicLayout = read(publicLayoutFile);
const trackerLayout = read(trackerLayoutFile);
if (
  !publicLayout.includes('import "../../base.css";') ||
  !publicLayout.includes('import "./tokens.css";')
) {
  fail("The public layout must import shared primitives before its scoped token sheet.");
}
if (
  !trackerLayout.includes('import "../../base.css";') ||
  !trackerLayout.includes('import "./tracker.css";')
) {
  fail("The tracker layout must use only the shared base and its independent tracker entrypoint.");
}
for (const file of walk(trackerRoot).filter((target) => /\.(css|ts|tsx)$/.test(target))) {
  const source = read(file);
  if (/tokens\.css|public-fonts|brand\/v1|--color-(commons|rice|signal|field|dataset)/.test(source)) {
    fail(`${relative(file)} crosses the public design-system boundary.`);
  }
}

const publicCss = read(publicCssFile);
for (const contract of [
  "--focus-ring",
  "--focus-gap",
  ".public-header-dark",
  ".public-header-tomato",
  ".commons-stage",
  ".contribute-stage",
  ".build-stage",
]) {
  if (!`${tokenCss}\n${publicCss}`.includes(contract)) fail(`Focus contract is missing ${contract}.`);
}

const design = read(designFile);
for (const documented of ["tokens.css", "/brand/v1/", "/fonts/v1/", "--color-text", "--focus-ring"]) {
  if (!design.includes(documented)) fail(`DESIGN.md does not document ${documented}.`);
}

if (failures.length > 0) {
  console.error("Living Commons design-system validation failed:\n");
  for (const failure of failures) console.error(`- ${failure}`);
  process.exit(1);
}

console.log(
  `Living Commons design system v1 validated: ${brandSurfaces.length} wordmarks, ${Object.keys(publicFontAssets).length} font files, ${Object.keys(expectedColors).length} canonical colors.`,
);
