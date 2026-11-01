import { test, expect, type BrowserContext, type Page } from "@playwright/test";
import AxeBuilder from "@axe-core/playwright";
import { existsSync } from "node:fs";
import { open } from "./pages";

// The left sidebar (CLAUDE.md "Left sidebar"; the owner, 2026-10-30): measured in a real browser, because CSS that says 40px can still render 39.6 and a ring
// "centred" by auto margins is only centred if the pixels say so.
//   npm run sidebar
// Needs the fixtures (scripts/seed_ui_fixtures.py: `ui_viewer`, five guilds) and the long lists (`scripts/ui/seed_sidebar.py`: `ui_sidebar`, 40 guilds).
// What it holds the site to: rows are 40px (44 under a finger) and 2px apart, a heading is 28px with 8px to its first row and a section is followed by
// 16px, every label starts at one x; a list stops at five rows and ends with one "See all" (only over five); the selected feed is a light shade of the
// site's blue with the blue bar kept and nothing changes on hover; a filter that is narrowing the feed has the same wash and a 34px ring in the site's blue
// round its symbol, centred, with nothing moving when it comes (the ring is a graphic: 2.5:1 on the page in light, the site colour's own exception, and 3:1 in dark); the ring is only on the three feeds, only for what is applied (a tick
// in an open panel changes nothing until Apply), on the rail too, and drawn in forced colours; the keyboard ring is inside the row; Compact, Extra large
// text and right to left lose nothing; and axe finds nothing in the sidebar.

const VIEWER = { storageState: ".auth/viewer.json" };
const MANY = { storageState: ".auth/sidebar.json" };
const FILTERS = ["Regional", "Language", "Categorical", "Post type", "Advanced filter"];

test.beforeAll(async ({ browser }) => {
  if (existsSync(".auth/sidebar.json")) return;
  const context = await browser.newContext();
  const page = await context.newPage();
  await page.goto("/login");
  await page.fill("#username", "ui_sidebar");
  await page.fill("#password", "ui-Password-1");
  await Promise.all([page.waitForURL((url) => !url.pathname.startsWith("/login")), page.click("#login_button")]);
  await context.storageState({ path: ".auth/sidebar.json" });
  await context.close();
});

// opens a page that has the sidebar, and asks again when the site answered a burst of reloads with its rate-limit page (no sidebar): this file loads
// the feed a great many times in a minute
async function openFeed(page: Page, path = "/all") {
  for (let attempt = 0; attempt < 6; attempt++) {
    await open(page, path);
    if ((await page.locator("#sidebar-left").count()) > 0) return;
    await page.waitForTimeout(2000 * (attempt + 1));
  }
  throw new Error(`${path} came back without the sidebar (the site's rate limit?)`);
}

// The filters are set through the context's own request API, never through page navigations: a page's sub-requests would race the cookie.
async function choose(context: BrowserContext, page: Page, which: string[] = ["regional", "language", "categorical", "post_type", "advanced"]) {
  // the page with its filter lists: a run in parallel can be answered with the site's rate-limit page (no sidebar), so wait and ask again
  let ready = false;
  for (let attempt = 0; attempt < 6 && !ready; attempt++) {
    await openFeed(page);
    ready = (await page.locator("#sidebar-regional-options li[data-value]").count()) > 0;
    if (!ready) await page.waitForTimeout(2000 * (attempt + 1));
  }
  expect(ready, "the feed came back with the sidebar's filter lists").toBe(true);
  const first = (id: string) => page.evaluate((i) => (document.querySelector(`#${i} li[data-value]:not([data-filter-heading])`) as HTMLElement).dataset.value, id);
  const r = context.request;
  if (which.includes("regional")) await r.get(`/regional?region=${encodeURIComponent((await first("sidebar-regional-options"))!)}`);
  if (which.includes("language")) await r.get(`/language?codes=${encodeURIComponent((await first("sidebar-language-options"))!)}`);
  if (which.includes("categorical")) await r.get(`/categorical?cats=-27`);
  if (which.includes("post_type")) await r.get(`/post_type?types=poll`);
  if (which.includes("advanced")) await r.get(`/feed_filter?min_votes=3`);
}

async function clear(context: BrowserContext) {
  const r = context.request;
  await r.get("/regional?region="); await r.get("/language?codes="); await r.get("/categorical?cats="); await r.get("/post_type?types="); await r.get("/feed_filter?clear=1");
}

// every visible row of the open sidebar, with the box it highlights and where its label starts
const rows = (page: Page) => page.evaluate(() => {
  const r = (e: Element) => { const b = e.getBoundingClientRect(); return { x: b.x, y: b.y, w: b.width, h: b.height }; };
  return Array.from(document.querySelectorAll("#sidebar-left .guild-recommendations-item > a")).filter((e) => e.getBoundingClientRect().width > 0 && !e.closest(".feed-defaults, .feed-defaults-rail"))
    .map((e) => { const label = e.querySelector(".sidebar-row-label"); const icon = e.querySelector(".sidebar-icon");
      return { text: (e.textContent || "").trim().replace(/\s+/g, " ").slice(0, 40), ...r(e), labelX: label ? label.getBoundingClientRect().x : null, iconY: icon ? icon.getBoundingClientRect().y + icon.getBoundingClientRect().height / 2 : null,
        filtering: e.classList.contains("is-filtering"), current: e.getAttribute("aria-current") === "page" }; });
});

const heads = (page: Page) => page.evaluate(() => Array.from(document.querySelectorAll("#sidebar-left .sidebar-head")).filter((e) => e.getBoundingClientRect().width > 0)
  .map((e) => { const b = e.getBoundingClientRect(); return { text: (e.textContent || "").trim().replace(/\s+/g, " ").slice(0, 20), y: b.y, h: b.height, bottom: b.bottom }; }));

const contrast = (a: number[], b: number[]) => {
  const lum = (c: number[]) => { const l = c.map((v) => { const s = v / 255; return s <= 0.03928 ? s / 12.92 : ((s + 0.055) / 1.055) ** 2.4; }); return 0.2126 * l[0] + 0.7152 * l[1] + 0.0722 * l[2]; };
  const [hi, lo] = [lum(a), lum(b)].sort((x, y) => y - x);
  return (hi + 0.05) / (lo + 0.05);
};

// pixels of a clip, read back in the page (the PNG is decoded on a canvas, as light.spec.ts does)
async function pixels(page: Page, clip: { x: number; y: number; width: number; height: number }) {
  const png = (await page.screenshot({ clip })).toString("base64");
  return page.evaluate(async (png) => {
    const img = new Image(); img.src = `data:image/png;base64,${png}`; await img.decode();
    const canvas = document.createElement("canvas"); canvas.width = img.width; canvas.height = img.height;
    const ctx = canvas.getContext("2d")!; ctx.drawImage(img, 0, 0);
    return { w: img.width, h: img.height, data: Array.from(ctx.getImageData(0, 0, img.width, img.height).data) };
  }, png);
}

