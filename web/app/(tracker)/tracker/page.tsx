import type { Metadata } from "next";

import { DailyLogApp } from "@/components/log/daily-log-app";

export const metadata: Metadata = {
  title: "Daily nutrition log · opennosh",
  description: "Your private, self-hosted nutrition and strength tracker.",
};

export default function TrackerPage() {
  return <DailyLogApp />;
}
