import assert from "node:assert/strict";
import { chmodSync, mkdirSync, mkdtempSync, readFileSync, realpathSync, rmSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { dirname, join } from "node:path";
import { spawnSync } from "node:child_process";
import test from "node:test";
import { fileURLToPath } from "node:url";

import {
  OpenNoshClient,
  OpenNoshProblem,
  PACKAGE_VERSION,
  normalizeTarget,
} from "../src/index.js";
import { PUBLIC_OPERATION_POLICIES } from "../src/generated-operation-policy.js";

function jsonResponse(body = { schema_version: "1.0" }, init = {}) {
  return new Response(JSON.stringify(body), {
    status: init.status ?? 200,
    headers: { "content-type": "application/json", ...init.headers },
  });
}

test("normalizes hosted, HTTPS, and exact loopback origins", () => {
  assert.equal(normalizeTarget(), "https://opennosh.org");
  assert.equal(normalizeTarget("hosted"), "https://opennosh.org");
  assert.equal(normalizeTarget("https://example.test/"), "https://example.test");
  assert.equal(normalizeTarget("http://localhost:8000"), "http://localhost:8000");
  assert.equal(normalizeTarget("http://127.0.0.1:8000/"), "http://127.0.0.1:8000");
  assert.equal(normalizeTarget("http://[::1]:8000"), "http://[::1]:8000");
});

test("rejects unsafe or non-origin targets", () => {
  for (const target of [
    "example.test",
    "ftp://example.test",
    "http://example.test",
    "http://localhost.example.test",
    "http://127.1",
    "http://2130706433",
    "https://example.test/path",
    "https://example.test/?query=1",
    "https://example.test/#fragment",
    " https://example.test",
  ]) {
    assert.throws(() => normalizeTarget(target), TypeError, target);
  }
  const targetWithUserInfo = new URL("https://example.test");
  targetWithUserInfo.username = "user";
  assert.throws(() => normalizeTarget(targetWithUserInfo.href), TypeError);
});

test("maps all ten public operations without credentials, redirects, retries, or hidden identifiers", async () => {
  const calls = [];
  const fetch = async (url, options) => {
    calls.push({ url: String(url), options });
    const path = new URL(url).pathname;
    if (path.endsWith("/provenance")) {
      return new Response("<main>verified provenance</main>", { headers: { "content-type": "text/html" } });
    }
    if (path.endsWith("/download")) {
      return new Response(new Uint8Array([80, 75, 3, 4]), { headers: { "content-type": "application/zip" } });
    }
    const mediaType = path.endsWith("/manifest") ? "application/vnd.opennosh.release+json" : "application/json";
    return new Response(JSON.stringify({ release_version: "1.2.3", attribution: "OpenNosh Commons", verification: "verified" }), {
      headers: { "content-type": mediaType, etag: '"digest"', "cache-control": "public, immutable" },
    });
  };
  const client = new OpenNoshClient({ target: "https://nosh.example", fetch });

  const results = await Promise.all([
    client.capabilities(),
    client.searchFoods({ q: "olive oil", locale: "en-US", pack: ["one", "two"], limit: 5, tracking: "must-not-send" }),
    client.getCommonsSnapshot({ ifNoneMatch: '"old"' }),
    client.getPublicFood({ source: "community", sourceId: "food-id", version: "2" }),
    client.listMissions({ limit: 12 }),
    client.getMissionActivity(),
    client.getReleaseFood({ releaseVersion: "1.2.3.4", source: "usda", sourceId: "42" }),
    client.getProvenance({ releaseVersion: "1.2.3.4", source: "usda", sourceId: "42" }),
    client.getReleaseManifest({ releaseVersion: "1.2.3.4" }),
    client.downloadPack({ releaseVersion: "1.2.3.4", packId: "core", packVersion: "4.5.6" }),
  ]);

  assert.equal(calls.length, 10);
  assert.deepEqual(calls.map(({ url }) => new URL(url).pathname), [
    "/api/v1/foods/capabilities",
    "/api/v1/foods/search",
    "/api/v1/public/commons-snapshot",
    "/api/v1/public/foods/community/food-id",
    "/api/v1/public/missions",
    "/api/v1/public/missions/activity",
    "/api/v1/public/releases/1.2.3.4/foods/usda/42",
    "/api/v1/public/releases/1.2.3.4/foods/usda/42/provenance",
    "/api/v1/public/releases/1.2.3.4/manifest",
    "/api/v1/public/releases/1.2.3.4/packs/core/4.5.6/download",
  ]);
  assert.deepEqual(new URL(calls[1].url).searchParams.getAll("pack"), ["one", "two"]);
  assert.equal(new URL(calls[1].url).searchParams.has("tracking"), false);
  assert.equal(new URL(calls[3].url).searchParams.get("version"), "2");
  assert.equal(new URL(calls[4].url).searchParams.get("limit"), "12");
  for (const { options } of calls) {
    assert.equal(options.method, "GET");
    assert.equal(options.credentials, "omit");
    assert.equal(options.redirect, "manual");
    assert.equal(options.headers.get("authorization"), null);
    assert.equal(options.headers.get("x-opennosh-client"), `js/${PACKAGE_VERSION}`);
    assert.equal("referrer" in options, false);
  }
  assert.equal(calls[2].options.headers.get("if-none-match"), '"old"');
  assert.equal(results[0].data.attribution, "OpenNosh Commons");
  assert.equal(results[0].data.verification, "verified");
  assert.equal(results[0].etag, '"digest"');
  assert.equal(results[0].cache_control, "public, immutable");
  assert.equal(results[7].data, "<main>verified provenance</main>");
  assert.deepEqual([...results[9].data], [80, 75, 3, 4]);
});

test("sends client identity only in Node and omits it in browser-worker globals", async () => {
  const previousProcess = globalThis.process;
  try {
    let headers;
    const client = new OpenNoshClient({
      fetch: async (_url, options) => {
        headers = options.headers;
        return jsonResponse();
      },
    });
    await client.capabilities();
    assert.equal(headers.get("x-opennosh-client"), `js/${PACKAGE_VERSION}`);

    globalThis.process = undefined;
    await client.capabilities();
    assert.equal(headers.get("x-opennosh-client"), null);
  } finally {
    globalThis.process = previousProcess;
  }
});

test("returns a typed 304 result with cache validators", async () => {
  const client = new OpenNoshClient({
    fetch: async () => new Response(null, { status: 304, headers: { etag: '"same"' } }),
  });
  const result = await client.getCommonsSnapshot();
  assert.equal(result.status, 304);
  assert.equal(result.data, null);
  assert.equal(result.etag, '"same"');
});

test("refuses redirects without following the target", async () => {
  let calls = 0;
  const client = new OpenNoshClient({
    fetch: async () => {
      calls += 1;
      return new Response(null, { status: 302, headers: { location: "https://attacker.example" } });
    },
  });
  await assert.rejects(client.capabilities(), (error) => {
    assert.equal(error.code, "redirect_refused");
    assert.equal(error.status, 302);
    return true;
  });
  assert.equal(calls, 1);

  const opaque = new OpenNoshClient({
    fetch: async () => ({ type: "opaqueredirect", status: 0, url: "", headers: new Headers(), ok: false }),
  });
  await assert.rejects(opaque.capabilities(), (error) => error.code === "redirect_refused");

  const followed = new OpenNoshClient({
    fetch: async () => {
      const response = jsonResponse();
      Object.defineProperty(response, "url", { value: "https://attacker.example/data" });
      return response;
    },
  });
  await assert.rejects(followed.capabilities(), (error) => error.code === "redirect_refused");
});

test("validates path values before URL dot-segment normalization", async () => {
  const client = new OpenNoshClient({ fetch: async () => { throw new Error("must not fetch"); } });
  await assert.rejects(client.getReleaseManifest(), /release_version is required/);
  await assert.rejects(client.getPublicFood({ source: "usda", sourceId: "" }), /source_id is required/);
  await assert.rejects(client.getReleaseManifest({ releaseVersion: ".." }), TypeError);
  await assert.rejects(client.getPublicFood({ source: "usda", sourceId: "." }), TypeError);
  await assert.rejects(client.getReleaseFood({ releaseVersion: "1.2.3.4", source: "federation", sourceId: "rice" }), TypeError);
  await assert.rejects(client.downloadPack({ releaseVersion: "1.2.3.4", packId: "..", packVersion: "1" }), TypeError);
});

test("maps valid RFC 9457 details and bounded Retry-After", async () => {
  const problem = {
    type: "https://opennosh.org/problems/rate-limited",
    title: "Rate limited",
    status: 429,
    detail: "Try again later.",
    code: "rate_limited",
    request_id: "00000000-0000-4000-8000-000000000000",
    recovery_actions: [{ id: "retry", label: "Retry" }],
  };
  const client = new OpenNoshClient({ fetch: async () => jsonResponse(problem, { status: 429, headers: { "content-type": "application/problem+json", "retry-after": "42" } }) });
  await assert.rejects(client.capabilities(), (error) => {
    assert.ok(error instanceof OpenNoshProblem);
    assert.equal(error.status, 429);
    assert.equal(error.code, "rate_limited");
    assert.equal(error.detail, "Try again later.");
    assert.equal(error.request_reference, problem.request_id);
    assert.deepEqual(error.recovery_actions, [{ id: "retry", label: "Retry" }]);
    assert.equal(error.retry_after_seconds, 42);
    return true;
  });
});

test("malformed failures never expose the raw response body", async () => {
  const secret = "upstream-secret-body";
  const client = new OpenNoshClient({
    fetch: async () => new Response(secret, { status: 502, headers: { "content-type": "text/plain", "retry-after": "86401", "x-request-id": "safe-reference" } }),
  });
  await assert.rejects(client.capabilities(), (error) => {
    assert.equal(error.code, "unexpected_response");
    assert.equal(error.request_reference, "safe-reference");
    assert.equal(error.retry_after_seconds, null);
    assert.doesNotMatch(error.message, new RegExp(secret));
    return true;
  });
});

test("rejects incomplete problem documents instead of upgrading them to typed problems", async () => {
  const incomplete = {
    status: 429,
    detail: "Missing required RFC 9457 fields.",
    code: "rate_limited",
    request_id: "00000000-0000-4000-8000-000000000000",
  };
  const client = new OpenNoshClient({ fetch: async () => jsonResponse(incomplete, { status: 429, headers: { "content-type": "application/problem+json" } }) });
  await assert.rejects(client.capabilities(), (error) => error.code === "unexpected_response");

  const extraRecoveryData = {
    type: "https://opennosh.org/problems/rate-limited",
    title: "Rate limited",
    status: 429,
    detail: "Try again later.",
    code: "rate_limited",
    request_id: "00000000-0000-4000-8000-000000000000",
    recovery_actions: [{ id: "retry", label: "Retry", secret: "must-not-expose" }],
  };
  const malformedRecovery = new OpenNoshClient({ fetch: async () => jsonResponse(extraRecoveryData, { status: 429, headers: { "content-type": "application/problem+json" } }) });
  await assert.rejects(malformedRecovery.capabilities(), (error) => {
    assert.equal(error.code, "unexpected_response");
    assert.doesNotMatch(JSON.stringify(error), /must-not-expose/);
    return true;
  });
});

test("requires problem media type and schema-safe relative recovery links", async () => {
  const base = {
    type: "https://opennosh.org/problems/rate-limited",
    title: "Rate limited",
    status: 429,
    detail: "Try again later.",
    code: "rate_limited",
    request_id: "00000000-0000-4000-8000-000000000000",
  };
  for (const recovery_actions of [
    [{ id: "retry", label: "Retry", href: "https://attacker.example" }],
    [{ id: "retry", label: "Retry", href: "javascript:alert(1)" }],
    [{ id: "retry", label: "Retry", href: "//attacker.example" }],
    [{ id: "retry", label: "Retry", href: "/safe\0unsafe" }],
    [{ id: "retry", label: "" }],
    [{ id: "retry", label: "x".repeat(121) }],
    Array.from({ length: 9 }, () => ({ id: "retry", label: "Retry" })),
  ]) {
    const client = new OpenNoshClient({
      fetch: async () => jsonResponse({ ...base, recovery_actions }, { status: 429, headers: { "content-type": "application/problem+json" } }),
    });
    await assert.rejects(client.capabilities(), (error) => error.code === "unexpected_response");
  }

  const wrongMedia = new OpenNoshClient({ fetch: async () => jsonResponse({ ...base, recovery_actions: [{ id: "retry", label: "Retry", href: "/safe" }] }, { status: 429 }) });
  await assert.rejects(wrongMedia.capabilities(), (error) => error.code === "unexpected_response");

  const unicodeLabel = "🍚".repeat(120);
  const unicodeProblem = new OpenNoshClient({
    fetch: async () => jsonResponse({ ...base, recovery_actions: [{ id: "retry", label: unicodeLabel, href: "/safe" }] }, { status: 429, headers: { "content-type": "application/problem+json" } }),
  });
  await assert.rejects(unicodeProblem.capabilities(), (error) => {
    assert.equal(error.code, "rate_limited");
    assert.equal(error.recovery_actions[0].label, unicodeLabel);
    return true;
  });
});

test("enforces the exact response limit for every public operation", async () => {
  const cases = [
    ["/api/v1/foods/capabilities", (client) => client.capabilities()],
    ["/api/v1/foods/search", (client) => client.searchFoods({ q: "rice" })],
    ["/api/v1/public/commons-snapshot", (client) => client.getCommonsSnapshot()],
    ["/api/v1/public/foods/{source}/{source_id}", (client) => client.getPublicFood({ source: "usda", sourceId: "rice" })],
    ["/api/v1/public/missions", (client) => client.listMissions()],
    ["/api/v1/public/missions/activity", (client) => client.getMissionActivity()],
    ["/api/v1/public/releases/{release_version}/foods/{source}/{source_id}", (client) => client.getReleaseFood({ releaseVersion: "1.2.3.4", source: "usda", sourceId: "rice" })],
    ["/api/v1/public/releases/{release_version}/foods/{source}/{source_id}/provenance", (client) => client.getProvenance({ releaseVersion: "1.2.3.4", source: "usda", sourceId: "rice" })],
    ["/api/v1/public/releases/{release_version}/manifest", (client) => client.getReleaseManifest({ releaseVersion: "1.2.3.4" })],
    ["/api/v1/public/releases/{release_version}/packs/{pack_id}/{pack_version}/download", (client) => client.downloadPack({ releaseVersion: "1.2.3.4", packId: "core", packVersion: "1" })],
  ];
  for (const [path, invoke] of cases) {
    const { maxResponseBytes: limit, mediaType: contentType } = PUBLIC_OPERATION_POLICIES[path];
    const body = contentType.includes("json") ? "{}" : contentType === "text/html" ? "x" : new Uint8Array([1]);
    const atLimit = new OpenNoshClient({
      fetch: async () => new Response(body, { headers: { "content-type": contentType, "content-length": String(limit) } }),
    });
    await invoke(atLimit);
    const overLimit = new OpenNoshClient({
      fetch: async () => new Response(body, { headers: { "content-type": contentType, "content-length": String(limit + 1) } }),
    });
    await assert.rejects(invoke(overLimit), (error) => error.code === "response_too_large");
  }
});

test("enforces media types, response sizes, and timeout ceilings", async () => {
  let wrongTypeCancelled = false;
  const wrongType = new OpenNoshClient({ fetch: async () => new Response(new ReadableStream({
    start(controller) { controller.enqueue(new TextEncoder().encode("{}")); },
    cancel() { wrongTypeCancelled = true; },
  }), { headers: { "content-type": "text/plain" } }) });
  await assert.rejects(wrongType.capabilities(), (error) => error.code === "unexpected_response");
  assert.equal(wrongTypeCancelled, true);

  let oversizedCancelled = false;
  const oversized = new OpenNoshClient({ fetch: async () => new Response(new ReadableStream({
    cancel() { oversizedCancelled = true; },
  }), { headers: { "content-type": "application/json", "content-length": "24577" } }) });
  await assert.rejects(oversized.getCommonsSnapshot(), (error) => error.code === "response_too_large");
  assert.equal(oversizedCancelled, true);
  const oversizedStream = new OpenNoshClient({
    fetch: async () => new Response(new Uint8Array(24_577), { headers: { "content-type": "application/json" } }),
  });
  await assert.rejects(oversizedStream.getCommonsSnapshot(), (error) => error.code === "response_too_large");

  const largePayload = JSON.stringify({ value: "x".repeat(70_000) });
  const largePayloadBytes = new TextEncoder().encode(largePayload);
  const growingBuffer = new OpenNoshClient({ fetch: async () => new Response(new ReadableStream({
    start(controller) {
      controller.enqueue(largePayloadBytes.subarray(0, 40_000));
      controller.enqueue(largePayloadBytes.subarray(40_000));
      controller.close();
    },
  }), { headers: { "content-type": "application/json" } }) });
  const grownResult = await growingBuffer.capabilities();
  assert.equal(grownResult.data.value.length, 70_000);

  const vendorPack = new OpenNoshClient({
    fetch: async () => new Response(new Uint8Array([80, 75]), {
      headers: { "content-type": "application/vnd.opennosh.pack+zip" },
    }),
  });
  const vendorResult = await vendorPack.downloadPack({
    releaseVersion: "1.2.3.4",
    packId: "core",
    packVersion: "1",
  });
  assert.deepEqual([...vendorResult.data], [80, 75]);

  const client = new OpenNoshClient({ fetch: async () => jsonResponse() });
  await assert.rejects(client.capabilities({ timeoutMs: 10_001 }), RangeError);
  await assert.rejects(client.downloadPack({ releaseVersion: "1.2.3.4", packId: "p", packVersion: "1" }, { timeoutMs: 30_001 }), RangeError);
});

test("propagates caller aborts and maps SDK deadlines", async () => {
  const pendingFetch = (_url, { signal }) => new Promise((_resolve, reject) => {
    signal.addEventListener("abort", () => reject(signal.reason), { once: true });
  });
  const client = new OpenNoshClient({ fetch: pendingFetch });
  const caller = new AbortController();
  const reason = new Error("caller stopped");
  const request = client.capabilities({ signal: caller.signal });
  caller.abort(reason);
  await assert.rejects(request, (error) => error === reason);

  await assert.rejects(client.capabilities({ timeoutMs: 5 }), (error) => {
    assert.equal(error.code, "request_timeout");
    assert.equal(error.status, 504);
    return true;
  });

  const slowBody = new OpenNoshClient({
    fetch: async (_url, { signal }) => {
      let streamController;
      const stream = new ReadableStream({ start(controller) { streamController = controller; } });
      signal.addEventListener("abort", () => streamController.error(signal.reason), { once: true });
      return new Response(stream, { headers: { "content-type": "application/json" } });
    },
  });
  await assert.rejects(slowBody.capabilities({ timeoutMs: 5 }), (error) => error.code === "request_timeout");

  let cancelled = false;
  const callerBody = new OpenNoshClient({
    fetch: async () => new Response(new ReadableStream({
      pull() { return new Promise(() => {}); },
      cancel() { cancelled = true; },
    }), { headers: { "content-type": "application/json" } }),
  });
  const bodyController = new AbortController();
  const bodyReason = new Error("stop body");
  const bodyRequest = callerBody.capabilities({ signal: bodyController.signal });
  await new Promise((resolve) => setTimeout(resolve, 0));
  bodyController.abort(bodyReason);
  await assert.rejects(bodyRequest, (error) => error === bodyReason);
  assert.equal(cancelled, true);
});

test("maps post-header body failures and preserves typed size errors when cancellation fails", async () => {
  const broken = new OpenNoshClient({
    fetch: async () => new Response(new ReadableStream({ start(controller) { controller.error(new Error("raw stream failure")); } }), { headers: { "content-type": "application/json" } }),
  });
  await assert.rejects(broken.capabilities(), (error) => {
    assert.equal(error.code, "network_error");
    assert.doesNotMatch(error.message, /raw stream failure/);
    return true;
  });

  const cancelFailure = new OpenNoshClient({
    fetch: async () => ({
      status: 200,
      ok: true,
      type: "basic",
      url: "",
      headers: new Headers({ "content-type": "application/json" }),
      body: new ReadableStream({
        start(controller) { controller.enqueue(new Uint8Array(24_577)); },
        cancel() { throw new Error("cancel failed"); },
      }),
    }),
  });
  await assert.rejects(cancelFailure.getCommonsSnapshot(), (error) => error.code === "response_too_large");
});

test("maps malformed encodings, invalid JSON, and fetch failures without leaking raw details", async () => {
  const invalidUtf8 = new Uint8Array([0xc3, 0x28]);
  const malformedProblem = new OpenNoshClient({
    fetch: async () => new Response(invalidUtf8, {
      status: 502,
      headers: { "content-type": "application/problem+json" },
    }),
  });
  await assert.rejects(malformedProblem.capabilities(), (error) => error.code === "unexpected_response");

  const malformedSuccess = new OpenNoshClient({
    fetch: async () => new Response(invalidUtf8, { headers: { "content-type": "application/json" } }),
  });
  await assert.rejects(malformedSuccess.capabilities(), (error) => error.code === "unexpected_response");

  const invalidJson = new OpenNoshClient({
    fetch: async () => new Response("{", { headers: { "content-type": "application/json" } }),
  });
  await assert.rejects(invalidJson.capabilities(), (error) => error.code === "unexpected_response");

  const networkSecret = "private socket failure";
  const rejectedFetch = new OpenNoshClient({ fetch: async () => { throw new Error(networkSecret); } });
  await assert.rejects(rejectedFetch.capabilities(), (error) => {
    assert.equal(error.code, "network_error");
    assert.doesNotMatch(error.message, new RegExp(networkSecret));
    return true;
  });
});

test("packed artifact exposes the ESM client and preserves the bootstrap binary", () => {
  const packageRoot = dirname(dirname(fileURLToPath(import.meta.url)));
  const directory = mkdtempSync(join(tmpdir(), "opennosh-sdk-pack-"));
  try {
    const packed = spawnSync("npm", ["pack", "--json", "--pack-destination", directory], {
      cwd: packageRoot,
      encoding: "utf8",
      env: { ...process.env, npm_config_cache: join(directory, "npm-cache") },
    });
    assert.equal(packed.status, 0, packed.stderr);
    const [{ filename }] = JSON.parse(packed.stdout);
    const install = spawnSync("npm", ["install", "--ignore-scripts", join(directory, filename)], {
      cwd: directory,
      encoding: "utf8",
      env: { ...process.env, npm_config_cache: join(directory, "npm-cache") },
    });
    assert.equal(install.status, 0, install.stderr);
    const imported = spawnSync(process.execPath, ["--input-type=module", "-e", "import {OpenNoshClient} from 'opennosh'; console.log(new OpenNoshClient().origin)"], {
      cwd: directory,
      encoding: "utf8",
    });
    assert.equal(imported.status, 0, imported.stderr);
    assert.equal(imported.stdout, "https://opennosh.org\n");

    const typeConsumer = join(directory, "consumer.ts");
    writeFileSync(typeConsumer, `
      import { OpenNoshClient, OpenNoshProblem, type PublicFoodRecordResponse } from "opennosh";
      const client = new OpenNoshClient("hosted");
      const result: Promise<{ data: PublicFoodRecordResponse }> = client.getPublicFood({ source: "community", sourceId: "lentils" });
      void client.searchFoods({ q: "lentils", source: "federation" });
      void result;
      void OpenNoshProblem;
    `);
    const typecheck = spawnSync(join(packageRoot, "node_modules", ".bin", "tsc"), [
      "--noEmit", "--strict", "--target", "ES2022", "--module", "NodeNext",
      "--moduleResolution", "NodeNext", "--lib", "ES2022,DOM", typeConsumer,
    ], { cwd: directory, encoding: "utf8" });
    assert.equal(typecheck.status, 0, typecheck.stderr || typecheck.stdout || "TypeScript compiler did not exit successfully");

    const binary = spawnSync(join(directory, "node_modules", ".bin", "opennosh"), ["--version"], { cwd: directory, encoding: "utf8" });
    assert.equal(binary.status, 0, binary.stderr);
    assert.equal(binary.stdout, `${PACKAGE_VERSION}\n`);

    const fakeBin = join(directory, "fake-bin");
    const gitArguments = join(directory, "git-arguments.txt");
    mkdirSync(fakeBin);
    const fakeGit = join(fakeBin, "git");
    writeFileSync(fakeGit, '#!/bin/sh\nif [ "$1" = "--version" ]; then exit 0; fi\nprintf "%s\\n" "$@" > "$OPENNOSH_GIT_ARGUMENTS"\n');
    chmodSync(fakeGit, 0o755);
    const initialized = spawnSync("npx", ["--no-install", "opennosh", "init", "sdk-checkout"], {
      cwd: directory,
      encoding: "utf8",
      env: {
        ...process.env,
        PATH: `${fakeBin}:${process.env.PATH}`,
        OPENNOSH_GIT_ARGUMENTS: gitArguments,
        npm_config_cache: join(directory, "npm-cache"),
      },
    });
    assert.equal(initialized.status, 0, initialized.stderr);
    assert.match(initialized.stdout, /Next: cd sdk-checkout/);
    assert.deepEqual(readFileSync(gitArguments, "utf8").trim().split("\n"), [
      "clone",
      "--depth",
      "1",
      "--single-branch",
      "https://github.com/RujitRaval/opennosh.git",
      join(realpathSync(directory), "sdk-checkout"),
    ]);
  } finally {
    rmSync(directory, { recursive: true, force: true });
  }
});
