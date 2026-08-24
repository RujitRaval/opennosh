import Link from "next/link";

import type {
  AcceptedActivityEvent,
  PublicCommonsSnapshot,
} from "@/lib/api/domain/public-commons";
import { routes, type InterfaceLanguage } from "@/lib/routes";
import { fallbackLanguage, formatMessage, formatPlural, getCatalog, pseudoLanguage } from "@/lib/i18n/catalog";

function formatDateTime(value: string, language: InterfaceLanguage) {
  return new Intl.DateTimeFormat(language === pseudoLanguage ? fallbackLanguage : language, {
    year: "numeric",
    month: "short",
    day: "numeric",
    hour: "numeric",
    minute: "2-digit",
    timeZone: "UTC",
    timeZoneName: "short",
  }).format(new Date(value));
}

function formatDate(value: string, language: InterfaceLanguage) {
  return new Intl.DateTimeFormat(language === pseudoLanguage ? fallbackLanguage : language, {
    dateStyle: "long",
    timeZone: "UTC",
  }).format(new Date(value));
}

function hasVerifiedRelease(snapshot: PublicCommonsSnapshot) {
  return (
    snapshot.release !== null &&
    snapshot.verified_record_count !== null &&
    snapshot.state !== "illustrative" &&
    snapshot.state !== "unavailable"
  );
}

export function HeroReleaseProof({
  snapshot,
  language,
}: {
  snapshot: PublicCommonsSnapshot;
  language: InterfaceLanguage;
}) {
  const copy = getCatalog(language).truth;
  if (!hasVerifiedRelease(snapshot) || !snapshot.release) return null;
  return (
    <aside className="hero-proof" aria-label={copy.verifiedRelease}>
      <strong>{snapshot.verified_record_count?.toLocaleString(language)}</strong>
      <span>{formatPlural(copy.verifiedRecords, snapshot.verified_record_count ?? 0, language)}</span>
      <small className="mono">
        {formatMessage(copy.release, { version: snapshot.release.version })}
        {snapshot.state === "stale" ? copy.staleSuffix : ""}
      </small>
    </aside>
  );
}

export function FooterReleaseProof({
  snapshot,
  language,
}: {
  snapshot?: PublicCommonsSnapshot;
  language: InterfaceLanguage;
}) {
  const copy = getCatalog(language).truth;
  if (!snapshot || !hasVerifiedRelease(snapshot) || !snapshot.release) return null;
  return (
    <p className="footer-release-proof mono">
      <strong>{snapshot.verified_record_count?.toLocaleString(language)}</strong>{" "}
      {formatPlural(copy.verifiedRecords, snapshot.verified_record_count ?? 0, language)}
      <span>
        {formatMessage(copy.release, { version: snapshot.release.version })}
        {snapshot.state === "stale" ? copy.staleSuffix : ""}
      </span>
    </p>
  );
}

function ActivityActions({ language }: { language: InterfaceLanguage }) {
  const copy = getCatalog(language).truth;
  return (
    <nav className="activity-actions" aria-label={copy.activityActions}>
      <Link href={routes.publicHub("explore", language)}>{copy.searchRecords}</Link>
      <Link href={routes.publicHub("contribute", language)}>{copy.contributeFood}</Link>
    </nav>
  );
}

function ActivityEvents({
  events,
  language,
}: {
  events: AcceptedActivityEvent[];
  language: InterfaceLanguage;
}) {
  const copy = getCatalog(language).truth;
  return (
    <ol className="activity-events">
      {events.map((event) => (
        <li key={event.event_id}>
          <span className="activity-event-type mono">{copy.eventLabels[event.event_type]}</span>
          <div>
            <strong>{event.summary}</strong>
            <p>{event.food_locale}</p>
          </div>
          <time className="mono" dateTime={event.accepted_at}>
            {formatDateTime(event.accepted_at, language)}
          </time>
          <a
            className="mono"
            href={`https://github.com/RujitRaval/opennosh/commit/${event.source_commit}`}
            aria-label={formatMessage(copy.sourceCommit, { summary: event.summary })}
          >
            {event.source_commit.slice(0, 7)} ↗
          </a>
        </li>
      ))}
    </ol>
  );
}

