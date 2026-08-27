import {
  publicHubIds,
  routes,
  type InterfaceLanguage,
  type PublicHubId,
} from "@/lib/routes";
import { getCatalog } from "@/lib/i18n/catalog";

export const publicFeatureIds = [
  "explorer-search",
  "contribution-flow",
  "public-packs",
  "api-reference",
] as const;

export type PublicFeatureId = (typeof publicFeatureIds)[number];

export type PublicNavigationChild = {
  id: string;
  label: string;
  description: string;
  href: string;
};

export type PublicNavigationHub = {
  id: PublicHubId;
  index: string;
  label: string;
  description: string;
  nextAction: {
    label: string;
    compactLabel: string;
    href: string;
    external?: boolean;
  };
  principles: readonly string[];
  children: readonly PublicNavigationChild[];
};

type ChildDefinition = {
  id: string;
  hub: PublicHubId;
  feature?: PublicFeatureId;
  target: string;
};

const childDefinitions: readonly ChildDefinition[] = [
  {
    id: "search",
    hub: "explore",
    feature: "explorer-search",
    target: "search",
  },
  {
    id: "start",
    hub: "contribute",
    target: "start",
  },
  {
    id: "packs",
    hub: "commons",
    feature: "public-packs",
    target: "packs",
  },
  {
    id: "api",
    hub: "build",
    feature: "api-reference",
    target: "api",
  },
  {
    id: "notices",
    hub: "build",
    target: "notices",
  },
];

export function getPublicShellCopy(language: InterfaceLanguage) {
  return getCatalog(language).shell;
}

export function parsePublicFeatureFlags(value: string | undefined): readonly PublicFeatureId[] {
  if (!value) return [];
  const known = new Set<string>(publicFeatureIds);
  return [...new Set(value.split(",").map((flag) => flag.trim()).filter((flag) => known.has(flag)))] as PublicFeatureId[];
}

export function buildPublicNavigation(
  language: InterfaceLanguage,
  enabledFeatures: readonly PublicFeatureId[] = [],
): readonly PublicNavigationHub[] {
  const enabled = new Set(enabledFeatures);
  const copy = getCatalog(language).navigation;

  return publicHubIds.map((id, position) => {
    const hub = copy.hubs[id];
    const children = childDefinitions
      .filter((child) => child.hub === id && (!child.feature || enabled.has(child.feature)))
      .map((child) => ({
        id: child.id,
        label: copy.children[child.id as keyof typeof copy.children].label,
        description: copy.children[child.id as keyof typeof copy.children].description,
        href:
          child.target === "notices"
            ? routes.publicNotices(language)
            : child.target === "start"
              ? routes.contributionStart(language)
            : `${routes.publicHub(id, language)}#${child.target}`,
      }));

    const nextAction = id === "contribute"
      ? { label: hub.action, compactLabel: hub.compactAction, href: routes.contributionStart(language) }
      : id === "commons"
        ? { label: hub.action, compactLabel: hub.compactAction, href: routes.publicNotices(language) }
        : id === "explore"
          ? { label: hub.action, compactLabel: hub.compactAction, href: enabled.has("explorer-search") ? "#search" : "#principles" }
        : id === "build"
          ? {
              label: hub.action,
              compactLabel: hub.compactAction,
              href: "https://github.com/RujitRaval/opennosh",
              external: true,
            }
          : { label: hub.action, compactLabel: hub.compactAction, href: "#search" };

    return {
      id,
      index: String(position + 1).padStart(2, "0"),
      label: hub.label,
      description: hub.description,
      nextAction,
      principles: hub.principles,
      children,
    };
  });
}

export function resolvePublicHub(
  pathname: string,
  language: InterfaceLanguage,
): PublicHubId | undefined {
  if (pathname === routes.publicNotices(language)) return "build";
  return publicHubIds.find((hub) => {
    const hubPath = routes.publicHub(hub, language);
    return pathname === hubPath || pathname.startsWith(`${hubPath}/`);
  });
}
