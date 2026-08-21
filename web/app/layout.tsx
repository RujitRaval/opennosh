import type { Metadata } from "next";
import Link from "next/link";
import type { ReactNode } from "react";

import "./styles.css";

export const metadata: Metadata = {
  title: "Daily nutrition log · opennosh",
  description: "Accessible, self-hosted nutrition and strength tracking.",
};

export function LegalFooter() {
  return (
    <footer className="site-footer">
      <Link href="/notices">Licenses &amp; data notices</Link>
    </footer>
  );
}

export default function RootLayout({ children }: Readonly<{ children: ReactNode }>) {
  return (
    <html lang="en">
      <body>
        {children}
        <LegalFooter />
      </body>
    </html>
  );
}
