import type { Metadata } from "next";

import { PublicBreadcrumbs } from "@/components/public/public-breadcrumbs";
import { PublicFooter } from "@/components/public/public-footer";
import { routes, type InterfaceLanguage } from "@/lib/routes";
import { getCatalog } from "@/lib/i18n/catalog";

export async function generateMetadata({
  params,
}: {
  params: Promise<{ language: InterfaceLanguage }>;
}): Promise<Metadata> {
  const { language } = await params;
  const copy = getCatalog(language).metadata;
  return { title: copy.noticesTitle, description: copy.noticesDescription };
}

export default async function NoticesPage({
  params,
}: {
  params: Promise<{ language: InterfaceLanguage }>;
}) {
  const { language } = await params;
  const catalog = getCatalog(language);
  const copy = catalog.notices;

  return (
    <>
      <main className="legal-page" id="main-content">
      <PublicBreadcrumbs
        label={catalog.common.breadcrumb}
        items={[
          { label: catalog.common.home, href: routes.publicHome(language) },
          { label: catalog.common.build, href: routes.publicHub("build", language) },
          { label: catalog.shell.licenses },
        ]}
      />
      <p className="eyebrow">{copy.sourceTransparency}</p>
      <h1>{copy.title}</h1>
      <p className="lede">{copy.lead}</p>

      <section aria-labelledby="software-notice">
        <h2 id="software-notice">{copy.software}</h2>
        <p>
          {copy.softwarePrefix}{" "}
          <a href="https://github.com/RujitRaval/opennosh/blob/main/LICENSE">{copy.mit}</a>.
        </p>
      </section>

      <section aria-labelledby="food-notices">
        <h2 id="food-notices">{copy.foodData}</h2>
        <dl>
          <div>
            <dt>{copy.communityPacks}</dt>
            <dd>{copy.communityBody}</dd>
          </div>
          <div>
            <dt>{copy.usda}</dt>
            <dd>{copy.usdaBody}</dd>
          </div>
          <div>
            <dt>{copy.off}</dt>
            <dd>{copy.offBody}</dd>
          </div>
        </dl>
      </section>

      <section aria-labelledby="exercise-notice">
        <h2 id="exercise-notice">{copy.exercise}</h2>
        <p>{copy.exerciseBody}</p>
      </section>

      <section aria-labelledby="private-notice">
        <h2 id="private-notice">{copy.privateData}</h2>
        <p>{copy.privateBody}</p>
      </section>

      <p className="legal-detail-link">
        {copy.readPrefix}{" "}
        <a href="https://github.com/RujitRaval/opennosh/blob/main/NOTICE.md">
          {copy.completeNotice}
        </a>{" "}
        {copy.readSuffix}
      </p>
      </main>
      <PublicFooter language={language} />
    </>
  );
}