// the ring of a row, found in the pixels: its outer diameter and where its centre is against the icon box's centre, both in CSS px
async function ringOf(page: Page, text: string, scale: number) {
  const icon = page.locator("#sidebar-left .guild-recommendations-item > a.is-filtering:visible", { hasText: text }).first().locator(".sidebar-icon");
  await icon.scrollIntoViewIfNeeded();
  const box = (await icon.boundingBox())!;
  const colour = await icon.evaluate((e) => getComputedStyle(e, "::after").borderTopColor);
  const want = (colour.match(/[\d.]+/g) || []).slice(0, 3).map(Number);
  // (3px each side across, 8px up and down: the ring is 34px in a 35 x 28 box, and the label starts 7px past the box, in the ring's own blue in dark)
  const padX = 3, padY = 8;
  const clip = { x: box.x - padX, y: box.y - padY, width: box.width + 2 * padX, height: box.height + 2 * padY };
  const { w, h, data } = await pixels(page, clip);
  let minX = w, maxX = -1, minY = h, maxY = -1;
  for (let y = 0; y < h; y++) for (let x = 0; x < w; x++) {
    const i = (y * w + x) * 4;
    if (Math.abs(data[i] - want[0]) + Math.abs(data[i + 1] - want[1]) + Math.abs(data[i + 2] - want[2]) < 36 && data[i + 3] > 200) { minX = Math.min(minX, x); maxX = Math.max(maxX, x); minY = Math.min(minY, y); maxY = Math.max(maxY, y); }
  }
  return { want, diameterX: (maxX - minX + 1) / scale, diameterY: (maxY - minY + 1) / scale,
           dx: (minX + maxX + 1) / 2 / scale - (padX + box.width / 2), dy: (minY + maxY + 1) / 2 / scale - (padY + box.height / 2), box };
}

for (const theme of ["light", "dark"] as const) {
  test(`${theme}: rows are 40px and 2px apart, headings 28px with 8px to the first row, sections 16px apart, and every label starts at one x`, async ({ browser }) => {
    const context = await browser.newContext({ ...MANY, viewport: { width: 1440, height: 1700 }, colorScheme: theme });
    const page = await context.newPage();
    await openFeed(page);
    const rs = await rows(page);
    expect(rs.length).toBeGreaterThan(15);
    for (const r of rs) expect(r.h, r.text).toBeCloseTo(40, 0);
    const hs = await heads(page);
    expect(hs.length).toBeGreaterThanOrEqual(6);
    for (const h of hs) expect(h.h, h.text).toBeCloseTo(28, 0);
    // the first row after each heading is 8px under it; two rows of one list are 2px apart; a section's last row is 16px above the next heading
    const subheads = await page.evaluate(() => Array.from(document.querySelectorAll("#sidebar-left .sidebar-subhead")).filter((e) => e.getBoundingClientRect().width > 0).map((e) => { const b = e.getBoundingClientRect(); return { y: b.y, bottom: b.bottom }; }));
    for (const h of hs) {
      const under = subheads.find((s) => s.y > h.bottom - 1 && s.y - h.bottom < 12);
      const first = rs.find((r) => r.y > (under ? under.bottom : h.bottom) - 1)!;
      expect((under ? under.y : first.y) - h.bottom, `${h.text}: heading to what follows it`).toBeCloseTo(8, 0);
      if (under) expect(first.y - under.bottom, `${h.text}: sub-heading to its first row`).toBeCloseTo(4, 0);
    }
    const consecutive = rs.slice(1).map((r, i) => ({ gap: r.y - (rs[i].y + rs[i].h), a: rs[i].text, b: r.text })).filter((g) => g.gap < 12);
    for (const g of consecutive) expect(g.gap, `${g.a} / ${g.b}`).toBeCloseTo(2, 0);
    // a section's last item (the Save button counts: it is the last thing under the filters) is 16px above the next heading
    const itemBottoms = await page.evaluate(() => Array.from(document.querySelectorAll("#sidebar-left li.guild-recommendations-item")).filter((e) => e.getBoundingClientRect().width > 0).map((e) => e.getBoundingClientRect().bottom));
    for (const h of hs.slice(1)) {
      const above = Math.max(...itemBottoms.filter((b) => b <= h.y + 0.5));
      expect(h.y - above, `last item to the heading ${h.text}`).toBeCloseTo(16, 0);
    }
    // one x for every label: the feeds, the filters, the guilds, the curations, the Spaces rows
    const xs = new Set(rs.filter((r) => r.labelX !== null).map((r) => Math.round(r.labelX! * 2) / 2));
    expect([...xs], "label x").toHaveLength(1);
    await context.close();
  });
}

test("a long list stops at five rows and ends with one See all that leads to the whole list", async ({ browser }) => {
  const context = await browser.newContext({ ...MANY, viewport: { width: 1440, height: 1700 } });
  const page = await context.newPage();
  await openFeed(page);
  const sections = await page.evaluate(() => {
    const out: Record<string, { rows: string[]; seeAll: string[] }> = {};
    for (const block of Array.from(document.querySelectorAll("#sidebar-left .sidebar-block"))) {
      const head = block.querySelector(".sidebar-head-title");
      if (!head || block.getBoundingClientRect().width === 0) continue;
      const items = Array.from(block.querySelectorAll(":scope .guild-recommendations-list:not(.sidebar-collapsed-visible) .guild-recommendations-item > a")).filter((e) => e.getBoundingClientRect().width > 0);
      out[(head.textContent || "").trim()] = { rows: items.filter((e) => !e.closest(".sidebar-see-all")).map((e) => (e.textContent || "").trim().replace(/\s+/g, " ")),
        seeAll: items.filter((e) => e.closest(".sidebar-see-all")).map((e) => e.getAttribute("href") || "") };
    }
    return out;
  });
  expect(sections["My guilds"].rows).toHaveLength(5);
  expect(sections["My guilds"].seeAll).toEqual(["/mine/guilds"]);
  expect(sections["Guildmaster of"].rows).toHaveLength(5);
  expect(sections["Guildmaster of"].seeAll).toEqual(["/mine/guilds"]);
  expect(sections["My Curations"].rows.length).toBe(2 + 5);          // two owned, five of the eight followed
  expect(sections["My Curations"].seeAll).toEqual(["/curations"]);  // one for both lists
  // in name order whatever order the guilds were joined in
  const names = sections["My guilds"].rows.map((t) => t.replace(/^\+/, "").toLowerCase());
  expect(names).toEqual([...names].sort());
  // the same on the folded rail: five avatars and one ellipsis tile, named
  await context.addCookies([{ name: "sidebar_pref", value: "collapsed", url: "http://localhost" }]);
  await openFeed(page);
  const rail = await page.evaluate(() => Array.from(document.querySelectorAll("#sidebar-left .sidebar-see-all > a")).filter((e) => e.getBoundingClientRect().width > 0).map((e) => e.getAttribute("aria-label")));
  expect(rail.length).toBe(3);
  for (const label of rail) expect(label).toMatch(/ · /);
  await context.close();
});

test("a list of five or fewer has no See all, and the fixtures' viewer sees all five of their guilds", async ({ browser }) => {
  const context = await browser.newContext({ ...VIEWER, viewport: { width: 1440, height: 1000 } });
  const page = await context.newPage();
  await openFeed(page);
  expect(await page.locator("#sidebar-left .sidebar-see-all:visible").count()).toBe(0);
  await context.close();
});

