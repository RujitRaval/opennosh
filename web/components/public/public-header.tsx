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
import {
  interfaceLanguageCookie,
  isPseudoLanguageEnabled,
  localizePublicPath,
  pseudoLanguage,
  routes,
  supportedLanguages,
  type InterfaceLanguage,
} from "@/lib/routes";
import { formatMessage, getCatalog } from "@/lib/i18n/catalog";

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
  const usesDarkHeader = pathname === routes.publicHub("build", language);
  const usesTomatoHeader = pathname.startsWith(`${routes.publicHub("contribute", language)}/`);
  const catalog = getCatalog(language);
  const copy = getPublicShellCopy(language);
  const contextHub = navigation.find((hub) => hub.id === activeHub) ?? navigation[0];
  const contextAction = usesTomatoHeader
      ? {
        href: routes.publicHub("contribute", language),
        compactLabel: copy.contributionContext,
      }
    : activeHub
      ? contextHub.nextAction
      : {
          href: routes.publicHub("explore", language),
          compactLabel: contextHub.label,
        };
  const [open, setOpen] = useState(false);
  const buttonRef = useRef<HTMLButtonElement>(null);
  const firstLinkRef = useRef<HTMLAnchorElement>(null);
  const languageOptions: readonly InterfaceLanguage[] =
    isPseudoLanguageEnabled()
      ? [...supportedLanguages, pseudoLanguage]
      : supportedLanguages;

  function changeLanguage(nextLanguage: InterfaceLanguage) {
    const search = window.location.search;
    document.cookie = interfaceLanguageCookie + "=" + encodeURIComponent(nextLanguage) + "; Path=/; Max-Age=31536000; SameSite=Lax";
    window.location.assign(localizePublicPath({
      pathname,
      search,
      language: nextLanguage,
    }));
  }

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
    <header
      className={`public-header${usesDarkHeader ? " public-header-dark" : ""}${usesTomatoHeader ? " public-header-tomato" : ""}`}
    >
      <Link className="public-brand" href={routes.publicHome(language)} aria-label={catalog.common.opennoshHome}>
        <BrandLogo
          surface={usesDarkHeader ? "commons-ink" : usesTomatoHeader ? "signal-tomato" : "rice-paper"}
          priority
          className="public-brand-image"
          decorative
        />
      </Link>

      <nav className="public-nav" aria-label={copy.primaryNavigation}>
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
        <select
          className="language-label"
          aria-label={formatMessage(copy.interfaceLabel, { language: copy.interfaceLanguage })}
          title={copy.foodLocaleIndependent}
          value={language}
          onChange={(event) => changeLanguage(event.target.value as InterfaceLanguage)}
        >
          {languageOptions.map((option) => (
            <option key={option} value={option}>{getCatalog(option).shell.languageCode}</option>
          ))}
        </select>
        <Link className="tracker-link" href={routes.tracker.home}>
          {copy.tracker} <span aria-hidden="true">{"\u2197"}</span>
        </Link>
        <Link className="mobile-context-action" href={contextAction.href}>
          {usesTomatoHeader
            ? contextAction.compactLabel
            : formatMessage(copy.nextAction, { action: contextAction.compactLabel })}
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

      <nav id="mobile-menu" className="mobile-menu" aria-label={copy.mobileNavigation} hidden={!open}>
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
        <div className="mobile-utilities" aria-label={copy.utilities}>
          <select
            aria-label={formatMessage(copy.interfaceLabel, { language: copy.interfaceLanguage })}
            title={copy.foodLocaleIndependent}
            value={language}
            onChange={(event) => changeLanguage(event.target.value as InterfaceLanguage)}
          >
            {languageOptions.map((option) => (
              <option key={option} value={option}>
                {formatMessage(getCatalog(option).shell.interfaceCompact, { code: getCatalog(option).shell.languageCode })}
              </option>
            ))}
          </select>
          <Link className="mobile-tracker" href={routes.tracker.home} onClick={() => closeMenu()}>
            {copy.mobileTracker} <span aria-hidden="true">{"\u2197"}</span>
          </Link>
        </div>
      </nav>
    </header>
  );
}
