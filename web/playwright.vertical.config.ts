import { defineConfig, devices } from "@playwright/test";

export default defineConfig({
  testDir: "./tests/vertical",
  outputDir: "./test-results/vertical-acceptance",
  fullyParallel: false,
  workers: 1,
  forbidOnly: true,
  retries: 0,
  reporter: process.env.CI ? "github" : "list",
  use: {
    baseURL: process.env.VERTICAL_BASE_URL ?? "http://127.0.0.1:3100",
    trace: "retain-on-failure",
  },
  projects: [
    { name: "vertical-trust-chromium", use: { ...devices["Desktop Chrome"] } },
  ],
});