test("long names are cut with an ellipsis on one line, with the whole name as their title, and nothing scrolls sideways", async ({ browser }) => {
  const context = await browser.newContext({ ...MANY, viewport: { width: 1440, height: 1700 } });
  const page = await context.newPage();
  await openFeed(page);
  const m = await page.evaluate(() => {
    const labels = Array.from(document.querySelectorAll("#sidebar-left .sidebar-row-label")).filter((e) => e.getBoundingClientRect().width > 0) as HTMLElement[];
    const sb = document.getElementById("sidebar-left")!;
    return { sideways: sb.scrollWidth - sb.clientWidth, labels: labels.map((e) => ({ text: (e.textContent || "").trim().slice(0, 30), oneLine: e.getBoundingClientRect().height < 30, cut: e.scrollWidth > e.clientWidth + 0.5, title: e.title })) };
  });
  expect(m.sideways).toBeLessThanOrEqual(0.5);
  for (const l of m.labels) expect(l.oneLine, l.text).toBe(true);
  const cut = m.labels.filter((l) => l.cut);
  expect(cut.length, "a long curation and a long guild name are cut").toBeGreaterThanOrEqual(1);
  for (const l of cut) expect(l.title.length, l.text).toBeGreaterThan(0);
  await context.close();
});

for (const theme of ["light", "dark"] as const) {
  test(`${theme}: the selected feed is a light blue wash with the blue bar kept, and hovering it changes nothing`, async ({ browser }) => {
    const context = await browser.newContext({ ...VIEWER, viewport: { width: 1440, height: 1000 }, colorScheme: theme });
    const page = await context.newPage();
    await openFeed(page);
    const row = page.locator('#sidebar-left .guild-recommendations-item > a[aria-current="page"]:visible');
    expect(await row.count()).toBe(1);
    const read = () => row.evaluate((e) => { const bar = getComputedStyle(e, "::before"); return { bg: getComputedStyle(e).backgroundColor, radius: getComputedStyle(e).borderTopLeftRadius, barW: bar.width, barBg: bar.backgroundColor, barStart: bar.insetInlineStart }; });
    const before = await read();
    const channels = (before.bg.match(/[\d.]+/g) || []).map(Number);
    expect(channels.slice(0, 3), `the site's blue ${before.bg}`).toEqual([35, 169, 242]);                             // #23a9f2 at a tint, never grey
    expect(channels[3]).toBeCloseTo(theme === "light" ? 0.12 : 0.18, 2);
    expect(before.radius).toBe("8px");
    expect(before.barW).toBe("3px");                                                                                 // the bar the owner kept
    expect(before.barStart).toBe("0px");
    const bar = (before.barBg.match(/[\d.]+/g) || []).map(Number);
    expect(bar[2], `the bar is blue: ${before.barBg}`).toBeGreaterThan(bar[0] + 40);
    await row.hover();
    await page.waitForTimeout(300);
    expect(await read()).toEqual(before);
    // a feed that is not selected still gets the hover tint
    const other = page.locator("#sidebar-left .guild-recommendations-item > a:visible", { hasText: "For You" }).first();
    await other.hover();
    await page.waitForTimeout(300);
    expect(await other.evaluate((e) => getComputedStyle(e).backgroundColor)).not.toBe("rgba(0, 0, 0, 0)");
    await context.close();
  });

  test(`${theme}: a filter that is narrowing the feed has the wash and a centred 34px ring that reads on the page and on the wash, and nothing moves`, async ({ browser }) => {
    const scale = 4;
    const context = await browser.newContext({ ...MANY, viewport: { width: 1440, height: 1700 }, colorScheme: theme, deviceScaleFactor: scale });
    const page = await context.newPage();
    await clear(context);
    await openFeed(page);
    const plain = await rows(page);
    try {
      await choose(context, page);
      await openFeed(page);
      const filtered = await rows(page);
      expect(filtered.filter((r) => r.filtering).map((r) => FILTERS.find((f) => r.text.startsWith(f)))).toEqual(FILTERS);
      // nothing moved: every row is where it was and as tall
      expect(filtered.map((r) => [Math.round(r.y * 10), Math.round(r.h * 10), Math.round(r.x * 10)])).toEqual(plain.map((r) => [Math.round(r.y * 10), Math.round(r.h * 10), Math.round(r.x * 10)]));
      for (const name of FILTERS) {
        const ring = await ringOf(page, name, scale);
        expect(ring.diameterX, `${name}: width`).toBeCloseTo(34, 0);
        expect(ring.diameterY, `${name}: height`).toBeCloseTo(34, 0);
        expect(Math.abs(ring.dx), `${name}: centred across`).toBeLessThanOrEqual(0.6);
        expect(Math.abs(ring.dy), `${name}: centred down`).toBeLessThanOrEqual(0.6);
      }
      // the ring is the site's blue, and shows on the page and on the wash, from real pixels: the gap between two rows is the page, the right edge of a
      // filtering row is the wash. A graphic: 2.6:1 on white is the site colour's own exception, so the floors are 2.5 / 2.2 in light and 3 in dark
      const regional = (await filtered.find((r) => r.text.startsWith("Regional")))!;
      const ring = await ringOf(page, "Regional", scale);
      const wash = await pixels(page, { x: regional.x + regional.w - 6, y: regional.y + regional.h / 2, width: 1, height: 1 });
      const pageBg = await pixels(page, { x: regional.x + regional.w / 2, y: regional.y - 1, width: 1, height: 1 });
      const px = (p: { data: number[] }) => p.data.slice(0, 3);
      expect(ring.want, "the ring is the site's blue").toEqual([35, 169, 242]);
      expect(contrast(ring.want, px(wash)), "ring on the wash").toBeGreaterThanOrEqual(theme === "light" ? 2.2 : 3);
      expect(contrast(ring.want, px(pageBg)), "ring on the page").toBeGreaterThanOrEqual(theme === "light" ? 2.5 : 3);
      expect(px(wash)).not.toEqual(px(pageBg));                                                                  // the wash is really there
    } finally { await clear(context); await context.close(); }
  });
}

