import type { Metadata } from "next";
import Link from "next/link";
import { notFound } from "next/navigation";
import { Suspense } from "react";

import { PublicFoodSearch } from "@/components/foods/public-food-search";
import { PublicBreadcrumbs } from "@/components/public/public-breadcrumbs";
import { PublicFooter } from "@/components/public/public-footer";
import {
  PublicMissionActivityLoading,
  PublicMissionActivitySurface,
  PublicMissionCatalogLoading,
  PublicMissionCatalogSurface,
  PublicMissionsFrame,
} from "@/components/public/public-missions";
import {
  buildPublicNavigation,
  parsePublicFeatureFlags,
} from "@/lib/public-navigation";
import {
  isPublicHub,
  isSupportedLanguage,
  publicHubIds,
  routes,
  type InterfaceLanguage,
} from "@/lib/routes";
import { formatMessage, getCatalog } from "@/lib/i18n/catalog";
import {
  getPublicMissionActivity,
  getPublicMissionCatalog,
} from "@/lib/public-missions";

export const dynamic = "force-dynamic";

function navigationFor(language: InterfaceLanguage) {
  return buildPublicNavigation(
    language,
    parsePublicFeatureFlags(process.env.OPENNOSH_PUBLIC_NAV_FEATURES),
  );
}

export function generateStaticParams() {
  return publicHubIds.map((hub) => ({ hub }));
}

async function PublicMissionCatalogServer({ language }: { language: InterfaceLanguage }) {
  const catalog = await getPublicMissionCatalog();
  return <PublicMissionCatalogSurface language={language} catalog={catalog} />;
}

async function PublicMissionActivityServer({ language }: { language: InterfaceLanguage }) {
  const activity = await getPublicMissionActivity();
  return <PublicMissionActivitySurface language={language} activity={activity} />;
}

export async function generateMetadata({
  params,
}: {
  params: Promise<{ language: string; hub: string }>;
}): Promise<Metadata> {
  const { language, hub } = await params;
  if (!isSupportedLanguage(language) || !isPublicHub(hub)) return {};

  const currentHub = navigationFor(language).find((item) => item.id === hub);
  return {
    title: (currentHub?.label ?? hub) + " - opennosh",
    description: currentHub?.description,
  };
}

export default async function PublicHubPage({
  params,
}: {
  params: Promise<{ language: string; hub: string }>;
}) {
  const { language, hub } = await params;
  if (!isSupportedLanguage(language) || !isPublicHub(hub)) notFound();

  const navigation = navigationFor(language);
  const currentHub = navigation.find((item) => item.id === hub);
  if (!currentHub) notFound();

  const action = currentHub.nextAction;
  const actionClassName = "hub-primary-action";
  const copy = getCatalog(language);

  return (
    <>
      <main id="main-content" className={`hub-page hub-page-${hub}`}>
      <PublicBreadcrumbs
        label={copy.common.breadcrumb}
        items={[
          { label: copy.common.home, href: routes.publicHome(language) },
          { label: currentHub.label },
        ]}
      />
      <section className="hub-intro" aria-labelledby="hub-title">
        <p className="hub-index mono">
          {formatMessage(copy.navigation.hubLabel, { index: currentHub.index, label: currentHub.label })}
        </p>
        <h1 id="hub-title">{currentHub.label}</h1>
        <p className="hub-description">{currentHub.description}</p>
        {action.external ? (
          <a className={actionClassName} href={action.href}>
            <span>{action.label}</span><span aria-hidden="true">{"\u2197"}</span>
          </a>
        ) : (
          <Link className={actionClassName} href={action.href}>
            <span>{action.label}</span><span aria-hidden="true">{"\u2193"}</span>
          </Link>
        )}
      </section>

      {hub === "explore" && currentHub.children.some((child) => child.id === "search")
        ? <PublicFoodSearch language={language} />
        : null}

      {hub === "commons" && currentHub.children.some((child) => child.id === "missions") ? (
        <PublicMissionsFrame
          language={language}
          catalogPanel={(
            <Suspense fallback={<PublicMissionCatalogLoading language={language} />}>
              <PublicMissionCatalogServer language={language} />
            </Suspense>
          )}
          activityPanel={(
            <Suspense fallback={<PublicMissionActivityLoading language={language} />}>
              <PublicMissionActivityServer language={language} />
            </Suspense>
          )}
        />
      ) : null}

      <section id="principles" className="hub-principles" aria-labelledby="principles-title">
        <p id="principles-title" className="mono">{copy.navigation.guides}</p>
        <ol>
          {currentHub.principles.map((principle, index) => (
            <li key={principle}>
              <span className="mono">{String(index + 1).padStart(2, "0")}</span>
              <strong>{principle}</strong>
            </li>
          ))}
        </ol>
      </section>

      <section className="hub-contents" aria-labelledby="contents-title">
        <div className="hub-contents-heading">
          <p className="mono">{copy.navigation.availableNow}</p>
          <h2 id="contents-title">{formatMessage(copy.navigation.inside, { label: currentHub.label })}</h2>
        </div>
        {currentHub.children.length > 0 ? (
          <div className="hub-content-ledger">
            {currentHub.children.map((child, index) => (
              <Link id={`hub-link-${child.id}`} key={child.id} href={child.href}>
                <span className="mono">{String(index + 1).padStart(2, "0")}</span>
                <strong>{child.label}</strong>
                <small>{child.description}</small>
                <i aria-hidden="true">{"\u2192"}</i>
              </Link>
            ))}
          </div>
        ) : (
          <p className="hub-quiet-state">
            {copy.navigation.quiet}
          </p>
        )}
      </section>
      </main>
      <PublicFooter language={language} />
    </>
  );
}
