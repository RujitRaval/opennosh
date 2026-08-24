import type { InterfaceLanguage } from "@/lib/routes";

export const contributionCatalogs = {
  en: {
    chapters: {
      begin: { label: "Begin the record", promise: "Start with the source and describe only what it supports." },
      verify: { label: "Verify the claim", promise: "Check for an existing record and make its origin explicit." },
      send: { label: "Send to the commons", promise: "Inspect the exact proposal before handing it to review." },
    },
    stages: {
      evidence: {
        heading: "Start with the source",
        description: "Tell us what supports this food. A public reference is enough to begin; automated extraction never replaces your confirmation.",
      },
      details: {
        heading: "Describe what the source says",
        description: "Keep the food name, preparation, original portion, and canonical gram weight together.",
      },
      duplicates: {
        heading: "Check what already exists",
        description: "A real commons improves existing records when it can. We check the current public food index before creating another claim.",
      },
      provenance: {
        heading: "Keep its origin attached",
        description: "Choose the pack, date, public credit, and source terms that will travel with this proposal.",
      },
      review: {
        heading: "Review the exact proposal",
        description: "This sends a reviewable contribution. It does not publish the food or count it as accepted.",
      },
    },
    actions: {
      back: "Back",
      continue: "Continue",
      submit: "Hand to review",
      viewAll: "View all steps",
      hideAll: "Hide all steps",
    },
  },
} as const;

export type ContributionCatalog = (typeof contributionCatalogs)[InterfaceLanguage];
export type ContributionCatalogKey =
  | `chapters.${keyof ContributionCatalog["chapters"]}.label`
  | `chapters.${keyof ContributionCatalog["chapters"]}.promise`
  | `stages.${keyof ContributionCatalog["stages"]}.heading`
  | `stages.${keyof ContributionCatalog["stages"]}.description`;

export function contributionCatalog(language: InterfaceLanguage): ContributionCatalog {
  return contributionCatalogs[language];
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