for (const theme of ["light", "dark"] as const) {
  test(`${theme}: a highlighted row's label turns blue (selected feed and narrowing filters), reads on the wash, and a plain or open row keeps its colour`, async ({ browser }) => {
    const context = await browser.newContext({ ...MANY, viewport: { width: 1440, height: 1700 }, colorScheme: theme });
    const page = await context.newPage();
    await clear(context);
    try {
      await choose(context, page, ["language", "post_type", "advanced"]);
      await openFeed(page);
      const m = await page.evaluate(() => {
        const probe = (v: string) => { const e = document.createElement("span"); e.style.color = v; document.body.appendChild(e); const c = getComputedStyle(e).color; e.remove(); return c; };
        const rows = Array.from(document.querySelectorAll("#sidebar-left .guild-recommendations-item > a")).filter((e) => e.getBoundingClientRect().width > 0 && e.querySelector(".sidebar-row-label") && !e.closest(".sidebar-see-all"));
        const label = (a: Element) => { const l = a.querySelector(".sidebar-row-label")!; const s = getComputedStyle(l); const b = a.getBoundingClientRect(); return { text: (l.textContent || "").trim().slice(0, 14), colour: s.color, weight: s.fontWeight, wash: getComputedStyle(a).backgroundColor, x: b.x + b.width - 6, y: b.y + b.height / 2 }; };
        return { link: probe("var(--link)"), text: label(rows.find((a) => a.textContent!.includes("For You"))!).colour, selected: label(rows.find((a) => a.getAttribute("aria-current") === "page")!),
                 filtering: rows.filter((a) => a.classList.contains("is-filtering")).map(label), plain: rows.filter((a) => !a.classList.contains("is-filtering") && a.getAttribute("aria-current") !== "page").slice(0, 6).map(label) };
      });
      expect(m.selected.colour).toBe(m.link);
      expect(m.selected.weight).toBe("600");
      expect(m.filtering).toHaveLength(3);
      for (const f of m.filtering) { expect(f.colour, f.text).toBe(m.link); expect(f.weight, `${f.text} keeps its weight`).toBe("400"); }
      for (const p of m.plain) expect(p.colour, p.text).toBe(m.text);
      expect(m.link).not.toBe(m.text);
      // it reads on the wash it sits on, from real pixels
      const wash = await pixels(page, { x: m.selected.x, y: m.selected.y, width: 1, height: 1 });
      const rgb = (c: string) => (c.match(/[\d.]+/g) || []).slice(0, 3).map(Number);
      expect(contrast(rgb(m.selected.colour), wash.data.slice(0, 3))).toBeGreaterThanOrEqual(4.5);
      // a filter whose panel is open is plain, and its label is the plain colour
      await page.locator("#sidebar-left a.is-filtering[aria-expanded]:visible").first().click();
      await page.mouse.move(700, 600);
      await page.waitForTimeout(500);
      const open = await page.evaluate(() => { const a = document.querySelector("#sidebar-left a.is-filtering[aria-expanded=\"true\"]")!; return getComputedStyle(a.querySelector(".sidebar-row-label")!).color; });
      expect(open).toBe(m.text);
    } finally { await clear(context); await context.close(); }
  });
}

test("the ring is only on the three feeds, and a tick in an open panel changes nothing until Apply", async ({ browser }) => {
  const context = await browser.newContext({ ...VIEWER, viewport: { width: 1440, height: 1000 } });
  const page = await context.newPage();
  await clear(context);
  try {
    await choose(context, page, ["regional", "post_type"]);
    for (const path of ["/all", "/for_you", "/following"]) { await open(page, path); expect(await page.locator("#sidebar-left .is-filtering:visible").count(), path).toBe(2); }
    // not narrowed there, so not marked there
    for (const path of ["/browse", "/+ui_photography", "/@ui_alice", "/spaces", "/search?q=cats"]) { await open(page, path); expect(await page.locator("#sidebar-left .is-filtering").count(), path).toBe(0); }
    // the open row is plain while its panel is open; the ticks are the state
    await clear(context);
    await openFeed(page);
    expect(await page.locator("#sidebar-left .is-filtering").count()).toBe(0);
    await page.locator("#sidebar-left a[data-target='#sidebar-filter-language']").click();
    await page.locator("#sidebar-language-options li[data-value]").nth(1).click();
    await page.waitForTimeout(400);
    expect(await page.locator("#sidebar-left .is-filtering").count(), "ticked, not applied").toBe(0);
    await page.locator("#sidebar-filter-language button", { hasText: "Apply" }).click();
    await page.waitForURL(/\/(all|for_you|following)/);
    await page.waitForLoadState("load");
    expect(await page.locator("#sidebar-left a.is-filtering:visible").count(), "applied").toBe(1);
    // opening the panel of a narrowing filter drops the wash and the ring until it is closed
    const row = page.locator("#sidebar-left a.is-filtering[aria-expanded]:visible");
    expect(await row.evaluate((e) => getComputedStyle(e).backgroundColor)).not.toBe("rgba(0, 0, 0, 0)");
    await row.click();
    await page.mouse.move(700, 500);
    await page.waitForTimeout(500);
    expect(await row.evaluate((e) => [getComputedStyle(e).backgroundColor, getComputedStyle(e.querySelector(".sidebar-icon")!, "::after").content])).toEqual(["rgba(0, 0, 0, 0)", "none"]);
  } finally { await clear(context); await context.close(); }
});

test("the folded rail has the same wash and ring, named for a screen reader, and its icons stay on the rail's centre line", async ({ browser }) => {
  const scale = 4;
  const context = await browser.newContext({ ...VIEWER, viewport: { width: 1440, height: 1000 }, deviceScaleFactor: scale });
  await context.addCookies([{ name: "sidebar_pref", value: "collapsed", url: "http://localhost" }]);
  const page = await context.newPage();
  await clear(context);
  try {
    await choose(context, page, ["regional", "language", "advanced"]);
    await openFeed(page);
    const marked = await page.evaluate(() => Array.from(document.querySelectorAll("#sidebar-left a.is-filtering")).filter((e) => e.getBoundingClientRect().width > 0).map((e) => ({ label: e.getAttribute("aria-label"), title: e.getAttribute("title"), h: e.getBoundingClientRect().height })));
    expect(marked.map((m) => (m.label || "").split(" · ")[0])).toEqual(["Regional", "Language", "Advanced filter"]);
    for (const m of marked) { expect(m.label).toBe(m.title); expect(m.label).toMatch(/ · \d+ (selected|set)$/); expect(m.h).toBeCloseTo(40, 0); }
    const rail = await page.evaluate(() => { const sb = document.getElementById("sidebar-left")!.getBoundingClientRect(); return { w: sb.width, centres: Array.from(document.querySelectorAll("#sidebar-left .sidebar-icon")).filter((e) => e.getBoundingClientRect().width > 0).map((e) => { const b = e.getBoundingClientRect(); return b.x + b.width / 2 - sb.x; }) }; });
    expect(rail.w).toBeCloseTo(65, 0);
    for (const c of rail.centres) expect(c, "icon centre vs the rail's").toBeCloseTo(rail.centres[0], 0);
    const ring = await ringOf(page, "", scale);
    expect(Math.abs(ring.dx)).toBeLessThanOrEqual(0.6);
    expect(Math.abs(ring.dy)).toBeLessThanOrEqual(0.6);
  } finally { await clear(context); await context.close(); }
});

test("forced colours: the ring is drawn in Highlight, the selected feed keeps its outline", async ({ browser }) => {
  const context = await browser.newContext({ ...VIEWER, viewport: { width: 1440, height: 1000 }, forcedColors: "active" });
  const page = await context.newPage();
  await clear(context);
  try {
    await choose(context, page, ["regional"]);
    await openFeed(page);
    const m = await page.evaluate(() => {
      const probe = document.createElement("span"); probe.style.color = "Highlight"; document.body.appendChild(probe);
      const highlight = getComputedStyle(probe).color; probe.remove();
      const ring = getComputedStyle(document.querySelector("#sidebar-left a.is-filtering:not(.x) .sidebar-icon")!, "::after").borderTopColor;
      const selected = document.querySelector('#sidebar-left a[aria-current="page"]') as HTMLElement;
      return { highlight, ring, outline: getComputedStyle(selected).outlineStyle, outlineColour: getComputedStyle(selected).outlineColor };
    });
    expect(m.ring).toBe(m.highlight);
    expect(m.outline).toBe("solid");
  } finally { await clear(context); await context.close(); }
});

