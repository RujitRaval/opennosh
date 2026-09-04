import type {
  PublicImpactSnapshot,
  PublicOperationsSnapshot,
  PublicReuseSnapshot,
} from "@/lib/api/domain/living-commons";
import { formatMessage, formatPlural, getCatalog } from "@/lib/i18n/catalog";
import { pseudoLanguage, type InterfaceLanguage } from "@/lib/routes";

function locale(language: InterfaceLanguage) {
  return language === pseudoLanguage ? "en" : language;
}

function formatDate(value: string, language: InterfaceLanguage) {
  return new Intl.DateTimeFormat(locale(language), {
    dateStyle: "medium",
    timeStyle: "short",
    timeZone: "UTC",
  }).format(new Date(value));
}

function digest(value: string) {
  return `${value.slice(0, 12)}…${value.slice(-8)}`;
}

export function PublicReuseSurface({
  language,
  snapshot,
}: {
  language: InterfaceLanguage;
  snapshot: PublicReuseSnapshot;
}) {
  const copy = getCatalog(language).livingCommons.reuse;
  if (snapshot.state === "unavailable") {
    return <section className="commons-state" role="status"><h2>{copy.unavailableTitle}</h2><p>{copy.unavailableBody}</p></section>;
  }
  if (snapshot.declarations.length === 0) {
    return <section className="commons-state" role="status"><h2>{copy.emptyTitle}</h2><p>{copy.emptyBody}</p></section>;
  }
  return (
    <>
      <section className="commons-ledger" aria-labelledby="reuse-registry-title">
        <div className="commons-section-heading">
          <p className="eyebrow">{copy.registryEyebrow}</p>
          <h2 id="reuse-registry-title">{copy.registryTitle}</h2>
        </div>
        <ol className="reuse-list">
          {snapshot.declarations.map((item) => (
            <li key={item.id}>
              <div className="commons-card-heading">
                <div><p className="mono">{item.organization_name}</p><h3>{item.project_name}</h3></div>
                <strong className={`proof-chip proof-${item.verification_label}`}>{copy.labels[item.verification_label]}</strong>
              </div>
              <p>{item.use_case}</p>
              {item.project_url ? <p className="mono proof-text">{copy.projectUrl}: {item.project_url}</p> : null}
              <dl className="compact-proof">
                <div><dt>{copy.revision}</dt><dd>{item.revision}</dd></div>
                <div><dt>{copy.updated}</dt><dd>{formatDate(item.updated_at, language)}</dd></div>
                {item.region_code ? <div><dt>{copy.region}</dt><dd>{item.region_level} / {item.region_code}</dd></div> : null}
                {item.evidence ? <div><dt>{copy.evidence}</dt><dd className="mono">{digest(item.evidence.content_sha256)}</dd></div> : null}
              </dl>
            </li>
          ))}
        </ol>
      </section>
      <section className="commons-ledger" aria-labelledby="dependency-title">
        <div className="commons-section-heading"><p className="eyebrow">{copy.dependencyEyebrow}</p><h2 id="dependency-title">{copy.dependencyTitle}</h2></div>
        {snapshot.dependencies.length === 0 ? <p>{copy.noDependencies}</p> : (
          <div className="dependency-table" role="table" aria-label={copy.dependencyTitle}>
            {snapshot.dependencies.map((edge) => (
              <div role="row" key={`${edge.declaration_id}-${edge.source_pack_id}-${edge.source_release_id}-${edge.dependency_kind}`}>
                <span role="cell"><strong>{edge.project_label}</strong><small>{edge.dependency_kind}</small></span>
                <span role="cell" className="mono">{edge.source_pack_id} / {edge.source_release_id}</span>
                <span role="cell" className="mono">{digest(edge.source_artifact_digest)}</span>
                <span role="cell" className="proof-chip proof-verified">{copy.labels.verified}</span>
              </div>
            ))}
          </div>
        )}
      </section>
    </>
  );
}

const impactKeys = [
  "verified_adopters", "community_declarations", "accepted_contributions",
  "pack_installs", "api_reads", "artifact_downloads",
] as const;

