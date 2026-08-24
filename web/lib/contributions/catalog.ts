import type { InterfaceLanguage } from "@/lib/routes";
import { getCatalog } from "@/lib/i18n/catalog";

export type ContributionCatalog = ReturnType<typeof getCatalog>["contribution"];
export type ContributionCatalogKey =
  | `chapters.${keyof ContributionCatalog["chapters"]}.label`
  | `chapters.${keyof ContributionCatalog["chapters"]}.promise`
  | `stages.${keyof ContributionCatalog["stages"]}.heading`
  | `stages.${keyof ContributionCatalog["stages"]}.description`;

export function contributionCatalog(language: InterfaceLanguage): ContributionCatalog {
  return getCatalog(language).contribution;
}

export function contributionMessage(
  language: InterfaceLanguage,
  key: ContributionCatalogKey,
): string {
  const [group, id, property] = key.split(".") as [
    "chapters" | "stages",
    string,
    "label" | "promise" | "heading" | "description",
  ];
  const catalog = contributionCatalog(language);
  if (group === "chapters") {
    const chapter = catalog.chapters[id as keyof typeof catalog.chapters];
    return chapter[property as "label" | "promise"];
  }
  const stage = catalog.stages[id as keyof typeof catalog.stages];
  return stage[property as "heading" | "description"];
}
