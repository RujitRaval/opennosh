import type { NextConfig } from "next";

const nextConfig: NextConfig = {
  output: "standalone",
  devIndicators: process.env.OPENNOSH_VISUAL_FIXTURES === "1" ? false : undefined,
};

export default nextConfig;
