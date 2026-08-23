import Link from "next/link";

export type PublicBreadcrumb = {
  label: string;
  href?: string;
};

export function PublicBreadcrumbs({ items }: { items: readonly PublicBreadcrumb[] }) {
  return (
    <nav className="public-breadcrumbs" aria-label="Breadcrumb">
      <ol>
        {items.map((item, index) => {
          const current = index === items.length - 1;
          return (
            <li key={`${item.label}-${index}`}>
              {item.href && !current ? (
                <Link href={item.href}>{item.label}</Link>
              ) : (
                <span aria-current={current ? "page" : undefined}>{item.label}</span>
              )}
            </li>
          );
        })}
      </ol>
    </nav>
  );
}
