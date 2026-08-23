import Link from "next/link";

import { routes, type InterfaceLanguage } from "@/lib/routes";

const foods = ["Jollof rice", "Masala dosa", "Mole poblano", "Gaeng keow wan", "Ful medames", "Feijoada"];

export default async function PublicHome({
  params,
}: {
  params: Promise<{ language: InterfaceLanguage }>;
}) {
  const { language } = await params;

  return (
    <main id="main-content">
      <section className="hero" aria-labelledby="hero-title" data-motion-region="hero">
        <div className="hero-grid" aria-hidden="true" />
        <div className="hero-meta mono">
          <span>The open food commons</span>
          <span>CC0 · public · versioned</span>
        </div>
        <h1 id="hero-title" className="hero-title">
          <span>Food data</span>
          <span>belongs to</span>
          <span>everyone<span className="signal-dot">.</span></span>
        </h1>
        <div className="commons-orbit" aria-hidden="true">
          <span className="orbit-ring orbit-one" />
          <span className="orbit-ring orbit-two" />
          <span className="orbit-core"><strong>OPEN</strong><small>by design</small></span>
          <i className="orbit-dot orbit-dot-one" />
          <i className="orbit-dot orbit-dot-two" />
        </div>
        <div className="hero-bottom">
          <p>Search it. Verify it. Add what is missing.<br />Reuse it anywhere.</p>
          <Link className="circle-link" href={routes.publicHub("explore", language)}>
            <span>Start</span><span aria-hidden="true">↘</span>
          </Link>
        </div>
      </section>

      <div className="food-ribbon" aria-label="Foods the commons should represent" data-motion-region="ribbon">
        <div className="ribbon-track">
          {[...foods, ...foods].map((food, index) => <span key={`${food}-${index}`}>{food}<i aria-hidden="true">◆</i></span>)}
        </div>
      </div>

      <section className="explore-stage motion-stage" id="explore" aria-labelledby="explore-title" data-motion-region="explore">
        <p className="section-index mono">01 / Explore</p>
        <div className="stage-heading">
          <h2 id="explore-title">Find the food.<br />See the source.</h2>
          <p>Food records should show where the information came from, what it describes, and how confidently it can be reused.</p>
        </div>
        <div className="search-runway" aria-label="Public explorer status">
          <span className="mono">Public explorer</span>
          <strong>Search is the next surface.</strong>
          <span className="runway-status">In development</span>
        </div>
        <div className="principle-ledger">
          <div><span className="mono">A</span><strong>Anonymous by default</strong><p>Look up public food knowledge without creating an account.</p></div>
          <div><span className="mono">B</span><strong>Context beside numbers</strong><p>Preparations, portions, locale, and uncertainty stay attached.</p></div>
          <div><span className="mono">C</span><strong>Provenance in the open</strong><p>Sources, versions, licenses, and contributors remain visible.</p></div>
        </div>
      </section>

      <section className="commons-stage motion-stage" id="commons" aria-labelledby="commons-title" data-motion-region="commons">
        <div className="commons-copy">
          <p className="section-index mono">02 / Commons</p>
          <h2 id="commons-title">A commons earns trust in public.</h2>
          <p>Accepted changes will become movement: new foods, verified portions, source additions, and published packs—drawn only from real repository events.</p>
        </div>
        <div className="activity-field">
          <div className="activity-head mono"><span>Contributions / last 24h</span><span>Production state</span></div>
          <div className="quiet-orbit" aria-hidden="true"><i /><i /><i /></div>
          <p className="quiet-state"><strong>No accepted changes to report yet.</strong><span>This area stays quiet until production events exist. No sample count is presented as fact.</span></p>
          <dl className="activity-legend mono">
            <div><dt>Food</dt><dd>Accepted new record</dd></div>
            <div><dt>Source</dt><dd>Evidence attached</dd></div>
            <div><dt>Pack</dt><dd>Version published</dd></div>
          </dl>
        </div>
      </section>

      <section className="contribute-stage motion-stage" id="contribute" aria-labelledby="contribute-title" data-motion-region="contribute">
        <p className="section-index mono">03 / Contribute</p>
        <div className="stage-heading">
          <h2 id="contribute-title">What is missing<br /><span>belongs here too.</span></h2>
          <p>Regional, restaurant, and home-cooked foods deserve records that preserve context instead of flattening it.</p>
        </div>
        <ol className="chapter-list">
          <li><span className="mono">Chapter 01</span><strong>Name + context</strong><p>Describe the food, preparation, and locale in your own words.</p></li>
          <li><span className="mono">Chapter 02</span><strong>Ingredients + portions</strong><p>Keep original units while nutrition is calculated in canonical grams.</p></li>
          <li><span className="mono">Chapter 03</span><strong>Sources + review</strong><p>Show the evidence, review the record, and publish through Git.</p></li>
        </ol>
        <a className="text-arrow-link" href="https://github.com/RujitRaval/opennosh/blob/main/CONTRIBUTING.md">Read the contribution guide <span aria-hidden="true">↗</span></a>
      </section>

      <section className="build-stage motion-stage" id="build" aria-labelledby="build-title" data-motion-region="build">
        <p className="section-index mono">04 / Build</p>
        <div className="stage-heading">
          <h2 id="build-title">Take the data.<br />Make it useful.</h2>
          <p>The schema, packs, API, and code are inspectable. The tracker is one proof—not the boundary of what can be built.</p>
        </div>
        <div className="build-ledger">
          <a href="https://github.com/RujitRaval/opennosh/blob/main/schemas/food-pack.schema.json"><span className="mono">01</span><strong>Food-pack schema</strong><small>JSON Schema</small><i aria-hidden="true">↗</i></a>
          <a href="https://github.com/RujitRaval/opennosh/tree/main/packs"><span className="mono">02</span><strong>Versioned packs</strong><small>CC0 data</small><i aria-hidden="true">↗</i></a>
          <a href="https://github.com/RujitRaval/opennosh"><span className="mono">03</span><strong>Source repository</strong><small>MIT software</small><i aria-hidden="true">↗</i></a>
          <Link href={routes.tracker.home}><span className="mono">04</span><strong>Private tracker</strong><small>Self-hosted utility</small><i aria-hidden="true">↗</i></Link>
        </div>
      </section>

      <section className="closing-stage motion-stage" aria-labelledby="closing-title" data-motion-region="closing">
        <p className="mono">The commons begins with what we can document together.</p>
        <h2 id="closing-title">Open the<br />record.</h2>
        <a className="closing-link" href="https://github.com/RujitRaval/opennosh"><span>Join on GitHub</span><span aria-hidden="true">↗</span></a>
      </section>
    </main>
  );
}
