import { expect, test } from "@playwright/test";
import { readFileSync } from "node:fs";
import { join } from "node:path";

const packageSource = join(process.cwd(), "../packages/npm/src");
const sdkSource = readFileSync(join(packageSource, "index.js"), "utf8");
const problemContract = readFileSync(
  join(packageSource, "generated-problem-contract.js"),
  "utf8",
);
const operationPolicy = readFileSync(
  join(packageSource, "generated-operation-policy.js"),
  "utf8",
);

test("npm SDK runs through the hosted proxy CORS policy without ambient credentials or client identity", async ({ page, context, baseURL }) => {
  expect(baseURL).toBeTruthy();
  const consumerUrl = new URL(baseURL!);
  consumerUrl.port = String(Number(consumerUrl.port) + 1_000);
  const consumerOrigin = consumerUrl.origin;
  await context.grantPermissions(["local-network-access"], { origin: consumerOrigin });
  let apiHeaders: Record<string, string> = {};
  const requestFailures: string[] = [];
  const browserMessages: string[] = [];
  await page.route(`${consumerOrigin}/**`, async (route) => {
    const path = new URL(route.request().url()).pathname;
    if (path === "/index.js") {
      await route.fulfill({ body: sdkSource, contentType: "application/javascript" });
    } else if (path === "/generated-problem-contract.js") {
      await route.fulfill({ body: problemContract, contentType: "application/javascript" });
    } else if (path === "/generated-operation-policy.js") {
      await route.fulfill({ body: operationPolicy, contentType: "application/javascript" });
    } else {
      await route.fulfill({ body: "<!doctype html><title>SDK test</title>", contentType: "text/html" });
    }
  });
  page.on("request", (request) => {
    if (request.url().startsWith(`${baseURL}/api/v1/`)) apiHeaders = request.headers();
  });
  page.on("requestfailed", (request) => {
    requestFailures.push(`${request.url()}: ${request.failure()?.errorText ?? "unknown failure"}`);
  });
  page.on("console", (message) => browserMessages.push(message.text()));

  await page.goto(`${consumerOrigin}/`);
  const outcome = await page.evaluate(async (target) => {
    try {
      const sdkModuleUrl = "/index.js";
      const { OpenNoshClient } = await import(sdkModuleUrl);
      const client = new OpenNoshClient(target);
      const food = await client.getPublicFood({
        source: "community",
        sourceId: "rajma-masala",
      });
      const snapshot = await client.getCommonsSnapshot({ ifNoneMatch: '"browser-etag"' });
      return { data: { food: food.data, snapshot: snapshot.data } };
    } catch (error) {
      return { error: String(error) };
    }
  }, baseURL!);

  expect(outcome.error, [...browserMessages, ...requestFailures].join("\n")).toBeUndefined();
  expect(outcome.data).toMatchObject({
    food: { schema_version: "1.0", record: { source_id: "rajma-masala" } },
    snapshot: { schema_version: "1" },
  });
  expect(apiHeaders.cookie).toBeUndefined();
  expect(apiHeaders.authorization).toBeUndefined();
  expect(apiHeaders["x-opennosh-client"]).toBeUndefined();
  expect(apiHeaders["if-none-match"]).toBe('"browser-etag"');
  expect(apiHeaders.referer).toBe(`${consumerOrigin}/`);
});
