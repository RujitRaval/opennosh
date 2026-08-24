import type {
  ContributionCapability as TransportCapability,
  ContributionStage as TransportStage,
} from "@/lib/generated/client/types.gen";
import {
  contributionStages,
  type ContributionCapability,
  type ContributionFields,
  type ContributionStage,
} from "@/lib/contributions/domain";

const transportStageParity: Record<TransportStage, ContributionStage> = {
  evidence: "evidence",
  details: "details",
  duplicates: "duplicates",
  provenance: "provenance",
  review: "review",
};

function stage(value: TransportStage): ContributionStage {
  return transportStageParity[value];
}

function safeStatusHref(value: string): string {
  if (!value.startsWith("/") || value.startsWith("//") || value.includes("\0")) {
    throw new Error("Malformed contribution receipt link");
  }
  return value;
}

function requiredBoolean(value: boolean | undefined, field: string): boolean {
  if (typeof value !== "boolean") throw new Error(`Malformed contribution ${field}`);
  return value;
}

export function contributionCapability(value: TransportCapability): ContributionCapability {
  if (value.schema_version !== "1" || value.workflow_version !== "1") {
    throw new Error("Unsupported contribution workflow");
  }
  const completedStages = value.completed_stages.map(stage);
  const accessibleStages = value.accessible_stages.map(stage);
  if (
    new Set(completedStages).size !== completedStages.length ||
    new Set(accessibleStages).size !== accessibleStages.length ||
    !accessibleStages.includes(stage(value.resolved_stage))
  ) throw new Error("Malformed contribution stage capability");
  const fields: ContributionFields = {
    evidence_type: value.fields.evidence_type ?? null,
    source_uri: value.fields.source_uri ?? "",
    rights_acknowledged: requiredBoolean(value.fields.rights_acknowledged, "rights acknowledgement"),
    name: value.fields.name ?? "",
    name_local: value.fields.name_local ?? "",
    locale: value.fields.locale ?? "",
    category: value.fields.category ?? "",
    portion_description: value.fields.portion_description ?? "",
    portion_amount: value.fields.portion_amount ?? "",
    portion_unit: value.fields.portion_unit ?? "g",
    portion_grams: value.fields.portion_grams ?? "",
    energy_kcal: value.fields.energy_kcal ?? "",
    protein_g: value.fields.protein_g ?? "",
    fat_g: value.fields.fat_g ?? "",
    carbohydrate_g: value.fields.carbohydrate_g ?? "",
    ingredients: value.fields.ingredients ?? "",
    duplicates_resolved: requiredBoolean(value.fields.duplicates_resolved, "duplicate resolution"),
    pack_id: value.fields.pack_id ?? "",
    source_date: value.fields.source_date ?? "",
    attribution: value.fields.attribution ?? "",
    source_license: value.fields.source_license ?? null,
    review_acknowledged: requiredBoolean(value.fields.review_acknowledged, "review acknowledgement"),
  };
  return {
    draftId: value.draft_id,
    draftVersion: value.draft_version,
    reviewState: value.review_state,
    completedStages,
    accessibleStages,
    blockers: value.blockers.map((blocker) => ({
      stage: stage(blocker.stage),
      field: blocker.field ?? null,
      code: blocker.code,
      message: blocker.message,
    })),
    nextSafeStage: stage(value.next_safe_stage),
    requestedStage: stage(value.requested_stage),
    resolvedStage: stage(value.resolved_stage),
    repairReason: value.repair_reason ?? null,
    savedAt: value.saved_at,
    fields,
    duplicateCandidates: value.duplicate_candidates.map((candidate) => ({
      source: candidate.source,
      sourceId: candidate.source_id,
      name: candidate.name,
      locale: candidate.locale ?? null,
    })),
    receipt: value.receipt
      ? {
          submissionId: value.receipt.submission_id,
          submittedAt: value.receipt.submitted_at,
          acknowledgementDueAt: value.receipt.acknowledgement_due_at,
          attribution: value.receipt.attribution,
          statusHref: safeStatusHref(value.receipt.status_href),
        }
      : null,
  };
}

export function assertGeneratedContributionStageParity(): true {
  return contributionStages.every((item) => transportStageParity[item] === item) as true;
}
