import { defineConfig, devices } from "@playwright/test";

const port = process.env.E2E_PORT || "3000";
const baseURL = `http://127.0.0.1:${port}`;
const apiPort = process.env.E2E_API_PORT || "8001";
const apiURL = `http://127.0.0.1:${apiPort}`;

export default defineConfig({
  testDir: "./tests/e2e",
  outputDir: "./test-results/ui-journeys",
  fullyParallel: true,
  forbidOnly: Boolean(process.env.CI),
  retries: process.env.CI ? 2 : 0,
  reporter: process.env.CI ? "github" : "list",
  use: {
    baseURL,
    trace: "on-first-retry",
  },
  projects: [
    { name: "ui-journey-chromium", use: { ...devices["Desktop Chrome"] } },
    { name: "ui-journey-mobile", use: { ...devices["Pixel 7"] } },
  ],
  webServer: [
    {
      command: `node tests/fixtures/public-food-api.mjs ${apiPort}`,
      url: `${apiURL}/health`,
      reuseExistingServer: !process.env.CI,
      timeout: 120_000,
    },
    {
      command: `API_URL=${apiURL} PUBLIC_ARTIFACT_READS_ENABLED=true OPENNOSH_PUBLIC_NAV_FEATURES=explorer-search OPENNOSH_GOVERNANCE_STEWARD_UI_ENABLED=true npm run dev -- --hostname 127.0.0.1 --port ${port}`,
      url: baseURL,
      reuseExistingServer: !process.env.CI,
      timeout: 120_000,
    },
  ],
});
