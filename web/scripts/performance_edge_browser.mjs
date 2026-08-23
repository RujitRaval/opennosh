import { createInterface } from "node:readline";
import { createRequire } from "node:module";

const require = createRequire(new URL("../package.json", import.meta.url));
const { chromium } = require("playwright");

function fail(message) {
  process.stderr.write(String(message) + "\n");
  process.exitCode = 1;
}

async function interaction(page, query, cold) {
  return page.evaluate(
    async ({ queryDefinition, bypassCache }) => {
      const started = performance.now();
      const params = { ...queryDefinition.params };
      const pages = Number(params.pages ?? 1);
      delete params.pages;
      if (queryDefinition.query) params.q = queryDefinition.query;
      const headers = bypassCache ? { "Cache-Control": "no-cache" } : {};
      const request = async () => {
        const url = new URL(queryDefinition.path, location.origin);
        for (const [key, value] of Object.entries(params)) {
          url.searchParams.set(key, String(value));
        }
        const controller = new AbortController();
        const timeout = setTimeout(() => controller.abort(), 15_000);
        try {
          return await fetch(url, {
            headers,
            redirect: "manual",
            signal: controller.signal,
          });
        } finally {
          clearTimeout(timeout);
        }
      };
      const parseObject = async (response) => {
        const value = await response.json();
        if (value === null || Array.isArray(value) || typeof value !== "object") {
          throw new Error("malformed_response");
        }
        return value;
      };
      try {
        let response = await request();
        let payload = null;
        if (response.status === 200) {
          try {
            payload = await parseObject(response);
          } catch {
            throw new Error("malformed_response");
          }
        }
        const rule = queryDefinition.relevance;
        let relevant = false;
        if (rule.mode === "status") {
          relevant = response.status === Number(rule.expected);
        } else if (response.status === 200 && rule.mode === "empty") {
          relevant = Array.isArray(payload.items) && payload.items.length === 0;
        } else if (response.status === 200) {
          relevant = JSON.stringify(payload)
            .toLocaleLowerCase()
            .includes(String(rule.expected).toLocaleLowerCase());
        }
        let requestFailed = response.status < 200 || response.status >= 300;
        let cursor = payload?.next_cursor ?? null;
        for (let pageNumber = 1; pageNumber < pages && cursor; pageNumber += 1) {
          params.cursor = cursor;
          response = await request();
          if (response.status !== 200) {
            requestFailed = true;
            relevant = false;
            break;
          }
          try {
            payload = await parseObject(response);
          } catch {
            throw new Error("malformed_response");
          }
          cursor = payload.next_cursor ?? null;
        }
        return {
          latency_ms: performance.now() - started,
          error: requestFailed,
          timeout: false,
          relevant,
          error_code: requestFailed ? "http_" + response.status : null,
        };
      } catch (error) {
        const timedOut = error instanceof DOMException && error.name === "AbortError";
        return {
          latency_ms: performance.now() - started,
          error: true,
          timeout: timedOut,
          relevant: false,
          error_code: timedOut
            ? "timeout"
            : error instanceof Error && error.message === "malformed_response"
              ? "malformed_response"
              : "browser_request_error",
        };
      }
    },
    { queryDefinition: query, bypassCache: cold },
  );
}

async function main() {
  const lines = createInterface({ input: process.stdin, crlfDelay: Infinity });
  const iterator = lines[Symbol.asyncIterator]();
  const first = await iterator.next();
  if (first.done) throw new Error("missing runner configuration");
  const configuration = JSON.parse(first.value);
  if (
    typeof configuration.base_url !== "string" ||
    !["cold", "warm"].includes(configuration.cache_state) ||
    !Number.isInteger(configuration.concurrency) ||
    configuration.concurrency < 1 ||
    !Array.isArray(configuration.query_mix) ||
    !Array.isArray(configuration.schedule)
  ) {
    throw new Error("invalid runner configuration");
  }
  const queries = new Map(configuration.query_mix.map((query) => [query.id, query]));
  const schedule = configuration.schedule.map((queryId) => {
    const query = queries.get(queryId);
    if (!query) throw new Error("unknown scheduled query: " + queryId);
    return query;
  });

  const browser = await chromium.launch({ headless: true });
  try {
    const context = await browser.newContext();
    const workerCount = Math.min(configuration.concurrency, schedule.length);
    const pages = await Promise.all(
      Array.from({ length: workerCount }, async () => {
        const page = await context.newPage();
        await page.goto(configuration.base_url, {
          waitUntil: "domcontentloaded",
          timeout: 15_000,
        });
        return page;
      }),
    );
    if (configuration.cache_state === "warm") {
      for (const query of configuration.query_mix) {
        await interaction(pages[0], query, false);
      }
    }

    process.stderr.write("READY\n");
    const second = await iterator.next();
    if (second.done || second.value !== "START") {
      throw new Error("missing synchronized START signal");
    }
    let nextIndex = 0;
    const samples = [];
    const started = performance.now();
    await Promise.all(
      pages.map(async (page) => {
        while (nextIndex < schedule.length) {
          const index = nextIndex;
          nextIndex += 1;
          samples[index] = await interaction(
            page,
            schedule[index],
            configuration.cache_state === "cold",
          );
        }
      }),
    );
    process.stdout.write(JSON.stringify({
      elapsed_ms: performance.now() - started,
      samples,
    }) + "\n");
    await context.close();
  } finally {
    await browser.close();
  }
}

main().catch((error) => {
  fail(error instanceof Error ? error.stack ?? error.message : String(error));
});
