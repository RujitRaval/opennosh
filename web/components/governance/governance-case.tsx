"use client";

import Link from "next/link";
import { type FormEvent, useCallback, useEffect, useState } from "react";

import { governanceApi } from "@/lib/api/governance";
import type { GovernanceReviewCase } from "@/lib/api/domain/governance";
import { routes } from "@/lib/routes";

function readable(value: string): string {
  return value.replaceAll("_", " ");
}

function truthFor(reviewCase: GovernanceReviewCase): string {
  if (reviewCase.state === "approved") {
    return "Approval is recorded. Publication is a separate protected workflow and is not claimed here.";
  }
  if (reviewCase.state === "rejected") return "This version was rejected and has not been published.";
  if (reviewCase.state === "closed") return "This exact version is closed. Its history remains immutable.";
  return "This contribution is not published. A steward decision does not bypass publication checks.";
}

function nextActionFor(reviewCase: GovernanceReviewCase): string {
  if (reviewCase.state === "pending" || reviewCase.state === "reopened") return "An eligible steward must acknowledge the case.";
  if (reviewCase.state === "in_review") return reviewCase.pause_reason
    ? `Review is paused until ${new Date(reviewCase.next_review_at ?? "").toLocaleString()}.`
    : "The responsible steward must record a reasoned decision.";
  if (reviewCase.state === "changes_requested") return "The contributor may revise this exact draft or open a dispute.";
  if (reviewCase.state === "disputed") return "An active steward must resolve the open dispute.";
  if (reviewCase.state === "appealed") return "A different active steward must decide the appeal.";
  return "No ordinary review action remains for this exact version.";
}

