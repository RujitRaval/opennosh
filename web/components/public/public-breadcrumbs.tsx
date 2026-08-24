import Link from "next/link";

export type PublicBreadcrumb = {
  label: string;
  href?: string;
};

export function PublicBreadcrumbs({
  items,
  label = "Breadcrumb",
}: {
  items: readonly PublicBreadcrumb[];
  label?: string;
}) {
  return (
    <nav className="public-breadcrumbs" aria-label={label}>
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
