import type { Metadata } from "next";
import { notFound } from "next/navigation";
import type { ReactNode } from "react";

import { PublicFooter } from "@/components/public/public-footer";
import { PublicHeader } from "@/components/public/public-header";
import { PublicPerformanceSignals } from "@/components/public/public-performance-signals";
import {
  buildPublicNavigation,
  parsePublicFeatureFlags,
} from "@/lib/public-navigation";
import { archivo, plexMono, sourceSans } from "@/lib/public-fonts";
import { isSupportedLanguage, supportedLanguages } from "@/lib/routes";

import "./public.css";

export const metadata: Metadata = {
  title: "Food data belongs to everyone - opennosh",
  description: "Search, verify, improve, and reuse an open, versioned food-data commons.",
};

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

  const navigation = buildPublicNavigation(
    language,
    parsePublicFeatureFlags(process.env.OPENNOSH_PUBLIC_NAV_FEATURES),
  );
  const decorationsEnabled = process.env.NEXT_PUBLIC_OPENNOSH_MOTION_DECORATIONS !== "off";

  return (
    <html
      lang={language}
      data-surface="public"
      data-scroll-behavior="smooth"
      data-motion="off"
      data-motion-state="paused"
      data-motion-reason={decorationsEnabled ? "server-static" : "kill-switch"}
      data-motion-decorations={decorationsEnabled ? "on" : "off"}
    >
      <body className={`public-root ${archivo.variable} ${sourceSans.variable} ${plexMono.variable}`}>
        <a className="skip-link" href="#main-content">Skip to content</a>
        <PublicHeader language={language} navigation={navigation} />
        <PublicPerformanceSignals decorationsEnabled={decorationsEnabled} />
        {children}
        <PublicFooter language={language} />
      </body>
    </html>
  );
}
