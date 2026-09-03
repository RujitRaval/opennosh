import { PROBLEM_SCHEMAS } from "./generated-problem-contract.js";
import { PUBLIC_OPERATION_POLICIES } from "./generated-operation-policy.js";

const HOSTED_ORIGIN = "https://opennosh.org";
const JSON_TIMEOUT_MS = 10_000;
const DOWNLOAD_TIMEOUT_MS = 30_000;
const ERROR_BODY_LIMIT = 65_536;

function operationPolicy(path, extras = {}) {
  const generated = PUBLIC_OPERATION_POLICIES[path];
  if (!generated) throw new Error(`Missing generated operation policy for ${path}`);
  return Object.freeze({
    path,
    acceptedMediaTypes: generated.acceptedMediaTypes,
    limit: generated.maxResponseBytes,
    mediaType: generated.mediaType,
    pathParameters: generated.pathParameters,
    ...extras,
  });
}

const POLICY = Object.freeze({
  capabilities: operationPolicy("/api/v1/foods/capabilities"),
  searchFoods: operationPolicy("/api/v1/foods/search"),
  getCommonsSnapshot: operationPolicy("/api/v1/public/commons-snapshot"),
  getPublicFood: operationPolicy("/api/v1/public/foods/{source}/{source_id}"),
  listMissions: operationPolicy("/api/v1/public/missions"),
  getMissionActivity: operationPolicy("/api/v1/public/missions/activity"),
  getReleaseFood: operationPolicy("/api/v1/public/releases/{release_version}/foods/{source}/{source_id}"),
  getProvenance: operationPolicy("/api/v1/public/releases/{release_version}/foods/{source}/{source_id}/provenance"),
  getReleaseManifest: operationPolicy("/api/v1/public/releases/{release_version}/manifest"),
  downloadPack: operationPolicy("/api/v1/public/releases/{release_version}/packs/{pack_id}/{pack_version}/download", { timeoutMs: DOWNLOAD_TIMEOUT_MS, binary: true }),
});

export const PACKAGE_VERSION = "0.86.0";

export class OpenNoshProblem extends Error {
  constructor(status, code, detail, requestReference = null, recoveryActions = [], retryAfterSeconds = null) {
    super(detail);
    this.name = "OpenNoshProblem";
    this.status = status;
    this.code = code;
    this.detail = detail;
    this.request_reference = requestReference;
    this.recovery_actions = recoveryActions;
    this.retry_after_seconds = retryAfterSeconds;
  }
}

export function normalizeTarget(target = "hosted") {
  if (target === "hosted") return HOSTED_ORIGIN;
  if (typeof target !== "string" || target.length === 0 || target !== target.trim()) {
    throw new TypeError("target must be 'hosted' or an absolute HTTP(S) origin");
  }

  let url;
  try {
    url = new URL(target);
  } catch {
    throw new TypeError("target must be 'hosted' or an absolute HTTP(S) origin");
  }
  if (!['https:', 'http:'].includes(url.protocol)) {
    throw new TypeError("target must use HTTPS, or HTTP for an exact loopback host");
  }
  if (url.username || url.password) throw new TypeError("target must not include user information");
  if (url.pathname !== "/" || url.search || url.hash) {
    throw new TypeError("target must be an origin without a path, query, or fragment");
  }
  if (url.protocol === "http:") {
    const authority = target.slice("http://".length).replace(/\/$/, "").toLowerCase();
    const exactLoopback = /^(localhost|127\.0\.0\.1|\[::1\])(?::[0-9]+)?$/.test(authority);
    if (!exactLoopback) {
      throw new TypeError("plaintext HTTP is allowed only for localhost, 127.0.0.1, or [::1]");
    }
  }
  return url.origin;
}

function appendQuery(url, query) {
  for (const [key, value] of Object.entries(query)) {
    if (value === undefined || value === null) continue;
    if (Array.isArray(value)) {
      for (const item of value) url.searchParams.append(key, String(item));
    } else {
      url.searchParams.set(key, String(value));
    }
  }
}

