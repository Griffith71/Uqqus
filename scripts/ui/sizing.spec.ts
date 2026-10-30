import { test, expect, type BrowserContext, type Page } from "@playwright/test";
import { open } from "./pages";

// Sizing and spacing (CLAUDE.md "Sizing and spacing"; the owner chose "B · Balanced", 2026-10-29): measured in a real browser, because CSS that says
// 36px can still render 35.6 or 38.
//   npm run sizing
// What it holds the site to: the bar is 56px (52 in Compact) and every control in it is one height; the nav glyphs keep their 14px in 36px buttons and the
// icons sit on a 36px pitch; the account menu is 264px wide with 36px rows (32 in Compact, 44 under a finger), a 56px profile row with a 40px avatar,
// 20px icon boxes 12px from their labels; the phone's menu is a top sheet attached to the bar with the page dimmed under it; nothing scrolls sideways;
// and the small text links the audit found have a 24px target.

const USER = { storageState: ".auth/viewer.json" };

const bar = (page: Page) => page.evaluate(() => {
  const nav = document.getElementById("navbar")!;
  const r = (e: Element) => { const b = e.getBoundingClientRect(); return { x: b.x, y: b.y, w: b.width, h: b.height }; };
  const icons = Array.from(document.querySelectorAll("#navbar-icon-row .nav-link:not(#dropdownMenuLink)")).filter((e) => e.getBoundingClientRect().width > 0).map(r);
  const glyph = document.querySelector("#navbar-icon-row .nav-link > .fas") as HTMLElement;
  const pill = document.querySelector("#navbar .form-inline.search .form-control");
  const avatar = document.querySelector("#navbar #dropdownMenuLink .profile-pic-35");
  return { height: nav.getBoundingClientRect().height, icons, glyph: glyph && parseFloat(getComputedStyle(glyph).fontSize), pill: pill && r(pill), avatar: avatar && r(avatar), cssHeight: getComputedStyle(document.documentElement).getPropertyValue("--navbar-height").trim() };
});

const menu = (page: Page) => page.evaluate(() => {
  const m = document.getElementById("accountDropdownMenu")!;
  const mr = m.getBoundingClientRect();
  const items = Array.from(m.querySelectorAll(".dropdown-item")).map((e) => { const r = e.getBoundingClientRect(); const cs = getComputedStyle(e); const icon = e.querySelector(".fa-fw"); const label = icon ? Array.from(e.childNodes).find((n) => n.nodeType === 3 && n.textContent!.trim()) : null; return { text: e.textContent!.trim().slice(0, 14), profile: e.classList.contains("account-menu-profile"), h: r.height, font: cs.fontSize, iconW: icon ? icon.getBoundingClientRect().width : 0, iconFont: icon ? parseFloat(getComputedStyle(icon).fontSize) : 0, iconGap: icon ? parseFloat(getComputedStyle(icon).marginRight) : 0, left: r.left - mr.left, right: mr.right - r.right }; });
  const avatar = m.querySelector(".account-menu-profile .profile-pic-35")!.getBoundingClientRect();
  const dividers = Array.from(m.querySelectorAll(".dropdown-divider")).map((e) => { const s = getComputedStyle(e); return [parseFloat(s.marginTop), parseFloat(s.marginBottom), e.getBoundingClientRect().height]; });
  return { w: mr.width, h: mr.height, top: mr.top, bottom: mr.bottom, radius: parseFloat(getComputedStyle(m).borderTopLeftRadius), items, avatar: avatar.width, dividers, fits: m.scrollWidth <= m.clientWidth + 1 };
});

async function openMenu(page: Page) {
  await page.locator("#dropdownMenuLink").click();
  await page.waitForTimeout(450);
}

