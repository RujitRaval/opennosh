import Link from "next/link";

import type { PublicCommonsSnapshot } from "@/lib/api/domain/public-commons";
import { routes, type InterfaceLanguage } from "@/lib/routes";
import { FooterReleaseProof } from "./public-truth-signals";

import { BrandLogo } from "./brand-logo";

export function PublicFooter({
  language,
  snapshot,
}: {
  language: InterfaceLanguage;
  snapshot?: PublicCommonsSnapshot;
}) {
  return (
    <footer className="public-footer">
      <Link href={routes.publicHome(language)} aria-label="opennosh home">
        <BrandLogo surface="signal-tomato" className="footer-brand" decorative />
      </Link>
      <nav aria-label="Footer navigation">
        <Link href={routes.publicNotices(language)}>Licenses + notices</Link>
        <a href="https://github.com/RujitRaval/opennosh">Source</a>
        <Link href={routes.tracker.home}>Private tracker</Link>
      </nav>
      <div className="footer-statement">
        <FooterReleaseProof language={language} snapshot={snapshot} />
        <p>Open infrastructure for food knowledge.<br />Built in public.</p>
      </div>
    </footer>
  );
}
