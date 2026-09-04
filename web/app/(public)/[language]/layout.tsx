import type { Metadata } from "next";
import { notFound } from "next/navigation";
import type { ReactNode } from "react";
import { preload } from "react-dom";

import { PublicHeader } from "@/components/public/public-header";
import { PublicPerformanceSignals } from "@/components/public/public-performance-signals";
import {
  buildPublicNavigation,
  parsePublicFeatureFlags,
} from "@/lib/public-navigation";
import { isSupportedLanguage, pseudoLanguage, supportedLanguages } from "@/lib/routes";
import { getCatalog } from "@/lib/i18n/catalog";

import "../../base.css";
import { criticalPublicFontPreloads } from "./fonts";
import "./fonts.css";
import "./tokens.css";
import "./public.css";
import "./contribution.css";
import "./truth-signals.css";
import "./living-commons.css";

export async function generateMetadata({
  params,
}: {
  params: Promise<{ language: string }>;
}): Promise<Metadata> {
  const { language } = await params;
  if (!isSupportedLanguage(language)) return {};
  const copy = getCatalog(language).metadata;
  return { title: copy.homeTitle, description: copy.homeDescription };
}

export function generateStaticParams() {
  return supportedLanguages.map((language) => ({ language }));
}

export default async function PublicLayout({
  children,
  params,
}: Readonly<{
  children: ReactNode;
  params: Promise<{ language: string }>;
}>) {
  const { language } = await params;
  if (!isSupportedLanguage(language)) notFound();
  const copy = getCatalog(language);

  const navigation = buildPublicNavigation(
    language,
    parsePublicFeatureFlags(process.env.OPENNOSH_PUBLIC_NAV_FEATURES),
  );
  const decorationsEnabled = process.env.NEXT_PUBLIC_OPENNOSH_MOTION_DECORATIONS !== "off";

  for (const href of criticalPublicFontPreloads) {
    preload(href, {
      as: "font",
      type: "font/woff2",
      crossOrigin: "anonymous",
    });
  }

  return (
    <html
      lang={language}
      data-surface="public"
      data-scroll-behavior="smooth"
      data-motion="off"
      data-motion-state="paused"
      data-motion-reason={decorationsEnabled ? "server-static" : "kill-switch"}
      data-motion-decorations={decorationsEnabled ? "on" : "off"}
      data-interface-language={language}
      data-pseudo-locale={language === pseudoLanguage ? "true" : undefined}
    >
      <body className="public-root">
        <a className="skip-link" href="#main-content">{copy.common.skipToContent}</a>
        <PublicHeader language={language} navigation={navigation} />
        {children}
        <PublicPerformanceSignals decorationsEnabled={decorationsEnabled} />
      </body>
    </html>
  );
}
