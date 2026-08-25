import Link from "next/link";

import { CrossRootLink } from "@/components/shell/cross-root-link";
import type { PublicCommonsSnapshot } from "@/lib/api/domain/public-commons";
import { routes, type InterfaceLanguage } from "@/lib/routes";
import { getCatalog } from "@/lib/i18n/catalog";
import { FooterReleaseProof } from "./public-truth-signals";

import { BrandLogo } from "./brand-logo";

export function PublicFooter({
  language,
  snapshot,
}: {
  language: InterfaceLanguage;
  snapshot?: PublicCommonsSnapshot;
}) {
  const copy = getCatalog(language);
  return (
    <footer className="public-footer">
      <Link href={routes.publicHome(language)} aria-label={copy.common.opennoshHome}>
        <BrandLogo surface="signal-tomato" className="footer-brand" decorative />
      </Link>
      <nav aria-label={copy.shell.footerNavigation}>
        <Link href={routes.publicNotices(language)}>{copy.shell.licenses}</Link>
        <a href="https://github.com/RujitRaval/opennosh">{copy.shell.source}</a>
        <CrossRootLink href={routes.tracker.home}>{copy.shell.privateTracker}</CrossRootLink>
      </nav>
      <div className="footer-statement">
        <FooterReleaseProof language={language} snapshot={snapshot} />
        <p>{copy.shell.footerStatement}<br />{copy.shell.footerStatementSecond}</p>
      </div>
    </footer>
  );
}
