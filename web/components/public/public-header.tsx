"use client";

import Link from "next/link";
import { useEffect, useRef, useState } from "react";

import { routes, type InterfaceLanguage } from "@/lib/routes";

import { BrandLogo } from "./brand-logo";

const hubs = [
  { id: "explore", label: "Explore" },
  { id: "contribute", label: "Contribute" },
  { id: "commons", label: "Commons" },
  { id: "build", label: "Build" },
] as const;

export function PublicHeader({ language }: { language: InterfaceLanguage }) {
  const [open, setOpen] = useState(false);
  const buttonRef = useRef<HTMLButtonElement>(null);

  function closeMenu({ restoreFocus = false } = {}) {
    setOpen(false);
    if (restoreFocus) requestAnimationFrame(() => buttonRef.current?.focus());
  }

  useEffect(() => {
    if (!open) return;
    function onKeyDown(event: KeyboardEvent) {
      if (event.key === "Escape") closeMenu({ restoreFocus: true });
    }
    window.addEventListener("keydown", onKeyDown);
    return () => window.removeEventListener("keydown", onKeyDown);
  }, [open]);

  return (
    <header className="public-header">
      <Link className="public-brand" href={routes.publicHome(language)} aria-label="opennosh home">
        <BrandLogo surface="rice-paper" priority className="public-brand-image" decorative />
      </Link>

      <nav className="public-nav" aria-label="Primary navigation">
        {hubs.map((hub) => (
          <Link key={hub.id} href={routes.publicHub(hub.id, language)}>
            {hub.label}
          </Link>
        ))}
      </nav>

      <div className="public-utilities">
        <span className="language-label" aria-label="Interface language: English">EN</span>
        <Link className="tracker-link" href={routes.tracker.home}>Tracker <span aria-hidden="true">↗</span></Link>
        <button
          ref={buttonRef}
          className="menu-button"
          type="button"
          aria-expanded={open}
          aria-controls="mobile-menu"
          onClick={() => setOpen((current) => !current)}
        >
          {open ? "Close" : "Menu"}
        </button>
      </div>

      <nav id="mobile-menu" className="mobile-menu" aria-label="Mobile navigation" hidden={!open}>
        {hubs.map((hub, index) => (
          <Link key={hub.id} href={routes.publicHub(hub.id, language)} onClick={() => closeMenu()}>
            <span className="mono">0{index + 1}</span>{hub.label}
          </Link>
        ))}
        <Link className="mobile-tracker" href={routes.tracker.home} onClick={() => closeMenu()}>
          Open private tracker <span aria-hidden="true">↗</span>
        </Link>
      </nav>
    </header>
  );
}
