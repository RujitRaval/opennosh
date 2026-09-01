import type { Metadata } from "next";
import { notFound } from "next/navigation";
import type { ReactNode } from "react";

import { TrackerWordmark } from "@/components/tracker/tracker-wordmark";

import "../../base.css";
import "./governance.css";

export const dynamic = "force-dynamic";

export const metadata: Metadata = {
  title: "Steward review · opennosh",
  description: "Accountable, pack-scoped review for the living food commons.",
};

export default function GovernanceLayout({ children }: Readonly<{ children: ReactNode }>) {
  if (process.env.OPENNOSH_GOVERNANCE_STEWARD_UI_ENABLED !== "true") notFound();

  return (
    <html lang="en" data-surface="governance">
      <body>
        <a className="governance-skip" href="#main-content">Skip to review</a>
        <header className="governance-header">
          <TrackerWordmark surface="commons-ink" priority />
          <div>
            <p className="governance-kicker">Accountable stewardship</p>
            <p>Every decision keeps its reason and exact reviewed version.</p>
          </div>
        </header>
        {children}
      </body>
    </html>
  );
}
