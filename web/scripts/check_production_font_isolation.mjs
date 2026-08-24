import { existsSync, readFileSync } from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";

const webRoot = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "..");
const manifestFile = path.join(webRoot, ".next/server/next-font-manifest.json");

if (!existsSync(manifestFile)) {
  console.error("Production font isolation check requires a completed Next.js build.");
  process.exit(1);
}

const manifest = JSON.parse(readFileSync(manifestFile, "utf8"));
const appEntries = Object.entries(manifest.app ?? {});
const trackerEntries = appEntries.filter(([route]) => route.includes("/app/(tracker)/"));
const publicEntries = appEntries.filter(([route]) => route.includes("/app/(public)/"));
const publicFontPattern =
  /archivo_latin_variable|source_sans_3_latin_variable|ibm_plex_mono_latin/;

if (trackerEntries.length === 0 || publicEntries.length === 0) {
  console.error("Production font manifest is missing the expected public or tracker routes.");
  process.exit(1);
}

const leakedTrackerFonts = trackerEntries.flatMap(([route, fonts]) =>
  fonts.filter((font) => publicFontPattern.test(font)).map((font) => ({ route, font })),
);
if (leakedTrackerFonts.length > 0) {
  console.error("Public font preloads crossed into the tracker production routes:");
  for (const { route, font } of leakedTrackerFonts) console.error(`- ${route}: ${font}`);
  process.exit(1);
}

for (const trackerHtml of [
  path.join(webRoot, ".next/server/app/tracker.html"),
  path.join(webRoot, ".next/server/app/tracker/trends.html"),
]) {
  if (!existsSync(trackerHtml)) {
    console.error(`Production build is missing ${path.relative(webRoot, trackerHtml)}.`);
    process.exit(1);
  }
  if (publicFontPattern.test(readFileSync(trackerHtml, "utf8"))) {
    console.error(`${path.relative(webRoot, trackerHtml)} references a public font asset.`);
    process.exit(1);
  }
}

console.log(
  `Production font isolation validated: ${publicEntries.length} public routes, ${trackerEntries.length} tracker routes, zero tracker font preloads.`,
);
