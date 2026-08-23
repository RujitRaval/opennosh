export const supportedLanguages = ["en"] as const;

export type InterfaceLanguage = (typeof supportedLanguages)[number];

export const defaultLanguage: InterfaceLanguage = "en";

export function isSupportedLanguage(value: string): value is InterfaceLanguage {
  return supportedLanguages.includes(value as InterfaceLanguage);
}

export const routes = {
  root: "/",
  publicHome: (language: InterfaceLanguage = defaultLanguage) => `/${language}`,
  publicNotices: (language: InterfaceLanguage = defaultLanguage) => `/${language}/notices`,
  publicHub: (
    hub: "explore" | "contribute" | "commons" | "build",
    language: InterfaceLanguage = defaultLanguage,
  ) => `/${language}#${hub}`,
  tracker: {
    home: "/tracker",
    trends: "/tracker/trends",
  },
} as const;
