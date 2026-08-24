import Link from "next/link";

import type {
  AcceptedActivityEvent,
  PublicCommonsSnapshot,
} from "@/lib/api/domain/public-commons";
import { routes, type InterfaceLanguage } from "@/lib/routes";

function formatDateTime(value: string, language: InterfaceLanguage) {
  return new Intl.DateTimeFormat(language, {
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
  return new Intl.DateTimeFormat(language, {
    dateStyle: "long",
    timeZone: "UTC",
  }).format(new Date(value));
}

function eventLabel(event: AcceptedActivityEvent) {
  return {
    food: "Food",
    source: "Source",
    portion: "Portion",
    pack: "Pack",
  }[event.event_type];
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
  if (!hasVerifiedRelease(snapshot) || !snapshot.release) return null;
  return (
    <aside className="hero-proof" aria-label="Verified commons release">
      <strong>{snapshot.verified_record_count?.toLocaleString(language)}</strong>
      <span>verified records</span>
      <small className="mono">
        release {snapshot.release.version}
        {snapshot.state === "stale" ? " · stale" : ""}
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
  if (!snapshot || !hasVerifiedRelease(snapshot) || !snapshot.release) return null;
  return (
    <p className="footer-release-proof mono">
      <strong>{snapshot.verified_record_count?.toLocaleString(language)}</strong> verified records
      <span>
        release {snapshot.release.version}
        {snapshot.state === "stale" ? " · stale" : ""}
      </span>
    </p>
  );
}

function ActivityActions({ language }: { language: InterfaceLanguage }) {
  return (
    <nav className="activity-actions" aria-label="Commons activity actions">
      <Link href={routes.publicHub("explore", language)}>Search verified records</Link>
      <Link href={routes.publicHub("contribute", language)}>Contribute a food</Link>
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
  return (
    <ol className="activity-events">
      {events.map((event) => (
        <li key={event.event_id}>
          <span className="activity-event-type mono">{eventLabel(event)}</span>
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
            aria-label={`View source commit for ${event.summary}`}
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
  const events = snapshot.activity.events ?? [];
  const recent = snapshot.activity.most_recent_verified_record;
  const status = {
    live: "Verified release",
    quiet: "Quiet · verified",
    stale: "Stale snapshot",
    partial: "Partial snapshot",
    illustrative: "Illustrative data",
    unavailable: "Unavailable",
  }[snapshot.state];

  return (
    <div
      className={`activity-field activity-state-${snapshot.state}`}
      data-activity-state={snapshot.state}
      data-motion-state={snapshot.state === "live" ? "running" : "paused"}
    >
      <div className="activity-head mono">
        <span>Accepted activity / last 24h</span>
        <span>{status}</span>
      </div>

      {snapshot.state === "live" && (
        <>
          <p className="activity-summary" role="status">
            <strong>{snapshot.activity.accepted_count} accepted changes</strong>
            <span>
              Through {formatDateTime(snapshot.activity.ends_at, language)} · release {snapshot.release?.version}
            </span>
          </p>
          <ActivityEvents events={events} language={language} />
        </>
      )}

      {snapshot.state === "quiet" && (
        <>
          <div className="quiet-orbit" aria-hidden="true"><i /><i /><i /></div>
          <p className="quiet-state" role="status">
            <strong>No accepted changes in the last 24 hours.</strong>
            {recent ? (
              <span>
                Most recently verified: <Link href={recent.href}>{recent.name}</Link>, {recent.food_locale}, on {formatDate(recent.verified_at, language)}.
              </span>
            ) : (
              <span>The signed release contains no earlier verified record to show here.</span>
            )}
          </p>
          <ActivityActions language={language} />
        </>
      )}

      {snapshot.state === "stale" && (
        <>
          <p className="activity-summary activity-warning" role="status">
            <strong>Activity is temporarily stale.</strong>
            <span>
              Frozen at the last verified release {snapshot.release?.version}.
              {snapshot.freshness.stale_since
                ? ` Stale since ${formatDateTime(snapshot.freshness.stale_since, language)}.`
                : " The stale time is unavailable."}
              {` Verification last retried ${formatDateTime(snapshot.freshness.checked_at, language)}.`}
            </span>
          </p>
          {events.length > 0 && <ActivityEvents events={events} language={language} />}
          <ActivityActions language={language} />
        </>
      )}

      {snapshot.state === "partial" && (
        <>
          <p className="activity-summary activity-warning" role="status">
            <strong>Accepted activity is still catching up.</strong>
            <span>
              The record count is verified for release {snapshot.release?.version}; this event list may be incomplete.
            </span>
          </p>
          {events.length > 0 && <ActivityEvents events={events} language={language} />}
          <ActivityActions language={language} />
        </>
      )}

      {snapshot.state === "illustrative" && (
        <>
          <p className="illustrative-label mono">Illustrative data</p>
          <p className="activity-summary" role="status">
            <strong>This preview is not production activity.</strong>
            <span>Sample events and counts are visibly separated from verified commons facts.</span>
          </p>
          {events.length > 0 && <ActivityEvents events={events} language={language} />}
        </>
      )}

      {snapshot.state === "unavailable" && (
        <>
          <div className="quiet-orbit quiet-orbit-unavailable" aria-hidden="true"><i /><i /><i /></div>
          <p className="quiet-state" role="status">
            <strong>Accepted activity is unavailable.</strong>
            <span>No verified release snapshot is available, so opennosh is not showing a count or invented activity.</span>
          </p>
          <ActivityActions language={language} />
        </>
      )}

      <dl className="activity-legend mono">
        <div><dt>Food</dt><dd>Accepted new record</dd></div>
        <div><dt>Source</dt><dd>Evidence attached</dd></div>
        <div><dt>Portion</dt><dd>Verified serving</dd></div>
        <div><dt>Pack</dt><dd>Version published</dd></div>
      </dl>
    </div>
  );
}

export function AcceptedActivityLoading() {
  return (
    <div className="activity-field activity-state-loading" aria-busy="true">
      <div className="activity-head mono"><span>Accepted activity / last 24h</span><span>Loading</span></div>
      <div className="quiet-orbit" aria-hidden="true"><i /><i /><i /></div>
      <p className="quiet-state" role="status">
        <strong>Checking the latest accepted events.</strong>
        <span>No speculative pulses or counts are shown while the signed release is resolved.</span>
      </p>
    </div>
  );
}