test("under a finger a row is 44px with its icon centred in it, and the keyboard's ring sits inside the row", async ({ browser }) => {
  const touch = await browser.newContext({ ...VIEWER, viewport: { width: 1100, height: 900 }, hasTouch: true, isMobile: true });
  const tp = await touch.newPage();
  await openFeed(tp);
  const rs = await rows(tp);
  expect(rs.length).toBeGreaterThan(5);
  for (const r of rs) { expect(r.h, r.text).toBeCloseTo(44, 0); if (r.iconY !== null) expect(r.iconY - (r.y + r.h / 2), `${r.text}: icon centre`).toBeCloseTo(0, 0); }
  await touch.close();
  const context = await browser.newContext({ ...VIEWER, viewport: { width: 1440, height: 1000 } });
  const page = await context.newPage();
  await openFeed(page);
  await page.keyboard.press("Tab");
  for (let i = 0; i < 60; i++) { const onRow = await page.evaluate(() => !!document.activeElement?.matches("#sidebar-left .guild-recommendations-item > a")); if (onRow) break; await page.keyboard.press("Tab"); }
  const focus = await page.evaluate(() => { const e = document.activeElement as HTMLElement; const s = getComputedStyle(e); return { inside: !!e.closest("#sidebar-left"), style: s.outlineStyle, offset: s.outlineOffset, width: s.outlineWidth }; });
  expect(focus).toEqual({ inside: true, style: "solid", offset: "-2px", width: "2px" });
  await context.close();
});

test("Compact keeps the rows on the ladder and Extra large text lets the rows grow without clipping the ring", async ({ browser }) => {
  const compact = await browser.newContext({ ...MANY, viewport: { width: 1440, height: 1700 } });
  await compact.addCookies([{ name: "density", value: "compact", url: "http://localhost" }]);
  const cp = await compact.newPage();
  await openFeed(cp);
  const crs = await rows(cp);
  for (const r of crs) expect(r.h, `${r.text} (Compact)`).toBeGreaterThanOrEqual(40 - 0.5);
  await compact.close();
  const big = await browser.newContext({ ...MANY, viewport: { width: 1440, height: 1700 } });
  await big.addCookies([{ name: "text_size", value: "xlarge", url: "http://localhost" }]);
  const bp = await big.newPage();
  await clear(big);
  try {
    await choose(big, bp, ["language"]);
    await openFeed(bp);
    const brs = await rows(bp);
    const base = brs[0].h;
    expect(base).toBeGreaterThan(40);                                  // a bigger text size may grow a row, never shrink it
    for (const r of brs) expect(r.h, r.text).toBeCloseTo(base, 0);     // and the rows stay one height
    const fit = await bp.evaluate(() => { const row = Array.from(document.querySelectorAll("#sidebar-left a.is-filtering")).find((e) => e.getBoundingClientRect().width > 0) as HTMLElement; const ring = parseFloat(getComputedStyle(row.querySelector(".sidebar-icon")!, "::after").height); return { ring, row: row.getBoundingClientRect().height }; });
    expect(fit.ring, "the ring fits inside its row").toBeLessThanOrEqual(fit.row - 2);
    const sideways = await bp.evaluate(() => { const sb = document.getElementById("sidebar-left")!; return sb.scrollWidth - sb.clientWidth; });
    expect(sideways).toBeLessThanOrEqual(0.5);
  } finally { await clear(big); await big.close(); }
});

test("right to left: the symbols move to the right edge, the bar and the wash mirror, and the ring is still centred on its symbol", async ({ browser }) => {
  // a visitor with the footer's choice (a member's own language wins over the cookie); ar-XB is the right-to-left pseudo-locale (I18N_PSEUDO=1, dev only)
  const scale = 4;
  const context = await browser.newContext({ viewport: { width: 1440, height: 1000 }, deviceScaleFactor: scale });
  await context.addCookies([{ name: "ui_lang", value: "ar-XB", url: "http://localhost" }]);
  const page = await context.newPage();
  try {
    await choose(context, page, ["regional", "language"]);
    await openFeed(page);
    expect(await page.evaluate(() => document.documentElement.dir)).toBe("rtl");
    const m = await page.evaluate(() => {
      const sb = document.getElementById("sidebar-left")!.getBoundingClientRect();
      const row = document.querySelector('#sidebar-left a[aria-current="page"]') as HTMLElement;
      const bar = getComputedStyle(row, "::before");
      const icon = Array.from(document.querySelectorAll("#sidebar-left a.is-filtering .sidebar-icon")).find((e) => e.getBoundingClientRect().width > 0)!;
      const ib = icon.getBoundingClientRect();
      const label = icon.parentElement!.querySelector(".sidebar-row-label")!.getBoundingClientRect();
      return { sbCentre: sb.x + sb.width / 2, iconCentre: ib.x + ib.width / 2, labelCentre: label.x + label.width / 2, barRight: bar.right, barW: bar.width, ringW: getComputedStyle(icon, "::after").width,
               rows: Array.from(document.querySelectorAll("#sidebar-left .guild-recommendations-item > a")).filter((e) => e.getBoundingClientRect().width > 0).map((e) => Math.round(e.getBoundingClientRect().height)) };
    });
    expect(m.iconCentre, "the symbols are at the right").toBeGreaterThan(m.sbCentre);
    expect(m.iconCentre).toBeGreaterThan(m.labelCentre);                   // and their labels are to the left of them
    expect(m.barRight, "the bar is on the right edge of its row").toBe("0px");
    expect(m.barW).toBe("3px");
    expect(m.ringW).toBe("34px");
    for (const h of m.rows) expect(h).toBe(40);
    const ring = await ringOf(page, "", scale);
    expect(Math.abs(ring.dx), "ring centred across").toBeLessThanOrEqual(0.6);
    expect(Math.abs(ring.dy), "ring centred down").toBeLessThanOrEqual(0.6);
    expect(ring.diameterX).toBeCloseTo(34, 0);
  } finally { await clear(context); await context.close(); }
});

// the owner's second round (2026-10-30): shorter search boxes, and category headings that are a row's own font in bold
test("every uppercase label is one size (the headings' 12px), smaller than the 14px rows, at every text size", async ({ browser }) => {
  for (const text of ["", "large", "xlarge"]) {
    const context = await browser.newContext({ ...MANY, viewport: { width: 1440, height: 1700 } });
    if (text) await context.addCookies([{ name: "text_size", value: text, url: "http://localhost" }]);
    const page = await context.newPage();
    await openFeed(page);
    await page.click('#sidebar-left a[data-target="#sidebar-filter-post_type"]');
    await page.waitForTimeout(800);
    const m = await page.evaluate(() => { const size = (e: Element) => parseFloat(getComputedStyle(e).fontSize); const vis = (sel: string) => Array.from(document.querySelectorAll(sel)).filter((e) => e.getBoundingClientRect().width > 0);
      return { rows: size(vis("#sidebar-left a > .d-flex > .sidebar-row-label")[0]), heads: vis("#sidebar-left .sidebar-head-title").map(size), groups: vis("#sidebar-left .post-type-group").map(size) }; });
    expect(m.heads.length).toBeGreaterThanOrEqual(6);
    expect(m.groups.length).toBeGreaterThanOrEqual(2);
    for (const g of m.groups) expect(g, `a group label (${text || "default"})`).toBeCloseTo(m.heads[0], 1);
    for (const h of m.heads) expect(h).toBeCloseTo(m.heads[0], 1);
    expect(m.heads[0]).toBeLessThan(m.rows);
    if (!text) expect(m.heads[0]).toBeCloseTo(12, 0);
    await context.close();
  }
});

