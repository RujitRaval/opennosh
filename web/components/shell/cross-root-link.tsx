import type { AnchorHTMLAttributes, ReactNode } from "react";

type CrossRootLinkProps = AnchorHTMLAttributes<HTMLAnchorElement> & {
  children: ReactNode;
  href: string;
};

/** A native anchor makes the intentional transition between independent roots explicit. */
export function CrossRootLink({ children, ...props }: Readonly<CrossRootLinkProps>) {
  return <a {...props}>{children}</a>;
}
