import { CrossRootLink } from "@/components/shell/cross-root-link";
import { routes, type InterfaceLanguage } from "@/lib/routes";

export function TrackerFooter({
  language,
  publicReturnPath,
}: {
  language: InterfaceLanguage;
  publicReturnPath: string;
}) {
  return (
    <footer className="site-footer">
      <p><strong>OPENNOSH / PRIVATE TRACKER</strong><span>Your records stay yours.</span></p>
      <nav aria-label="Public commons links">
        <CrossRootLink href={publicReturnPath}>Return to the commons</CrossRootLink>
        <CrossRootLink href={routes.publicNotices(language)}>Licenses &amp; data notices</CrossRootLink>
        <CrossRootLink href={`${routes.publicNotices(language)}#hosted-data`}>Hosting &amp; your data</CrossRootLink>
      </nav>
    </footer>
  );
}