test("the search box in each filter's panel is 32px, under the 40px row it opens from (44px under a finger), and the phone sheets keep theirs", async ({ browser }) => {
  const context = await browser.newContext({ ...VIEWER, viewport: { width: 1440, height: 1500 } });
  const page = await context.newPage();
  await openFeed(page);
  for (const id of ["regional", "language", "categorical"]) await page.click(`#sidebar-left a[data-target="#sidebar-filter-${id}"]`);
  await page.waitForTimeout(800);
  const m = await page.evaluate(() => ({ row: document.querySelector('#sidebar-left a[data-target="#sidebar-filter-regional"]')!.getBoundingClientRect().height,
    boxes: Array.from(document.querySelectorAll("#sidebar-left .collapse input.filter-search")).map((e) => ({ h: e.getBoundingClientRect().height, font: getComputedStyle(e).fontSize })) }));
  expect(m.boxes).toHaveLength(3);
  for (const b of m.boxes) { expect(b.h).toBeCloseTo(32, 0); expect(b.h).toBeLessThan(m.row); }
  expect(m.row).toBeCloseTo(40, 0);
  await context.close();
  const touch = await browser.newContext({ ...VIEWER, viewport: { width: 1100, height: 900 }, hasTouch: true, isMobile: true });
  const tp = await touch.newPage();
  await openFeed(tp);
  const heights = await tp.evaluate(() => Array.from(document.querySelectorAll("#sidebar-left input.filter-search")).map((e) => parseFloat(getComputedStyle(e).minHeight)));
  expect(heights.length).toBeGreaterThan(0);
  for (const h of heights) expect(h, "under a finger").toBeGreaterThanOrEqual(44);
  await touch.close();
});

for (const theme of ["light", "dark"] as const) {
  test(`${theme}: a category is a row in the rows' own font but bold, its subcategories sit one tick step in beneath it, and nothing explains the tick box`, async ({ browser }) => {
    const context = await browser.newContext({ ...VIEWER, viewport: { width: 1440, height: 1500 }, colorScheme: theme });
    const page = await context.newPage();
    await openFeed(page);
    await page.click('#sidebar-left a[data-target="#sidebar-filter-categorical"]');
    await page.waitForTimeout(800);
    const m = await page.evaluate(() => {
      const list = document.getElementById("sidebar-categorical-options")!;
      const font = (e: Element) => { const s = getComputedStyle(e); return [s.fontSize, s.fontFamily, s.lineHeight, s.letterSpacing, s.textTransform, s.fontStyle, s.color]; };
      const heading = list.querySelector("li[data-filter-heading]:not(.is-selected)")!;
      const row = heading.parentElement!.querySelector("li[data-filter-row]:not([data-filter-heading]):not(.is-selected)")!;
      const x = (e: Element) => Math.round(e.getBoundingClientRect().x * 10) / 10;
      const second = list.querySelectorAll("[data-filter-group]")[2]?.querySelector("li[data-filter-heading]") as HTMLElement | undefined;
      const firstGroupLast = list.querySelectorAll("[data-filter-group]")[1]?.querySelector("li:last-child") as HTMLElement | undefined;
      const lone = list.querySelector("[data-filter-group]:first-child li[data-filter-row]") as HTMLElement;
      return { headFont: font(heading.querySelector("span")!), rowFont: font(row.querySelector("span")!), headWeight: getComputedStyle(heading.querySelector("span")!).fontWeight,
        rowWeight: getComputedStyle(row.querySelector("span")!).fontWeight, headTick: x(heading.querySelector("i")!), headName: x(heading.querySelector("span")!), rowTick: x(row.querySelector("i")!),
        headH: heading.getBoundingClientRect().height, rowH: row.getBoundingClientRect().height, loneTick: x(lone.querySelector("i")!), loneIsHeading: lone.hasAttribute("data-filter-heading"),
        gap: second && firstGroupLast ? second.getBoundingClientRect().top - firstGroupLast.getBoundingClientRect().bottom : null,
        paragraphs: list.parentElement!.querySelectorAll("p").length, hint: /Tick a category/.test(document.getElementById("sidebar-filter-categorical")!.textContent || "") };
    });
    expect(m.headFont, "the heading's font is the row's, in every respect but weight").toEqual(m.rowFont);
    expect(m.headWeight).toBe("700");
    expect(m.rowWeight).toBe("400");
    expect(m.headH, "a category is a row high").toBeCloseTo(m.rowH, 0);
    expect(m.rowTick - m.headTick, "a subcategory's tick is under the category's name").toBeCloseTo(m.headName - m.headTick, 0);
    expect(m.rowTick - m.headTick).toBeGreaterThan(14);
    expect(m.loneIsHeading).toBe(false);
    expect(m.loneTick, "the lone Uncategorized row has no heading to be under").toBeCloseTo(m.headTick, 0);
    if (m.gap !== null) expect(m.gap, "a group after another has room above its heading").toBeGreaterThanOrEqual(7);
    expect(m.paragraphs).toBe(0);
    expect(m.hint).toBe(false);
    await context.close();
  });
}

test("a ticked category says what it stands for on a line under its name, and a long name is never broken mid-word", async ({ browser }) => {
  const context = await browser.newContext({ ...VIEWER, baseURL: "http://localhost", viewport: { width: 1440, height: 1500 } });
  const page = await context.newPage();
  await openFeed(page);
  const sub = await page.evaluate(() => { const l = document.getElementById("sidebar-categorical-options")!; return (l.querySelectorAll("[data-filter-group]")[1].querySelector("li[data-filter-row]:not([data-filter-heading])") as HTMLElement).dataset.value; });
  await context.request.get(`/categorical?cats=-27,${sub}`);                                   // a category and one of its rows: "some"
  try {
    await openFeed(page);
    await page.click('#sidebar-left a[data-target="#sidebar-filter-categorical"]');
    await page.waitForTimeout(800);
    const m = await page.evaluate(() => {
      const h = document.querySelector("#sidebar-categorical-options li[data-filter-heading].is-selected")!;
      const name = h.querySelector("span")!.getBoundingClientRect(); const state = h.querySelector("[data-filter-state]") as HTMLElement;
      const sb = document.getElementById("sidebar-left")!;
      return { nameH: name.height, stateTop: state.getBoundingClientRect().top, nameBottom: name.bottom, stateTextX: state.getBoundingClientRect().x + parseFloat(getComputedStyle(state).paddingLeft), nameX: name.x, stateText: state.textContent, sideways: sb.scrollWidth - sb.clientWidth };
    });
    expect(m.nameH, "the name keeps one line").toBeLessThan(25);
    expect(m.stateTop, "the state is under the name").toBeGreaterThanOrEqual(m.nameBottom - 1);
    expect(m.stateTextX, "and in step with it").toBeCloseTo(m.nameX, 0);
    expect(m.stateText).toMatch(/\S/);
    expect(m.sideways).toBeLessThanOrEqual(0.5);
  } finally { await context.request.get("/categorical?cats="); await context.close(); }
});