test("desktop: the bar is 56px, every control in it is 36px, the glyphs keep their 14px and the icons sit on a 36px pitch", async ({ browser }) => {
  for (const width of [1440, 1280, 1024]) {
    const context = await browser.newContext({ ...USER, viewport: { width, height: 800 } });
    const page = await context.newPage();
    await open(page, "/all");
    const b = await bar(page);
    expect(b.height, `bar at ${width}`).toBeCloseTo(56, 0);
    expect(b.cssHeight).toMatch(/^56px$|^calc\(56px/);                     // what the page below it starts from is what the bar is
    expect(b.pill!.h).toBeCloseTo(36, 0);
    for (const icon of b.icons) { expect(icon.w).toBeCloseTo(36, 0); expect(icon.h).toBeCloseTo(36, 0); }
    const pitches = b.icons.slice(1).map((icon, i) => Math.round(icon.x - b.icons[i].x));
    expect(pitches, `icon pitch at ${width}`).toEqual(pitches.map(() => 36));
    expect(b.glyph).toBeCloseTo(14, 0);                                      // Bootstrap's own: they were 20px for a day and the owner found them too big
    expect(b.avatar!.w).toBeCloseTo(32, 0);
    await context.close();
  }
});

test("desktop: the account menu is 264px wide with 36px rows, a 56px profile row, 20px icon boxes and 8px of inset", async ({ browser }) => {
  const context = await browser.newContext({ ...USER, viewport: { width: 1440, height: 900 } });
  const page = await context.newPage();
  await open(page, "/all");
  await openMenu(page);
  const m = await menu(page);
  expect(m.w).toBeCloseTo(264, 0);                                          // it was 210; Reddit 256, YouTube 300
  expect(m.radius).toBe(12);
  const rows = m.items.filter((i) => !i.profile);
  expect(new Set(rows.map((i) => Math.round(i.h)))).toEqual(new Set([36]));
  const profile = m.items.find((i) => i.profile)!;
  expect(Math.round(profile.h)).toBe(56);
  expect(m.avatar).toBeCloseTo(40, 0);
  for (const row of rows) {
    expect(row.iconW, row.text).toBeCloseTo(20, 0);
    expect(row.iconFont, row.text).toBeCloseTo(16, 0);
    expect(row.iconGap, row.text).toBeCloseTo(12, 0);
    expect(row.left, `${row.text} inset`).toBeGreaterThanOrEqual(8);          // a row's highlight stops 8px short of the menu's edge
    expect(row.right).toBeGreaterThanOrEqual(8);
    expect(row.font).toBe("14px");
  }
  for (const [above, below, thickness] of m.dividers) { expect(above).toBe(4); expect(below).toBe(4); expect(thickness).toBe(1); }
  expect(m.bottom, "the menu fits a 900px window").toBeLessThan(900);
  expect(m.h).toBeGreaterThan(470);
  expect(m.h).toBeLessThan(520);
  await context.close();
});

test("a short window scrolls the menu, not the page", async ({ browser }) => {
  const context = await browser.newContext({ ...USER, viewport: { width: 1280, height: 520 } });
  const page = await context.newPage();
  await open(page, "/all");
  await openMenu(page);
  const m = await menu(page);
  expect(m.bottom).toBeLessThanOrEqual(520);
  expect(await page.evaluate(() => { const e = document.getElementById("accountDropdownMenu")!; return e.scrollHeight > e.clientHeight && getComputedStyle(e).overflowY === "auto"; })).toBe(true);
  await context.close();
});

test("Compact density: the bar is 52px and the rows 32px, from the same tokens", async ({ browser }) => {
  const context = await browser.newContext({ ...USER, viewport: { width: 1280, height: 800 } });
  await context.addCookies([{ name: "density", value: "compact", url: "http://localhost" }]);
  const page = await context.newPage();
  await open(page, "/all");
  expect((await bar(page)).height).toBeCloseTo(52, 0);
  expect((await bar(page)).pill!.h).toBeCloseTo(32, 0);
  await openMenu(page);
  const rows = (await menu(page)).items.filter((i) => !i.profile);
  expect(new Set(rows.map((i) => Math.round(i.h)))).toEqual(new Set([32]));
  await context.close();
});

test("the largest text size grows the menu and the bar with the text and cuts nothing off", async ({ browser }) => {
  const context = await browser.newContext({ ...USER, viewport: { width: 1280, height: 900 } });
  await context.addCookies([{ name: "text_size", value: "xlarge", url: "http://localhost" }]);
  const page = await context.newPage();
  await open(page, "/all");
  const b = await bar(page);
  expect(b.height).toBeGreaterThanOrEqual(56);                              // never shorter than the ladder says; taller when the text is
  await openMenu(page);
  const m = await menu(page);
  expect(m.w).toBeGreaterThan(264);                                         // the width is rem: it grows with the text
  expect(m.fits).toBe(true);
  expect(m.items.filter((i) => !i.profile).every((i) => i.h >= 36)).toBe(true);
  await context.close();
});

test("a touch screen: the bar is 56px, the menu is a top sheet with 44px rows attached to the bar, the page dimmed under it, and a tap on the dim closes it", async ({ browser }) => {
  const context = await browser.newContext({ ...USER, viewport: { width: 390, height: 844 }, hasTouch: true, isMobile: true });
  const page = await context.newPage();
  await open(page, "/all");
  expect((await bar(page)).height).toBeCloseTo(56, 0);
  await page.locator("#dropdownMenuLink").tap();
  await page.waitForTimeout(500);
  const m = await menu(page);
  expect(m.w).toBeCloseTo(390, 0);
  expect(m.top).toBeCloseTo(56, 0);                                         // attached to the bar (the markup's mt-2 left a 7px strip of page)
  expect(new Set(m.items.filter((i) => !i.profile).map((i) => Math.round(i.h)))).toEqual(new Set([44]));    // a finger's target
  expect(m.bottom).toBeLessThanOrEqual(844);
  const dim = await page.evaluate(() => { const s = getComputedStyle(document.body, "::before"); return { content: s.content, position: s.position, z: +s.zIndex, colour: s.backgroundColor }; });
  expect(dim.position).toBe("fixed");
  expect(dim.z).toBeLessThan(1030);                                         // under the navbar's own stacking context, which holds the menu
  expect(dim.colour).toMatch(/rgba\(0, 0, 0, 0\.[56]\)/);
  // the dim is above the page, under the menu: the menu's own text is not blurred (a pixel check would need the text; the stacking order is the thing)
  expect(await page.evaluate(() => { const e = document.getElementById("accountDropdownMenu")!; const r = e.getBoundingClientRect(); const top = document.elementFromPoint(r.x + 100, r.y + 100); return e.contains(top); })).toBe(true);
  await page.touchscreen.tap(195, 800);                                     // a tap on the dim, below the sheet
  await page.waitForTimeout(500);
  expect(await page.evaluate(() => document.getElementById("accountDropdownMenu")!.classList.contains("show"))).toBe(false);
  expect(await page.evaluate(() => getComputedStyle(document.body, "::before").content)).toMatch(/none|normal/);
  await context.close();
});

// (the round icons and the editor's toolbar buttons have an invisible 44px ::after, which is overflow by design: scrollWidth counts it)
test("nothing scrolls sideways at a phone's width, and no button is squeezed under its label", async ({ browser }) => {
  const context = await browser.newContext({ ...USER, viewport: { width: 390, height: 844 }, hasTouch: true, isMobile: true });
  const page = await context.newPage();
  for (const path of ["/all", "/browse", "/settings/profile", "/post/15", "/@ui_alice", "/submit", "/notifications"]) {
    await open(page, path);
    const r = await page.evaluate(() => ({ wide: document.documentElement.scrollWidth - innerWidth, squeezed: Array.from(document.querySelectorAll(".btn:not(.profile-x-icon):not(.post-tb-btn)")).filter((e) => e.getBoundingClientRect().width > 0 && e.scrollWidth > e.clientWidth + 1).length }));
    expect(r, path).toEqual({ wide: 0, squeezed: 0 });
  }
  await context.close();
});

test("the small text links the audit found have a 24px target, and the ladder's controls are never short of their rung", async ({ browser }) => {
  const context = await browser.newContext({ ...USER, viewport: { width: 1280, height: 900 } });
  const page = await context.newPage();
  await open(page, "/all");
  const links = await page.evaluate(() => Array.from(document.querySelectorAll(".list-inline-item > a, a.happening-all, a.wtf-name")).filter((e) => e.getBoundingClientRect().width > 0).map((e) => ({ text: (e.textContent || "").trim().slice(0, 16), h: e.getBoundingClientRect().height })));
  expect(links.length).toBeGreaterThan(3);
  for (const link of links) expect(link.h, link.text).toBeGreaterThanOrEqual(24);
  const buttons = await page.evaluate(() => Array.from(document.querySelectorAll(".btn:not(.btn-sm):not(.btn-lg)")).filter((e) => e.getBoundingClientRect().width > 0 && getComputedStyle(e).display !== "inline").map((e) => e.getBoundingClientRect().height));
  expect(buttons.length).toBeGreaterThan(0);
  for (const h of buttons) expect(h).toBeGreaterThanOrEqual(35.9);        // the default control: 36 (it was 35.6)
  await context.close();
});

// What the sizing reviewers found under a finger (2026-10-29): every button is 44px there, and three places have things placed from a height.
const PHONE = { viewport: { width: 390, height: 844 }, isMobile: true, hasTouch: true } as const;

test("a touch phone: the sort bar stays clear of the feed's tabs and a visitor's bar is the same 56px as a member's", async ({ browser }) => {
  const member = await browser.newContext({ ...USER, ...PHONE });
  const page = await member.newPage();
  await open(page, "/all");
  const bar = await page.evaluate(() => { const b = document.getElementById("fixed-bar-mobile")!.getBoundingClientRect(); return { top: b.top, bottom: b.bottom, h: b.height, btn: document.querySelector("#fixed-bar-mobile .btn")!.getBoundingClientRect().height }; });
  expect(bar.h).toBeLessThan(52);                    // it was 50.6 and grew to 59 when its buttons went to 44px
  expect(bar.btn).toBeCloseTo(36, 0);                // the bar's buttons stay on the default rung
  // what is under the bar starts at or below its bottom
  const below = await page.evaluate(() => { const first = Array.from(document.querySelectorAll("#frontpage a, #frontpage button")).map((e) => e.getBoundingClientRect()).filter((r) => r.width > 0 && r.top > 60).sort((a, b) => a.top - b.top)[0]; return first ? first.top : null; });
  expect(below).not.toBeNull();
  expect(below!).toBeGreaterThanOrEqual(bar.bottom - 1);
  await member.close();
  const visitor = await browser.newContext({ ...PHONE });
  const vpage = await visitor.newPage();
  await open(vpage, "/");
  const v = await vpage.evaluate(() => ({ bar: document.getElementById("navbar")!.getBoundingClientRect().height, signIn: document.querySelector("#navbar .btn")!.getBoundingClientRect().height }));
  expect(v.bar).toBeCloseTo(56, 0);                  // it was 64px: the Sign In pill is a 44px button under a finger
  expect(v.signIn).toBeCloseTo(36, 0);
  await visitor.close();
});

test("a touch phone: round icon buttons stay round, the editor's toolbar stays one row, and a link styled as a button centres its label", async ({ browser }) => {
  const context = await browser.newContext({ ...USER, ...PHONE });
  const page = await context.newPage();
  await open(page, "/@ui_alice");
  const icons = await page.evaluate(() => Array.from(document.querySelectorAll(".profile-x-icon")).filter((e) => e.getBoundingClientRect().width > 0).map((e) => { const r = e.getBoundingClientRect(); const a = getComputedStyle(e, "::after"); return { w: r.width, h: r.height, reach: parseFloat(a.top) }; }));
  expect(icons.length).toBeGreaterThan(0);
  for (const icon of icons) { expect(icon.w).toBeCloseTo(icon.h, 0); expect(icon.reach).toBe(-4); }       // a circle with a 44px area round it
  await open(page, "/submit");
  const tb = await page.evaluate(() => Array.from(document.querySelectorAll(".post-tb-btn:not(.post-tb-tab)")).filter((e) => e.getBoundingClientRect().width > 0).map((e) => e.getBoundingClientRect().height));
  expect(tb.length).toBeGreaterThan(4);
  for (const h of tb) expect(h).toBeLessThan(32);                                                   // it was 44px each, a 35px taller toolbar
  await context.close();
  for (const touch of [true, false]) {
    const ctx = await browser.newContext(touch ? { ...PHONE } : { viewport: { width: 1280, height: 800 } });
    const p = await ctx.newPage();
    await open(p, "/help/about");
    const offsets = await p.evaluate(() => Array.from(document.querySelectorAll("a.btn")).filter((e) => e.getBoundingClientRect().width > 0).slice(0, 4).map((e) => {
      const r = e.getBoundingClientRect(); const range = document.createRange(); range.selectNodeContents(e);
      const t = Array.from(range.getClientRects()).filter((x) => x.width > 0);
      return ((Math.min(...t.map((x) => x.top)) + Math.max(...t.map((x) => x.bottom))) / 2) - (r.top + r.height / 2);
    }));
    expect(offsets.length).toBeGreaterThan(0);
    for (const o of offsets) expect(Math.abs(o), `label offset (${touch ? "touch" : "mouse"})`).toBeLessThan(2);       // it was 5 to 7px high under a finger
    await ctx.close();
  }
});

test("block links in a column grow their target and not the layout: What's happening is as tall as its three sections say", async ({ browser }) => {
  const context = await browser.newContext({ viewport: { width: 1440, height: 900 } });
  const page = await context.newPage();
  await open(page, "/");
  await page.waitForTimeout(2000);
  const h = await page.evaluate(() => { const e = document.querySelector("[data-happening], #happening-box, .happening-box"); return e ? e.getBoundingClientRect().height : null; });
  expect([600, 417, 234]).toContain(Math.round(h!));              // it was 605 with the padding added to the layout
  await context.close();
});

declare global { interface Window { jQuery: any; } }
