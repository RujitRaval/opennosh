import type { Metadata } from "next";
import { notFound } from "next/navigation";

import { ContributionJourney } from "@/components/contributions/contribution-journey";
import { isSupportedLanguage } from "@/lib/routes";

export const metadata: Metadata = {
  title: "Contribute a food record - opennosh",
  description: "Document, verify, and hand a food record to the open commons for review.",
};

export default async function ContributionPage({
  params,
}: {
  params: Promise<{ language: string; draft: string; requested_stage: string }>;
}) {
  const { language, draft, requested_stage: requestedStage } = await params;
  if (!isSupportedLanguage(language)) notFound();
  return <ContributionJourney language={language} routeDraftId={draft} requestedStage={requestedStage} />;
}