export function AcceptedActivity({
  snapshot,
  language,
}: {
  snapshot: PublicCommonsSnapshot;
  language: InterfaceLanguage;
}) {
  const copy = getCatalog(language).truth;
  const events = snapshot.activity.events ?? [];
  const recent = snapshot.activity.most_recent_verified_record;
  const status = copy.statuses[snapshot.state];

  return (
    <div
      className={`activity-field activity-state-${snapshot.state}`}
      data-activity-state={snapshot.state}
      data-motion-state={snapshot.state === "live" ? "running" : "paused"}
    >
      <div className="activity-head mono">
        <span>{copy.heading}</span>
        <span>{status}</span>
      </div>

      {snapshot.state === "live" && (
        <>
          <p className="activity-summary" role="status">
            <strong>{formatPlural(copy.acceptedChanges, snapshot.activity.accepted_count, language)}</strong>
            <span>
              {formatMessage(copy.throughRelease, {
                date: formatDateTime(snapshot.activity.ends_at, language),
                version: snapshot.release?.version ?? "",
              })}
            </span>
          </p>
          <ActivityEvents events={events} language={language} />
        </>
      )}

      {snapshot.state === "quiet" && (
        <>
          <div className="quiet-orbit" aria-hidden="true"><i /><i /><i /></div>
          <p className="quiet-state" role="status">
            <strong>{copy.quietTitle}</strong>
            {recent ? (
              <span>
                {copy.recentPrefix} <Link href={recent.href}>{recent.name}</Link>, {recent.food_locale}, {copy.recentOn} {formatDate(recent.verified_at, language)}.
              </span>
            ) : (
              <span>{copy.noRecent}</span>
            )}
          </p>
          <ActivityActions language={language} />
        </>
      )}

      {snapshot.state === "stale" && (
        <>
          <p className="activity-summary activity-warning" role="status">
            <strong>{copy.staleTitle}</strong>
            <span>
              {formatMessage(copy.frozen, { version: snapshot.release?.version ?? "" })}
              {snapshot.freshness.stale_since
                ? formatMessage(copy.staleSince, { date: formatDateTime(snapshot.freshness.stale_since, language) })
                : copy.staleUnknown}
              {formatMessage(copy.retried, { date: formatDateTime(snapshot.freshness.checked_at, language) })}
            </span>
          </p>
          {events.length > 0 && <ActivityEvents events={events} language={language} />}
          <ActivityActions language={language} />
        </>
      )}

      {snapshot.state === "partial" && (
        <>
          <p className="activity-summary activity-warning" role="status">
            <strong>{copy.partialTitle}</strong>
            <span>
              {formatMessage(copy.partialBody, { version: snapshot.release?.version ?? "" })}
            </span>
          </p>
          {events.length > 0 && <ActivityEvents events={events} language={language} />}
          <ActivityActions language={language} />
        </>
      )}

      {snapshot.state === "illustrative" && (
        <>
          <p className="illustrative-label mono">{copy.illustrativeLabel}</p>
          <p className="activity-summary" role="status">
            <strong>{copy.illustrativeTitle}</strong>
            <span>{copy.illustrativeBody}</span>
          </p>
          {events.length > 0 && <ActivityEvents events={events} language={language} />}
        </>
      )}

      {snapshot.state === "unavailable" && (
        <>
          <div className="quiet-orbit quiet-orbit-unavailable" aria-hidden="true"><i /><i /><i /></div>
          <p className="quiet-state" role="status">
            <strong>{copy.unavailableTitle}</strong>
            <span>{copy.unavailableBody}</span>
          </p>
          <ActivityActions language={language} />
        </>
      )}

      <dl className="activity-legend mono">
        {Object.entries(copy.legend).map(([key, item]) => (
          <div key={key}><dt>{item.term}</dt><dd>{item.description}</dd></div>
        ))}
      </dl>
    </div>
  );
}

export function AcceptedActivityLoading({ language = "en" }: { language?: InterfaceLanguage }) {
  const copy = getCatalog(language).truth;
  return (
    <div className="activity-field activity-state-loading" aria-busy="true">
      <div className="activity-head mono"><span>{copy.heading}</span><span>{copy.statuses.loading}</span></div>
      <div className="quiet-orbit" aria-hidden="true"><i /><i /><i /></div>
      <p className="quiet-state" role="status">
        <strong>{copy.checking}</strong>
        <span>{copy.checkingBody}</span>
      </p>
    </div>
  );
}
