import { type NextRequest, NextResponse } from "next/server";

import {
  interfaceLanguageCookie,
  resolveInterfaceLanguage,
  routes,
} from "@/lib/routes";
import {
  isLocalizedPublicPath,
  isPublicRootEnabled,
  publicReturnPathCookie,
  rootPreferenceCookieOptions,
} from "@/lib/root-topology";

function requestLanguage(request: NextRequest) {
  return resolveInterfaceLanguage({
    savedLanguage: request.cookies.get(interfaceLanguageCookie)?.value,
    acceptLanguage: request.headers.get("accept-language") ?? undefined,
  });
}

export function proxy(request: NextRequest) {
  const pathname = request.nextUrl.pathname;
  let destination: string | undefined;

  if (pathname === "/") {
    destination = isPublicRootEnabled()
      ? routes.publicHome(requestLanguage(request))
      : routes.tracker.home;
  }
  if (pathname === "/notices") destination = routes.publicNotices(requestLanguage(request));
  if (pathname === "/trends") destination = routes.tracker.trends;
  const response = destination
    ? (() => {
        const destinationUrl = request.nextUrl.clone();
        destinationUrl.pathname = destination;
        return NextResponse.redirect(destinationUrl, 307);
      })()
    : NextResponse.next();
  const publicPath = isLocalizedPublicPath(destination ?? pathname)
    ? `${destination ?? pathname}${request.nextUrl.search}`
    : null;
  if (publicPath) {
    response.cookies.set(publicReturnPathCookie, publicPath, {
      ...rootPreferenceCookieOptions,
      secure: request.nextUrl.protocol === "https:",
    });
  }
  return response;
}

export const config = {
  matcher: [
    "/((?!api|_next/static|_next/image|favicon.ico|manifest.webmanifest|brand/|fonts/).*)",
  ],
};
