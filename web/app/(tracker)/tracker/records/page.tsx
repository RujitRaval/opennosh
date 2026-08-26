import type { Metadata } from "next";

import { RecordsApp } from "@/components/tracker/records-app";

export const metadata: Metadata = {
  title: "Body and strength records · opennosh",
  description: "Private body measurement and strength records in the opennosh Tracker.",
};

export default function RecordsPage() {
  return <RecordsApp />;
}
