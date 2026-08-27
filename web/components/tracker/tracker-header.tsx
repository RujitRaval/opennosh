import Link from "next/link";

import { routes } from "@/lib/routes";

import { TrackerWordmark } from "./tracker-wordmark";

export function TrackerHeader({
  active,
  email,
  onLogout,
}: {
  active: "daily" | "records" | "trends" | "account";
  email: string;
  onLogout: () => void;
}) {
  return (
    <header className="app-header">
      <div className="tracker-identity">
        <TrackerWordmark surface="commons-ink" priority />
        <span className="tracker-mode">Private tracker</span>
      </div>
      <nav className="primary-nav" aria-label="Primary navigation">
        <Link aria-current={active === "daily" ? "page" : undefined} href={routes.tracker.home}>
          Daily log
        </Link>
        <Link aria-current={active === "records" ? "page" : undefined} href={routes.tracker.records}>
          Records
        </Link>
        <Link aria-current={active === "trends" ? "page" : undefined} href={routes.tracker.trends}>
          Trends
        </Link>
      </nav>
      <div className="account-menu">
        <Link aria-current={active === "account" ? "page" : undefined} href={routes.tracker.account}>
          Account
        </Link>
        <span className="account-email">{email}</span>
        <button className="header-text-button" type="button" onClick={onLogout}>
          Sign out
        </button>
      </div>
    </header>
  );
}
