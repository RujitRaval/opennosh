import Link from "next/link";

import { routes } from "@/lib/routes";

import { TrackerWordmark } from "./tracker-wordmark";

export function TrackerHeader({
  active,
  email,
  onLogout,
}: {
  active: "daily" | "trends";
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
        <Link aria-current={active === "trends" ? "page" : undefined} href={routes.tracker.trends}>
          Trends
        </Link>
      </nav>
      <div className="account-menu">
        <span className="account-email">{email}</span>
        <button className="header-text-button" type="button" onClick={onLogout}>
          Sign out
        </button>
      </div>
    </header>
  );
}
