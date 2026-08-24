import { describe, expect, it } from "vitest";

import { contributionMessage } from "@/lib/contributions/catalog";
import {
  emptyContributionFields,
  localAccessibleStages,
  localCompletedStages,
  localStageBlockers,
  newLocalContributionDraft,
  readLocalContributionDraft,
  serverCandidatesNeedReview,
} from "@/lib/contributions/local-draft";
import {
  contributionStageList,
  contributionStageRegistry,
  isContributionStage,
} from "@/lib/contributions/stage-registry";
import { buildPublicNavigation } from "@/lib/public-navigation";

function completeEvidence() {
  const draft = newLocalContributionDraft("device-draft");
  draft.fields = {
    ...emptyContributionFields,
    evidence_type: "public_document",
    source_uri: "https://example.test/food",
    rights_acknowledged: true,
  };
  return draft;
}

describe("contribution journey contract", () => {
  it("owns every stable stage and every public presentation key in one registry", () => {
    const stages = contributionStageList();
    expect(stages.map((stage) => stage.slug)).toEqual([
      "evidence", "details", "duplicates", "provenance", "review",
    ]);
    expect(stages.map((stage) => stage.order)).toEqual([1, 2, 3, 4, 5]);
    expect(new Set(stages.map((stage) => stage.headingAnchor)).size).toBe(5);
    expect(new Set(stages.map((stage) => stage.analyticsId)).size).toBe(5);
    for (const stage of stages) {
      expect(isContributionStage(stage.slug)).toBe(true);
      expect(contributionMessage("en", stage.headingKey)).not.toBe("");
      expect(contributionMessage("en", stage.descriptionKey)).not.toBe("");
      expect(contributionStageRegistry[stage.slug]).toBe(stage);
    }
  });

  it("unlocks only the next safe stage and repairs invalidated duplicate work", () => {
    const draft = completeEvidence();
    expect(localCompletedStages(draft)).toEqual(["evidence"]);
    expect(localAccessibleStages(draft)).toEqual(["evidence", "details"]);

    draft.fields = {
      ...draft.fields,
      name: "Dal",
      locale: "en-IN",
      category: "meal",
      portion_description: "1 bowl",
      portion_amount: "1",
      portion_unit: "serving",
      portion_grams: "240",
      energy_kcal: "280",
      protein_g: "18",
      fat_g: "6",
      carbohydrate_g: "40",
    };
    draft.duplicateQuery = "Dal|en-IN";
    draft.duplicateCandidates = [{ source: "community", sourceId: "dal", name: "Dal", locale: "en-IN" }];
    expect(localStageBlockers(draft, "duplicates")).toHaveLength(1);
    draft.fields.duplicates_resolved = true;
    expect(localStageBlockers(draft, "duplicates")).toEqual([]);

    draft.fields.name = "Prepared dal";
    expect(localStageBlockers(draft, "duplicates")[0]?.code).toBe("duplicate_check_required");
  });

  it("restores a compatible device draft and rejects malformed or unknown storage", () => {
    const original = completeEvidence();
    const restored = readLocalContributionDraft(JSON.stringify(original));
    expect(restored?.clientDraftId).toBe("device-draft");
    expect(restored?.fields.source_uri).toBe("https://example.test/food");
    expect(restored?.saveState).toBe("saved_on_device");
    expect(readLocalContributionDraft("not json")).toBeNull();
    expect(readLocalContributionDraft(JSON.stringify({ schemaVersion: "2" }))).toBeNull();
  });

  it("requires review when the server finds a duplicate absent from the earlier check", () => {
    const draft = newLocalContributionDraft("device-draft");
    draft.fields.duplicates_resolved = true;
    const newlyFound = {
      source: "community" as const,
      sourceId: "existing-dal",
      name: "Existing dal",
      locale: "en-IN",
    };

    expect(serverCandidatesNeedReview(draft, [newlyFound])).toBe(true);
    draft.duplicateCandidates = [newlyFound];
    expect(serverCandidatesNeedReview(draft, [newlyFound])).toBe(false);
    expect(serverCandidatesNeedReview(draft, [])).toBe(false);
  });

  it("publishes the real contribution entry point without a feature flag", () => {
    const contribution = buildPublicNavigation("en").find((hub) => hub.id === "contribute");
    expect(contribution?.nextAction).toMatchObject({ label: "Start a contribution", href: "/en/contribute/local/evidence" });
    expect(contribution?.children).toContainEqual(expect.objectContaining({ id: "start", href: "/en/contribute/local/evidence" }));
  });
});
