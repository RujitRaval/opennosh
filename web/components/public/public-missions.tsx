import type { CSSProperties } from "react";

import type {
  PublicMission,
  PublicMissionActivityMap,
  PublicMissionCatalog,
  PublicMissionState,
} from "@/lib/api/domain/public-missions";
import { formatMessage, formatPlural, getCatalog } from "@/lib/i18n/catalog";
import { pseudoLanguage, type InterfaceLanguage } from "@/lib/routes";

function localeFor(language: InterfaceLanguage) {
  return language === pseudoLanguage ? "en" : language;
}

function formatDate(value: string, language: InterfaceLanguage) {
  return new Intl.DateTimeFormat(localeFor(language), {
    dateStyle: "medium",
    timeStyle: "short",
    timeZone: "UTC",
  }).format(new Date(value));
}

function regionName(code: string, language: InterfaceLanguage) {
  try {
    return new Intl.DisplayNames([localeFor(language)], { type: "region" }).of(code) ?? code;
  } catch {
    return code;
  }
}

function progressLabel(mission: PublicMission, language: InterfaceLanguage) {
  const copy = getCatalog(language).missions;
  const count = mission.accepted_count;
  const messages: Record<Exclude<PublicMissionState, "unavailable" | "zero">, string> = {
    partial: copy.progressCount,
    live: copy.progressReached,
    stale: copy.progressStale,
    paused: copy.progressPaused,
    completed: copy.progressCompleted,
    released: copy.progressReleased,
    closed: copy.progressClosed,
  };
  if (mission.progress_state === "unavailable") return copy.progressUnavailable;
  if (mission.progress_state === "zero") return copy.progressZero;
  return formatMessage(messages[mission.progress_state], {
    accepted: count ?? 0,
    target: mission.acceptance_target,
  });
}

function MissionRow({ mission, language }: { mission: PublicMission; language: InterfaceLanguage }) {
  const copy = getCatalog(language).missions;
  const progress = mission.accepted_count === null
    ? 0
    : Math.min(100, Math.round((mission.accepted_count / mission.acceptance_target) * 100));
  const style = { "--mission-progress": `${progress}%` } as CSSProperties;

  return (
    <li className={`mission-row mission-progress-${mission.progress_state}`}>
      <div className="mission-row-heading">
        <p className="mono">
          {copy.gapKinds[mission.gap_kind]} · {formatMessage(copy.definition, { version: mission.definition_version })}
        </p>
        <h3>{mission.title}</h3>
        <p>{mission.summary}</p>
      </div>
      <dl className="mission-definition-ledger">
        <div>
          <dt className="mono">{copy.target}</dt>
          <dd>{formatMessage(copy.targetValue, { count: mission.acceptance_target })}</dd>
        </div>
        <div>
          <dt className="mono">{copy.destination}</dt>
          <dd>{mission.target_pack_id} / {mission.target_dataset}</dd>
        </div>
        <div>
          <dt className="mono">{copy.acceptanceRule}</dt>
          <dd>{mission.acceptance_criteria}</dd>
        </div>
        <div>
          <dt className="mono">{copy.publicReason}</dt>
          <dd>{mission.public_reason}</dd>
        </div>
      </dl>
      <div className="mission-progress" style={style}>
        <p className="mono">{copy.progress}</p>
        <strong role="status">{progressLabel(mission, language)}</strong>
        <span className="mission-progress-track" aria-hidden="true"><i /></span>
        {mission.next_review_at ? <small>{formatMessage(copy.reviewAt, { date: formatDate(mission.next_review_at, language) })}</small> : null}
        {mission.release_receipt_digest ? <small className="mono">{formatMessage(copy.releaseProof, { digest: mission.release_receipt_digest.slice(0, 12) })}</small> : null}
      </div>
    </li>
  );
}

