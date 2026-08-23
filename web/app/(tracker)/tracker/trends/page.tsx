import type { Metadata } from "next";

import { TrendsApp } from "@/components/trends/trends-app";

export const metadata: Metadata = {
  title: "Trends · opennosh",
  description: "Accessible nutrition, body-metric, and strength history.",
};

export default function TrendsPage() {
  return <TrendsApp />;
}