// the owner's third round (2026-10-30): the Regions link and the countries hover, and room under an open panel's Apply button
test("the Regional panel starts with a link to the Regions page, and hovering or focusing a region lists its countries", async ({ browser }) => {
  const context = await browser.newContext({ ...VIEWER, viewport: { width: 1440, height: 1400 } });
  const page = await context.newPage();
  await openFeed(page);
  await page.click('#sidebar-left a[data-target="#sidebar-filter-regional"]');
  await page.waitForTimeout(700);
  const link = page.locator("#sidebar-filter-regional .regions-link");
  expect(await link.getAttribute("href")).toBe("/regions");
  expect(await link.evaluate((e) => e.parentElement!.firstElementChild === e)).toBe(true);        // the first thing in the panel
  expect((await link.boundingBox())!.height).toBeGreaterThanOrEqual(24);                           // a target
  const row = page.locator("#sidebar-regional-options li a[data-countries]").first();
  const countries = (await row.getAttribute("data-countries"))!;
  expect(countries.split(", ").length).toBeGreaterThanOrEqual(3);
  await row.hover();
  const tip = page.locator(".region-tooltip");
  await expect(tip).toBeVisible();
  expect((await tip.innerText()).trim()).toBe(countries);
  const rowBox = (await row.boundingBox())!, tipBox = (await tip.boundingBox())!;
  expect(tipBox.x, "beside the row, not on top of the list").toBeGreaterThanOrEqual(rowBox.x + rowBox.width - 1);
  expect((await tip.locator(".tooltip-inner").boundingBox())!.width, "the text box is capped at 18rem").toBeLessThanOrEqual(18 * 14 + 1);
  expect(await tip.evaluate((e) => getComputedStyle(e.querySelector(".tooltip-inner")!).fontSize)).toBe("12px");
  await page.mouse.move(700, 900);
  await expect(tip).toHaveCount(0);
  // keyboard focus shows it too
  await row.focus();
  await expect(page.locator(".region-tooltip")).toBeVisible();
  await page.keyboard.press("Tab");
  // typing a country finds its region
  const country = countries.split(", ")[0];
  await page.fill("#sidebar-filter-regional input.filter-search", country);
  await page.waitForTimeout(400);
  const shown = await page.evaluate(() => Array.from(document.querySelectorAll("#sidebar-regional-options li")).filter((e) => (e as HTMLElement).offsetParent !== null).length);
  expect(shown).toBeGreaterThanOrEqual(1);
  expect(shown).toBeLessThan(32);
  await context.close();
});

// the review of 2026-10-30: a finger can't hover, and the tip used to sit on the list it described and swallow the next tap
test("on a phone the Regional sheet has the link and no countries tooltip, and tapping rows ticks them and shows nothing on top of the list", async ({ browser }) => {
  const context = await browser.newContext({ ...VIEWER, viewport: { width: 390, height: 844 }, hasTouch: true, isMobile: true });
  const page = await context.newPage();
  await openFeed(page, "/all");
  await page.evaluate(() => (window as any).jQuery("#regionalFilterModal").modal("show"));
  await page.waitForTimeout(800);
  const sheet = page.locator("#regionalFilterModal");
  await expect(sheet.locator(".regions-link")).toHaveAttribute("href", "/regions");
  expect(await sheet.locator("[data-countries]").count(), "the sheet draws no hover text").toBe(0);
  const rows = sheet.locator("li[data-filter-row] a");
  for (const i of [3, 4, 5]) await rows.nth(i).tap();
  await page.waitForTimeout(600);
  expect(await page.locator(".region-tooltip").count()).toBe(0);
  expect(await sheet.locator("li.is-selected").count(), "every tap ticked its own row: none was lost to a tip on top").toBe(3);
  await context.close();
});

for (const [mode, cookie] of [["default", ""], ["Extra large text", "xlarge"], ["Compact", "density"]] as const) {
  test(`${mode}: an open filter panel leaves as much room under its Apply button as above the panel`, async ({ browser }) => {
    const context = await browser.newContext({ ...VIEWER, viewport: { width: 1440, height: 1800 } });
    if (cookie === "xlarge") await context.addCookies([{ name: "text_size", value: "xlarge", url: "http://localhost" }]);
    if (cookie === "density") await context.addCookies([{ name: "density", value: "compact", url: "http://localhost" }]);
    const page = await context.newPage();
    await openFeed(page);
    for (const id of ["regional", "language", "categorical", "post_type"]) {
      await page.click(`#sidebar-left a[data-target="#sidebar-filter-${id}"]`);
      await page.waitForTimeout(600);
      const g = await page.evaluate((id) => {
        const panel = document.getElementById(`sidebar-filter-${id}`)!;
        const row = panel.closest("li")!;
        const apply = Array.from(panel.querySelectorAll("button")).find((b) => /apply/i.test(b.textContent || ""))!.getBoundingClientRect();
        const next = row.nextElementSibling!.querySelector(":scope > a")!.getBoundingClientRect();
        const own = row.querySelector(":scope > a")!.getBoundingClientRect();
        const first = panel.getBoundingClientRect();
        return { below: next.top - apply.bottom, above: first.top - own.bottom };
      }, id);
      expect(g.below, `${id}: under Apply`).toBeGreaterThanOrEqual(g.above - 1.5);
      expect(g.below, `${id}: under Apply`).toBeGreaterThanOrEqual(6);
      await page.click(`#sidebar-left a[data-target="#sidebar-filter-${id}"]`);                  // close it again
      await page.waitForTimeout(500);
    }
    await context.close();
  });
}

// found by the independent review (2026-10-30)
const MODES: [string, { cookies: { name: string; value: string }[]; touch?: boolean }][] = [
  ["default", { cookies: [] }],
  ["Compact", { cookies: [{ name: "density", value: "compact" }] }],
  ["Large text", { cookies: [{ name: "text_size", value: "large" }] }],
  ["Extra large text", { cookies: [{ name: "text_size", value: "xlarge" }] }],
  ["a finger", { cookies: [], touch: true }],
];

for (const [mode, { cookies, touch }] of MODES) {
  test(`${mode}: every heading is one height, the fold button included, and its target keeps its size`, async ({ browser }) => {
    const context = await browser.newContext({ ...MANY, viewport: { width: 1440, height: 1700 }, ...(touch ? { hasTouch: true, isMobile: true } : {}) });
    if (cookies.length) await context.addCookies(cookies.map((c) => ({ ...c, url: "http://localhost" })));
    const page = await context.newPage();
    await openFeed(page);
    const hs = await heads(page);
    expect(hs.length).toBeGreaterThanOrEqual(6);
    for (const h of hs) expect(h.h, `${h.text} (${mode})`).toBeCloseTo(hs[hs.length - 1].h, 0);       // the first, with the fold button, is as tall as the last
    const fold = await page.evaluate(() => { const b = document.querySelector("#sidebar-left .sidebar-head .btn")!.getBoundingClientRect(); const cs = getComputedStyle(document.querySelector("#sidebar-left .sidebar-head .btn")!, "::after"); return { h: b.height, reach: cs.content, reachH: parseFloat(cs.height) || 0 }; });
    expect(fold.h, `fold button (${mode})`).toBeGreaterThanOrEqual(31.5);                              // 36px, or 32 in Compact: the ladder's, not the text's
    if (touch) expect(fold.reachH, "a finger's 44px comes from an invisible reach").toBeGreaterThanOrEqual(43.5);
    await context.close();
  });
}

