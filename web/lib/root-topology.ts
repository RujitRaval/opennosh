import {
  defaultLanguage,
  isSupportedLanguage,
  resolveInterfaceLanguage,
  routes,
  type InterfaceLanguage,
} from "@/lib/routes";

export const publicReturnPathCookie = "opennosh_public_return_path";

export const rootPreferenceCookieOptions = {
  httpOnly: true,
  maxAge: 365 * 24 * 60 * 60,
  path: "/",
  sameSite: "lax" as const,
};

export function isPublicRootEnabled(value = process.env.OPENNOSH_PUBLIC_ROOT_ENABLED): boolean {
  return !["0", "false", "off"].includes(value?.trim().toLowerCase() ?? "");
}

export function isLocalizedPublicPath(pathname: string): boolean {
  const [language, ...segments] = pathname.split("/").filter(Boolean);
  if (!language || !isSupportedLanguage(language)) return false;
  if (segments.length === 0) return true;
  if (segments.length === 1) {
    return ["explore", "contribute", "commons", "build", "notices"]
      .includes(segments[0] ?? "");
  }
  if (segments[0] === "explore") {
    return segments.length === 4
      && segments[1] === "foods"
      && segments.slice(2).every(Boolean);
  }
  if (segments[0] === "contribute") {
    return segments.length === 3
      && Boolean(segments[1])
      && ["evidence", "details", "duplicates", "provenance", "review", "status"]
        .includes(segments[2] ?? "");
  }
  return false;
}

export function resolvePublicReturnPath(
  savedPath: string | undefined,
  fallbackLanguage: InterfaceLanguage = defaultLanguage,
): string {
  if (!savedPath || savedPath.length > 2_048 || !savedPath.startsWith("/")) {
    return routes.publicHome(fallbackLanguage);
  }
  let parsed: URL;
  try {
    parsed = new URL(savedPath, "https://opennosh.invalid");
  } catch {
    return routes.publicHome(fallbackLanguage);
  }
  if (parsed.origin !== "https://opennosh.invalid" || !isLocalizedPublicPath(parsed.pathname)) {
    return routes.publicHome(fallbackLanguage);
  }
  return `${parsed.pathname}${parsed.search}`;
}

export function resolveTrackerRootContext({
  savedLanguage,
  savedPublicPath,
}: {
  savedLanguage?: string;
  savedPublicPath?: string;
}) {
  const language = resolveInterfaceLanguage({ savedLanguage });
  return {
    language,
    publicReturnPath: resolvePublicReturnPath(savedPublicPath, language),
  };
}