function fillPath(template, values, schemas) {
  return template.replace(/\{([^}]+)\}/g, (_match, name) => {
    const value = values[name];
    if (value === undefined || value === null || value === "") {
      throw new TypeError(`${name} is required`);
    }
    if (!matchesSchema(value, schemas[name])) throw new TypeError(`${name} is invalid`);
    return encodeURIComponent(String(value));
  });
}

function boundedTimeout(value, maximum) {
  if (value === undefined) return maximum;
  if (!Number.isFinite(value) || value <= 0 || value > maximum) {
    throw new RangeError(`timeoutMs must be greater than 0 and at most ${maximum}`);
  }
  return value;
}

function requestSignal(callerSignal, timeoutMs) {
  const controller = new AbortController();
  const timeoutReason = new DOMException("OpenNosh request timed out", "TimeoutError");
  const timer = setTimeout(() => controller.abort(timeoutReason), timeoutMs);
  const abort = () => controller.abort(callerSignal.reason);
  if (callerSignal) {
    if (callerSignal.aborted) abort();
    else callerSignal.addEventListener("abort", abort, { once: true });
  }
  return {
    signal: controller.signal,
    timeoutReason,
    cleanup() {
      clearTimeout(timer);
      callerSignal?.removeEventListener("abort", abort);
    },
  };
}

function parseRetryAfter(value) {
  if (!value || !/^[0-9]+$/.test(value)) return null;
  const seconds = Number(value);
  return seconds >= 1 && seconds <= 86_400 ? seconds : null;
}

async function cancelBody(response) {
  await response.body?.cancel().catch(() => undefined);
}

async function readLimited(response, limit, signal) {
  const declared = response.headers.get("content-length");
  if (declared !== null && (!/^[0-9]+$/.test(declared) || Number(declared) > limit)) {
    await cancelBody(response);
    throw new OpenNoshProblem(response.status, "response_too_large", `Response exceeds the ${limit}-byte limit.`);
  }
  if (!response.body) return new Uint8Array();
  const reader = response.body.getReader();
  const cancel = () => { void reader.cancel(signal.reason).catch(() => undefined); };
  if (signal) signal.addEventListener("abort", cancel, { once: true });
  const declaredSize = declared === null ? null : Number(declared);
  let bytes = new Uint8Array(declaredSize ?? Math.min(limit, 65_536));
  let size = 0;
  try {
    while (true) {
      const { done, value } = await reader.read();
      if (done) break;
      size += value.byteLength;
      if (size > limit) {
        await reader.cancel().catch(() => undefined);
        throw new OpenNoshProblem(response.status, "response_too_large", `Response exceeds the ${limit}-byte limit.`);
      }
      if (size > bytes.byteLength) {
        const capacity = Math.min(limit, Math.max(size, Math.max(1, bytes.byteLength * 2)));
        const grown = new Uint8Array(capacity);
        grown.set(bytes);
        bytes = grown;
      }
      bytes.set(value, size - value.byteLength);
    }
    if (signal?.aborted) throw signal.reason;
  } finally {
    signal?.removeEventListener("abort", cancel);
  }
  return bytes.subarray(0, size);
}

function responseMetadata(response, data) {
  return {
    data,
    status: response.status,
    url: response.url || null,
    etag: response.headers.get("etag"),
    last_modified: response.headers.get("last-modified"),
    cache_control: response.headers.get("cache-control"),
    content_type: response.headers.get("content-type"),
  };
}

function mediaType(response) {
  return (response.headers.get("content-type") || "").split(";", 1)[0].trim().toLowerCase();
}

function dereference(schema) {
  if (!schema?.$ref) return schema;
  return PROBLEM_SCHEMAS[schema.$ref.split("/").at(-1)];
}

