import Link from "next/link";

import { routes, type InterfaceLanguage } from "@/lib/routes";

import { BrandLogo } from "./brand-logo";

export function PublicFooter({ language }: { language: InterfaceLanguage }) {
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
      <p>Open infrastructure for food knowledge.<br />Built in public.</p>
    </footer>
  );
}
