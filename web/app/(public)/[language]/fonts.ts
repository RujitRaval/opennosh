import { publicFontAssets } from "@/lib/public-font-assets";

export const criticalPublicFontPreloads = Object.values(publicFontAssets)
  .filter((asset) => asset.delivery === "critical")
  .map((asset) => asset.href);
