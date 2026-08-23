export const supportedLanguages = ["en"] as const;

export type InterfaceLanguage = (typeof supportedLanguages)[number];

export const defaultLanguage: InterfaceLanguage = "en";
export const interfaceLanguageCookie = "opennosh_interface_language";

export const publicHubIds = ["explore", "contribute", "commons", "build"] as const;
export type PublicHubId = (typeof publicHubIds)[number];

export function isSupportedLanguage(value: string): value is InterfaceLanguage {
  return supportedLanguages.includes(value as InterfaceLanguage);
}

export function isPublicHub(value: string): value is PublicHubId {
  return publicHubIds.includes(value as PublicHubId);
}

function languageFromTag(value: string | undefined): InterfaceLanguage | undefined {
  if (!value) return undefined;
  const normalized = value.trim().toLowerCase();
  if (!normalized) return undefined;

  return supportedLanguages.find(
    (language) => normalized === language || normalized.startsWith(`${language}-`),
  );
}

export function resolveInterfaceLanguage({
  savedLanguage,
  acceptLanguage,
}: {
  savedLanguage?: string;
  acceptLanguage?: string;
}): InterfaceLanguage {
  const saved = languageFromTag(savedLanguage);
  if (saved) return saved;

  const browserPreferences = (acceptLanguage ?? "")
    .split(",")
    .map((entry, index) => {
      const [tag = "", ...parameters] = entry.trim().split(";");
      const qualityParameter = parameters.find((parameter) => parameter.trim().startsWith("q="));
      const quality = qualityParameter
        ? Number.parseFloat(qualityParameter.trim().slice(2))
        : 1;

      return {
        index,
        language: languageFromTag(tag),
        quality: Number.isFinite(quality) ? quality : 0,
      };
    })
    .filter(
      (
        preference,
      ): preference is { index: number; language: InterfaceLanguage; quality: number } =>
        Boolean(preference.language) && preference.quality > 0,
    )
    .sort((left, right) => right.quality - left.quality || left.index - right.index);

  return browserPreferences[0]?.language ?? defaultLanguage;
}

export function localizePublicPath({
  pathname,
  search = "",
  language,
}: {
  pathname: string;
  search?: string;
  language: InterfaceLanguage;
}) {
  const segments = pathname.split("/").filter(Boolean);
  if (segments.length === 0) return `${routes.publicHome(language)}${search}`;

  if (isSupportedLanguage(segments[0] ?? "")) segments[0] = language;
  else segments.unshift(language);

  return `/${segments.join("/")}${search}`;
}

export const routes = {
  root: "/",
  publicHome: (language: InterfaceLanguage = defaultLanguage) => `/${language}`,
  publicNotices: (language: InterfaceLanguage = defaultLanguage) => `/${language}/notices`,
  publicHub: (
    hub: PublicHubId,
    language: InterfaceLanguage = defaultLanguage,
  ) => `/${language}/${hub}`,
  publicFoodRecord: (
    source: "usda" | "community",
    sourceId: string,
    language: InterfaceLanguage = defaultLanguage,
  ) => `/${language}/explore/foods/${source}/${encodeURIComponent(sourceId)}`,
  tracker: {
    home: "/tracker",
    trends: "/tracker/trends",
  },
} as const;