function matchesSchema(value, originalSchema) {
  const schema = dereference(originalSchema);
  if (!schema) return false;
  if (schema.anyOf) return schema.anyOf.some((candidate) => matchesSchema(value, candidate));
  if (schema.const !== undefined && value !== schema.const) return false;
  if (schema.enum && !schema.enum.includes(value)) return false;
  if (schema.type === "null") return value === null;
  if (schema.type === "integer") {
    return Number.isInteger(value) && (schema.minimum === undefined || value >= schema.minimum)
      && (schema.maximum === undefined || value <= schema.maximum);
  }
  if (schema.type === "string") {
    if (typeof value !== "string") return false;
    const codePoints = [...value].length;
    return (schema.minLength === undefined || codePoints >= schema.minLength)
      && (schema.maxLength === undefined || codePoints <= schema.maxLength)
      && (schema.pattern === undefined || new RegExp(schema.pattern, "u").test(value));
  }
  if (schema.type === "array") {
    return Array.isArray(value) && (schema.maxItems === undefined || value.length <= schema.maxItems)
      && value.every((item) => matchesSchema(item, schema.items));
  }
  if (schema.type === "object") {
    if (!value || typeof value !== "object" || Array.isArray(value)) return false;
    const properties = schema.properties ?? {};
    if (!(schema.required ?? []).every((name) => Object.hasOwn(value, name))) return false;
    if (schema.additionalProperties === false && !Object.keys(value).every((name) => Object.hasOwn(properties, name))) return false;
    return Object.entries(value).every(([name, item]) => !properties[name] || matchesSchema(item, properties[name]));
  }
  return false;
}

function isProblem(body, status) {
  return matchesSchema(body, PROBLEM_SCHEMAS.ProblemDetails) && body.status === status;
}

async function problemFromResponse(response, signal) {
  const retryAfter = parseRetryAfter(response.headers.get("retry-after"));
  if (mediaType(response) !== "application/problem+json") {
    await cancelBody(response);
    return new OpenNoshProblem(
      response.status,
      "unexpected_response",
      `OpenNosh returned HTTP ${response.status} without a valid problem document.`,
      response.headers.get("x-request-id"),
      [],
      retryAfter,
    );
  }
  let body = null;
  const bytes = await readLimited(response, ERROR_BODY_LIMIT, signal);
  try {
    body = JSON.parse(new TextDecoder("utf-8", { fatal: true }).decode(bytes));
  } catch {
    body = null;
  }
  if (isProblem(body, response.status)) {
    const recoveryActions = (body.recovery_actions ?? []).map(({ id, label, href }) =>
      href === undefined ? { id, label } : { id, label, href });
    return new OpenNoshProblem(
      response.status,
      body.code,
      body.detail,
      body.request_id,
      recoveryActions,
      retryAfter,
    );
  }
  return new OpenNoshProblem(
    response.status,
    "unexpected_response",
    `OpenNosh returned HTTP ${response.status} without a valid problem document.`,
    response.headers.get("x-request-id"),
    [],
    retryAfter,
  );
}

export class OpenNoshClient {
  constructor(targetOrOptions = "hosted") {
    const options = typeof targetOrOptions === "string" ? { target: targetOrOptions } : targetOrOptions;
    if (!options || typeof options !== "object") throw new TypeError("client options must be an object");
    this.origin = normalizeTarget(options.target ?? "hosted");
    this.fetch = options.fetch ?? globalThis.fetch?.bind(globalThis);
    if (typeof this.fetch !== "function") throw new TypeError("a Fetch API implementation is required");
  }

  capabilities(options) {
    return this.#request(POLICY.capabilities, {}, {}, options);
  }

  searchFoods(query, options) {
    if (!query || typeof query.q !== "string") throw new TypeError("searchFoods requires q");
    const { q, locale, source, pack, limit, cursor } = query;
    return this.#request(POLICY.searchFoods, {}, { q, locale, source, pack, limit, cursor }, options);
  }

  getCommonsSnapshot(options = {}) {
    const headers = options.ifNoneMatch ? { "If-None-Match": options.ifNoneMatch } : {};
    return this.#request(POLICY.getCommonsSnapshot, {}, {}, options, headers, true);
  }

  getPublicFood(parameters, options) {
    const { source, sourceId, version } = parameters ?? {};
    return this.#request(POLICY.getPublicFood, { source, source_id: sourceId }, { version }, options);
  }

  listMissions(parameters = {}, options) {
    return this.#request(POLICY.listMissions, {}, { limit: parameters.limit }, options);
  }

  getMissionActivity(options) {
    return this.#request(POLICY.getMissionActivity, {}, {}, options);
  }

  getReleaseFood(parameters, options) {
    const { releaseVersion, source, sourceId } = parameters ?? {};
    return this.#request(POLICY.getReleaseFood, { release_version: releaseVersion, source, source_id: sourceId }, {}, options);
  }

