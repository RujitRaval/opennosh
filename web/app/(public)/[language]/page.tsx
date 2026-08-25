import Link from "next/link";

import { CrossRootLink } from "@/components/shell/cross-root-link";

import { PublicFooter } from "@/components/public/public-footer";
import { AcceptedActivity, HeroReleaseProof } from "@/components/public/public-truth-signals";
import type { PublicCommonsSnapshot } from "@/lib/api/domain/public-commons";
import { getPublicCommonsSnapshot } from "@/lib/public-commons";

import { routes, type InterfaceLanguage } from "@/lib/routes";
import { getCatalog } from "@/lib/i18n/catalog";

export default async function PublicHome({
  params,
}: {
  params: Promise<{ language: InterfaceLanguage }>;
}) {
  const { language } = await params;

  const snapshot = await getPublicCommonsSnapshot();

  return <PublicHomeView language={language} snapshot={snapshot} />;
}

export function PublicHomeView({
  language,
  snapshot,
}: {
  language: InterfaceLanguage;
  snapshot: PublicCommonsSnapshot;
}) {
  const copy = getCatalog(language).home;
  return (
    <>
      <main id="main-content">
      <section className="hero" aria-labelledby="hero-title" data-motion-region="hero">
        <div className="hero-grid" aria-hidden="true" />
        <div className="hero-meta mono">
          <span>{copy.heroMeta}</span>
          <span>{copy.heroTerms}</span>
        </div>
        <HeroReleaseProof snapshot={snapshot} language={language} />
        <h1 id="hero-title" className="hero-title">
          <span>{copy.heroLine1}</span>
          <span>{copy.heroLine2}</span>
          <span>{copy.heroLine3}<span className="signal-dot">.</span></span>
        </h1>
        <div className="commons-orbit" aria-hidden="true">
          <span className="orbit-ring orbit-one" />
          <span className="orbit-ring orbit-two" />
          <span className="orbit-core"><strong>{copy.open}</strong><small>{copy.byDesign}</small></span>
          <i className="orbit-dot orbit-dot-one" />
          <i className="orbit-dot orbit-dot-two" />
        </div>
        <div className="hero-bottom">
          <p>{copy.heroLead}<br />{copy.heroLeadSecond}</p>
          <Link className="circle-link" href={routes.publicHub("explore", language)}>
            <span>{copy.start}</span><span aria-hidden="true">↘</span>
          </Link>
        </div>
      </section>

      <div className="food-ribbon" aria-label={copy.ribbonLabel} data-motion-region="ribbon">
        <div className="ribbon-track">
          {[...copy.foods, ...copy.foods].map((food, index) => <span key={food + "-" + index}>{food}<i aria-hidden="true">◆</i></span>)}
        </div>
      </div>

      <section className="explore-stage motion-stage" id="explore" aria-labelledby="explore-title" data-motion-region="explore">
        <p className="section-index mono">{copy.exploreIndex}</p>
        <div className="stage-heading">
          <h2 id="explore-title">{copy.exploreTitle}<br />{copy.exploreTitleSecond}</h2>
          <p>{copy.exploreDescription}</p>
        </div>
        <div className="search-runway" aria-label={copy.explorerStatusLabel}>
          <span className="mono">{copy.publicExplorer}</span>
          <strong>{copy.searchNext}</strong>
          <span className="runway-status">{copy.inDevelopment}</span>
        </div>
        <div className="principle-ledger">
          {copy.principles.map((principle, index) => (
            <div key={principle.title}>
              <span className="mono">{String.fromCharCode(65 + index)}</span>
              <strong>{principle.title}</strong>
              <p>{principle.description}</p>
            </div>
          ))}
        </div>
      </section>

      <section className="commons-stage motion-stage" id="commons" aria-labelledby="commons-title" data-motion-region="commons">
        <div className="commons-copy">
          <p className="section-index mono">{copy.commonsIndex}</p>
          <h2 id="commons-title">{copy.commonsTitle}</h2>
          <p>{copy.commonsDescription}</p>
        </div>
        <AcceptedActivity snapshot={snapshot} language={language} />
      </section>

      <section className="contribute-stage motion-stage" id="contribute" aria-labelledby="contribute-title" data-motion-region="contribute">
        <p className="section-index mono">{copy.contributeIndex}</p>
        <div className="stage-heading">
          <h2 id="contribute-title">{copy.contributeTitle}<br /><span>{copy.contributeTitleSecond}</span></h2>
          <p>{copy.contributeDescription}</p>
        </div>
        <ol className="chapter-list">
          {copy.chapters.map((chapter) => (
            <li key={chapter.label}><span className="mono">{chapter.label}</span><strong>{chapter.title}</strong><p>{chapter.description}</p></li>
          ))}
        </ol>
        <a className="text-arrow-link" href="https://github.com/RujitRaval/opennosh/blob/main/CONTRIBUTING.md">{copy.contributionGuide} <span aria-hidden="true">↗</span></a>
      </section>

      <section className="build-stage motion-stage" id="build" aria-labelledby="build-title" data-motion-region="build">
        <p className="section-index mono">{copy.buildIndex}</p>
        <div className="stage-heading">
          <h2 id="build-title">{copy.buildTitle}<br />{copy.buildTitleSecond}</h2>
          <p>{copy.buildDescription}</p>
        </div>
        <div className="build-ledger">
          <a href="https://github.com/RujitRaval/opennosh/blob/main/schemas/food-pack.schema.json"><span className="mono">01</span><strong>{copy.buildItems[0]?.title}</strong><small>{copy.buildItems[0]?.detail}</small><i aria-hidden="true">↗</i></a>
          <a href="https://github.com/RujitRaval/opennosh/tree/main/packs"><span className="mono">02</span><strong>{copy.buildItems[1]?.title}</strong><small>{copy.buildItems[1]?.detail}</small><i aria-hidden="true">↗</i></a>
          <a href="https://github.com/RujitRaval/opennosh"><span className="mono">03</span><strong>{copy.buildItems[2]?.title}</strong><small>{copy.buildItems[2]?.detail}</small><i aria-hidden="true">↗</i></a>
          <CrossRootLink href={routes.tracker.home}><span className="mono">04</span><strong>{copy.buildItems[3]?.title}</strong><small>{copy.buildItems[3]?.detail}</small><i aria-hidden="true">↗</i></CrossRootLink>
        </div>
      </section>

      <section className="closing-stage motion-stage" aria-labelledby="closing-title" data-motion-region="closing">
        <p className="mono">{copy.closingLead}</p>
        <h2 id="closing-title">{copy.closingTitle}<br />{copy.closingTitleSecond}</h2>
        <a className="closing-link" href="https://github.com/RujitRaval/opennosh"><span>{copy.joinGitHub}</span><span aria-hidden="true">↗</span></a>
      </section>
      </main>
      <PublicFooter language={language} snapshot={snapshot} />
    </>
  );
}
