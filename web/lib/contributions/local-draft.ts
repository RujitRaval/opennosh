import {
  contributionStages,
  type ContributionBlocker,
  type ContributionFields,
  type ContributionStage,
  type LocalContributionDraft,
} from "@/lib/contributions/domain";

export const localContributionStorageKey = "opennosh.contribution.local.v1";

export const emptyContributionFields: ContributionFields = {
  evidence_type: null,
  source_uri: "",
  rights_acknowledged: false,
  name: "",
  name_local: "",
  locale: "",
  category: "",
  portion_description: "",
  portion_amount: "",
  portion_unit: "g",
  portion_grams: "",
  energy_kcal: "",
  protein_g: "",
  fat_g: "",
  carbohydrate_g: "",
  ingredients: "",
  duplicates_resolved: false,
  pack_id: "",
  source_date: "",
  attribution: "",
  source_license: null,
  review_acknowledged: false,
};

export function newLocalContributionDraft(id = crypto.randomUUID()): LocalContributionDraft {
  return {
    schemaVersion: "1",
    clientDraftId: id,
    fields: { ...emptyContributionFields },
    duplicateCandidates: [],
    duplicateQuery: null,
    savedAt: new Date().toISOString(),
    saveState: "saved_on_device",
  };
}

function present(value: string) {
  return value.trim().length > 0;
}

function positive(value: string) {
  const parsed = Number(value);
  return Number.isFinite(parsed) && parsed >= 0 && value.trim() !== "";
}

export function localStageBlockers(
  draft: LocalContributionDraft,
  stage: ContributionStage,
): ContributionBlocker[] {
  const fields = draft.fields;
  const required = (
    field: keyof ContributionFields,
    condition: boolean,
    message: string,
  ): ContributionBlocker[] => condition ? [] : [{ stage, field, code: "required", message }];
  switch (stage) {
    case "evidence":
      return [
        ...required("evidence_type", fields.evidence_type !== null, "Choose the source type."),
        ...required("source_uri", /^https:\/\/[^\s]+$/i.test(fields.source_uri), "Add a public HTTPS source URL."),
        ...required("rights_acknowledged", fields.rights_acknowledged, "Confirm the source-reference terms."),
      ];
    case "details":
      return [
        ...required("name", present(fields.name), "Add the food name."),
        ...required("locale", present(fields.locale), "Add the food locale."),
        ...required("category", present(fields.category), "Add a category."),
        ...required("portion_description", present(fields.portion_description), "Describe the portion."),
        ...required("portion_amount", positive(fields.portion_amount), "Add the original portion amount."),
        ...required("portion_grams", positive(fields.portion_grams), "Add the canonical gram weight."),
        ...required("energy_kcal", positive(fields.energy_kcal), "Add energy per portion."),
        ...required("protein_g", positive(fields.protein_g), "Add protein per portion."),
        ...required("fat_g", positive(fields.fat_g), "Add fat per portion."),
        ...required("carbohydrate_g", positive(fields.carbohydrate_g), "Add carbohydrate per portion."),
      ];
    case "duplicates":
      if (draft.duplicateQuery !== `${fields.name.trim()}|${fields.locale.trim()}`) {
        return [{ stage, field: null, code: "duplicate_check_required", message: "Check the current food index before continuing." }];
      }
      return draft.duplicateCandidates.length > 0 && !fields.duplicates_resolved
        ? [{ stage, field: "duplicates_resolved", code: "candidate_unresolved", message: "Review the possible matches and confirm this proposal is still needed." }]
        : [];
    case "provenance":
      return [
        ...required("pack_id", present(fields.pack_id), "Choose a target pack."),
        ...required("source_date", present(fields.source_date), "Add the source date."),
        ...required("attribution", present(fields.attribution), "Add the public contributor credit."),
        ...required("source_license", fields.source_license !== null, "Choose the source license."),
      ];
    case "review":
      return required("review_acknowledged", fields.review_acknowledged, "Confirm the attribution, CC0 terms, and review process.");
    default: {
      const exhaustive: never = stage;
      return exhaustive;
    }
  }
}

export function localCompletedStages(draft: LocalContributionDraft): ContributionStage[] {
  const completed: ContributionStage[] = [];
  for (const stage of contributionStages) {
    if (localStageBlockers(draft, stage).length > 0) break;
    completed.push(stage);
  }
  return completed;
}

export function localAccessibleStages(draft: LocalContributionDraft): ContributionStage[] {
  const completed = localCompletedStages(draft);
  const next = contributionStages[completed.length];
  return next ? [...completed, next] : [...contributionStages];
}

export function readLocalContributionDraft(raw: string | null): LocalContributionDraft | null {
  if (!raw) return null;
  try {
    const value = JSON.parse(raw) as Partial<LocalContributionDraft>;
    if (
      value.schemaVersion !== "1" ||
      typeof value.clientDraftId !== "string" ||
      !value.fields ||
      typeof value.savedAt !== "string" ||
      !Array.isArray(value.duplicateCandidates)
    ) return null;
    return {
      ...newLocalContributionDraft(value.clientDraftId),
      ...value,
      fields: { ...emptyContributionFields, ...value.fields },
      duplicateCandidates: value.duplicateCandidates,
      saveState: "saved_on_device",
    };
  } catch {
    return null;
  }
}

