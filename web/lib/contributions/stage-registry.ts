import type { ContributionCatalogKey } from "@/lib/contributions/catalog";
import {
  contributionStages,
  type ContributionChapter,
  type ContributionStage,
} from "@/lib/contributions/domain";
import { routes, type InterfaceLanguage } from "@/lib/routes";

export type ContributionStagePresentation = {
  slug: ContributionStage;
  order: 1 | 2 | 3 | 4 | 5;
  chapter: ContributionChapter;
  headingKey: ContributionCatalogKey;
  descriptionKey: ContributionCatalogKey;
  headingAnchor: string;
  validationAnchor: string;
  analyticsId: string;
  previous: ContributionStage | null;
  next: ContributionStage | null;
};

export const contributionStageRegistry = {
  evidence: {
    slug: "evidence",
    order: 1,
    chapter: "begin",
    headingKey: "stages.evidence.heading",
    descriptionKey: "stages.evidence.description",
    headingAnchor: "contribution-stage-evidence",
    validationAnchor: "contribution-errors-evidence",
    analyticsId: "contribution_evidence",
    previous: null,
    next: "details",
  },
  details: {
    slug: "details",
    order: 2,
    chapter: "begin",
    headingKey: "stages.details.heading",
    descriptionKey: "stages.details.description",
    headingAnchor: "contribution-stage-details",
    validationAnchor: "contribution-errors-details",
    analyticsId: "contribution_details",
    previous: "evidence",
    next: "duplicates",
  },
  duplicates: {
    slug: "duplicates",
    order: 3,
    chapter: "verify",
    headingKey: "stages.duplicates.heading",
    descriptionKey: "stages.duplicates.description",
    headingAnchor: "contribution-stage-duplicates",
    validationAnchor: "contribution-errors-duplicates",
    analyticsId: "contribution_duplicates",
    previous: "details",
    next: "provenance",
  },
  provenance: {
    slug: "provenance",
    order: 4,
    chapter: "verify",
    headingKey: "stages.provenance.heading",
    descriptionKey: "stages.provenance.description",
    headingAnchor: "contribution-stage-provenance",
    validationAnchor: "contribution-errors-provenance",
    analyticsId: "contribution_provenance",
    previous: "duplicates",
    next: "review",
  },
  review: {
    slug: "review",
    order: 5,
    chapter: "send",
    headingKey: "stages.review.heading",
    descriptionKey: "stages.review.description",
    headingAnchor: "contribution-stage-review",
    validationAnchor: "contribution-errors-review",
    analyticsId: "contribution_review",
    previous: "provenance",
    next: null,
  },
} as const satisfies Record<ContributionStage, ContributionStagePresentation>;

export function isContributionStage(value: string): value is ContributionStage {
  return contributionStages.includes(value as ContributionStage);
}

export function contributionStageList(): readonly ContributionStagePresentation[] {
  return contributionStages.map((stage) => contributionStageRegistry[stage]);
}

export function contributionStageHref(
  language: InterfaceLanguage,
  draftId: string,
  stage: ContributionStage,
): string {
  return routes.contributionDraft(language, draftId, contributionStageRegistry[stage].slug);
}