function MissionCatalog({ catalog, language }: { catalog: PublicMissionCatalog; language: InterfaceLanguage }) {
  const copy = getCatalog(language).missions;
  if (catalog.state === "unavailable") {
    const disabled = catalog.reason === "disabled";
    return (
      <div className="mission-quiet-state" role="status">
        <strong>{disabled ? copy.disabledTitle : copy.unavailableTitle}</strong>
        <p>{disabled ? copy.disabledBody : copy.unavailableBody}</p>
      </div>
    );
  }
  if (catalog.state === "zero") {
    return (
      <div className="mission-quiet-state" role="status">
        <strong>{copy.zeroTitle}</strong>
        <p>{copy.zeroBody}</p>
      </div>
    );
  }
  return <ol className="mission-list">{catalog.missions.map((item) => <MissionRow key={item.mission_id} mission={item} language={language} />)}</ol>;
}

function MissionActivity({ activity, language }: { activity: PublicMissionActivityMap; language: InterfaceLanguage }) {
  const copy = getCatalog(language).missions;
  if (activity.state === "unavailable") {
    const disabled = activity.reason === "disabled";
    return (
      <div className="mission-activity-quiet" role="status">
        <strong>{disabled ? copy.activityDisabledTitle : copy.activityUnavailableTitle}</strong>
        <p>{disabled ? copy.activityDisabledBody : copy.activityUnavailableBody}</p>
      </div>
    );
  }
  if (activity.state === "zero") {
    return (
      <div className="mission-activity-quiet" role="status">
        <strong>{copy.activityZeroTitle}</strong>
        <p>{formatMessage(copy.activityZeroBody, { count: activity.minimum_cohort })}</p>
      </div>
    );
  }
  return (
    <ol className="mission-region-field">
      {activity.regions.map((region, index) => (
        <li key={`${region.level}:${region.region_code}`}>
          <span className="mono">{String(index + 1).padStart(2, "0")} / {region.level === "country" ? copy.country : copy.macroregion}</span>
          <strong>{regionName(region.region_code, language)}</strong>
          <span className="mono">{region.region_code}</span>
          <p>{formatPlural(copy.regionAccepted, region.accepted_count, language)}</p>
        </li>
      ))}
    </ol>
  );
}

export function PublicMissions({
  catalog,
  activity,
  language,
}: {
  catalog: PublicMissionCatalog;
  activity: PublicMissionActivityMap;
  language: InterfaceLanguage;
}) {
  const copy = getCatalog(language).missions;
  return (
    <section id="missions" className="public-missions" aria-labelledby="missions-title">
      <div className="mission-section-heading">
        <p className="mono">{copy.eyebrow}</p>
        <div>
          <h2 id="missions-title">{copy.title}</h2>
          <p>{copy.lead}</p>
        </div>
      </div>
      <div className={`mission-catalog mission-catalog-${catalog.state}`} aria-label={copy.catalogLabel}>
        <div className="mission-state-head mono"><span>{copy.catalogLabel}</span><span>{copy.states[catalog.state]}</span></div>
        <MissionCatalog catalog={catalog} language={language} />
      </div>
      <div className="mission-activity-heading">
        <p className="mono">{copy.activityEyebrow}</p>
        <div>
          <h2 id="mission-activity-title">{copy.activityTitle}</h2>
          <p>{copy.activityLead}</p>
        </div>
      </div>
      <div className={`mission-activity mission-activity-${activity.state}`} aria-labelledby="mission-activity-title" aria-label={copy.activityLabel}>
        <div className="mission-state-head mono"><span>{copy.activityLabel}</span><span>{copy.activityStates[activity.state]}</span></div>
        <MissionActivity activity={activity} language={language} />
      </div>
    </section>
  );
}

export function PublicMissionsLoading({ language = "en" }: { language?: InterfaceLanguage }) {
  const copy = getCatalog(language).missions;
  return (
    <section id="missions" className="public-missions mission-loading" aria-labelledby="missions-title" aria-busy="true">
      <div className="mission-section-heading">
        <p className="mono">{copy.eyebrow}</p>
        <div><h2 id="missions-title">{copy.title}</h2><p>{copy.lead}</p></div>
      </div>
      <div className="mission-quiet-state" role="status">
        <strong>{copy.loadingTitle}</strong>
        <p>{copy.loadingBody}</p>
      </div>
    </section>
  );
}
