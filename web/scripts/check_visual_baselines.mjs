import { createHash } from "node:crypto";
import { readdir, readFile, writeFile } from "node:fs/promises";
import { dirname, relative, resolve } from "node:path";
import { fileURLToPath } from "node:url";

const webRoot = resolve(dirname(fileURLToPath(import.meta.url)), "..");
const screenshotsRoot = resolve(webRoot, "tests/visual/__screenshots__");
const manifestPath = resolve(webRoot, "tests/visual/baselines.json");
const runtimeImage = "mcr.microsoft.com/playwright@sha256:dcc5531e97840b9b5e794f2814476b21571c5124a3fca2267d73041f56e7580e";

async function listPngFiles(directory) {
  const entries = await readdir(directory, { withFileTypes: true });
  const files = [];
  for (const entry of entries) {
    const path = resolve(directory, entry.name);
    if (entry.isDirectory()) files.push(...await listPngFiles(path));
    else if (entry.isFile() && entry.name.endsWith(".png")) files.push(path);
  }
  return files.sort();
}

async function hashes() {
  const files = await listPngFiles(screenshotsRoot);
  return Object.fromEntries(await Promise.all(files.map(async (path) => [
    relative(screenshotsRoot, path),
    createHash("sha256").update(await readFile(path)).digest("hex"),
  ])));
}

function required(value, name) {
  if (!value?.trim()) throw new Error(`${name} is required when writing visual baselines.`);
  return value.trim();
}

async function writeManifest() {
  const manifest = {
    schemaVersion: 1,
    runtimeImage,
    generatedAt: new Date().toISOString(),
    approval: {
      reason: required(process.env.VISUAL_BASELINE_REASON, "VISUAL_BASELINE_REASON"),
      designDecision: required(process.env.VISUAL_BASELINE_DESIGN_DECISION, "VISUAL_BASELINE_DESIGN_DECISION"),
      reviewerAcknowledgement: required(process.env.VISUAL_BASELINE_REVIEWER, "VISUAL_BASELINE_REVIEWER"),
    },
    releaseBlockingPatterns: ["**/logo-colorways.png", "**/tracker-*.png"],
    files: await hashes(),
  };
  await writeFile(manifestPath, `${JSON.stringify(manifest, null, 2)}\n`);
  console.log(`Visual baseline manifest written for ${Object.keys(manifest.files).length} PNG files.`);
}

async function checkManifest() {
  const manifest = JSON.parse(await readFile(manifestPath, "utf8"));
  if (manifest.schemaVersion !== 1) throw new Error("Unsupported visual baseline manifest schema.");
  if (manifest.runtimeImage !== runtimeImage) {
    throw new Error(`Visual runtime must remain pinned to ${runtimeImage}.`);
  }
  for (const field of ["reason", "designDecision", "reviewerAcknowledgement"]) {
    if (typeof manifest.approval?.[field] !== "string" || !manifest.approval[field].trim()) {
      throw new Error(`Visual baseline approval is missing ${field}.`);
    }
  }
  const actual = await hashes();
  const expected = manifest.files ?? {};
  const actualNames = Object.keys(actual);
  const expectedNames = Object.keys(expected);
  if (actualNames.length !== expectedNames.length) {
    throw new Error(`Visual baseline count drift: manifest=${expectedNames.length}, disk=${actualNames.length}.`);
  }
  for (const name of actualNames) {
    if (actual[name] !== expected[name]) {
      throw new Error(`Visual baseline drift: ${name}. Regenerate, inspect the rendered diff, and update approval metadata.`);
    }
  }
  console.log(`Visual baseline contract valid: ${actualNames.length} PNG files, pinned runtime, approval recorded.`);
}

try {
  if (process.argv.includes("--write")) await writeManifest();
  else await checkManifest();
} catch (error) {
  console.error(error instanceof Error ? error.message : error);
  process.exitCode = 1;
}
