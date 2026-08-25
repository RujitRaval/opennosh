import Link from "next/link";

import { BrandLogo } from "@/components/public/brand-logo";
import type { BrandSurface } from "@/lib/brand-assets";
import { routes } from "@/lib/routes";

export function TrackerWordmark({
  surface = "rice-paper",
  priority = false,
  className = "tracker-wordmark",
}: {
  surface?: BrandSurface;
  priority?: boolean;
  className?: string;
}) {
  return (
    <Link className={className} href={routes.tracker.home} aria-label="opennosh tracker">
      <BrandLogo surface={surface} priority={priority} decorative />
    </Link>
  );
}
