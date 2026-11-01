import { test, expect, type Page } from "@playwright/test";
import { SITE_PATHS } from "./pages";

// One type scale across the site (CLAUDE.md "Type scale"; the owner, 2026-10-30: "I want the text sizing to be consistent across the website"):
//   npm run typescale
// Every visible piece of text on the pages below is one of the steps in _tokens.scss (12 / 13 / 14 / 15 / 18 / 22px at the 14px root, in rem, so
// they scale together with Text size), or sits in one of the documented exceptions: the heading ladder above 22px, code, badges and the logo mark,
// which are proportions of their surroundings or a ladder of their own. The right sidebar's box titles are the left sidebar's small label, and the
// top bar is still 56px. Needs the fixtures (scripts/seed_ui_fixtures.py) and scripts/ui/seed_sidebar.py (the curation page it opens).

const STEPS = [12, 13, 14, 15, 18, 22];
const EXCEPT = "h1, h2, h3, h4, .h1, .h2, .h3, .h4, .explore-title, code, pre, kbd, .badge, .brand-main, svg";
const PAGES = SITE_PATHS;

// the root is 14px at the default text size, 16px at Large and 18px at Extra large: a step is in rem, so it is compared at that root
const ROOTS: [string, string, number][] = [["default", "", 14], ["Extra large text", "xlarge", 18]];

async function strays(page: Page, root: number) {
  return page.evaluate(({ STEPS, EXCEPT, root }) => {
    const found: string[] = [];
    const walker = document.createTreeWalker(document.body, NodeFilter.SHOW_TEXT);
    const seen = new Set<Element>();
    let n: Node | null;
    while ((n = walker.nextNode())) {
      const text = (n.textContent || "").trim();
      const e = n.parentElement;
      if (!text || !e || seen.has(e) || e.closest("script, style, template") || e.closest(EXCEPT)) continue;
      const r = e.getBoundingClientRect();
      if (r.width === 0 || r.height === 0) continue;
      const s = getComputedStyle(e);
      if (s.visibility === "hidden") continue;
      seen.add(e);
      const step = parseFloat(s.fontSize) * 14 / root;                                   // in the default root's px
      if (STEPS.some((v: number) => Math.abs(v - step) < 0.07)) continue;
      found.push(`${Math.round(step * 100) / 100}px  ${e.tagName.toLowerCase()}.${(e.className && e.className.toString ? e.className.toString() : "").split(" ")[0]}  "${text.slice(0, 28)}"`);
    }
    return found;
  }, { STEPS, EXCEPT, root });
}

for (const [mode, cookie, root] of ROOTS) {
  test(`every piece of text is on a step of the scale, on every page (${mode})`, async ({ browser }) => {
    test.setTimeout(600_000);
    const context = await browser.newContext({ storageState: ".auth/viewer.json", viewport: { width: 1440, height: 1400 } });
    if (cookie) await context.addCookies([{ name: "text_size", value: cookie, url: "http://localhost" }]);
    const bad: Record<string, string[]> = {};
    for (const path of PAGES) {
      const page = await context.newPage();
      for (let attempt = 0; attempt < 4; attempt++) {                                      // the site's rate limit answers a burst with a bare page
        await page.goto(path, { waitUntil: "load" });
        if ((await page.locator("#navbar").count()) > 0) break;
        await page.waitForTimeout(3000 * (attempt + 1));
      }
      await page.waitForTimeout(2200);                                                    // the sidebar's boxes load after the page
      const found = await strays(page, root);
      if (found.length) bad[path] = [...new Set(found)].slice(0, 6);
      await page.close();
    }
    expect(bad, "text on no step of the scale").toEqual({});
    await context.close();
  });
}

test("the right sidebar's box titles are the left sidebar's small label, and the top bar is still 56px", async ({ browser }) => {
  const context = await browser.newContext({ storageState: ".auth/viewer.json", viewport: { width: 1440, height: 1400 } });
  const page = await context.newPage();
  for (const path of ["/all", "/@ui_alice", "/+ui_photography", "/post/15"]) {
    for (let attempt = 0; attempt < 4; attempt++) {
      await page.goto(path, { waitUntil: "load" });
      if ((await page.locator("#sidebar-left").count()) > 0) break;
      await page.waitForTimeout(3000 * (attempt + 1));
    }
    await page.waitForTimeout(2200);
    const m = await page.evaluate(() => {
      const look = (e: Element) => { const s = getComputedStyle(e); return [s.fontSize, s.fontWeight, s.textTransform, s.letterSpacing, s.color, s.fontFamily].join(" | "); };
      const left = Array.from(document.querySelectorAll("#sidebar-left .sidebar-head-title")).filter((e) => e.getBoundingClientRect().width > 0).map(look);
      const right = Array.from(document.querySelectorAll("#main-content-row > .sidebar:not(#sidebar-left) .sidebar-section > .title, #main-content-row > .sidebar:not(#sidebar-left) .sidebar-section > .title")).filter((e) => e.getBoundingClientRect().width > 0).map(look);
      return { left, right, bar: document.getElementById("navbar")!.getBoundingClientRect().height };
    });
    expect(m.left.length, path).toBeGreaterThanOrEqual(5);
    expect(m.right.length, `${path}: a box title`).toBeGreaterThanOrEqual(1);
    for (const r of m.right) expect(r, `${path}: a right-hand box title`).toBe(m.left[0]);
    expect(m.bar, `${path}: the top bar`).toBeCloseTo(56, 0);
  }
  await context.close();
});
