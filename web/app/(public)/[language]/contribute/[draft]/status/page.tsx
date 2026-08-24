import { notFound } from "next/navigation";

import { ContributionStatus } from "@/components/contributions/contribution-status";
import { PublicBreadcrumbs } from "@/components/public/public-breadcrumbs";
import { PublicFooter } from "@/components/public/public-footer";
import { isSupportedLanguage, routes } from "@/lib/routes";
import { getCatalog } from "@/lib/i18n/catalog";

export default async function ContributionStatusPage({
  params,
}: {
  params: Promise<{ language: string; draft: string }>;
}) {
  const { language, draft } = await params;
  if (!isSupportedLanguage(language)) notFound();
  const copy = getCatalog(language);
  return <>
    <main id="main-content" className="contribution-status-page">
      <PublicBreadcrumbs label={copy.common.breadcrumb} items={[
        { label: copy.common.home, href: routes.publicHome(language) },
        { label: copy.common.contribute, href: routes.publicHub("contribute", language) },
        { label: copy.common.status },
      ]} />
      <ContributionStatus language={language} draftId={draft} />
    </main>
    <PublicFooter language={language} />
  </>;
}