export function PublicImpactSurface({
  language,
  snapshot,
}: {
  language: InterfaceLanguage;
  snapshot: PublicImpactSnapshot;
}) {
  const copy = getCatalog(language).livingCommons.impact;
  if (snapshot.state === "unavailable") {
    return <section className="commons-state" role="status"><h2>{copy.unavailableTitle}</h2><p>{copy.unavailableBody}</p></section>;
  }
  return (
    <>
      {snapshot.state === "zero" ? <section className="commons-state" role="status"><h2>{copy.zeroTitle}</h2><p>{copy.zeroBody}</p></section> : null}
      <section className="impact-proof" aria-labelledby="impact-totals-title">
        <div className="commons-section-heading"><p className="eyebrow">{copy.globalEyebrow}</p><h2 id="impact-totals-title">{copy.globalTitle}</h2></div>
        <dl className="impact-totals">
          {impactKeys.map((key) => <div key={key}><dt>{copy.metrics[key]}</dt><dd>{snapshot.global[key].toLocaleString(locale(language))}</dd></div>)}
        </dl>
        <p className="mono proof-text">{formatMessage(copy.checkpoint, { checkpoint: snapshot.source_checkpoint_id ?? copy.none })} · {digest(snapshot.digest)} · {formatDate(snapshot.observed_at, language)}</p>
      </section>
      <section className="commons-ledger" aria-labelledby="impact-regions-title">
        <div className="commons-section-heading"><p className="eyebrow">{formatMessage(copy.privacyEyebrow, { count: snapshot.minimum_cohort })}</p><h2 id="impact-regions-title">{copy.regionsTitle}</h2><p>{copy.regionsLead}</p></div>
        {snapshot.regions.length === 0 ? <p>{copy.noRegions}</p> : (
          <ol className="region-list">
            {snapshot.regions.map((region) => (
              <li key={`${region.level}-${region.region_code}`}>
                <strong>{region.level} / {region.region_code}</strong>
                <span>{formatPlural(copy.regionAdopters, region.verified_adopters, language)}</span>
                <span>{formatPlural(copy.regionDeclarations, region.community_declarations, language)}</span>
                <span>{formatPlural(copy.regionContributions, region.accepted_contributions, language)}</span>
              </li>
            ))}
          </ol>
        )}
      </section>
    </>
  );
}

export function PublicOperationsSurface({
  language,
  snapshot,
}: {
  language: InterfaceLanguage;
  snapshot: PublicOperationsSnapshot;
}) {
  const copy = getCatalog(language).livingCommons.status;
  if (snapshot.state === "unavailable") {
    return <section className="commons-state" role="status"><h2>{copy.unavailableTitle}</h2><p>{copy.unavailableBody}</p></section>;
  }
  return (
    <>
      <section className="commons-ledger" aria-labelledby="component-status-title">
        <div className="commons-section-heading"><p className="eyebrow">{copy.componentsEyebrow}</p><h2 id="component-status-title">{copy.componentsTitle}</h2></div>
        <ol className="component-list">
          {snapshot.components.map((component) => (
            <li key={component.component_id}>
              <div><h3>{component.display_name}</h3><p className="mono">{component.component_id}</p></div>
              <strong className={`status-chip status-${component.state}`}>{copy.states[component.state]}</strong>
              <p>{component.reason ? copy.reasons[component.reason] : copy.monitorVerified}</p>
              <p className="mono proof-text">{component.observed_at ? formatDate(component.observed_at, language) : copy.noObservation}{component.evidence_digest ? ` · ${digest(component.evidence_digest)}` : ""}</p>
            </li>
          ))}
        </ol>
        <p className="mono proof-text">{copy.configuration}: {snapshot.configuration_digest ? digest(snapshot.configuration_digest) : copy.none}</p>
      </section>
      <section className="commons-ledger" aria-labelledby="incident-title">
        <div className="commons-section-heading"><p className="eyebrow">{copy.incidentsEyebrow}</p><h2 id="incident-title">{copy.incidentsTitle}</h2></div>
        {snapshot.incidents.length === 0 ? <p className="commons-state-inline" role="status">{copy.noIncidents}</p> : (
          <ol className="incident-list">
            {snapshot.incidents.map((incident) => (
              <li key={incident.incident_id}>
                <div className="commons-card-heading"><h3>{incident.title}</h3><strong className={`status-chip status-${incident.state}`}>{copy.incidentStates[incident.state]}</strong></div>
                <p>{incident.public_summary}</p><p><strong>{copy.guidance}:</strong> {incident.guidance}</p>
                <p className="mono proof-text">{incident.affected_component_ids.join(" · ")} · {formatDate(incident.updated_at, language)}</p>
                {incident.recovery_evidence ? <p className="mono proof-text">{copy.recovery}: {digest(incident.recovery_evidence.content_sha256)}</p> : null}
              </li>
            ))}
          </ol>
        )}
      </section>
    </>
  );
}
