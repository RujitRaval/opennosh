export type ReuseVerificationLabel = "community_declared" | "unverified" | "verified";
export type ReuseRegionLevel = "country" | "macroregion";
export type ReuseDependencyKind = "runtime" | "data" | "research" | "derived";

export type PublicReuseEvidence = {
  source_url: string;
  observed_at: string;
  content_sha256: string;
};

export type PublicReuseDeclaration = {
  schema_version: "1.0";
  id: string;
  organization_name: string;
  project_name: string;
  project_url: string | null;
  use_case: string;
  region_level: ReuseRegionLevel | null;
  region_code: string | null;
  verification_label: ReuseVerificationLabel;
  revision: number;
  updated_at: string;
  evidence: PublicReuseEvidence | null;
};

export type PublicReuseDependency = {
  declaration_id: string;
  project_label: string;
  source_pack_id: string;
  source_release_id: string;
  source_artifact_digest: string;
  dependency_kind: ReuseDependencyKind;
  verification_label: "verified";
  evidence_observed_on: string;
};

export type PublicReuseSnapshot = {
  state: "available" | "unavailable";
  declarations: PublicReuseDeclaration[];
  dependencies: PublicReuseDependency[];
};

export type PublicImpactTotals = {
  verified_adopters: number;
  community_declarations: number;
  accepted_contributions: number;
  pack_installs: number;
  api_reads: number;
  artifact_downloads: number;
};

export type PublicImpactRegion = {
  region_code: string;
  level: ReuseRegionLevel;
  verified_adopters: number;
  community_declarations: number;
  accepted_contributions: number;
};

export type PublicImpactSnapshot = {
  schema_version: "1.0";
  state: "unavailable" | "zero" | "live";
  reason: "disabled" | "proof_unavailable" | null;
  metric_definition_version: "1.0";
  observed_at: string;
  source_checkpoint_id: string | null;
  minimum_cohort: 10;
  global: PublicImpactTotals;
  regions: PublicImpactRegion[];
  digest: string;
};

export type PublicComponentState =
  | "operational"
  | "degraded"
  | "outage"
  | "maintenance"
  | "unknown";

export type PublicComponentStatus = {
  component_id: string;
  display_name: string;
  state: PublicComponentState;
  reason: "missing_evidence" | "stale_evidence" | "malformed_evidence" | null;
  observed_at: string | null;
  freshness_window_seconds: number;
  evidence_digest: string | null;
  affected_versions: string[];
};

export type PublicIncident = {
  incident_id: string;
  title: string;
  public_summary: string;
  affected_component_ids: string[];
  affected_versions: string[];
  guidance: string;
  state: "investigating" | "identified" | "monitoring" | "resolved";
  opened_at: string;
  updated_at: string;
  resolved_at: string | null;
  recovery_evidence: {
    status: "verified";
    observed_at: string;
    content_sha256: string;
  } | null;
};

export type PublicOperationsSnapshot = {
  state: "available" | "unavailable";
  configuration_digest: string | null;
  components: PublicComponentStatus[];
  incidents: PublicIncident[];
};
