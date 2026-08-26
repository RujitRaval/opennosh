import react from "@vitejs/plugin-react";
import { defineConfig } from "vitest/config";

import trustGates from "../config/trust-gates.v1.json" with { type: "json" };

export default defineConfig({
  plugins: [react()],
  resolve: {
    tsconfigPaths: true,
  },
  test: {
    environment: "jsdom",
    environmentOptions: {
      jsdom: { url: "https://opennosh.test/" },
    },
    setupFiles: ["./vitest.setup.ts"],
    coverage: {
      provider: "v8",
      reporter: ["text", "json-summary"],
      include: ["components/**/*.{ts,tsx}", "lib/**/*.{ts,tsx}"],
      exclude: ["lib/generated/**", "lib/server/**", "**/*.d.ts"],
      thresholds: trustGates.coverage.web_repository,
    },
    exclude: [
      "tests/e2e/**",
      "tests/vertical/**",
      "tests/localization/**",
      "tests/visual/**",
      "node_modules/**",
    ],
  },
});
