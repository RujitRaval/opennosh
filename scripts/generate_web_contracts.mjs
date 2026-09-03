import { createHash } from "node:crypto";
import { copyFile, readFile, readdir, writeFile } from "node:fs/promises";
import { spawnSync } from "node:child_process";

const inputPath = new URL("../web/lib/generated/openapi.json", import.meta.url);
const manifestPath = new URL("../web/lib/generated/manifest.json", import.meta.url);
const compatibilityPath = new URL("../config/developer-compatibility.v1.json", import.meta.url);
const packagePath = new URL("../web/node_modules/@hey-api/openapi-ts/package.json", import.meta.url);
const webDirectory = new URL("../web/", import.meta.url);
const clientDirectory = new URL("../web/lib/generated/client/", import.meta.url);
const npmGeneratedTypesPath = new URL("../packages/npm/src/generated-types.d.ts", import.meta.url);
const npmProblemContractPath = new URL("../packages/npm/src/generated-problem-contract.js", import.meta.url);
const npmOperationPolicyPath = new URL("../packages/npm/src/generated-operation-policy.js", import.meta.url);

async function generatedFiles(directory, prefix = "") {
  const entries = await readdir(directory, { withFileTypes: true });
  const files = [];
  for (const entry of entries) {
    const relative = `${prefix}${entry.name}`;
    if (entry.isDirectory()) {
      files.push(...(await generatedFiles(new URL(`${entry.name}/`, directory), `${relative}/`)));
    } else if (entry.isFile()) {
      files.push(relative);
    }
  }
  return files.sort();
}

const result = spawnSync(
  process.execPath,
  ["node_modules/@hey-api/openapi-ts/bin/run.js", "-f", "openapi-ts.config.ts"],
  { cwd: webDirectory, encoding: "utf8", stdio: "inherit" },
);
if (result.error) throw result.error;
if (result.status !== 0) process.exit(result.status ?? 1);

await copyFile(new URL("types.gen.ts", clientDirectory), npmGeneratedTypesPath);

const input = await readFile(inputPath);
const compatibility = JSON.parse(await readFile(compatibilityPath, "utf8"));
const generator = JSON.parse(await readFile(packagePath, "utf8"));
const contract = JSON.parse(input.toString("utf8"));
const problemSchemas = Object.fromEntries(
  ["FieldError", "LatestStateReference", "ProblemCode", "ProblemDetails", "RecoveryAction"].map(
    (name) => [name, contract.components.schemas[name]],
  ),
);
await writeFile(
  npmProblemContractPath,
  `// Generated from web/lib/generated/openapi.json. Do not edit.\nexport const PROBLEM_SCHEMAS = Object.freeze(${JSON.stringify(problemSchemas, null, 2)});\n`,
);
const operationPolicies = Object.fromEntries(compatibility.public_operations.map((operation) => {
  const openApiOperation = contract.paths[operation.path].get;
  const parameters = openApiOperation.parameters ?? [];
  const pathParameters = Object.fromEntries(
    parameters.filter((parameter) => parameter.in === "path").map((parameter) => [parameter.name, parameter.schema]),
  );
  return [operation.path, {
    acceptedMediaTypes: Object.keys(openApiOperation.responses["200"].content ?? {}).sort(),
    mediaType: operation.media_type,
    maxResponseBytes: operation.max_response_bytes,
    pathParameters,
  }];
}));
await writeFile(
  npmOperationPolicyPath,
  `// Generated from the developer compatibility manifest and OpenAPI. Do not edit.\nexport const PUBLIC_OPERATION_POLICIES = Object.freeze(${JSON.stringify(operationPolicies, null, 2)});\n`,
);
const clientFiles = await generatedFiles(clientDirectory);
const clientDigest = createHash("sha256");
for (const relative of clientFiles) {
  clientDigest.update(relative);
  clientDigest.update("\0");
  clientDigest.update(await readFile(new URL(relative, clientDirectory)));
  clientDigest.update("\0");
}
const operations = Object.values(contract.paths).flatMap((path) =>
  Object.entries(path).filter(([method]) =>
    ["delete", "get", "patch", "post", "put"].includes(method),
  ),
);
const manifest = {
  contract_version: contract.info["x-opennosh-contract-version"],
  generator: "@hey-api/openapi-ts",
  generator_version: generator.version,
  input_sha256: createHash("sha256").update(input).digest("hex"),
  operation_count: operations.length,
  client_files: clientFiles,
  client_sha256: clientDigest.digest("hex"),
};
await writeFile(manifestPath, JSON.stringify(manifest, null, 2) + "\n");
