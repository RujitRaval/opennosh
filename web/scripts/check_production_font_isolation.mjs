import { existsSync, readFileSync } from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";

const webRoot = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "..");
const manifestFile = path.join(webRoot, ".next/server/next-font-manifest.json");
const buildManifestFile = path.join(webRoot, "assets/fonts/v2/font-build.v2.json");

if (!existsSync(manifestFile) || !existsSync(buildManifestFile)) {
  console.error("Production font isolation check requires a completed Next.js build.");
  process.exit(1);
}

const manifest = JSON.parse(readFileSync(manifestFile, "utf8"));
const buildManifest = JSON.parse(readFileSync(buildManifestFile, "utf8"));
const appEntries = Object.entries(manifest.app ?? {});
const allFontHrefs = buildManifest.fonts.map(
  (font) => `/fonts/${buildManifest.assetVersion}/${path.basename(font.output)}`,
);
const criticalHrefs = buildManifest.fonts
  .filter((font) => font.delivery === "critical")
  .map((font) => `/fonts/${buildManifest.assetVersion}/${path.basename(font.output)}`);
const deferredHrefs = allFontHrefs.filter((href) => !criticalHrefs.includes(href));

const leakedNextFontEntries = appEntries.flatMap(([route, fonts]) =>
  fonts.filter((font) => allFontHrefs.some((href) => font.includes(path.basename(href))))
    .map((font) => ({ route, font })),
);
if (leakedNextFontEntries.length > 0) {
  console.error("Living Commons fonts must use the route-local static delivery contract:");
  for (const { route, font } of leakedNextFontEntries) console.error(`- ${route}: ${font}`);
  process.exit(1);
}

const trackerHtmlFiles = [
  path.join(webRoot, ".next/server/app/tracker.html"),
  path.join(webRoot, ".next/server/app/tracker/trends.html"),
];
for (const trackerHtml of trackerHtmlFiles) {
  if (!existsSync(trackerHtml)) {
    console.error(`Production build is missing ${path.relative(webRoot, trackerHtml)}.`);
    process.exit(1);
  }
  const html = readFileSync(trackerHtml, "utf8");
  if (allFontHrefs.some((href) => html.includes(href))) {
    console.error(`${path.relative(webRoot, trackerHtml)} references a public font asset.`);
    process.exit(1);
  }
  const stylesheetTags = [...html.matchAll(/<link\b[^>]*>/g)]
    .map((match) => match[0])
    .filter((tag) => tag.includes('rel="stylesheet"'));
  for (const tag of stylesheetTags) {
    const href = tag.match(/\bhref="([^"]+)"/)?.[1];
    if (!href?.startsWith("/_next/")) continue;
    const cssFile = path.join(
      webRoot,
      ".next",
      href.slice("/_next/".length).split("?")[0],
    );
    if (!existsSync(cssFile)) {
      console.error(`Production build is missing Tracker stylesheet ${href}.`);
      process.exit(1);
    }
    if (readFileSync(cssFile, "utf8").includes(`/fonts/${buildManifest.assetVersion}/`)) {
      console.error(`${path.relative(webRoot, trackerHtml)} links a public font stylesheet.`);
      process.exit(1);
    }
  }
}

const publicHtmlFile = path.join(webRoot, ".next/server/app/en.html");
if (!existsSync(publicHtmlFile)) {
  console.error("Production build is missing the default localized public document.");
  process.exit(1);
}
const publicHtml = readFileSync(publicHtmlFile, "utf8");
const preloadTags = [...publicHtml.matchAll(/<link\b[^>]*>/g)]
  .map((match) => match[0])
  .filter((tag) => tag.includes('rel="preload"') && tag.includes('as="font"'));
const publicFontPreloads = preloadTags.filter((tag) =>
  allFontHrefs.some((href) => tag.includes(`href="${href}"`)),
);
if (publicFontPreloads.length !== criticalHrefs.length) {
  console.error(`Default public route emitted ${publicFontPreloads.length} Living Commons font preloads; expected ${criticalHrefs.length}.`);
  process.exit(1);
}
for (const href of criticalHrefs) {
  if (!preloadTags.some((tag) => tag.includes(`href="${href}"`) && tag.includes('as="font"'))) {
    console.error(`Default public route does not preload critical font ${href}.`);
    process.exit(1);
  }
}
for (const href of deferredHrefs) {
  if (preloadTags.some((tag) => tag.includes(`href="${href}"`))) {
    console.error(`Default public route preloads deferred font ${href}.`);
    process.exit(1);
  }
}

console.log(
  `Production font isolation validated: ${criticalHrefs.length} public critical preloads, ${deferredHrefs.length} deferred faces, ${trackerHtmlFiles.length} Tracker documents with zero Living Commons font bytes.`,
);
