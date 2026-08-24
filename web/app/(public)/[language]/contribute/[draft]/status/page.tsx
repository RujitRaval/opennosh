import { notFound } from "next/navigation";

import { ContributionStatus } from "@/components/contributions/contribution-status";
import { PublicBreadcrumbs } from "@/components/public/public-breadcrumbs";
import { PublicFooter } from "@/components/public/public-footer";
import { isSupportedLanguage, routes } from "@/lib/routes";

export default async function ContributionStatusPage({
  params,
}: {
  params: Promise<{ language: string; draft: string }>;
}) {
  const { language, draft } = await params;
  if (!isSupportedLanguage(language)) notFound();
  return <>
    <main id="main-content" className="contribution-status-page">
      <PublicBreadcrumbs items={[
        { label: "Home", href: routes.publicHome(language) },
        { label: "Contribute", href: routes.publicHub("contribute", language) },
        { label: "Status" },
      ]} />
      <ContributionStatus language={language} draftId={draft} />
    </main>
    <PublicFooter language={language} />
  </>;
}
