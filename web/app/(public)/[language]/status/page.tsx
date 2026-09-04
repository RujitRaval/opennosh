import type { Metadata } from "next";
import { notFound } from "next/navigation";
import { PublicBreadcrumbs } from "@/components/public/public-breadcrumbs";
import { PublicFooter } from "@/components/public/public-footer";
import { PublicOperationsSurface } from "@/components/public/living-commons-surfaces";
import { getCatalog } from "@/lib/i18n/catalog";
import { getPublicOperations } from "@/lib/living-commons";
import { isSupportedLanguage, routes } from "@/lib/routes";

export const dynamic = "force-dynamic";
export async function generateMetadata({ params }: { params: Promise<{ language: string }> }): Promise<Metadata> {
  const { language } = await params; if (!isSupportedLanguage(language)) return {};
  const copy = getCatalog(language).metadata; return { title: copy.statusTitle, description: copy.statusDescription };
}
export default async function StatusPage({ params }: { params: Promise<{ language: string }> }) {
  const { language } = await params; if (!isSupportedLanguage(language)) notFound();
  const catalog = getCatalog(language); const copy = catalog.livingCommons.status;
  return <><main id="main-content" className="living-commons-page"><PublicBreadcrumbs label={catalog.common.breadcrumb} items={[{ label: catalog.common.home, href: routes.publicHome(language) }, { label: catalog.common.commons, href: routes.publicHub("commons", language) }, { label: copy.title }]} /><header className="living-commons-intro"><p className="eyebrow">{copy.eyebrow}</p><h1>{copy.title}</h1><p className="lede">{copy.lead}</p></header><PublicOperationsSurface language={language} snapshot={await getPublicOperations()} /></main><PublicFooter language={language} /></>;
}