for (const [mode, text] of [["default", ""], ["Large text", "large"], ["Extra large text", "xlarge"]] as const) {
  test(`${mode}: on the folded rail every icon, avatar and ring is on the rail's centre line`, async ({ browser }) => {
    const scale = 4;
    const context = await browser.newContext({ ...MANY, viewport: { width: 1440, height: 1700 }, deviceScaleFactor: scale });
    await context.addCookies([{ name: "sidebar_pref", value: "collapsed", url: "http://localhost" }, ...(text ? [{ name: "text_size", value: text, url: "http://localhost" }] : [])]);
    const page = await context.newPage();
    await clear(context);
    try {
      await choose(context, page, ["regional", "language", "advanced"]);
      await openFeed(page);
      const m = await page.evaluate(() => {
        const sb = document.getElementById("sidebar-left")!.getBoundingClientRect();
        return Array.from(document.querySelectorAll("#sidebar-left .sidebar-collapsed-visible .guild-recommendations-item > a")).filter((e) => e.getBoundingClientRect().width > 0).map((a) => {
          const row = a.getBoundingClientRect(); const glyph = (a.querySelector(".sidebar-icon, img.profile-pic") as HTMLElement).getBoundingClientRect();
          return { label: a.getAttribute("aria-label") || a.querySelector("img")?.getAttribute("alt") || "", glyph: glyph.x + glyph.width / 2 - sb.x, row: row.x + row.width / 2 - sb.x, rowW: row.width, rail: sb.width };
        });
      });
      expect(m.length).toBeGreaterThan(8);
      for (const r of m) { expect(r.glyph, `${r.label} (${mode}): glyph centre`).toBeCloseTo(r.row, 0); expect(r.row, `${r.label}: row centre`).toBeCloseTo(m[0].row, 0); expect(r.rowW).toBeCloseTo(58, 0); }
      const ring = await ringOf(page, "", scale);                 // (a rail link has no text to find it by)
      expect(Math.abs(ring.dx), `ring centred across (${mode})`).toBeLessThanOrEqual(0.6);
      // the wash reaches 6px or more past the ring on both sides, whatever the text size
      const wash = await page.evaluate(() => { const a = Array.from(document.querySelectorAll("#sidebar-left a.is-filtering")).find((e) => e.getBoundingClientRect().width > 0)!; const row = a.getBoundingClientRect(); const ringW = parseFloat(getComputedStyle(a.querySelector(".sidebar-icon")!, "::after").width); return { room: (row.width - ringW) / 2 }; });
      expect(wash.room, `room each side of the ring (${mode})`).toBeGreaterThanOrEqual(6);
    } finally { await clear(context); await context.close(); }
  });
}

test("the rail's Save button has its glyph on the centre line and shows the saved state's colour", async ({ browser }) => {
  const context = await browser.newContext({ ...VIEWER, viewport: { width: 1440, height: 1000 } });
  await context.addCookies([{ name: "sidebar_pref", value: "collapsed", url: "http://localhost" }]);
  const page = await context.newPage();
  await openFeed(page);
  const m = await page.evaluate(() => {
    const sb = document.getElementById("sidebar-left")!.getBoundingClientRect();
    const btn = document.querySelector("#sidebar-left .feed-defaults-rail-btn") as HTMLElement;
    const glyph = btn.querySelector("i")!.getBoundingClientRect();
    const firstRow = document.querySelector("#sidebar-left .sidebar-collapsed-visible .guild-recommendations-item > a")!.getBoundingClientRect();
    const out: Record<string, number | string> = { rail: firstRow.x + firstRow.width / 2 - sb.x, glyph: glyph.x + glyph.width / 2 - sb.x, margin: getComputedStyle(btn.querySelector("i")!).marginRight, btnW: btn.getBoundingClientRect().width };
    // the saved state, in this tab only (nothing is sent: pressing the real button would save a default)
    btn.classList.add("is-saved");
    const probe = document.createElement("span"); probe.style.color = getComputedStyle(document.documentElement).getPropertyValue("--success"); document.body.appendChild(probe);
    out.success = getComputedStyle(probe).color; probe.remove();
    return out;
  });
  await page.waitForTimeout(500);                                              // past the button's colour transition
  m.glyphColour = await page.evaluate(() => getComputedStyle(document.querySelector("#sidebar-left .feed-defaults-rail-btn i")!).color);
  expect(m.margin).toBe("0px");
  expect(m.glyph as number).toBeCloseTo(m.rail as number, 0);
  expect(m.btnW as number).toBeLessThanOrEqual(34.5);                         // the rail's own width: wider, it overflowed to the right
  expect(m.glyphColour).toBe(m.success);                                      // the legacy grey no longer overrides the saved state's green
  await context.close();
});

test("a name says its own direction: an Arabic curation reads right to left in an English page, a guild's name left to right", async ({ browser }) => {
  const context = await browser.newContext({ ...MANY, viewport: { width: 1440, height: 1700 } });
  const page = await context.newPage();
  await openFeed(page);
  const dirs = await page.evaluate(() => Array.from(document.querySelectorAll("#sidebar-left .sidebar-row-label[dir=auto]")).filter((e) => e.getBoundingClientRect().width > 0).map((e) => ({ text: (e.textContent || "").trim(), direction: getComputedStyle(e).direction })));
  expect(dirs.find((d) => /^\+ui_sb_/.test(d.text))!.direction).toBe("ltr");
  expect(dirs.find((d) => /مختارات/.test(d.text))!.direction).toBe("rtl");
  await context.close();
});

test("axe finds nothing serious on the folded rail either, its expand button named", async ({ browser }) => {
  const context = await browser.newContext({ ...MANY, viewport: { width: 1440, height: 1700 }, reducedMotion: "reduce" });
  await context.addCookies([{ name: "sidebar_pref", value: "collapsed", url: "http://localhost" }]);
  const page = await context.newPage();
  await clear(context);
  try {
    await choose(context, page);
    await openFeed(page);
    const result = await new AxeBuilder({ page }).include("#sidebar-left").analyze();
    const bad = result.violations.filter((v) => v.impact === "serious" || v.impact === "critical");
    expect(bad.map((v) => `${v.id}: ${v.nodes.map((n) => n.target.join(" ")).slice(0, 3).join(" | ")}`)).toEqual([]);
    expect(await page.locator("#sidebar-left button[onclick='toggle_sidebar_expand();']").getAttribute("aria-label")).toBeTruthy();
  } finally { await clear(context); await context.close(); }
});

for (const theme of ["light", "dark"] as const) {
  test(`${theme}: axe finds nothing serious in the sidebar, with filters on`, async ({ browser }) => {
    // reduced motion: the site's transition durations are 0 under it, so axe never reads a row's colour half way through one
    const context = await browser.newContext({ ...MANY, viewport: { width: 1440, height: 1700 }, colorScheme: theme, reducedMotion: "reduce" });
    const page = await context.newPage();
    await clear(context);
    try {
      await choose(context, page);
      await openFeed(page);
      const result = await new AxeBuilder({ page }).include("#sidebar-left").analyze();
      const bad = result.violations.filter((v) => v.impact === "serious" || v.impact === "critical");
      // the one exception to AA is the owner's: white labels on the site's own button fill (CLAUDE.md "Colour contrast"); the sidebar has none of those
      expect(bad.map((v) => `${v.id}: ${v.nodes.map((n) => n.target.join(" ")).slice(0, 3).join(" | ")}`)).toEqual([]);
    } finally { await clear(context); await context.close(); }
  });
}