export function GovernanceCase({ reviewCaseId }: { reviewCaseId: string }) {
  const [reviewCase, setReviewCase] = useState<GovernanceReviewCase | null>(null);
  const [failure, setFailure] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  const refresh = useCallback(async () => {
    const result = await governanceApi.reviewCase(reviewCaseId);
    setReviewCase(result);
    setFailure(null);
  }, [reviewCaseId]);

  useEffect(() => {
    let active = true;
    governanceApi.reviewCase(reviewCaseId)
      .then((result) => {
        if (!active) return;
        setReviewCase(result);
        setFailure(null);
      })
      .catch((caught: unknown) => {
        if (active) setFailure(caught instanceof Error ? caught.message : "Review case unavailable.");
      });
    return () => { active = false; };
  }, [reviewCaseId]);

  async function run(action: () => Promise<unknown>) {
    setBusy(true);
    setFailure(null);
    try {
      await action();
      await refresh();
    } catch (caught) {
      setFailure(caught instanceof Error ? caught.message : "The review action failed safely.");
      await refresh().catch(() => undefined);
    } finally {
      setBusy(false);
    }
  }

  if (!reviewCase && !failure) return <main className="governance-shell" aria-busy="true">Loading exact review history…</main>;
  if (!reviewCase) return <main className="governance-shell"><p className="governance-alert" role="alert">{failure}</p><Link href={routes.governanceQueue}>Return to queue</Link></main>;

  const fields = reviewCase.submitted_fields;
  const canClaim = reviewCase.state === "pending" || reviewCase.state === "reopened";
  const canDecide = reviewCase.state === "in_review";
  const canRespond = reviewCase.state === "changes_requested";
  const canDispute = ["changes_requested", "approved", "rejected"].includes(reviewCase.state);

  return (
    <main className="governance-shell" id="main-content">
      <Link className="governance-back" href={routes.governanceQueue}>← Steward queue</Link>
      <section className="governance-case-head">
        <div>
          <p className="governance-kicker">Exact review version {reviewCase.source_draft_version}</p>
          <h1>{String(fields.name ?? "Unnamed contribution")}</h1>
          <p>{truthFor(reviewCase)}</p>
        </div>
        <span className={`governance-state governance-state-${reviewCase.state}`}>{readable(reviewCase.state)}</span>
      </section>

      {failure ? <p className="governance-alert" role="alert">{failure}</p> : null}

      <section className="governance-grid" aria-label="Review facts">
        <article>
          <p className="governance-kicker">Responsibility</p>
          <h2>{reviewCase.assigned_steward_actor_id ? "Assigned steward" : "Unassigned"}</h2>
          <p>{nextActionFor(reviewCase)}</p>
          <dl>
            <div><dt>Pack</dt><dd>{reviewCase.pack_id}</dd></div>
            <div><dt>Case revision</dt><dd>{reviewCase.revision}</dd></div>
            <div><dt>Opened</dt><dd>{new Date(reviewCase.opened_at).toLocaleString()}</dd></div>
          </dl>
        </article>
        <article>
          <p className="governance-kicker">Evidence-safe comparison</p>
          <h2>{String(fields.evidence_type ?? "Evidence type unavailable")}</h2>
          <p>Only submitted metadata is shown. Evidence bytes, object keys, provider revisions, and private notes never enter browser state.</p>
          <dl>
            <div><dt>Source</dt><dd>{String(fields.source_uri ?? "Not provided")}</dd></div>
            <div><dt>Attribution</dt><dd>{String(fields.attribution ?? "Not provided")}</dd></div>
            <div><dt>License</dt><dd>{String(fields.source_license ?? "Not provided")}</dd></div>
          </dl>
        </article>
      </section>

      <section className="governance-actions" aria-labelledby="actions-heading">
        <div>
          <p className="governance-kicker">Recorded actions</p>
          <h2 id="actions-heading">Act with an explicit reason.</h2>
        </div>
        {canClaim ? <button disabled={busy} onClick={() => run(() => governanceApi.claim(reviewCaseId, reviewCase.revision))}>Acknowledge this case</button> : null}
        {canDecide ? <StewardActions reviewCase={reviewCase} busy={busy} run={run} /> : null}
        {canRespond ? <ContributorResponse reviewCase={reviewCase} busy={busy} run={run} /> : null}
        {canDispute ? <DisputeForm reviewCase={reviewCase} busy={busy} run={run} /> : null}
      </section>

      <section className="governance-history" aria-labelledby="history-heading">
        <p className="governance-kicker">Public-safe history</p>
        <h2 id="history-heading">Nothing is silently rewritten.</h2>
        <ol>
          {(reviewCase.events ?? []).map((event) => (
            <li key={event.sequence}>
              <strong>{readable(event.event_type)}</strong>
              <time dateTime={event.occurred_at}>{new Date(event.occurred_at).toLocaleString()}</time>
              <p>{event.public_reason ?? "No public reason was required for this event."}</p>
            </li>
          ))}
        </ol>
        {(reviewCase.disputes ?? []).map((dispute) => <article key={dispute.dispute_id}><h3>Dispute · {readable(dispute.category)}</h3><p>{dispute.public_reason}</p><p>Requested remedy: {dispute.requested_remedy}</p><p>State: {dispute.state}{dispute.resolution ? ` — ${dispute.resolution}` : ""}</p></article>)}
        {(reviewCase.appeals ?? []).map((appeal) => <article key={appeal.appeal_id}><h3>Appeal · {appeal.state}</h3><p>{appeal.public_reason}</p><p>Requested remedy: {appeal.requested_remedy}</p><p>{appeal.resolution ?? "An independent decision is pending."}</p></article>)}
      </section>
    </main>
  );
}

type ActionRunner = (action: () => Promise<unknown>) => Promise<void>;

