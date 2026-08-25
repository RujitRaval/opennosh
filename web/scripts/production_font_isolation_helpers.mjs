export function routeCssFiles(manifestSource, routeRoot) {
  const assignment = manifestSource.match(/=\s*(\{.*\});\s*$/s);
  if (!assignment?.[1]) throw new Error("Client reference manifest is not valid JSON assignment output.");
  const manifest = JSON.parse(assignment[1]);
  const entries = Object.entries(manifest.entryCSSFiles ?? {})
    .filter(([entry]) => entry.startsWith(routeRoot));
  if (entries.length === 0) throw new Error(`Client reference manifest has no CSS entries for ${routeRoot}.`);
  return [...new Set(entries.flatMap(([, files]) =>
    files.map((file) => file.path),
  ))];
}
