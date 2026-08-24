"use client";

import Link from "next/link";
import { useEffect, useState } from "react";

import { api } from "@/lib/api";
import type { ContributionCapability } from "@/lib/contributions/domain";
import { routes, type InterfaceLanguage } from "@/lib/routes";
import { contributionCatalog } from "@/lib/contributions/catalog";
import { fallbackLanguage, pseudoLanguage } from "@/lib/i18n/catalog";

export function ContributionStatus({ language, draftId }: { language: InterfaceLanguage; draftId: string }) {
  const copy = contributionCatalog(language);
  const [capability, setCapability] = useState<ContributionCapability | null>(null);
  const [failure, setFailure] = useState<string | null>(null);

  useEffect(() => {
    let active = true;
    api.contributionDraft(draftId, "review")
      .then((value) => { if (active) setCapability(value); })
      .catch((caught) => { if (active) setFailure(caught instanceof Error ? caught.message : copy.statusFallback); });
    return () => { active = false; };
  }, [copy.statusFallback, draftId]);

  if (failure) return <section className="contribution-status-state" role="alert"><p className="mono">{copy.statusUnavailable}</p><h1>{copy.verifyDraftTitle}</h1><p>{failure} {copy.verifyDraftBody}</p><Link className="contribution-primary" href={routes.contributionStart(language)}>{copy.returnContribution}</Link></section>;
  if (!capability) return <section className="contribution-status-state" aria-busy="true"><p className="mono">{copy.verifying}</p><h1>{copy.openingStatus}</h1></section>;

  const received = capability.receipt;
  const acknowledgementDue = received
    ? new Intl.DateTimeFormat(language === pseudoLanguage ? fallbackLanguage : language, { dateStyle: "long", timeStyle: "short" }).format(
        new Date(received.acknowledgementDueAt),
      )
    : null;
  return <section className="contribution-status-state">
    <p className="mono">{received ? copy.receiptLabel : copy.stableContribution}</p>
    <h1>{received ? copy.receiptTitle : copy.draftNotSubmitted}</h1>
    <p>{received ? copy.receivedBody : copy.draftBody}</p>
    <dl>
      <div><dt>{copy.draftReference}</dt><dd>{capability.draftId}</dd></div>
      <div><dt>{copy.verifiedState}</dt><dd>{capability.reviewState.replaceAll("_", " ")}</dd></div>
      {received ? <div><dt>{copy.submission}</dt><dd>{received.submissionId}</dd></div> : null}
      {received ? <div><dt>{copy.publicCredit}</dt><dd>{received.attribution}</dd></div> : null}
      {received ? <div><dt>{copy.acknowledgement}</dt><dd>{acknowledgementDue}</dd></div> : null}
    </dl>
    {received ? <p>{copy.receiptBody}</p> : null}
    <Link className="contribution-primary" href={received ? routes.publicHub("commons", language) : routes.contributionDraft(language, capability.draftId, capability.resolvedStage)}>{received ? copy.governed : copy.resume}</Link>
  </section>;
}
