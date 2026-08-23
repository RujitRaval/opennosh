import { gzipSync } from "node:zlib";
import { mkdir, readFile, readdir, writeFile } from "node:fs/promises";
import { dirname, join, relative } from "node:path";
import { fileURLToPath } from "node:url";

const webRoot = fileURLToPath(new URL("../", import.meta.url));
const buildRoot = join(webRoot, ".next");
const contract = JSON.parse(
  await readFile(join(webRoot, "performance/public-motion-budget.v1.json"), "utf8"),
);

async function listJavaScript(directory) {
  const files = [];
  for (const entry of await readdir(directory, { withFileTypes: true })) {
    const path = join(directory, entry.name);
    if (entry.isDirectory()) files.push(...await listJavaScript(path));
    else if (entry.isFile() && entry.name.endsWith(".js")) files.push(path);
  }
  return files;
}

function fail(message) {
  throw new Error(`public motion budget: ${message}`);
}

function gzipBytes(buffer) {
  return gzipSync(buffer, { level: 9 }).byteLength;
}

const sourcePaths = [
  "components/public/public-performance-signals.tsx",
  "lib/public-motion-policy.ts",
  "lib/public-motion-runtime.ts",
];
const sourceBuffers = await Promise.all(sourcePaths.map((path) => readFile(join(webRoot, path))));
const motionSourceGzipBytes = gzipBytes(Buffer.concat(sourceBuffers));

const manifestPath = join(
  buildRoot,
  "server/app/(public)/[language]/page_client-reference-manifest.js",
);
const manifestSource = await readFile(manifestPath, "utf8");
const routeKey = 'globalThis.__RSC_MANIFEST["/(public)/[language]/page"] = ';
const routeAssignment = manifestSource.indexOf(routeKey);
if (routeAssignment < 0) fail("could not find the public route in the client-reference manifest");
const manifest = JSON.parse(
  manifestSource.slice(routeAssignment + routeKey.length).trim().replace(/;$/, ""),
);
const entryFiles = manifest.entryJSFiles ?? {};
const initialChunks = new Set([
  ...(entryFiles["[project]/app/(public)/[language]/layout"] ?? []),
  ...(entryFiles["[project]/app/(public)/[language]/page"] ?? []),
]);

const emittedFiles = await listJavaScript(join(buildRoot, "static/chunks"));
const emittedSources = await Promise.all(
  emittedFiles.map(async (path) => ({ path, source: await readFile(path, "utf8") })),
);
const runtimeChunks = emittedSources
  .filter(({ source }) => source.includes("opennosh:motion-runtime:v1"))
  .map(({ path }) => relative(buildRoot, path));
const gateChunks = emittedSources
  .filter(({ source }) => source.includes("opennosh:motion-gate:v1"))
  .map(({ path }) => relative(buildRoot, path));

if (gateChunks.length !== 1) fail(`expected one emitted motion gate chunk, found ${gateChunks.length}`);
if (runtimeChunks.length !== 1) fail(`expected one emitted optional runtime chunk, found ${runtimeChunks.length}`);
if (runtimeChunks.some((chunk) => initialChunks.has(chunk))) {
  fail("the optional motion runtime is part of the initial public route payload");
}

const designDeltaChunks = new Set([...initialChunks, ...runtimeChunks]);
let publicDesignDeltaGzipBytes = 0;
const chunkSizes = {};
for (const chunk of designDeltaChunks) {
  const size = gzipBytes(await readFile(join(buildRoot, chunk)));
  chunkSizes[chunk] = size;
  publicDesignDeltaGzipBytes += size;
}

const budgets = contract.budgets;
if (motionSourceGzipBytes > budgets.motion_source_gzip_bytes_max) {
  fail(`${motionSourceGzipBytes} motion-source gzip bytes exceed ${budgets.motion_source_gzip_bytes_max}`);
}
if (publicDesignDeltaGzipBytes > budgets.public_design_delta_gzip_bytes_max) {
  fail(`${publicDesignDeltaGzipBytes} public design-delta gzip bytes exceed ${budgets.public_design_delta_gzip_bytes_max}`);
}

const report = {
  contract_id: contract.contract_id,
  status: "pass",
  motion_source_gzip_bytes: motionSourceGzipBytes,
  public_design_delta_gzip_bytes: publicDesignDeltaGzipBytes,
  initial_public_chunks: [...initialChunks],
  optional_motion_chunks: runtimeChunks,
  motion_gate_chunks: gateChunks,
  chunk_gzip_bytes: chunkSizes,
};
const outputPath = join(webRoot, "test-results/motion-bundle-budget.json");
await mkdir(dirname(outputPath), { recursive: true });
await writeFile(outputPath, `${JSON.stringify(report, null, 2)}\n`);
process.stdout.write(
  `public motion budget: pass (motion ${motionSourceGzipBytes}/${budgets.motion_source_gzip_bytes_max} gzip bytes, design delta ${publicDesignDeltaGzipBytes}/${budgets.public_design_delta_gzip_bytes_max})\n`,
);
