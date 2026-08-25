import type { Metadata } from "next";
import { cookies } from "next/headers";
import type { ReactNode } from "react";

import { TrackerFooter } from "@/components/tracker/tracker-footer";
import { interfaceLanguageCookie } from "@/lib/routes";
import { publicReturnPathCookie, resolveTrackerRootContext } from "@/lib/root-topology";

import "../../base.css";
import "./tracker.css";

export const metadata: Metadata = {
  title: "Daily nutrition log · opennosh",
  description: "Accessible, self-hosted nutrition and strength tracking.",
};

export default async function TrackerLayout({ children }: Readonly<{ children: ReactNode }>) {
  const cookieStore = await cookies();
  const context = resolveTrackerRootContext({
    savedLanguage: cookieStore.get(interfaceLanguageCookie)?.value,
    savedPublicPath: cookieStore.get(publicReturnPathCookie)?.value,
  });
  return (
    <html
      lang={context.language}
      data-surface="tracker"
      data-interface-language={context.language}
    >
      <body>
        {children}
        <TrackerFooter
          language={context.language}
          publicReturnPath={context.publicReturnPath}
        />
      </body>
    </html>
  );
}
