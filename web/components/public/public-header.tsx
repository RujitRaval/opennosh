"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";
import { useCallback, useEffect, useRef, useState } from "react";

import {
  buildPublicNavigation,
  getPublicShellCopy,
  resolvePublicHub,
  type PublicNavigationHub,
} from "@/lib/public-navigation";
import { routes, type InterfaceLanguage } from "@/lib/routes";

import { BrandLogo } from "./brand-logo";

export function PublicHeader({
  language,
  navigation = buildPublicNavigation(language),
}: {
  language: InterfaceLanguage;
  navigation?: readonly PublicNavigationHub[];
}) {
  const pathname = usePathname() ?? routes.publicHome(language);
  const activeHub = resolvePublicHub(pathname, language);
  const contextHub = navigation.find((hub) => hub.id === activeHub) ?? navigation[0];
  const copy = getPublicShellCopy(language);
  const [open, setOpen] = useState(false);
  const buttonRef = useRef<HTMLButtonElement>(null);
  const firstLinkRef = useRef<HTMLAnchorElement>(null);

  const closeMenu = useCallback(({ restoreFocus = false } = {}) => {
    setOpen(false);
    if (restoreFocus) requestAnimationFrame(() => buttonRef.current?.focus());
  }, []);

  useEffect(() => {
    if (!open) return;
    const frame = requestAnimationFrame(() => firstLinkRef.current?.focus());
    function onKeyDown(event: KeyboardEvent) {
      if (event.key === "Escape") closeMenu({ restoreFocus: true });
    }
    window.addEventListener("keydown", onKeyDown);
    return () => {
      cancelAnimationFrame(frame);
      window.removeEventListener("keydown", onKeyDown);
    };
  }, [closeMenu, open]);

  return (
    <header className="public-header">
      <Link className="public-brand" href={routes.publicHome(language)} aria-label="opennosh home">
        <BrandLogo surface="rice-paper" priority className="public-brand-image" decorative />
      </Link>

      <nav className="public-nav" aria-label="Primary navigation">
        {navigation.map((hub) => (
          <Link
            key={hub.id}
            href={routes.publicHub(hub.id, language)}
            aria-current={hub.id === activeHub ? "page" : undefined}
          >
            {hub.label}
          </Link>
        ))}
      </nav>

      <div className="public-utilities">
        <span
          className="language-label"
          aria-label={`Interface language: ${copy.interfaceLanguage}`}
          title="Food locale is selected independently in Explore"
        >
          EN
        </span>
        <Link className="tracker-link" href={routes.tracker.home}>
          {copy.tracker} <span aria-hidden="true">&nearr;</span>
        </Link>
        <Link className="mobile-context-action" href={contextHub.nextAction.href}>
          Next: {contextHub.nextAction.label}
        </Link>
        <button
          ref={buttonRef}
          className="menu-button"
          type="button"
          aria-expanded={open}
          aria-controls="mobile-menu"
          onClick={() => (open ? closeMenu() : setOpen(true))}
        >
          {open ? copy.close : copy.menu}
        </button>
      </div>

      <nav id="mobile-menu" className="mobile-menu" aria-label="Mobile navigation" hidden={!open}>
        <div className="mobile-hubs">
          {navigation.map((hub, index) => (
            <section key={hub.id} className="mobile-hub" aria-labelledby={`mobile-${hub.id}`}>
              <Link
                ref={index === 0 ? firstLinkRef : undefined}
                id={`mobile-${hub.id}`}
                className="mobile-hub-link"
                href={routes.publicHub(hub.id, language)}
                aria-current={hub.id === activeHub ? "page" : undefined}
                onClick={() => closeMenu()}
              >
                <span className="mono">{hub.index}</span>
                <span>{hub.label}</span>
              </Link>
              {hub.children.length > 0 ? (
                <div className="mobile-child-links">
                  {hub.children.map((child) => (
                    <Link key={child.id} href={child.href} onClick={() => closeMenu()}>
                      {child.label}
                    </Link>
                  ))}
                </div>
              ) : null}
            </section>
          ))}
        </div>
        <div className="mobile-utilities" aria-label="Utilities">
          <span aria-label={`Interface language: ${copy.interfaceLanguage}`}>
            Interface / EN
          </span>
          <Link className="mobile-tracker" href={routes.tracker.home} onClick={() => closeMenu()}>
            {copy.mobileTracker} <span aria-hidden="true">&nearr;</span>
          </Link>
        </div>
      </nav>
    </header>
  );
}
