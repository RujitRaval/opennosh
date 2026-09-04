import type {
  PublicComponentState,
  PublicComponentStatus,
  PublicImpactRegion,
  PublicImpactSnapshot,
  PublicImpactTotals,
  PublicIncident,
  PublicOperationsSnapshot,
  PublicReuseDeclaration,
  PublicReuseDependency,
  PublicReuseSnapshot,
} from "@/lib/api/domain/living-commons";
import { createHash } from "node:crypto";

type JsonRecord = Record<string, unknown>;

const uuid = /^[0-9a-f]{8}-[0-9a-f]{4}-[1-5][0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/i;
const digest = /^[0-9a-f]{64}$/;
const release = /^\d+\.\d+\.\d+\.\d+$/;
const componentId = /^[a-z0-9]+(?:-[a-z0-9]+)*$/;
const packId = /^[a-z0-9]+(?:-[a-z0-9]+)*$/;
const date = /^\d{4}-\d{2}-\d{2}$/;
const timestampOffset = /(?:Z|[+-]\d{2}:\d{2})$/;
const privateText = /(?:\b(?:\d{1,3}\.){3}\d{1,3}\b|(?<![0-9a-f:])(?:[0-9a-f]{1,4}:){2,7}[0-9a-f]{0,4}(?![0-9a-f:])|\barn:(?:aws|aws-cn|aws-us-gov):|\b(?:dpg|srv)-[a-z0-9]{8,}\b|\b(?:authorization|password|passwd|secret|token|api[-_ ]?key)\s*[:=]\s*\S+|(?:[a-z0-9-]+\.)+(?:internal|local|localhost|svc|cluster\.local)\b)/i;
const componentStates = new Set<PublicComponentState>([
  "operational", "degraded", "outage", "maintenance", "unknown",
]);
const unknownReasons = new Set(["missing_evidence", "stale_evidence", "malformed_evidence"]);
const incidentStates = new Set(["investigating", "identified", "monitoring", "resolved"]);
const verificationLabels = new Set(["community_declared", "unverified", "verified"]);
const dependencyKinds = new Set(["runtime", "data", "research", "derived"]);
const totalKeys = [
  "verified_adopters", "community_declarations", "accepted_contributions",
  "pack_installs", "api_reads", "artifact_downloads",
] as const;
const regionalTotalKeys = [
  "verified_adopters", "community_declarations", "accepted_contributions",
] as const;
const publicComponentIds = [
  "api", "contributions", "downloads", "evidence-processing",
  "publication", "reuse-registry", "search", "tracker",
] as const;

function malformed(field: string): never {
  throw new Error(`Malformed Living Commons ${field}`);
}

function record(value: unknown, field: string): JsonRecord {
  if (!value || typeof value !== "object" || Array.isArray(value)) malformed(field);
  return value as JsonRecord;
}

function exact(input: JsonRecord, keys: readonly string[], field: string) {
  if (Object.keys(input).some((key) => !keys.includes(key))) malformed(`${field} fields`);
}

function string(value: unknown, field: string, maximum: number): string {
  if (typeof value !== "string" || value.length === 0 || value.length > maximum) malformed(field);
  return value;
}

function publicText(value: unknown, field: string, maximum: number): string {
  const result = string(value, field, maximum);
  if (/[<>\r\n\x00-\x08\x0b\x0c\x0e-\x1f\x7f]/.test(result) || privateText.test(result)) malformed(field);
  return result;
}

function httpsUrl(value: unknown, field: string): string {
  const result = string(value, field, 2048);
  try {
    const parsed = new URL(result);
    if (parsed.protocol !== "https:" || parsed.username || parsed.password || !parsed.hostname) malformed(field);
  } catch { malformed(field); }
  return result;
}

function stable(value: unknown): unknown {
  if (Array.isArray(value)) return value.map(stable);
  if (value && typeof value === "object") {
    return Object.fromEntries(Object.entries(value as JsonRecord).sort(([a], [b]) => a.localeCompare(b)).map(([key, item]) => [key, stable(item)]));
  }
  return value;
}

function patterned(value: unknown, field: string, pattern: RegExp, maximum = 160): string {
  const result = string(value, field, maximum);
  if (!pattern.test(result)) malformed(field);
  return result;
}

function integer(value: unknown, field: string, minimum = 0): number {
  if (!Number.isSafeInteger(value) || (value as number) < minimum) malformed(field);
  return value as number;
}

function timestamp(value: unknown, field: string): string {
  const result = string(value, field, 64);
  if (!timestampOffset.test(result) || !Number.isFinite(Date.parse(result))) malformed(field);
  return result;
}

function nullableTimestamp(value: unknown, field: string): string | null {
  return value === null ? null : timestamp(value, field);
}

function sortedStrings(
  value: unknown,
  field: string,
  pattern: RegExp,
  maximumItems: number,
): string[] {
  if (!Array.isArray(value) || value.length > maximumItems) malformed(field);
  const values = value.map((item) => patterned(item, field, pattern, 64));
  if (new Set(values).size !== values.length || values.some((item, index) => index > 0 && values[index - 1]! > item)) {
    malformed(`${field} order`);
  }
  return values;
}

function isSortedBy(values: readonly string[]): boolean {
  return values.every((value, index) => index === 0 || values[index - 1]! <= value);
}

function totals(value: unknown, field: string): PublicImpactTotals {
  const input = record(value, field);
  exact(input, totalKeys, field);
  return Object.fromEntries(totalKeys.map((key) => [key, integer(input[key], `${field} ${key}`)])) as PublicImpactTotals;
}

function declaration(value: unknown): PublicReuseDeclaration {
  const input = record(value, "reuse declaration");
  exact(input, [
    "schema_version", "id", "organization_name", "project_name", "project_url", "use_case",
    "region_level", "region_code", "verification_label", "revision", "updated_at", "evidence",
  ], "reuse declaration");
  if (input.schema_version !== "1.0" || !verificationLabels.has(String(input.verification_label))) {
    malformed("reuse declaration contract");
  }
  const regionLevel = input.region_level;
  const regionCode = input.region_code;
  if ((regionLevel === null) !== (regionCode === null)) malformed("reuse region proof");
  if (regionLevel !== null && regionLevel !== "country" && regionLevel !== "macroregion") {
    malformed("reuse region level");
  }
  if (typeof regionCode === "string") {
    const pattern = regionLevel === "country" ? /^[A-Z]{2}$/ : /^\d{3}$/;
    if (!pattern.test(regionCode)) malformed("reuse region code");
  }
  const evidenceInput = input.evidence === null ? null : record(input.evidence, "reuse evidence");
  if (evidenceInput) exact(evidenceInput, ["source_url", "observed_at", "content_sha256"], "reuse evidence");
  return {
    schema_version: "1.0",
    id: patterned(input.id, "reuse declaration ID", uuid, 36),
    organization_name: publicText(input.organization_name, "organization name", 160),
    project_name: publicText(input.project_name, "project name", 160),
    project_url: input.project_url === null ? null : httpsUrl(input.project_url, "project URL"),
    use_case: publicText(input.use_case, "reuse use case", 1000),
    region_level: regionLevel as PublicReuseDeclaration["region_level"],
    region_code: regionCode as string | null,
    verification_label: input.verification_label as PublicReuseDeclaration["verification_label"],
    revision: integer(input.revision, "reuse revision", 1),
    updated_at: timestamp(input.updated_at, "reuse update time"),
    evidence: evidenceInput ? {
      source_url: httpsUrl(evidenceInput.source_url, "evidence URL"),
      observed_at: timestamp(evidenceInput.observed_at, "evidence observation time"),
      content_sha256: patterned(evidenceInput.content_sha256, "evidence digest", digest, 64),
    } : null,
  };
}

function dependency(value: unknown): PublicReuseDependency {
  const input = record(value, "reuse dependency");
  exact(input, [
    "declaration_id", "project_label", "source_pack_id", "source_release_id",
    "source_artifact_digest", "dependency_kind", "verification_label", "evidence_observed_on",
  ], "reuse dependency");
  if (input.verification_label !== "verified" || !dependencyKinds.has(String(input.dependency_kind))) {
    malformed("reuse dependency proof");
  }
  return {
    declaration_id: patterned(input.declaration_id, "dependency declaration ID", uuid, 36),
    project_label: publicText(input.project_label, "dependency project", 160),
    source_pack_id: patterned(input.source_pack_id, "dependency pack", packId),
    source_release_id: patterned(input.source_release_id, "dependency release", release),
    source_artifact_digest: patterned(input.source_artifact_digest, "dependency digest", digest, 64),
    dependency_kind: input.dependency_kind as PublicReuseDependency["dependency_kind"],
    verification_label: "verified",
    evidence_observed_on: patterned(input.evidence_observed_on, "dependency evidence date", date, 10),
  };
}

export function publicReuseSnapshot(registryValue: unknown, dependencyValue: unknown): PublicReuseSnapshot {
  const registry = record(registryValue, "reuse registry");
  const dependencies = record(dependencyValue, "reuse dependency list");
  exact(registry, ["schema_version", "declarations"], "reuse registry");
  exact(dependencies, ["schema_version", "dependencies"], "reuse dependency list");
  if (registry.schema_version !== "1.0" || dependencies.schema_version !== "1.0") {
    malformed("reuse schema version");
  }
  if (!Array.isArray(registry.declarations) || registry.declarations.length > 100) malformed("reuse list");
  if (!Array.isArray(dependencies.dependencies) || dependencies.dependencies.length > 100) malformed("dependency list");
  const declarations = registry.declarations.map(declaration);
  const edges = dependencies.dependencies.map(dependency);
  const declarationOrderValid = declarations.every((item, index) => {
    const previous = declarations[index - 1];
    return !previous || previous.updated_at > item.updated_at || (previous.updated_at === item.updated_at && previous.id <= item.id);
  });
  const edgeKeys = edges.map((item) => `${item.source_pack_id}|${item.source_release_id}|${item.dependency_kind}|${item.declaration_id}`);
  if (new Set(declarations.map((item) => item.id)).size !== declarations.length || !declarationOrderValid) malformed("reuse identity or order");
  if (new Set(edgeKeys).size !== edges.length) malformed("dependency identity");
  const declarationIds = new Set(declarations.map((item) => item.id));
  if (edges.some((edge) => !declarationIds.has(edge.declaration_id))) malformed("dependency declaration");
  return { state: "available", declarations, dependencies: edges };
}

function impactRegion(value: unknown): PublicImpactRegion {
  const input = record(value, "impact region");
  exact(input, ["region_code", "level", ...regionalTotalKeys], "impact region");
  if (input.level !== "country" && input.level !== "macroregion") malformed("impact region level");
  const pattern = input.level === "country" ? /^[A-Z]{2}$/ : /^\d{3}$/;
  return {
    region_code: patterned(input.region_code, "impact region code", pattern, 3),
    level: input.level,
    verified_adopters: integer(input.verified_adopters, "impact region verified adopters"),
    community_declarations: integer(input.community_declarations, "impact region declarations"),
    accepted_contributions: integer(input.accepted_contributions, "impact region contributions"),
  };
}

export function publicImpactSnapshot(value: unknown): PublicImpactSnapshot {
  const input = record(value, "impact snapshot");
  exact(input, [
    "schema_version", "state", "reason", "metric_definition_version", "observed_at",
    "source_checkpoint_id", "minimum_cohort", "global", "regions", "digest",
  ], "impact snapshot");
  if (input.schema_version !== "1.0" || input.metric_definition_version !== "1.0") malformed("impact version");
  if (input.state !== "unavailable" && input.state !== "zero" && input.state !== "live") malformed("impact state");
  if (input.minimum_cohort !== 10 || !Array.isArray(input.regions) || input.regions.length > 300) malformed("impact policy");
  const reason = input.reason;
  if (reason !== null && reason !== "disabled" && reason !== "proof_unavailable") malformed("impact reason");
  const regions = input.regions.map(impactRegion);
  const global = totals(input.global, "global impact");
  const unavailable = input.state === "unavailable";
  if (unavailable !== (reason !== null) || (unavailable && (input.source_checkpoint_id !== null || regions.length > 0))) {
    malformed("impact availability");
  }
  const hasActivity = Object.values(global).some(Boolean) || regions.length > 0;
  if ((unavailable && hasActivity) || (input.state === "zero" && hasActivity) || (input.state === "live" && !hasActivity)) {
    malformed("impact activity state");
  }
  if (!unavailable && input.source_checkpoint_id === null) malformed("impact checkpoint");
  if (regions.some((region) => !regionalTotalKeys.some((key) => region[key] >= 10))) {
    malformed("impact privacy cohort");
  }
  const regionKeys = regions.map((region) => `${region.level}|${region.region_code}`);
  if (new Set(regionKeys).size !== regions.length || !isSortedBy(regionKeys)) malformed("impact region identity or order");
  const unsigned = { ...input };
  delete unsigned.digest;
  const computedDigest = createHash("sha256").update(JSON.stringify(stable(unsigned))).digest("hex");
  if (input.digest !== computedDigest) malformed("impact digest proof");
  return {
    schema_version: "1.0",
    state: input.state,
    reason: reason as PublicImpactSnapshot["reason"],
    metric_definition_version: "1.0",
    observed_at: timestamp(input.observed_at, "impact observation time"),
    source_checkpoint_id: input.source_checkpoint_id === null ? null : string(input.source_checkpoint_id, "impact checkpoint", 160),
    minimum_cohort: 10,
    global,
    regions,
    digest: patterned(input.digest, "impact digest", digest, 64),
  };
}

function component(value: unknown): PublicComponentStatus {
  const input = record(value, "status component");
  exact(input, [
    "component_id", "display_name", "state", "reason", "observed_at",
    "freshness_window_seconds", "evidence_digest", "affected_versions",
  ], "status component");
  if (!componentStates.has(String(input.state) as PublicComponentState)) malformed("component state");
  const reason = input.reason;
  const isUnknown = input.state === "unknown";
  if (isUnknown !== (typeof reason === "string" && unknownReasons.has(reason))) malformed("component unknown proof");
  const observedAt = nullableTimestamp(input.observed_at, "component observation time");
  const evidenceDigest = input.evidence_digest === null ? null : patterned(input.evidence_digest, "component digest", digest, 64);
  if (!isUnknown && (observedAt === null || evidenceDigest === null)) malformed("component monitor proof");
  if (reason === "missing_evidence" && (observedAt !== null || evidenceDigest !== null)) malformed("missing component proof");
  if ((reason === "malformed_evidence") && (observedAt !== null || evidenceDigest !== null)) malformed("malformed component proof");
  if (reason === "stale_evidence" && (observedAt === null || evidenceDigest === null)) malformed("stale component proof");
  return {
    component_id: patterned(input.component_id, "component ID", componentId, 64),
    display_name: publicText(input.display_name, "component name", 80),
    state: input.state as PublicComponentState,
    reason: reason as PublicComponentStatus["reason"],
    observed_at: observedAt,
    freshness_window_seconds: integer(input.freshness_window_seconds, "freshness window", 30),
    evidence_digest: evidenceDigest,
    affected_versions: sortedStrings(input.affected_versions, "affected versions", release, 20),
  };
}

function incident(value: unknown): PublicIncident {
  const input = record(value, "incident");
  exact(input, [
    "incident_id", "title", "public_summary", "affected_component_ids", "affected_versions",
    "guidance", "state", "opened_at", "updated_at", "resolved_at", "recovery_evidence",
  ], "incident");
  if (!incidentStates.has(String(input.state))) malformed("incident state");
  const recovery = input.recovery_evidence === null ? null : record(input.recovery_evidence, "recovery evidence");
  if (recovery) exact(recovery, ["status", "observed_at", "content_sha256"], "recovery evidence");
  const resolved = input.state === "resolved";
  if (resolved !== (input.resolved_at !== null && recovery !== null) || (recovery && recovery.status !== "verified")) {
    malformed("incident recovery proof");
  }
  const openedAt = timestamp(input.opened_at, "incident opening time");
  const updatedAt = timestamp(input.updated_at, "incident update time");
  const resolvedAt = nullableTimestamp(input.resolved_at, "incident resolution time");
  if (Date.parse(updatedAt) < Date.parse(openedAt) || (resolvedAt !== null && resolvedAt !== updatedAt)) {
    malformed("incident chronology");
  }
  return {
    incident_id: patterned(input.incident_id, "incident ID", uuid, 36),
    title: publicText(input.title, "incident title", 160),
    public_summary: publicText(input.public_summary, "incident summary", 1000),
    affected_component_ids: sortedStrings(input.affected_component_ids, "affected components", componentId, 20),
    affected_versions: sortedStrings(input.affected_versions, "affected versions", release, 20),
    guidance: publicText(input.guidance, "incident guidance", 1000),
    state: input.state as PublicIncident["state"],
    opened_at: openedAt,
    updated_at: updatedAt,
    resolved_at: resolvedAt,
    recovery_evidence: recovery ? {
      status: "verified",
      observed_at: timestamp(recovery.observed_at, "recovery observation time"),
      content_sha256: patterned(recovery.content_sha256, "recovery digest", digest, 64),
    } : null,
  };
}

export function publicOperationsSnapshot(statusValue: unknown, incidentsValue: unknown): PublicOperationsSnapshot {
  const status = record(statusValue, "public status");
  const incidents = record(incidentsValue, "public incidents");
  exact(status, ["schema_version", "configuration_digest", "components"], "public status");
  exact(incidents, ["schema_version", "incidents"], "public incidents");
  if (status.schema_version !== "1.0" || incidents.schema_version !== "1.0") malformed("operations version");
  if (!Array.isArray(status.components) || status.components.length !== 8) malformed("component inventory");
  if (!Array.isArray(incidents.incidents) || incidents.incidents.length > 100) malformed("incident list");
  const components = status.components.map(component);
  if (components.some((item) => item.freshness_window_seconds > 3600)) malformed("component freshness");
  if (components.map((item) => item.component_id).join("|") !== publicComponentIds.join("|")) {
    malformed("component inventory order");
  }
  const publicIncidents = incidents.incidents.map(incident);
  const incidentOrderValid = publicIncidents.every((item, index) => {
    const previous = publicIncidents[index - 1];
    return !previous || previous.opened_at > item.opened_at || (previous.opened_at === item.opened_at && previous.incident_id <= item.incident_id);
  });
  if (new Set(publicIncidents.map((item) => item.incident_id)).size !== publicIncidents.length || !incidentOrderValid) {
    malformed("incident identity or order");
  }
  return {
    state: "available",
    configuration_digest: patterned(status.configuration_digest, "status configuration digest", digest, 64),
    components,
    incidents: publicIncidents,
  };
}
