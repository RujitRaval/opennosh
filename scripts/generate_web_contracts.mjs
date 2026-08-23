import { createHash } from "node:crypto";
import { readFile, writeFile } from "node:fs/promises";
import { spawnSync } from "node:child_process";

const inputPath = new URL("../web/lib/generated/openapi.json", import.meta.url);
const manifestPath = new URL("../web/lib/generated/manifest.json", import.meta.url);
const packagePath = new URL("../web/node_modules/@hey-api/openapi-ts/package.json", import.meta.url);
const webDirectory = new URL("../web/", import.meta.url);

const result = spawnSync(
  process.execPath,
  ["node_modules/@hey-api/openapi-ts/bin/run.js", "-f", "openapi-ts.config.ts"],
  { cwd: webDirectory, encoding: "utf8", stdio: "inherit" },
);
if (result.error) throw result.error;
if (result.status !== 0) process.exit(result.status ?? 1);

const input = await readFile(inputPath);
const generator = JSON.parse(await readFile(packagePath, "utf8"));
const contract = JSON.parse(input.toString("utf8"));
const manifest = {
  contract_version: contract.info["x-opennosh-contract-version"],
  generator: "@hey-api/openapi-ts",
  generator_version: generator.version,
  input_sha256: createHash("sha256").update(input).digest("hex"),
};
await writeFile(manifestPath, JSON.stringify(manifest, null, 2) + "\n");
