import { defineConfig } from "@playwright/test";

// The site under test is the Docker stack (README.md in this folder). Nothing is started from here.
export default defineConfig({
  testDir: ".",
  timeout: 120_000,
  workers: 2,
  reporter: [["list"]],
  globalSetup: "./sign-in.ts",
  use: {
    baseURL: process.env.UI_BASE_URL ?? "http://localhost",
    // the live-updates stream never goes idle, so pages are waited for by their load event (pages.ts)
    actionTimeout: 15_000,
    navigationTimeout: 60_000,          // a page that stalls fails the step instead of holding a long run for hours
  },
});
