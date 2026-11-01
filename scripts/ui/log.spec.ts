import { test, expect } from "@playwright/test";
import AxeBuilder from "@axe-core/playwright";

// The public Log (routes/log_pages.py, helpers/log_view.py). Seed it first:   PYTHONPATH=. python scripts/ui/seed_log.py
//   npm run log
// A hide shows who, where, the rule and the reason, a post by its id and never its title; a private guild's decision is not in it; the page is the
// same for a signed-in member; no sideways scroll on a phone; the filters and tabs are targets a finger can hit; axe finds nothing serious.

const WIDTHS = [390, 1280];
const THEMES = ["light", "dark"] as const;

for (const theme of THEMES) {
  for (const width of WIDTHS) {
    test(`the public log ${theme} ${width}`, async ({ browser }) => {
      const context = await browser.newContext({ colorScheme: theme, viewport: { width, height: 900 } });
      const page = await context.newPage();
      await page.goto("/log", { waitUntil: "load" });
      const list = page.locator("#site-log");
      await expect(list.locator(".log-entry")).toHaveCount(2);                           // Moderation: the guildmaster's hide and the admin's removal (the private guild's is not here)
      const text = await list.innerText();
      expect(text).toContain("Advertising a shop");
      expect(text).toContain("Free expression and good faith");
      expect(text).not.toContain("A title nobody may read in the log");
      expect(text).not.toContain("PRIVATE-GUILD-REASON");
      expect(text).not.toContain("ui_averyverylongusername");

      // an admin's decision is the site's, not a name
      expect(text).toContain("Copyright infringement");
      expect(await list.locator("a[href='/@ui_admin']").count()).toBe(0);

      // sanctions tab: the account ban names no guild and no post
      await page.goto("/log?type=sanction", { waitUntil: "load" });
      const sanctions = await page.locator("#site-log").innerText();
      expect(sanctions).toContain("Posting the same link in many guilds");
      expect(sanctions).toContain("Repeated insults in the comments");
      await expect(page.locator("#site-log .log-entry")).toHaveCount(2);

      await page.goto("/log?type=guild", { waitUntil: "load" });
      await expect(page.locator("#site-log .log-entry")).toHaveCount(1);                 // a guild change

      // no sideways scroll, and the controls a finger meets are not tiny
      const scroll = await page.evaluate(() => document.documentElement.scrollWidth - window.innerWidth);
      expect(scroll).toBeLessThanOrEqual(0);
      for (const control of await page.locator("#log-filter input:not([type=hidden]), #log-filter select, #log-filter button").all()) {
        const box = await control.boundingBox();
        expect(box!.height).toBeGreaterThanOrEqual(24);                                  // WCAG 2.5.8
      }

      const { violations } = await new AxeBuilder({ page }).include("#site-log").withTags(["wcag2a", "wcag2aa", "wcag21aa"]).analyze();
      // the white label on the site's own button fill is the owner's accepted exception (CLAUDE.md "Colour contrast")
      const found = violations.filter((v) => v.impact === "serious" || v.impact === "critical")
        .filter((v) => !(v.id === "color-contrast" && v.nodes.every((n) => String(n.target).includes(".btn-primary"))));
      expect(found.map((v) => `${v.id}: ${v.nodes[0]?.target}`)).toEqual([]);
      await page.goto("/log", { waitUntil: "load" });
      await page.screenshot({ path: `shots/log-${theme}-${width}.png`, fullPage: true });
      await context.close();
    });
  }
}

test("a signed-in member reads the same page, and the old address lands on it", async ({ browser }) => {
  const context = await browser.newContext({ storageState: ".auth/viewer.json" });
  const page = await context.newPage();
  await page.goto("/log/deleted", { waitUntil: "load" });
  expect(new URL(page.url()).pathname).toBe("/log");
  await expect(page.locator("#site-log .log-entry")).toHaveCount(2);
  const text = await page.locator("#site-log").innerText();
  expect(text).not.toContain("PRIVATE-GUILD-REASON");
  await context.close();
});

test("a guild the reader has no way to see answers 404 for its log", async ({ page }) => {
  expect((await page.goto("/+ui_no_such_guild/mod/log"))!.status()).toBe(404);
});

// the log of one account (/@name/log): what was decided about it and about what it wrote, by id and never by words
for (const theme of THEMES) {
  for (const width of WIDTHS) {
    test(`an account's log ${theme} ${width}`, async ({ browser }) => {
      const context = await browser.newContext({ colorScheme: theme, viewport: { width, height: 900 } });
      const page = await context.newPage();
      const answer = await page.goto("/@ui_carol/log", { waitUntil: "load" });
      expect(answer!.status()).toBe(200);
      await expect(page.locator("h1")).toContainText("Log of @ui_carol");
      const text = await page.locator("#site-log").innerText();
      expect(text).toContain("Repeated insults in the comments");
      expect(text).not.toContain("A title nobody may read in the log");
      expect(text).not.toContain("PRIVATE-GUILD-REASON");
      expect(await page.evaluate(() => document.documentElement.scrollWidth - window.innerWidth)).toBeLessThanOrEqual(0);
      const { violations } = await new AxeBuilder({ page }).include("#site-log").withTags(["wcag2a", "wcag2aa", "wcag21aa"]).analyze();
      const found = violations.filter((v) => v.impact === "serious" || v.impact === "critical");
      expect(found.map((v) => `${v.id}: ${v.nodes[0]?.target}`)).toEqual([]);
      await page.screenshot({ path: `shots/log-account-${theme}-${width}.png`, fullPage: true });
      await context.close();
    });
  }
}

test("an account that is not there has no log", async ({ page }) => {
  expect((await page.goto("/@ui_no_such_account_here/log"))!.status()).toBe(404);
});
