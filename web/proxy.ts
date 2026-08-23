import { type NextRequest, NextResponse } from "next/server";

import { routes } from "@/lib/routes";

const legacyRedirects: Readonly<Record<string, string>> = {
  "/": routes.publicHome(),
  "/trends": routes.tracker.trends,
  "/notices": routes.publicNotices(),
};

export function proxy(request: NextRequest) {
  const destination = legacyRedirects[request.nextUrl.pathname];
  if (!destination) return NextResponse.next();

  return NextResponse.redirect(new URL(destination, request.url), 307);
}

export const config = {
  matcher: ["/", "/trends", "/notices"],
};
