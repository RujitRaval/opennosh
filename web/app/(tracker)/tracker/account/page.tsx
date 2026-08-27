import type { Metadata } from "next";

import { AccountApp } from "@/components/tracker/account-app";

export const metadata: Metadata = {
  title: "Account settings · opennosh",
  description: "Manage your private opennosh Tracker account and measurement preferences.",
};

export default function AccountPage() {
  return <AccountApp />;
}
