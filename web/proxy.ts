import { type NextRequest, NextResponse } from "next/server";

import {
  interfaceLanguageCookie,
  resolveInterfaceLanguage,
  routes,
} from "@/lib/routes";

function requestLanguage(request: NextRequest) {
  return resolveInterfaceLanguage({
    savedLanguage: request.cookies.get(interfaceLanguageCookie)?.value,
    acceptLanguage: request.headers.get("accept-language") ?? undefined,
  });
}

export function proxy(request: NextRequest) {
  const pathname = request.nextUrl.pathname;
  let destination: string | undefined;

  if (pathname === "/") destination = routes.publicHome(requestLanguage(request));
  if (pathname === "/notices") destination = routes.publicNotices(requestLanguage(request));
  if (pathname === "/trends") destination = routes.tracker.trends;
  if (!destination) return NextResponse.next();

  const destinationUrl = request.nextUrl.clone();
  destinationUrl.pathname = destination;
  return NextResponse.redirect(destinationUrl, 307);
}

export const config = {
  matcher: ["/", "/trends", "/notices"],
};
