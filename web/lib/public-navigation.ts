import {
  publicHubIds,
  routes,
  type InterfaceLanguage,
  type PublicHubId,
} from "@/lib/routes";

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
    href: string;
    external?: boolean;
  };
  principles: readonly [string, string, string];
  children: readonly PublicNavigationChild[];
};

type HubCopy = Omit<PublicNavigationHub, "id" | "index" | "children">;

const shellCatalogs = {
  en: {
    interfaceLanguage: "English",
    menu: "Menu",
    close: "Close",
    tracker: "Tracker",
    mobileTracker: "Open private tracker",
    hubs: {
      explore: {
        label: "Explore",
        description:
          "Find food knowledge with its source, preparation, portion, locale, and uncertainty still attached.",
        nextAction: { label: "See how records work", href: "#principles" },
        principles: ["Public by default", "Context beside numbers", "Provenance in the open"],
      },
      contribute: {
        label: "Contribute",
        description:
          "Document a missing food without flattening the place, preparation, or people that give it meaning.",
        nextAction: {
          label: "Read the contribution guide",
          href: "https://github.com/RujitRaval/opennosh/blob/main/CONTRIBUTING.md",
          external: true,
        },
        principles: ["Name the context", "Keep original units", "Publish through review"],
      },
      commons: {
        label: "Commons",
        description:
          "Inspect the rules, sources, versions, and stewardship that let shared food data earn trust in public.",
        nextAction: { label: "Read licenses and notices", href: "/notices" },
        principles: ["Visible stewardship", "Versioned releases", "Licenses stay attached"],
      },
      build: {
        label: "Build",
        description:
          "Use inspectable schemas, packs, APIs, and source code to make food knowledge useful elsewhere.",
        nextAction: {
          label: "View the source repository",
          href: "https://github.com/RujitRaval/opennosh",
          external: true,
        },
        principles: ["Portable schemas", "Reusable public data", "Open-source tools"],
      },
    } satisfies Record<PublicHubId, HubCopy>,
  },
} as const;

type ChildDefinition = {
  id: string;
  hub: PublicHubId;
  label: string;
  description: string;
  feature?: PublicFeatureId;
  target: string;
};

const childDefinitions: readonly ChildDefinition[] = [
  {
    id: "search",
    hub: "explore",
    label: "Search foods",
    description: "Search public food records without an account.",
    feature: "explorer-search",
    target: "search",
  },
  {
    id: "start",
    hub: "contribute",
    label: "Start a contribution",
    description: "Begin a guided food record contribution.",
    feature: "contribution-flow",
    target: "start",
  },
  {
    id: "packs",
    hub: "commons",
    label: "Browse data packs",
    description: "Inspect public, versioned food-data releases.",
    feature: "public-packs",
    target: "packs",
  },
  {
    id: "api",
    hub: "build",
    label: "API reference",
    description: "Use the public contract in another product.",
    feature: "api-reference",
    target: "api",
  },
  {
    id: "notices",
    hub: "build",
    label: "Licenses + notices",
    description: "Understand the terms attached to each source and export.",
    target: "notices",
  },
];

export function getPublicShellCopy(language: InterfaceLanguage) {
  return shellCatalogs[language];
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
  const copy = getPublicShellCopy(language);

  return publicHubIds.map((id, position) => {
    const hub = copy.hubs[id];
    const children = childDefinitions
      .filter((child) => child.hub === id && (!child.feature || enabled.has(child.feature)))
      .map((child) => ({
        id: child.id,
        label: child.label,
        description: child.description,
        href:
          child.target === "notices"
            ? routes.publicNotices(language)
            : `${routes.publicHub(id, language)}#${child.target}`,
      }));

    const nextAction =
      hub.nextAction.href === "/notices"
        ? { ...hub.nextAction, href: routes.publicNotices(language) }
        : hub.nextAction;

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
  return publicHubIds.find((hub) => pathname === routes.publicHub(hub, language));
}
