import type { Metadata } from "next";

import { PublicBreadcrumbs } from "@/components/public/public-breadcrumbs";
import { routes, type InterfaceLanguage } from "@/lib/routes";

export const metadata: Metadata = {
  title: "Licenses and data notices - opennosh",
  description: "The software and dataset terms that apply to opennosh.",
};

export default async function NoticesPage({
  params,
}: {
  params: Promise<{ language: InterfaceLanguage }>;
}) {
  const { language } = await params;

  return (
    <main className="legal-page" id="main-content">
      <PublicBreadcrumbs
        items={[
          { label: "Home", href: routes.publicHome(language) },
          { label: "Build", href: routes.publicHub("build", language) },
          { label: "Licenses + notices" },
        ]}
      />
      <p className="eyebrow">Source transparency</p>
      <h1>Licenses and data notices</h1>
      <p className="lede">
        opennosh keeps software, community food packs, public reference data, share-alike data,
        and private account data separate. These notices do not relicense any dataset.
      </p>

      <section aria-labelledby="software-notice">
        <h2 id="software-notice">Software</h2>
        <p>
          Original opennosh software and documentation are licensed under the{" "}
          <a href="https://github.com/RujitRaval/opennosh/blob/main/LICENSE">MIT License</a>.
        </p>
      </section>

      <section aria-labelledby="food-notices">
        <h2 id="food-notices">Food data</h2>
        <dl>
          <div>
            <dt>Community food packs</dt>
            <dd>
              CC0 1.0 Universal. Contributor credit stays visible as a community promise, not an
              extra legal restriction.
            </dd>
          </div>
          <div>
            <dt>USDA FoodData Central</dt>
            <dd>CC0 1.0 Universal, with FoodData Central retained as the source.</dd>
          </div>
          <div>
            <dt>Open Food Facts</dt>
            <dd>
              Database rights under ODbL 1.0 and individual contents under DbCL 1.0. The optional
              cache stays separate, and product images are not used.
            </dd>
          </div>
        </dl>
      </section>

      <section aria-labelledby="exercise-notice">
        <h2 id="exercise-notice">Exercise data</h2>
        <p>
          Accepted wger exercise entries retain their exact per-entry attribution and CC BY-SA 3.0
          terms. ShareAlike requirements remain attached to the separate exercise export.
        </p>
      </section>

      <section aria-labelledby="private-notice">
        <h2 id="private-notice">Private account data</h2>
        <p>
          Your custom foods, logs, recipes, targets, body metrics, and workouts are private account
          data. They are not included in any public food or exercise dataset export.
        </p>
      </section>

      <p className="legal-detail-link">
        Read the{" "}
        <a href="https://github.com/RujitRaval/opennosh/blob/main/NOTICE.md">
          complete distribution notice
        </a>{" "}
        for operative links and packaging details.
      </p>
    </main>
  );
}
