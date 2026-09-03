import { defineConfig, devices } from "@playwright/test";

const port = process.env.E2E_VISUAL_PORT || "3020";
const baseURL = `http://127.0.0.1:${port}`;
const apiPort = process.env.E2E_VISUAL_API_PORT || "8020";
const apiURL = `http://127.0.0.1:${apiPort}`;

process.env.TZ = "UTC";

export const visualRuntimeImage = "mcr.microsoft.com/playwright@sha256:dcc5531e97840b9b5e794f2814476b21571c5124a3fca2267d73041f56e7580e";

export default defineConfig({
  testDir: "./tests/visual",
  outputDir: "./test-results/visual",
  snapshotPathTemplate: "{testDir}/__screenshots__/{projectName}/{arg}{ext}",
  fullyParallel: false,
  workers: 1,
  forbidOnly: Boolean(process.env.CI),
  retries: 0,
  reporter: process.env.CI
    ? [["github"], ["html", { outputFolder: "playwright-report/visual", open: "never" }]]
    : [["list"], ["html", { outputFolder: "playwright-report/visual", open: "never" }]],
  expect: {
    toHaveScreenshot: {
      animations: "disabled",
      caret: "hide",
      scale: "css",
      threshold: 0.2,
      maxDiffPixels: 0,
    },
  },
  use: {
    baseURL,
    browserName: "chromium",
    colorScheme: "light",
    locale: "en-US",
    timezoneId: "UTC",
    trace: "retain-on-failure",
    screenshot: "only-on-failure",
  },
  projects: [
    {
      name: "reflow-320",
      grep: /@reflow/,
      use: { ...devices["Desktop Chrome"], viewport: { width: 320, height: 900 } },
    },
    {
      name: "mobile-390",
      grep: /@mobile/,
      use: { ...devices["Desktop Chrome"], viewport: { width: 390, height: 844 }, isMobile: true },
    },
    {
      name: "tablet-768",
      grep: /@tablet/,
      use: { ...devices["Desktop Chrome"], viewport: { width: 768, height: 1024 } },
    },
    {
      name: "desktop-1440",
      grep: /@(desktop|focused)/,
      use: { ...devices["Desktop Chrome"], viewport: { width: 1440, height: 1000 } },
    },
    {
      name: "wide-1728",
      grep: /@wide/,
      use: { ...devices["Desktop Chrome"], viewport: { width: 1728, height: 1117 } },
    },
  ],
  webServer: [
    {
      command: `node tests/fixtures/public-food-api.mjs ${apiPort}`,
      url: `${apiURL}/health`,
      reuseExistingServer: false,
      timeout: 120_000,
    },
    {
      command: `API_URL=${apiURL} OPENNOSH_VISUAL_FIXTURES=1 OPENNOSH_PUBLIC_NAV_FEATURES=explorer-search,commons-missions NEXT_PUBLIC_OPENNOSH_ENABLE_PSEUDO_LOCALE=1 NEXT_PUBLIC_OPENNOSH_MOTION_DECORATIONS=off npm run dev -- --hostname 127.0.0.1 --port ${port}`,
      url: baseURL,
      reuseExistingServer: false,
      timeout: 120_000,
    },
  ],
});
