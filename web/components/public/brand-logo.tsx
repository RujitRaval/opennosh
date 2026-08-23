import Image from "next/image";

import { brandWordmarks, type BrandSurface } from "@/lib/brand-assets";

type BrandLogoProps = {
  surface?: BrandSurface;
  priority?: boolean;
  className?: string;
  decorative?: boolean;
};

export function BrandLogo({
  surface = "rice-paper",
  priority = false,
  className,
  decorative = false,
}: BrandLogoProps) {
  const asset = brandWordmarks[surface];

  return (
    <Image
      src={asset.src}
      width={asset.width}
      height={asset.height}
      alt={decorative ? "" : "opennosh"}
      aria-hidden={decorative || undefined}
      className={className}
      priority={priority}
      unoptimized
    />
  );
}
