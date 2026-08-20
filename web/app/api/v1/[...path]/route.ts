import type { NextRequest } from "next/server";

export const dynamic = "force-dynamic";

const forwardedRequestHeaders = ["accept", "content-type", "cookie", "x-csrf-token"];

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
    return Response.json({ detail: "Invalid API path" }, { status: 400 });
  }
  const apiOrigin = (process.env.API_URL ?? "http://localhost:8000").replace(/\/$/, "");
  const encodedPath = path.map((segment) => encodeURIComponent(segment)).join("/");
  const target = new URL(`/api/v1/${encodedPath}${request.nextUrl.search}`, apiOrigin);
  const headers = new Headers();
  for (const name of forwardedRequestHeaders) {
    const value = request.headers.get(name);
    if (value) headers.set(name, value);
  }
  const proxyToken = process.env.WEB_PROXY_TOKEN;
  const clientAddress = request.headers.get("x-forwarded-for");
  if (proxyToken && clientAddress) {
    headers.set("x-opennosh-client-address", clientAddress);
    headers.set("x-opennosh-proxy-token", process.env.WEB_PROXY_TOKEN!);
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
    responseHeaders.set("Cache-Control", "no-store");
    return new Response(upstream.body, { status: upstream.status, headers: responseHeaders });
  } catch {
    const responseHeaders = new Headers({ "Cache-Control": "no-store" });
    return Response.json(
      { detail: "opennosh could not reach the API. Please try again." },
      { status: 502, headers: responseHeaders },
    );
  }
}

export const GET = proxy;
export const POST = proxy;
export const PUT = proxy;
export const DELETE = proxy;
