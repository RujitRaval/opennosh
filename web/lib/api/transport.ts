import { networkProblem } from "./problem-adapter";
import { problemFromResponse } from "./problem-adapter";

function csrfTokenFromCookie(): string | null {
  if (typeof document === "undefined") return null;

  for (const name of ["__Host-opennosh-csrf", "opennosh_csrf"]) {
    const prefix = `${name}=`;
    const match = document.cookie.split("; ").find((cookie) => cookie.startsWith(prefix));
    if (match) return match.slice(prefix.length);
  }
  return null;
}

export async function request<TTransport>(
  path: `/api/v1/${string}`,
  init: RequestInit = {},
): Promise<TTransport> {
  const headers = new Headers(init.headers);
  headers.set("Accept", "application/json, application/problem+json");
  if (init.body) headers.set("Content-Type", "application/json");

  if (init.method && !["GET", "HEAD"].includes(init.method.toUpperCase())) {
    const csrfToken = csrfTokenFromCookie();
    if (csrfToken) headers.set("X-CSRF-Token", csrfToken);
  }

  let response: Response;
  try {
    response = await fetch(path, {
      ...init,
      headers,
      credentials: "same-origin",
      cache: "no-store",
    });
  } catch {
    throw networkProblem();
  }

  if (!response.ok) {
    let body: unknown;
    try {
      body = await response.json();
    } catch {
      body = undefined;
    }
    throw problemFromResponse(body, response);
  }
  if (response.status === 204) return undefined as TTransport;
  return (await response.json()) as TTransport;
}
