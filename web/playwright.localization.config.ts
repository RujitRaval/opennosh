import { defineConfig, devices } from "@playwright/test";

const port = process.env.E2E_LOCALIZATION_PORT || "3010";
const baseURL = `http://127.0.0.1:${port}`;
const apiPort = process.env.E2E_LOCALIZATION_API_PORT || "8010";
const apiURL = `http://127.0.0.1:${apiPort}`;

export default defineConfig({
  testDir: "./tests/localization",
  fullyParallel: false,
  forbidOnly: Boolean(process.env.CI),
  retries: 0,
  reporter: process.env.CI ? "github" : "list",
  use: { baseURL, trace: "on-first-retry" },
  projects: [
    { name: "shipped-language-desktop", grep: /@shipped/, use: { ...devices["Desktop Chrome"] } },
    { name: "shipped-language-mobile", grep: /@shipped/, use: { ...devices["Pixel 7"] } },
    { name: "pseudo-locale-desktop", grep: /@pseudo/, use: { ...devices["Desktop Chrome"] } },
    { name: "pseudo-locale-mobile", grep: /@pseudo/, use: { ...devices["Pixel 7"] } },
  ],
  webServer: [
    {
      command: `node tests/fixtures/public-food-api.mjs ${apiPort}`,
      url: `${apiURL}/health`,
      reuseExistingServer: false,
      timeout: 120_000,
    },
    {
      command: `API_URL=${apiURL} NEXT_PUBLIC_OPENNOSH_ENABLE_PSEUDO_LOCALE=1 npm run dev -- --hostname 127.0.0.1 --port ${port}`,
      url: baseURL,
      reuseExistingServer: false,
      timeout: 120_000,
    },
  ],
});
