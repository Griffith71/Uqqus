import { chromium, type FullConfig } from "@playwright/test";
import { mkdirSync } from "node:fs";

// Signs the seeded viewer in once (scripts/seed_ui_fixtures.py) and keeps the cookies for the signed-in shots.
export default async function signIn(config: FullConfig) {
  const baseURL = config.projects[0].use.baseURL ?? "http://localhost";
  mkdirSync(".auth", { recursive: true });
  const browser = await chromium.launch();
  const page = await browser.newPage({ baseURL });
  await page.goto("/login");
  await page.fill("#username", "ui_viewer");
  await page.fill("#password", "ui-Password-1");
  await Promise.all([page.waitForURL((url) => !url.pathname.startsWith("/login")), page.click("#login_button")]);
  await page.context().storageState({ path: ".auth/viewer.json" });
  await browser.close();
}
