import type { NextRequest } from "next/server";

export const dynamic = "force-dynamic";

const forwardedRequestHeaders = [
  "accept",
  "content-type",
  "cookie",
  "idempotency-key",
  "if-none-match",
  "x-csrf-token",
];

const sdkCorsHeaders = {
  "Access-Control-Allow-Origin": "*",
  "Access-Control-Expose-Headers": "ETag, Retry-After, X-Request-ID",
};

function isSdkAnonymousRead(path: string[]): boolean {
  const joined = path.join("/");
  if ([
    "foods/capabilities",
    "foods/search",
    "public/commons-snapshot",
    "public/missions",
    "public/missions/activity",
  ].includes(joined)) return true;
  if (path.length === 4 && path[0] === "public" && path[1] === "foods") return true;
  if (path[0] !== "public" || path[1] !== "releases") return false;
  return (path.length === 4 && path[3] === "manifest")
    || (path.length === 6 && path[3] === "foods")
    || (path.length === 7 && path[3] === "foods" && path[6] === "provenance")
    || (path.length === 7 && path[3] === "packs" && path[6] === "download");
}

function withSdkCors(response: Response): Response {
  const headers = new Headers(response.headers);
  for (const [name, value] of Object.entries(sdkCorsHeaders)) headers.set(name, value);
  return new Response(response.body, { status: response.status, headers });
}

function proxyProblem(
  status: 400 | 502,
  code: "invalid_request" | "upstream_unavailable",
  title: string,
  detail: string,
): Response {
  const requestId = crypto.randomUUID();
  return Response.json(
    {
      type: `https://opennosh.org/problems/${code.replaceAll("_", "-")}`,
      title,
      status,
      detail,
      code,
      schema_version: "1.0",
      request_id: requestId,
      recovery_actions: code === "upstream_unavailable"
        ? [{ id: "retry", label: "Try again" }]
        : undefined,
    },
    {
      status,
      headers: {
        "Cache-Control": "no-store",
        "Content-Type": "application/problem+json",
        "X-Request-ID": requestId,
      },
    },
  );
}

async function proxy(request: NextRequest, context: { params: Promise<{ path: string[] }> }) {
  const { path } = await context.params;
  const sdkAnonymousRead = ["GET", "HEAD"].includes(request.method) && isSdkAnonymousRead(path);
  if (
    path.some(
      (segment) =>
        !segment ||
        segment === "." ||
        segment === ".." ||
        segment.includes("/") ||
        segment.includes("\\") ||
        segment.includes("\0"),
    )
  ) {
    return proxyProblem(400, "invalid_request", "Invalid request", "The API path is invalid.");
  }
  const apiOrigin = (process.env.API_URL ?? "http://localhost:8000").replace(/\/$/, "");
  const encodedPath = path.map((segment) => encodeURIComponent(segment)).join("/");
  const cacheableAnonymousPublicRead = sdkAnonymousRead && encodedPath.startsWith("public/");
  const upstreamPath = encodedPath === "healthz" ? "/healthz" : `/api/v1/${encodedPath}`;
  const target = new URL(`${upstreamPath}${request.nextUrl.search}`, apiOrigin);
  const headers = new Headers();
  for (const name of forwardedRequestHeaders) {
    const value = request.headers.get(name);
    if (value) headers.set(name, value);
  }
  const proxyToken = process.env.WEB_PROXY_TOKEN;
  const clientAddress = request.headers.get("x-forwarded-for");
  if (sdkAnonymousRead) {
    headers.delete("cookie");
    const sdkClient = request.headers.get("x-opennosh-client");
    if (sdkClient) headers.set("x-opennosh-client", sdkClient);
  }
  if (proxyToken && clientAddress) {
    headers.set("x-opennosh-client-address", clientAddress);
    headers.set("x-opennosh-proxy-token", proxyToken);
  }

  try {
    const upstreamInit: RequestInit & { duplex?: "half" } = {
      method: request.method,
      headers,
      body: ["GET", "HEAD"].includes(request.method) ? undefined : request.body,
      cache: "no-store",
      redirect: "manual",
    };
    if (request.body) upstreamInit.duplex = "half";
    const upstream = await fetch(target, upstreamInit);
    const responseHeaders = new Headers(upstream.headers);
    const cookies = upstream.headers.getSetCookie();
    responseHeaders.delete("set-cookie");
    for (const cookie of cookies) responseHeaders.append("set-cookie", cookie);
    responseHeaders.delete("content-encoding");
    responseHeaders.delete("content-length");
    if (!cacheableAnonymousPublicRead) responseHeaders.set("Cache-Control", "no-store");
    const response = new Response(upstream.body, { status: upstream.status, headers: responseHeaders });
    return sdkAnonymousRead ? withSdkCors(response) : response;
  } catch {
    const response = proxyProblem(
      502,
      "upstream_unavailable",
      "Upstream service unavailable",
      "opennosh could not reach the API. Please try again.",
    );
    return sdkAnonymousRead ? withSdkCors(response) : response;
  }
}

export async function OPTIONS(
  request: NextRequest,
  context: { params: Promise<{ path: string[] }> },
): Promise<Response> {
  const { path } = await context.params;
  if (!isSdkAnonymousRead(path)) return new Response(null, { status: 405 });
  const requestedMethod = request.headers.get("access-control-request-method")?.toUpperCase();
  const requestedHeaders = (request.headers.get("access-control-request-headers") ?? "")
    .split(",")
    .map((value) => value.trim().toLowerCase())
    .filter(Boolean);
  if ((requestedMethod && !["GET", "HEAD"].includes(requestedMethod))
    || requestedHeaders.some((header) => !["accept", "if-none-match"].includes(header))) {
    return new Response(null, { status: 403 });
  }
  return new Response(null, {
    status: 204,
    headers: {
      ...sdkCorsHeaders,
      "Access-Control-Allow-Headers": "Accept, If-None-Match",
      "Access-Control-Allow-Methods": "GET, HEAD, OPTIONS",
      "Access-Control-Max-Age": "86400",
    },
  });
}

export const GET = proxy;
export const HEAD = proxy;
export const POST = proxy;
export const PUT = proxy;
export const PATCH = proxy;
export const DELETE = proxy;
