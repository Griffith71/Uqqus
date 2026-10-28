import { test } from "@playwright/test";
import AxeBuilder from "@axe-core/playwright";
import { mkdirSync, writeFileSync } from "node:fs";
import { PAGES, THEMES, open } from "./pages";

// Writes reports/axe-<label>.json: serious and critical findings per page and theme, at a desktop and a phone width.
const label = process.env.SHOTS_LABEL ?? "current";
const results: Record<string, unknown> = {};

for (const target of PAGES) {
  for (const theme of THEMES) {
    for (const width of [390, 1280]) {
      test(`axe ${target.name} ${theme} ${width}`, async ({ browser }) => {
        const context = await browser.newContext({
          colorScheme: theme,
          viewport: { width, height: 900 },
          storageState: target.signedIn ? ".auth/viewer.json" : undefined,
        });
        const page = await context.newPage();
        await open(page, target.path);
        const { violations } = await new AxeBuilder({ page }).withTags(["wcag2a", "wcag2aa", "wcag21aa"]).analyze();
        results[`${target.name} ${theme} ${width}`] = violations
          .filter((v) => v.impact === "serious" || v.impact === "critical")
          .map((v) => ({ id: v.id, impact: v.impact, count: v.nodes.length, sample: v.nodes[0]?.target }));
        await context.close();
      });
    }
  }
}

test.afterAll(() => {
  mkdirSync("reports", { recursive: true });
  writeFileSync(`reports/axe-${label}.json`, JSON.stringify(results, null, 2));
});
