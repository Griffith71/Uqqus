import { test, expect, type Browser, type BrowserContext, type Page } from "@playwright/test";
import AxeBuilder from "@axe-core/playwright";
import { execSync } from "node:child_process";
import { mkdirSync } from "node:fs";
import { open } from "./pages";

// The count of page requests (CLAUDE.md "Traffic"; helpers/traffic.py, assets/js/infinite_scroll.js, templates/admin/traffic.html):
//   docker compose exec -T ruqqus env PYTHONPATH=. /opt/ruqqus/service/venv/bin/python scripts/ui/seed_traffic.py     first
//   npm run traffic
// Each browser says it is in a made-up country, so its counts can be told from anyone else's in the shared dev Redis, and has an
// ordinary user agent: a headless browser's says so and is never counted. It reads Redis through `docker exec`.
//  - a feed page opened is 1: scrolling it, its "new posts" check and a script's own fetch of a page add nothing, a reload adds 1
//  - the side panels' frames add nothing
//  - the page, at 390 and 1280px in both themes: no sideways scroll, nothing serious from axe, every number reachable

const AGENT = "Mozilla/5.0 (Macintosh; Intel Mac OS X 14_5) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.5 Safari/605.1.15";
const REDIS = process.env.REDIS_CONTAINER ?? "ruqqus-redis-1";
const DESKTOP = { viewport: { width: 1280, height: 900 } } as const;
const PHONE = { viewport: { width: 390, height: 844 }, isMobile: true, hasTouch: true } as const;
let country = 0;

function liveKey() {
  return `traffic:live:${Math.floor(Date.now() / 86400000)}:fixed`;
}

function freshCountry() {
  country += 1;
  const code = `Q${String.fromCharCode(64 + country)}`;  // QA, QB...: no real country
  execSync(`docker exec ${REDIS} redis-cli hdel ${liveKey()} c:${code}`);   // what the last run left
  return code;
}

function counted(code: string): number {
  const out = execSync(`docker exec ${REDIS} redis-cli hget ${liveKey()} c:${code}`).toString().trim();
  return Number(out) || 0;
}

async function settled(code: string, want: number) {
  await expect.poll(() => counted(code), { timeout: 5000 }).toBe(want);
}

async function context(browser: Browser, code: string, options: object = DESKTOP, storageState?: string): Promise<BrowserContext> {
  return browser.newContext({ ...options, userAgent: AGENT, extraHTTPHeaders: { "CF-IPCountry": code }, storageState });
}

const sideways = (page: Page) => page.evaluate(() => document.documentElement.scrollWidth - document.documentElement.clientWidth);

test.describe.configure({ mode: "serial" });

test("a feed opened is one request: scrolling, its check for new posts and a script's fetch add nothing, a reload adds one", async ({ browser }) => {
  const code = freshCountry();
  const ctx = await context(browser, code);
  await ctx.addInitScript(() => { (window as unknown as { FEED_POLL_MS: number }).FEED_POLL_MS = 500; });
  const page = await ctx.newPage();
  const feedFetches: string[] = [];
  page.on("request", (request) => {
    if (request.resourceType() === "fetch" && /\/all(\?|$)/.test(new URL(request.url()).pathname + new URL(request.url()).search)) feedFetches.push(request.headers()["x-background-fetch"] ?? "none");
  });
  await open(page, "/all");
  await settled(code, 1);

  await page.evaluate(() => window.scrollTo(0, document.body.scrollHeight));
  await page.waitForTimeout(800);
  await page.evaluate(() => window.scrollTo(0, 0));
  await page.waitForTimeout(2500);                       // several checks for new posts, at 500ms each
  console.log(`feed fetches seen: ${feedFetches.length}`);
  expect(feedFetches.length, "the feed's own check for new posts should have run").toBeGreaterThan(0);
  expect(new Set(feedFetches)).toEqual(new Set(["1"]));  // every one of them carries the marker
  await settled(code, 1);

  // a script fetching a page with no marker: the browser says it isn't a document
  await page.evaluate(() => fetch("/all").then((r) => r.text()));
  await page.waitForTimeout(300);
  await settled(code, 1);

  await page.reload();
  await settled(code, 2);
  await ctx.close();
});

