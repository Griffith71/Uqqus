import { test } from "@playwright/test";
import { PAGES, WIDTHS, THEMES, open } from "./pages";

// SHOTS_LABEL=before npm run shots  ->  shots/before/<page>/<width>-<theme>.png
const label = process.env.SHOTS_LABEL ?? "current";
// SHOTS_ONLY=all,post-thread and SHOTS_WIDTHS=390,1280 narrow a run while iterating
const only = process.env.SHOTS_ONLY?.split(",");
const widths = process.env.SHOTS_WIDTHS?.split(",").map(Number) ?? WIDTHS;

for (const target of PAGES.filter((t) => !only || only.includes(t.name))) {
  for (const theme of THEMES) {
    test(`${target.name} ${theme}`, async ({ browser }) => {
      // no saved theme: the device's preference decides (partials/theme.html), as for a new visitor
      const context = await browser.newContext({
        colorScheme: theme,
        storageState: target.signedIn ? ".auth/viewer.json" : undefined,
      });
      if (target.density) {
        const url = new URL(process.env.UI_BASE_URL ?? "http://localhost");
        await context.addCookies([{ name: "density", value: target.density, domain: url.hostname, path: "/" }]);
      }
      const page = await context.newPage();
      for (const width of widths) {
        await page.setViewportSize({ width, height: width < 768 ? 844 : 900 });
        await open(page, target.path);
        await page.screenshot({
          path: `shots/${label}/${target.name}/${width}-${theme}.png`,
          fullPage: true,
          animations: "disabled",
          mask: (target.mask ?? []).map((selector) => page.locator(selector)),
        });
      }
      await context.close();
    });
  }
}
