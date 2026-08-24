"use client";

import Link from "next/link";
import { useEffect, useState } from "react";

import { api } from "@/lib/api";
import type { ContributionCapability } from "@/lib/contributions/domain";
import { routes, type InterfaceLanguage } from "@/lib/routes";

export function ContributionStatus({ language, draftId }: { language: InterfaceLanguage; draftId: string }) {
  const [capability, setCapability] = useState<ContributionCapability | null>(null);
  const [failure, setFailure] = useState<string | null>(null);

  useEffect(() => {
    let active = true;
    api.contributionDraft(draftId, "review")
      .then((value) => { if (active) setCapability(value); })
      .catch((caught) => { if (active) setFailure(caught instanceof Error ? caught.message : "This status could not be verified."); });
    return () => { active = false; };
  }, [draftId]);

  if (failure) return <section className="contribution-status-state" role="alert"><p className="mono">Status unavailable</p><h1>We could not verify this draft</h1><p>{failure} Sign in with the account that owns it, or return to your device draft.</p><Link className="contribution-primary" href={routes.contributionStart(language)}>Return to contribution</Link></section>;
  if (!capability) return <section className="contribution-status-state" aria-busy="true"><p className="mono">Verifying server record</p><h1>Opening status…</h1></section>;

  const received = capability.receipt;
  return <section className="contribution-status-state">
    <p className="mono">Stable contribution status</p>
    <h1>{received ? "Review is in progress" : "Draft not handed over"}</h1>
    <p>{received ? "This proposal was received for review. Approval and publication are separate public events; a queued submission is not accepted data." : "This food record remains a draft. It has not entered review, been approved, or been published."}</p>
    <dl>
      <div><dt>Draft reference</dt><dd>{capability.draftId}</dd></div>
      <div><dt>Verified state</dt><dd>{capability.reviewState.replaceAll("_", " ")}</dd></div>
      {received ? <div><dt>Submission</dt><dd>{received.submissionId}</dd></div> : null}
    </dl>
    <Link className="contribution-primary" href={received ? routes.publicHub("commons", language) : routes.contributionDraft(language, capability.draftId, capability.resolvedStage)}>{received ? "How the commons is governed" : "Resume this draft"}</Link>
  </section>;
}
