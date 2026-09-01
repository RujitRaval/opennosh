"use client";

import Link from "next/link";
import { useEffect, useState } from "react";

import { governanceApi } from "@/lib/api/governance";
import type { GovernanceReviewCase } from "@/lib/api/domain/governance";
import { routes } from "@/lib/routes";

function readable(value: string): string {
  return value.replaceAll("_", " ");
}

export function GovernanceQueue() {
  const [packId, setPackId] = useState("global-core");
  const [cases, setCases] = useState<GovernanceReviewCase[]>([]);
  const [failure, setFailure] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    let active = true;
    governanceApi.queue(packId)
      .then((result) => {
        if (!active) return;
        setCases(result.cases);
        setFailure(null);
      })
      .catch((caught: unknown) => {
        if (active) setFailure(caught instanceof Error ? caught.message : "Review queue unavailable.");
      })
      .finally(() => { if (active) setLoading(false); });
    return () => { active = false; };
  }, [packId]);

  return (
    <main className="governance-shell" id="main-content">
      <section className="governance-intro" aria-labelledby="queue-heading">
        <div>
          <p className="governance-kicker">Steward queue</p>
          <h1 id="queue-heading">Oldest unacknowledged work comes first.</h1>
          <p>There is no hidden score. Ownership, pauses, and next-review dates stay visible.</p>
        </div>
        <label>
          Governed pack
          <input value={packId} onChange={(event) => { setLoading(true); setPackId(event.target.value); }} />
        </label>
      </section>

      {failure ? <p className="governance-alert" role="alert">{failure}</p> : null}
      {loading ? <p aria-busy="true">Loading accountable review cases…</p> : null}
      {!loading && !failure && cases.length === 0 ? (
        <section className="governance-empty">
          <h2>No open cases</h2>
          <p>This pack has no review work visible to your steward role.</p>
        </section>
      ) : null}
      <ol className="governance-queue" aria-label="Review cases">
        {cases.map((reviewCase) => (
          <li key={reviewCase.review_case_id}>
            <Link href={routes.governanceCase(reviewCase.review_case_id)}>
              <span className={`governance-state governance-state-${reviewCase.state}`}>
                {readable(reviewCase.state)}
              </span>
              <strong>{String(reviewCase.submitted_fields.name ?? "Unnamed contribution")}</strong>
              <span>Version {reviewCase.source_draft_version} · opened {new Date(reviewCase.opened_at).toLocaleString()}</span>
              <span>{reviewCase.assigned_steward_actor_id ? "Owned by a steward" : "Needs acknowledgement"}</span>
              {reviewCase.next_review_at ? <span>Next review {new Date(reviewCase.next_review_at).toLocaleString()}</span> : null}
            </Link>
          </li>
        ))}
      </ol>
    </main>
  );
}