function StewardActions({ reviewCase, busy, run }: { reviewCase: GovernanceReviewCase; busy: boolean; run: ActionRunner }) {
  function decide(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const data = new FormData(event.currentTarget);
    return run(() => governanceApi.decide(reviewCase.review_case_id, {
      expected_revision: reviewCase.revision,
      outcome: String(data.get("outcome")) as "changes_requested" | "rejected",
      reason: String(data.get("reason")),
    }));
  }
  function approve(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const data = new FormData(event.currentTarget);
    return run(() => governanceApi.approve(reviewCase.review_case_id, {
      expected_revision: reviewCase.revision,
      pack_id: reviewCase.pack_id,
      record_id: String(data.get("record_id")),
      expected_base_commit: String(data.get("expected_base_commit")),
      files: [{ path: String(data.get("path")), content: String(data.get("content")) }],
      reason: String(data.get("reason")),
    }));
  }
  return <div className="governance-action-grid">
    <form onSubmit={decide}>
      <h3>Request changes or reject</h3>
      <label>Outcome<select name="outcome"><option value="changes_requested">Request changes</option><option value="rejected">Reject this version</option></select></label>
      <label>Public-safe reason<textarea name="reason" required maxLength={2000} /></label>
      <button disabled={busy}>Record decision</button>
    </form>
    <form onSubmit={approve}>
      <h3>Approve through protected publication</h3>
      <label>Record ID<input name="record_id" required maxLength={160} /></label>
      <label>Expected base commit<input name="expected_base_commit" required pattern="[0-9a-f]{40}([0-9a-f]{24})?" /></label>
      <label>Governed file path<input name="path" required defaultValue={`packs/${reviewCase.pack_id}/foods/`} /></label>
      <label>Reviewed file content<textarea name="content" required /></label>
      <label>Public-safe reason<textarea name="reason" required maxLength={2000} /></label>
      <button disabled={busy}>Approve and enqueue publication</button>
    </form>
    <form onSubmit={(event) => { event.preventDefault(); const data = new FormData(event.currentTarget); void run(() => governanceApi.pause(reviewCase.review_case_id, { expected_revision: reviewCase.revision, reason: String(data.get("reason")), next_review_at: new Date(String(data.get("next_review_at"))).toISOString() })); }}>
      <h3>Pause with a next-review date</h3><label>Reason<input name="reason" required /></label><label>Next review<input name="next_review_at" type="datetime-local" required /></label><button disabled={busy}>Pause review</button>
    </form>
    {reviewCase.pause_reason ? <button disabled={busy} onClick={() => run(() => governanceApi.resume(reviewCase.review_case_id, { expected_revision: reviewCase.revision, reason: "The stated pause condition has been resolved." }))}>Resume review</button> : null}
    <form onSubmit={(event) => { event.preventDefault(); const data = new FormData(event.currentTarget); void run(() => governanceApi.recuse(reviewCase.review_case_id, { expected_revision: reviewCase.revision, reason: String(data.get("reason")) })); }}>
      <h3>Declare a conflict</h3><label>Public-safe reason<input name="reason" required /></label><button disabled={busy}>Recuse and release</button>
    </form>
  </div>;
}

function ContributorResponse({ reviewCase, busy, run }: { reviewCase: GovernanceReviewCase; busy: boolean; run: ActionRunner }) {
  return <form onSubmit={(event) => { event.preventDefault(); const data = new FormData(event.currentTarget); void run(() => governanceApi.respond(reviewCase.review_case_id, { expected_revision: reviewCase.revision, expected_draft_version: reviewCase.source_draft_version, patches: [{ field: String(data.get("field")) as "name", value: String(data.get("value")) }], public_reason: String(data.get("reason")) })); }}>
    <h3>Respond with a new exact version</h3>
    <label>Field<select name="field"><option value="name">Name</option></select></label>
    <label>Corrected value<input name="value" required /></label>
    <label>Public response<textarea name="reason" required maxLength={2000} /></label>
    <button disabled={busy}>Create revised review case</button>
    <p>Fresh exact-version evidence is required before the revision can be approved.</p>
  </form>;
}

function DisputeForm({ reviewCase, busy, run }: { reviewCase: GovernanceReviewCase; busy: boolean; run: ActionRunner }) {
  return <form onSubmit={(event) => { event.preventDefault(); const data = new FormData(event.currentTarget); void run(() => governanceApi.dispute(reviewCase.review_case_id, { expected_revision: reviewCase.revision, category: String(data.get("category")) as "evidence", public_reason: String(data.get("reason")), requested_remedy: String(data.get("remedy")) })); }}>
    <h3>Open a dispute</h3>
    <label>Category<select name="category"><option value="evidence">Evidence</option><option value="accuracy">Accuracy</option><option value="rights">Rights</option><option value="process">Process</option><option value="other">Other</option></select></label>
    <label>Public-safe reason<textarea name="reason" required maxLength={2000} /></label>
    <label>Requested remedy<input name="remedy" required maxLength={1000} /></label>
    <button disabled={busy}>Open dispute</button>
  </form>;
}
