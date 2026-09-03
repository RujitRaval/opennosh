import { pathToFileURL } from "node:url";

import { OpenNoshClient } from "opennosh";

const releasePattern = /^[0-9]+\.[0-9]+\.[0-9]+\.[0-9]+$/;

export async function runPublicRead({
  target = process.env.OPENNOSH_TARGET || "hosted",
  query = process.env.OPENNOSH_QUERY || "rajma",
  client = new OpenNoshClient(target),
} = {}) {
  const search = await client.searchFoods({ q: query, limit: 1 });
  const match = search.data.items[0];
  if (!match) throw new Error("No public food matched the starter query.");

  const detail = await client.getPublicFood({
    source: match.source,
    sourceId: match.source_id,
  });
  const food = detail.data;
  const { attribution } = food.record;
  const releaseVersion = food.release.release_version;
  const expectedPath =
    `/api/v1/public/releases/${releaseVersion}/foods/${match.source}/${match.source_id}`;
  if (
    !["verified", "stale"].includes(food.release.state)
    || !releasePattern.test(releaseVersion)
    || food.record.source !== match.source
    || food.record.source_id !== match.source_id
    || food.immutable_url !== expectedPath
    || food.provenance_url !== `${expectedPath}/provenance`
    || typeof attribution.license !== "string"
    || !attribution.license.trim()
  ) {
    throw new Error("The public detail did not contain bound publication proof.");
  }

  return {
    schema_version: "1.0",
    state: food.release.state === "stale" ? "stale_verified" : "verified",
    food: {
      attribution:
        attribution.contributed_by || attribution.pack_id || attribution.source,
      license: attribution.license,
      name: food.record.name,
      provenance_url: food.provenance_url,
      release_version: releaseVersion,
      source: `${food.record.source}:${food.record.source_id}`,
    },
  };
}

async function main() {
  try {
    process.stdout.write(`${JSON.stringify(await runPublicRead())}\n`);
  } catch (error) {
    const code = typeof error?.code === "string" ? error.code : "unavailable";
    process.stderr.write(`opennosh starter failed: ${code}\n`);
    process.exitCode = 1;
  }
}

if (process.argv[1] && import.meta.url === pathToFileURL(process.argv[1]).href) await main();
