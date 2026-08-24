import type { Metadata } from "next";
import Link from "next/link";
import { notFound } from "next/navigation";

import { PublicBreadcrumbs } from "@/components/public/public-breadcrumbs";
import { PublicFooter } from "@/components/public/public-footer";
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

function navigationFor(language: InterfaceLanguage) {
  return buildPublicNavigation(
    language,
    parsePublicFeatureFlags(process.env.OPENNOSH_PUBLIC_NAV_FEATURES),
  );
}

export function generateStaticParams() {
  return publicHubIds.map((hub) => ({ hub }));
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
    title: `${currentHub?.label ?? hub} - opennosh`,
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

  return (
    <>
      <main id="main-content" className={`hub-page hub-page-${hub}`}>
      <PublicBreadcrumbs
        items={[
          { label: "Home", href: routes.publicHome(language) },
          { label: currentHub.label },
        ]}
      />
      <section className="hub-intro" aria-labelledby="hub-title">
        <p className="hub-index mono">
          Hub {currentHub.index} / {currentHub.label}
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

      <section id="principles" className="hub-principles" aria-labelledby="principles-title">
        <p id="principles-title" className="mono">What guides this hub</p>
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
          <p className="mono">Available now</p>
          <h2 id="contents-title">Inside {currentHub.label}</h2>
        </div>
        {currentHub.children.length > 0 ? (
          <div className="hub-content-ledger">
            {currentHub.children.map((child, index) => (
              <Link id={child.id} key={child.id} href={child.href}>
                <span className="mono">{String(index + 1).padStart(2, "0")}</span>
                <strong>{child.label}</strong>
                <small>{child.description}</small>
                <i aria-hidden="true">{"\u2192"}</i>
              </Link>
            ))}
          </div>
        ) : (
          <p className="hub-quiet-state">
            This hub is in place. Its first public tool will appear here when that capability is
            ready; the navigation will not advertise unfinished work.
          </p>
        )}
      </section>
      </main>
      <PublicFooter language={language} />
    </>
  );
}
