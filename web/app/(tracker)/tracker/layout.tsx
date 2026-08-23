import type { Metadata } from "next";
import type { ReactNode } from "react";

import { TrackerFooter } from "@/components/tracker/tracker-footer";

import "./tracker.css";

export const metadata: Metadata = {
  title: "Daily nutrition log · opennosh",
  description: "Accessible, self-hosted nutrition and strength tracking.",
};

export default function TrackerLayout({ children }: Readonly<{ children: ReactNode }>) {
  return (
    <html lang="en" data-surface="tracker">
      <body>
        {children}
        <TrackerFooter />
      </body>
    </html>
  );
}
