import type { MetadataRoute } from "next";

import { routes } from "@/lib/routes";

export default function manifest(): MetadataRoute.Manifest {
  return {
    name: "opennosh tracker",
    short_name: "opennosh",
    description: "Private, self-hosted nutrition and strength tracking.",
    start_url: routes.tracker.home,
    display: "standalone",
    background_color: "#f6f4ed",
    theme_color: "#235b43",
  };
}