test("a side panel's frame is not a page opened", async ({ browser }) => {
  const code = freshCountry();
  const ctx = await context(browser, code, DESKTOP, ".auth/viewer.json");
  const page = await ctx.newPage();
  await open(page, "/all");
  await settled(code, 1);
  await page.click('[data-side-panel="notifications"]');
  await page.waitForSelector("#side-panel iframe");
  const frame = page.locator("#side-panel iframe").first();
  await expect(frame).toHaveAttribute("src", /embed=1/);
  await page.waitForTimeout(1500);
  await settled(code, 1);
  await ctx.close();
});

test("a visitor's page gets no cookie from being counted, and the What's happening poll adds nothing", async ({ browser }) => {
  const code = freshCountry();
  const ctx = await context(browser, code);
  const page = await ctx.newPage();
  await open(page, "/help/privacy");
  await settled(code, 1);
  expect((await ctx.cookies()).map((c) => c.name)).not.toContain("session_ruqqus");
  await page.evaluate(() => fetch("/inpage/happening").then((r) => r.text()));
  await page.waitForTimeout(300);
  await settled(code, 1);
  await ctx.close();
});

test.describe("the admin page", () => {
  let state: Awaited<ReturnType<BrowserContext["storageState"]>>;

  test.beforeAll(async ({ browser }) => {
    const first = await browser.newContext(DESKTOP);
    const form = await first.newPage();
    await open(form, "/login");
    await form.fill("#username", "ui_traffic_admin");
    await form.fill("#password", "ui-Password-1");
    await form.click("#login_button");
    await form.waitForURL((url) => !url.pathname.startsWith("/login"), { timeout: 15_000 });
    state = await first.storageState();
    await first.close();
  });

  for (const [width, options] of [["1280", DESKTOP], ["390", PHONE]] as const) {
    for (const scheme of ["light", "dark"] as const) {
      test(`${width} ${scheme}`, async ({ browser }) => {
        mkdirSync("shots/traffic", { recursive: true });
        const ctx = await browser.newContext({ ...options, colorScheme: scheme, storageState: state });
        const page = await ctx.newPage();
        await open(page, "/admin/traffic?days=90");
        await expect(page.locator("h1")).toHaveText("Traffic");
        await expect(page.getByText("These are requests, not people.")).toBeVisible();
        await expect(page.locator(".traffic-bar-col")).toHaveCount(90);
        expect(await sideways(page), "sideways scroll").toBeLessThanOrEqual(0);

        // the rows' numbers fit on the screen with the longest host the column allows
        const off = await page.evaluate(() => [...document.querySelectorAll("#admin-traffic *")].filter((el) => {
          const box = el.getBoundingClientRect();
          return box.width > 0 && box.right > document.documentElement.clientWidth + 0.5;
        }).length);
        expect(off, "boxes past the screen's edge").toBe(0);

        // the tiles, then a bar on a real day is at least visible and a day with nothing has none
        const bars = await page.locator(".traffic-bar").evaluateAll((els) => els.map((el) => el.getBoundingClientRect().height));
        expect(Math.max(...bars)).toBeGreaterThan(100);
        expect(bars.filter((h) => h === 0).length).toBeGreaterThanOrEqual(2);

        await page.locator(".traffic-days summary").click();
        await expect(page.locator(".traffic-days tbody tr")).toHaveCount(90);

        // a long table says it stops and its last row holds the rest
        const last = (heading: string) => page.locator(".traffic-rank", { has: page.getByRole("heading", { name: heading }) }).locator("tbody tr").last().locator("th");
        await expect(last("Referrers")).toHaveText("Everything else");
        await expect(last("Countries")).toHaveText(/^\d+ more countr(y|ies)$/);

        await page.screenshot({ path: `shots/traffic/${width}-${scheme}.png`, fullPage: true });
        const axe = await new AxeBuilder({ page }).include("#admin-traffic").withTags(["wcag2a", "wcag2aa"]).analyze();
        const bad = axe.violations.filter((v) => v.impact === "serious" || v.impact === "critical");
        expect(bad.map((v) => `${v.id}: ${v.nodes.map((n) => n.target).join(", ")}`)).toEqual([]);
        await ctx.close();
      });
    }
  }

  // the narrowest phone, the largest text, seven-digit days: the table scrolls in its own box and the page doesn't
  for (const direction of ["ltr", "rtl"] as const) {
    test(`320px at Extra large text, ${direction}: Day by day doesn't scroll the page`, async ({ browser }) => {
      const ctx = await browser.newContext({ viewport: { width: 320, height: 700 }, isMobile: true, hasTouch: true, storageState: state });
      await ctx.addCookies([{ name: "text_size", value: "xlarge", domain: "localhost", path: "/" },
        ...(direction === "rtl" ? [{ name: "ui_lang", value: "ar", domain: "localhost", path: "/" }] : [])]);
      const page = await ctx.newPage();
      await open(page, "/admin/traffic?days=90");
      expect(await page.locator("html").getAttribute("dir") ?? "ltr").toBe(direction);
      await page.locator(".traffic-days summary").click();
      await page.locator(".traffic-days td.traffic-num").evaluateAll((cells) => cells.forEach((cell) => { cell.textContent = "1,234,567"; }));
      expect(await sideways(page), "sideways scroll").toBeLessThanOrEqual(0);
      const scroller = page.locator(".traffic-scroll");
      const fits = await scroller.evaluate((el) => ({ scroll: el.scrollWidth, client: el.clientWidth }));
      console.log(`320px ${direction}: table ${fits.scroll}px in a box of ${fits.client}px`);
      expect(fits.scroll).toBeGreaterThanOrEqual(fits.client);
      // the day is a heading, on one line: the numbers don't wrap, so a day that may wrap is squeezed to a letter a line
      const day = await page.locator(".traffic-days tbody tr:first-child th").evaluate((el) => {
        const style = getComputedStyle(el);
        return { height: el.getBoundingClientRect().height - parseFloat(style.paddingTop) - parseFloat(style.paddingBottom) - 1, line: parseFloat(style.lineHeight) };
      });
      expect(day.height, "the day's own line").toBeLessThan(day.line * 1.5);

      // every number can be reached by keyboard: the box takes focus and says so, and at its far end the last column is on the screen
      await page.locator(".traffic-days summary").focus();
      await page.keyboard.press("Tab");
      const focused = await page.evaluate(() => {
        const el = document.activeElement as HTMLElement;
        const style = getComputedStyle(el);
        return { name: el.className, ring: style.outlineStyle !== "none" && parseFloat(style.outlineWidth) > 0 || style.boxShadow !== "none" };
      });
      console.log(`focused: ${JSON.stringify(focused)}`);
      expect(focused).toEqual({ name: "traffic-scroll", ring: true });
      await scroller.evaluate((el) => { el.scrollLeft = (document.documentElement.dir === "rtl" ? -1 : 1) * el.scrollWidth; });
      const box = await page.locator(".traffic-days tbody tr:first-child td:last-child").evaluate((el) => {
        const { left, right } = el.getBoundingClientRect();
        return { left, right, width: document.documentElement.clientWidth };
      });
      expect(box.left).toBeGreaterThanOrEqual(-0.5);
      expect(box.right).toBeLessThanOrEqual(box.width + 0.5);
      await page.screenshot({ path: `shots/traffic/320-xlarge-${direction}.png`, fullPage: true });
      const axe = await new AxeBuilder({ page }).include(".traffic-days").withTags(["wcag2a", "wcag2aa"]).analyze();
      expect(axe.violations.filter((v) => v.impact === "serious" || v.impact === "critical").map((v) => v.id)).toEqual([]);
      await ctx.close();
    });
  }

  test("the window changes with the link and says which it is", async ({ browser }) => {
    const ctx = await browser.newContext({ ...DESKTOP, storageState: state });
    const page = await ctx.newPage();
    await open(page, "/admin/traffic");
    await expect(page.locator('.traffic-windows [aria-current="page"]')).toHaveText("28 days");
    await page.click('.traffic-windows a:has-text("7 days")');
    await expect(page.locator('.traffic-windows [aria-current="page"]')).toHaveText("7 days");
    await expect(page.locator(".traffic-bar-col")).toHaveCount(7);
    await ctx.close();
  });
});
