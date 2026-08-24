import type { NextRequest } from "next/server";

export const dynamic = "force-dynamic";

const forwardedRequestHeaders = ["accept", "content-type", "cookie", "if-none-match", "x-csrf-token"];

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
  const cacheablePublicSnapshot =
    request.method === "GET" && encodedPath === "public/commons-snapshot";
  const target = new URL(`/api/v1/${encodedPath}${request.nextUrl.search}`, apiOrigin);
  const headers = new Headers();
  for (const name of forwardedRequestHeaders) {
    const value = request.headers.get(name);
    if (value) headers.set(name, value);
  }
  const proxyToken = process.env.WEB_PROXY_TOKEN;
  const clientAddress = request.headers.get("x-forwarded-for");
  if (cacheablePublicSnapshot) headers.delete("cookie");
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
    if (!cacheablePublicSnapshot) responseHeaders.set("Cache-Control", "no-store");
    return new Response(upstream.body, { status: upstream.status, headers: responseHeaders });
  } catch {
    return proxyProblem(
      502,
      "upstream_unavailable",
      "Upstream service unavailable",
      "opennosh could not reach the API. Please try again.",
    );
  }
}

export const GET = proxy;
export const POST = proxy;
export const PUT = proxy;
export const DELETE = proxy;