  getProvenance(parameters, options) {
    const { releaseVersion, source, sourceId } = parameters ?? {};
    return this.#request(POLICY.getProvenance, { release_version: releaseVersion, source, source_id: sourceId }, {}, options);
  }

  getReleaseManifest(parameters, options) {
    return this.#request(POLICY.getReleaseManifest, { release_version: parameters?.releaseVersion }, {}, options);
  }

  downloadPack(parameters, options) {
    const { releaseVersion, packId, packVersion } = parameters ?? {};
    return this.#request(POLICY.downloadPack, { release_version: releaseVersion, pack_id: packId, pack_version: packVersion }, {}, options);
  }

  async #request(policy, pathValues, query, options = {}, extraHeaders = {}, allowNotModified = false) {
    const path = fillPath(policy.path, pathValues, policy.pathParameters);
    const url = new URL(path, this.origin);
    appendQuery(url, query);
    const timeoutMs = boundedTimeout(options?.timeoutMs, policy.timeoutMs ?? JSON_TIMEOUT_MS);
    const managedSignal = requestSignal(options?.signal, timeoutMs);
    const headers = new Headers({ Accept: policy.mediaType, ...extraHeaders });
    const isNode = typeof process !== "undefined" && process?.release?.name === "node";
    if (isNode) headers.set("X-OpenNosh-Client", `js/${PACKAGE_VERSION}`);

    let response;
    try {
      response = await this.fetch(url, {
        method: "GET",
        headers,
        credentials: "omit",
        redirect: "manual",
        signal: managedSignal.signal,
      });
    } catch (error) {
      managedSignal.cleanup();
      if (options?.signal?.aborted) throw options.signal.reason ?? error;
      if (managedSignal.signal.reason === managedSignal.timeoutReason) {
        throw new OpenNoshProblem(504, "request_timeout", `OpenNosh did not respond within ${timeoutMs} ms.`);
      }
      throw new OpenNoshProblem(0, "network_error", "The OpenNosh endpoint could not be reached.");
    }
    try {
      const returnedUrlChanged = response.url && response.url !== url.href;
      if (response.type === "opaqueredirect" || returnedUrlChanged
        || (response.status >= 300 && response.status < 400 && !(allowNotModified && response.status === 304))) {
        await cancelBody(response);
        throw new OpenNoshProblem(response.status, "redirect_refused", "OpenNosh refused a cross-origin or redirected response.", response.headers.get("x-request-id"));
      }
      if (allowNotModified && response.status === 304) return responseMetadata(response, null);
      if (!response.ok) throw await problemFromResponse(response, managedSignal.signal);
      if (!policy.acceptedMediaTypes.includes(mediaType(response))) {
        await cancelBody(response);
        throw new OpenNoshProblem(response.status, "unexpected_response", `OpenNosh returned an unexpected media type for ${policy.path}.`);
      }

      const bytes = await readLimited(response, policy.limit, managedSignal.signal);
      if (policy.binary) return responseMetadata(response, bytes);
      let text;
      try {
        text = new TextDecoder("utf-8", { fatal: true }).decode(bytes);
      } catch {
        throw new OpenNoshProblem(response.status, "unexpected_response", `OpenNosh returned invalid UTF-8 for ${policy.path}.`);
      }
      if (policy.mediaType === "text/html") return responseMetadata(response, text);
      try {
        return responseMetadata(response, JSON.parse(text));
      } catch {
        throw new OpenNoshProblem(response.status, "unexpected_response", `OpenNosh returned invalid JSON for ${policy.path}.`);
      }
    } catch (error) {
      if (options?.signal?.aborted) throw options.signal.reason ?? error;
      if (managedSignal.signal.reason === managedSignal.timeoutReason) {
        throw new OpenNoshProblem(504, "request_timeout", `OpenNosh did not respond within ${timeoutMs} ms.`);
      }
      if (!(error instanceof OpenNoshProblem)) {
        throw new OpenNoshProblem(0, "network_error", "The OpenNosh endpoint could not be reached.");
      }
      throw error;
    } finally {
      managedSignal.cleanup();
    }
  }
}
