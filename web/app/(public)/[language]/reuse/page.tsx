import type { Metadata } from "next";
import { notFound } from "next/navigation";
import { PublicBreadcrumbs } from "@/components/public/public-breadcrumbs";
import { PublicFooter } from "@/components/public/public-footer";
import { PublicReuseSurface } from "@/components/public/living-commons-surfaces";
import { getCatalog } from "@/lib/i18n/catalog";
import { getPublicReuse } from "@/lib/living-commons";
import { isSupportedLanguage, routes } from "@/lib/routes";

export const dynamic = "force-dynamic";
export async function generateMetadata({ params }: { params: Promise<{ language: string }> }): Promise<Metadata> {
  const { language } = await params; if (!isSupportedLanguage(language)) return {};
  const copy = getCatalog(language).metadata; return { title: copy.reuseTitle, description: copy.reuseDescription };
}
export default async function ReusePage({ params }: { params: Promise<{ language: string }> }) {
  const { language } = await params; if (!isSupportedLanguage(language)) notFound();
  const catalog = getCatalog(language); const copy = catalog.livingCommons.reuse;
  return <><main id="main-content" className="living-commons-page"><PublicBreadcrumbs label={catalog.common.breadcrumb} items={[{ label: catalog.common.home, href: routes.publicHome(language) }, { label: catalog.common.commons, href: routes.publicHub("commons", language) }, { label: copy.title }]} /><header className="living-commons-intro"><p className="eyebrow">{copy.eyebrow}</p><h1>{copy.title}</h1><p className="lede">{copy.lead}</p></header><PublicReuseSurface language={language} snapshot={await getPublicReuse()} /></main><PublicFooter language={language} /></>;
}
