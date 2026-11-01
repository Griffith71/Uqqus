import { test, expect, type Page } from "@playwright/test";
import { readFileSync } from "node:fs";
import { open } from "./pages";

// The post editor's toolbar as a column beside the text box (CLAUDE.md "Post editor toolbar"; the owner, 2026-10-30), measured in a real browser
// because a sticky column, a grid that stretches and fixed pop-outs are things CSS can only claim.
//   npm run rail
// What it holds the site to, on Create a post and on the feed composer: from 992px the toolbar is a 42px column of 36px buttons with 14px symbols (the comment box's size),
// just outside the text box on the end side and level with its top; the body grows with the text (no scrollbar of its own), the page follows the
// last line and the column stays pinned under the top bar (and under the feed's sort bar) while the box scrolls past; the menus and the link
// dialog open fully inside the window, beside the column, above Create a post's fixed bottom bar; below 992px it is the old row at the top.
// And (the owner, 2026-10-30: "the pre-post look identical to what it would look like when it's posted"): the text of Create a post's box is exactly as
// wide as the text on the post page at the same window, with the sidebar open or folded and at every text size, so the words wrap where they will wrap
// once posted; and from 992px Preview, in both composers, is an overlay holding the post page's own card (see the end of this file).

const USER = { storageState: ".auth/viewer.json" };
const RAIL = "#body-write > .post-toolbar";
const BOX = "#body-write > .input-group";

type Box = { x: number; y: number; w: number; h: number; r: number; b: number };
const rect = (page: Page, selector: string) => page.evaluate((s) => {
  const e = document.querySelector(s);
  if (!e) return null;
  const b = e.getBoundingClientRect();
  return { x: b.x, y: b.y, w: b.width, h: b.height, r: b.right, b: b.bottom };
}, selector) as Promise<Box | null>;

async function typeLines(page: Page, selector: string, lines: number) {
  await page.click(selector);
  for (let i = 0; i < lines; i++) {
    await page.keyboard.type(`Line ${i} of a long post that someone is writing here`, { delay: 0 });
    await page.keyboard.press("Enter");
  }
  await page.waitForTimeout(250);
}

test("create a post: a 42px column of 36px buttons with 14px symbols, level with the box and just outside it", async ({ browser }) => {
  const context = await browser.newContext({ ...USER, viewport: { width: 1280, height: 800 } });
  const page = await context.newPage();
  await open(page, "/submit");
  const rail = (await rect(page, RAIL))!, box = (await rect(page, BOX))!;
  expect(rail.w).toBeCloseTo(42, 0);
  expect(Math.abs(rail.y - box.y)).toBeLessThanOrEqual(2);                 // level with the top of the box
  expect(rail.x).toBeGreaterThanOrEqual(box.r);                            // outside the box, on the end side
  expect(rail.x - box.r).toBeLessThanOrEqual(12);
  expect(Math.abs(rail.h - box.h)).toBeLessThanOrEqual(6);                 // the box is as tall as the folded column, no taller
  const buttons = await page.evaluate((s) => Array.from(document.querySelectorAll(`${s} .post-tb-btn`)).filter((e) => e.getBoundingClientRect().width > 0).map((e) => {
    const b = e.getBoundingClientRect();
    return { w: b.width, h: b.height, font: parseFloat(getComputedStyle(e).fontSize) };
  }), RAIL);
  expect(buttons.length).toBeGreaterThan(8);                               // the folded ones; the column's height against the box's, above, is what ties their number to the stylesheet
  for (const b of buttons) { expect(b.w).toBeCloseTo(36, 0); expect(b.h).toBeCloseTo(36, 0); expect(b.font).toBe(14); }
  // folded, nothing scrolls inside the column
  const scroll = await page.evaluate((s) => { const e = document.querySelector(s)!; return [e.scrollHeight, e.clientHeight]; }, RAIL);
  expect(scroll[0]).toBeLessThanOrEqual(scroll[1] + 1);
  await context.close();
});

test("create a post: the body grows with the text, the page follows the last line and the column stays pinned", async ({ browser }) => {
  const context = await browser.newContext({ ...USER, viewport: { width: 1280, height: 800 } });
  const page = await context.newPage();
  await open(page, "/submit");
  const before = (await rect(page, BOX))!;
  await typeLines(page, "#post-body", 80);
  const bar = (await rect(page, ".composer-bar"))!;
  const state = await page.evaluate(() => {
    const ta = document.getElementById("post-body") as HTMLTextAreaElement;
    return { scroll: ta.scrollHeight, client: ta.clientHeight, y: window.scrollY, navbar: document.getElementById("navbar")!.getBoundingClientRect().bottom };
  });
  const rail = (await rect(page, RAIL))!, box = (await rect(page, BOX))!;
  expect(box.h).toBeGreaterThan(before.h * 3);                              // grew with the text
  expect(state.scroll).toBeLessThanOrEqual(state.client + 1);               // and has no scrollbar of its own
  expect(state.y).toBeGreaterThan(0);                                       // the page scrolled to follow the caret
  expect(box.b).toBeLessThanOrEqual(bar.y - 8);                             // the last line is above the fixed bar, not under it
  expect(rail.y).toBeCloseTo(state.navbar + 12, 0);                         // pinned just under the top bar while the box's top is far above
  expect(box.y).toBeLessThan(0);
  // and it stays pinned while the page is scrolled either way, until the end of the box
  await page.evaluate(() => window.scrollTo(0, 400));
  await page.waitForTimeout(150);
  expect((await rect(page, RAIL))!.y).toBeCloseTo(state.navbar + 12, 0);
  await context.close();
});

test("create a post: the menus, the dialog and the status line open beside the column, inside the window and above the bottom bar", async ({ browser }) => {
  const context = await browser.newContext({ ...USER, viewport: { width: 1280, height: 800 } });
  const page = await context.newPage();
  await open(page, "/submit");
  await page.evaluate(() => { try { localStorage.removeItem("post_toolbar_open"); } catch (e) { /* none */ } });
  // a long text, scrolled to its middle: the column is pinned there, clear of the fixed bottom bar (unpinned, its lower buttons
  // are under that bar and a click would scroll the page, which closes a menu as it should)
  await typeLines(page, "#post-body", 60);
  await page.evaluate(() => window.scrollTo(0, 500));
  await page.waitForTimeout(200);
  await page.click(`${RAIL} .post-tb-toggle`);                              // the formatting
  await page.waitForTimeout(200);
  const expanded = (await rect(page, RAIL))!;
  expect(expanded.h).toBeGreaterThan(500);                                  // took the window's height, not the folded 447px
  const scroll = await page.evaluate((s) => { const e = document.querySelector(s)!; return [e.scrollHeight, e.clientHeight]; }, RAIL);
  expect(scroll[0]).toBeGreaterThan(scroll[1]);                             // and scrolls by itself past that
  const bar = (await rect(page, ".composer-bar"))!;
  for (const label of ["Text style", "Text colour", "Alignment", "Template", "More"]) {
    await page.evaluate((s) => { document.querySelector(s)!.scrollTop = 0; }, RAIL);
    const button = page.locator(`${RAIL} [aria-label="${label}"]`);
    await button.scrollIntoViewIfNeeded();
    await page.waitForTimeout(150);                                         // a scroll that is still arriving would close the menu (it is fixed to the window)
    await button.click();
    await page.waitForTimeout(label === "Template" ? 400 : 150);
    const rail = (await rect(page, RAIL))!, menu = await rect(page, `${RAIL} .post-tb-menu:not(.d-none)`);
    expect(menu, `${label} opened`).not.toBeNull();
    expect(menu!.r, label).toBeLessThanOrEqual(rail.x - 7);                 // beside the column, never over it
    expect(menu!.x, label).toBeGreaterThanOrEqual(0);
    expect(menu!.y, label).toBeGreaterThanOrEqual(56);                      // under the top bar
    expect(menu!.b, label).toBeLessThanOrEqual(bar.y);                      // above the fixed bottom bar
    await page.keyboard.press("Escape");
  }
  await page.evaluate((s) => { document.querySelector(s)!.scrollTop = 0; }, RAIL);
  await page.click(`${RAIL} [aria-label="Link (Ctrl+K)"]`);
  const dialog = (await rect(page, `${RAIL} .post-tb-dialog`))!, rail = (await rect(page, RAIL))!;
  expect(dialog.r).toBeLessThanOrEqual(rail.x - 7);
  expect(dialog.b).toBeLessThanOrEqual(bar.y);
  // it moves with the column when the page moves
  const gap = rail.y - dialog.y;
  await page.evaluate(() => window.scrollBy(0, -120));
  await page.waitForTimeout(150);
  expect((await rect(page, RAIL))!.y - (await rect(page, `${RAIL} .post-tb-dialog`))!.y).toBeCloseTo(gap, 0);
  await context.close();
});

test("create a post: the focus ring is on the text box and a focused toolbar button does not light it", async ({ browser }) => {
  const context = await browser.newContext({ ...USER, viewport: { width: 1280, height: 800 } });
  const page = await context.newPage();
  await open(page, "/submit");
  const ring = (selector: string) => page.evaluate((s) => getComputedStyle(document.querySelector(s)!).boxShadow, selector);
  await page.focus("#post-body");
  expect(await ring(BOX)).not.toBe("none");
  expect(await ring("#body-write")).toBe("none");
  await page.focus(`${RAIL} .post-tb-btn:not(:disabled)`);                  // the keyboard's focus on a button
  expect(await ring(BOX)).toBe("none");
  await context.close();
});

for (const width of [991, 800, 390]) {
  test(`create a post at ${width}px: the row at the top of the box, as before`, async ({ browser }) => {
    const context = await browser.newContext({ ...USER, viewport: { width, height: 800 } });
    const page = await context.newPage();
    await open(page, "/submit");
    const info = await page.evaluate((s) => {
      const bar = document.querySelector(s)!, cs = getComputedStyle(bar);
      const tab = document.querySelector(".post-tb-tab-icon")!, text = document.querySelector(".post-tb-tab-text")!;
      return { rail: cs.getPropertyValue("--post-rail").trim(), position: cs.position, icon: getComputedStyle(tab).display, textPosition: getComputedStyle(text).position,
               sideways: document.documentElement.scrollWidth > innerWidth };
    }, RAIL);
    expect(info.rail).toBe("");
    expect(info.position).toBe("static");
    expect(info.icon).toBe("none");                                          // the tabs show their words
    expect(info.textPosition).toBe("static");
    expect(info.sideways).toBe(false);
    const bar = (await rect(page, RAIL))!, box = (await rect(page, BOX))!;
    expect(bar.b).toBeLessThanOrEqual(box.y + 1);                            // above the text, not beside it
    await context.close();
  });
}

test("the feed composer: the column stays under the sort bar while the text grows", async ({ browser }) => {
  const context = await browser.newContext({ ...USER, viewport: { width: 1440, height: 900 } });
  const page = await context.newPage();
  await open(page, "/");
  await page.click("#ic-title");
  await page.waitForTimeout(400);
  const rail = "#inline-composer .post-editor > .post-toolbar", box = "#inline-composer .post-editor > .input-group";
  const r0 = (await rect(page, rail))!, b0 = (await rect(page, box))!;
  expect(r0.w).toBeCloseTo(42, 0);
  expect(r0.x).toBeGreaterThanOrEqual(b0.r);
  expect(Math.abs(r0.y - b0.y)).toBeLessThanOrEqual(2);
  await typeLines(page, "#ic-body", 45);
  const sort = (await rect(page, ".row.sticky"))!, r1 = (await rect(page, rail))!, b1 = (await rect(page, box))!;
  expect(b1.h).toBeGreaterThan(b0.h * 2);
  expect(b1.y).toBeLessThan(sort.b);                                        // the box has scrolled up under the sort bar
  expect(r1.y).toBeGreaterThanOrEqual(sort.b + 8);                          // and the column is held clear of it
  await context.close();
});

// --- Create a post looks like the post it makes -----------------------------------------------------------------------------------------------

const FIXTURES = JSON.parse(readFileSync(".fixtures.json", "utf-8"));
const POSTED = `/post/${FIXTURES.long}`;

// the width of a box's text: its content box (the browser's own numbers, to the thousandth of a pixel)
const textWidth = (page: Page, selector: string) => page.evaluate((s) => {
  const e = document.querySelector(s)!, cs = getComputedStyle(e), r = e.getBoundingClientRect();
  return r.width - parseFloat(cs.paddingLeft) - parseFloat(cs.paddingRight) - parseFloat(cs.borderLeftWidth) - parseFloat(cs.borderRightWidth);
}, selector);

for (const folded of [false, true]) {
  for (const textSize of ["default", "large", "xlarge"]) {
    test(`the box's text is as wide as the posted text (${folded ? "sidebar folded" : "sidebar open"}, ${textSize} text)`, async ({ browser }) => {
      for (const width of [992, 1100, 1199, 1200, 1260, 1280, 1366, 1440, 1920]) {
        const context = await browser.newContext({ ...USER, viewport: { width, height: 900 } });
        const cookies = [];
        if (folded) cookies.push({ name: "sidebar_pref", value: "collapsed", domain: "localhost", path: "/" });
        if (textSize !== "default") cookies.push({ name: "text_size", value: textSize, domain: "localhost", path: "/" });
        if (cookies.length) await context.addCookies(cookies);
        const page = await context.newPage();
        await open(page, POSTED);
        const posted = await textWidth(page, "#post-body .post-text");
        await open(page, "/submit");
        const box = await textWidth(page, "#post-body");
        expect(Math.abs(box - posted), `${width}px: the box's text is ${box}px, the posted text ${posted}px`).toBeLessThan(0.05);
        expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth), `${width}px: nothing scrolls sideways`).toBe(true);
        await context.close();
      }
    });
  }
}

test("only Create a post (and the shared draft) mirror the posted text: the feed composer keeps its own width", async ({ browser }) => {
  const context = await browser.newContext({ ...USER, viewport: { width: 1440, height: 900 } });
  const page = await context.newPage();
  await open(page, "/submit");
  expect(await page.evaluate(() => document.querySelector(".submit-main-col")!.classList.contains("posted-width"))).toBe(true);
  await open(page, "/");
  expect(await page.evaluate(() => document.querySelector(".posted-width"))).toBeNull();          // the feed composer is not mirrored
  await context.close();
});

// --- Preview is an overlay that looks like the post (the owner, 2026-10-30) ----------------------------------------------------------------------
// From 992px both composers' Preview opens the post page's own card, built by the server at the post page's own width. It is held here to a real post
// page (the fixture post): the same boxes (relative to each card) and type for the vote column, the text block, the title and the text, the same wrapped
// lines of the title and the text, and the same height of the action row.

const REAL = `/post/${FIXTURES.thread}`;
const OVERLAY = "#postPreviewModal .post-preview-card";

const cardMetrics = (page: Page, cardSelector: string) => page.evaluate((selector) => {
  const card = document.querySelector(selector) as HTMLElement, origin = card.getBoundingClientRect();
  const part = (s: string) => {
    const e = card.querySelector(s);
    if (!e) return null;
    const r = e.getBoundingClientRect(), cs = getComputedStyle(e);
    return { x: r.x - origin.x, y: r.y - origin.y, w: r.width, h: r.height, size: cs.fontSize, weight: cs.fontWeight, family: cs.fontFamily, leading: cs.lineHeight, tracking: cs.letterSpacing };
  };
  const lines = (s: string) => {
    const root = card.querySelector(s)!, out: string[] = [], range = document.createRange();
    let last: number | null = null, current = "";
    const walker = document.createTreeWalker(root, NodeFilter.SHOW_TEXT);
    for (let n = walker.nextNode() as Text | null; n; n = walker.nextNode() as Text | null) {
      for (let i = 0; i < n.data.length; i++) {
        range.setStart(n, i); range.setEnd(n, i + 1);
        const rects = range.getClientRects();
        if (!rects.length) continue;
        const y = Math.round(rects[0].top);
        if (last !== null && Math.abs(y - last) > 4) { out.push(current.trim()); current = ""; }
        last = y; current += n.data[i];
      }
    }
    out.push(current.trim());
    return out.filter(Boolean);
  };
  return { card: origin.width, voting: part(".voting"), block: part(".card-block"), meta: part(".post-meta"), title: part("h1.post-title"), body: part(".post-body"),
           text: part(".post-text"), p: part(".post-text p"), actions: part(".post-actions"), titleLines: lines("h1.post-title"), textLines: lines(".post-text") };
}, cardSelector);

// what the fixture post says, to type the same into a composer
const realWords = (page: Page) => page.evaluate(() => ({
  title: document.querySelector("h1.post-title")!.textContent!.trim(),
  body: Array.from(document.querySelectorAll("#post-body .post-text p")).map((p) => p.textContent!.trim()).join("\n\n"),
}));

type Metrics = Awaited<ReturnType<typeof cardMetrics>>;
function sameCard(mine: Metrics, real: Metrics, where: string) {
  expect(Math.abs(mine.card - real.card), `${where}: the card is as wide as the post page's`).toBeLessThan(0.05);
  for (const key of ["voting", "block", "title", "body", "text", "p"] as const) {
    const a = mine[key]!, b = real[key]!;
    expect(a, `${where}: ${key} is drawn`).not.toBeNull();
    for (const edge of ["x", "w"] as const) expect(Math.abs(a[edge] - b[edge]), `${where}: ${key}.${edge} ${a[edge]} against ${b[edge]}`).toBeLessThan(0.05);
    expect([a.size, a.weight, a.family, a.leading, a.tracking], `${where}: the type of ${key}`).toEqual([b.size, b.weight, b.family, b.leading, b.tracking]);
  }
  // the same words in the same lines, one under the other at the same distances
  expect(mine.titleLines, `${where}: the title's lines`).toEqual(real.titleLines);
  expect(mine.textLines, `${where}: the text's lines`).toEqual(real.textLines);
  for (const key of ["title", "text", "actions"] as const) expect(Math.abs(mine[key]!.y - real[key]!.y), `${where}: ${key}.y ${mine[key]!.y} against ${real[key]!.y}`).toBeLessThan(0.6);
  expect(Math.abs(mine.actions!.h - real.actions!.h), `${where}: the action row's height`).toBeLessThan(0.6);
}

async function openOverlay(page: Page, composer: "feed" | "create") {
  const toolbar = composer === "feed" ? "#inline-composer .post-toolbar" : "#body-write > .post-toolbar";
  await page.click(`${toolbar} .post-tb-tab:not(.post-tb-tab-write)`);
  // the card, or what the server said was wrong: anything but the "Loading preview..." it opens with
  await page.waitForFunction(() => { const sheet = document.querySelector("#postPreviewModal.show .post-preview-sheet"); return !!sheet && !!sheet.textContent && !sheet.textContent.includes("Loading preview"); });
  await page.waitForTimeout(500);          // the fade
}

for (const composer of ["feed", "create"] as const) {
  for (const folded of [false, true]) {
    test(`the ${composer === "feed" ? "feed composer's" : "Create a post's"} Preview is the post page's card (${folded ? "sidebar folded" : "sidebar open"})`, async ({ browser }) => {
      for (const width of [1100, 1280, 1440, 1920]) {
        const context = await browser.newContext({ ...USER, viewport: { width, height: 900 } });
        if (folded) await context.addCookies([{ name: "sidebar_pref", value: "collapsed", domain: "localhost", path: "/" }]);
        const page = await context.newPage();
        await open(page, REAL);
        const words = await realWords(page);
        const real = await cardMetrics(page, `#post-${FIXTURES.thread}`);
        if (composer === "feed") {
          await open(page, "/");
          await page.click("#ic-title");
          await page.fill("#ic-title", words.title);
          await page.fill("#ic-body", words.body);
        } else {
          await open(page, "/submit");
          await page.fill("#post-title", words.title);
          await page.fill("#post-body", words.body);
        }
        await openOverlay(page, composer);
        sameCard(await cardMetrics(page, OVERLAY), real, `${composer} at ${width}px`);
        await context.close();
      }
    });
  }
}

test("the overlay closes three ways, hands the keyboard back to the text and leaves the composer where it was", async ({ browser }) => {
  for (const composer of ["feed", "create"] as const) {
    const context = await browser.newContext({ ...USER, viewport: { width: 1280, height: 900 } });
    const page = await context.newPage();
    await open(page, composer === "feed" ? "/" : "/submit");
    const text = composer === "feed" ? "#ic-body" : "#post-body";
    if (composer === "feed") { await page.click("#ic-title"); await page.fill("#ic-title", "A title"); } else { await page.fill("#post-title", "A title"); }
    await page.fill(text, "Some words");
    const before = await page.evaluate((s) => { const r = document.querySelector(s)!.getBoundingClientRect(); return [r.x, r.y, r.width, r.height]; }, text);
    // one Preview button, no Write
    const toolbar = composer === "feed" ? "#inline-composer .post-toolbar" : "#body-write > .post-toolbar";
    const labels = await page.evaluate((s) => Array.from(document.querySelectorAll(`${s} .post-tb-btn`)).filter((e) => e.getBoundingClientRect().width > 0).map((e) => e.getAttribute("aria-label")), toolbar);
    expect(labels.filter((l) => l === "Preview")).toHaveLength(1);
    expect(labels).not.toContain("Write");
    for (const how of ["Escape", "close", "backdrop"]) {
      await openOverlay(page, composer);
      expect(await page.evaluate(() => document.querySelector("#postPreviewModal .modal-title")!.textContent)).toBe("Preview");
      if (how === "Escape") await page.keyboard.press("Escape");
      else if (how === "close") await page.click("#postPreviewModal .close");
      else await page.mouse.click(5, 450);
      await page.waitForSelector("#postPreviewModal:not(.show)", { state: "attached" });
      await page.waitForTimeout(400);
      expect(await page.evaluate(() => document.activeElement && document.activeElement.id), `${composer}: focus is back in the text after ${how}`).toBe(text.slice(1));
      expect(await page.evaluate((s) => { const r = document.querySelector(s)!.getBoundingClientRect(); return [r.x, r.y, r.width, r.height]; }, text)).toEqual(before);
    }
    await context.close();
  }
});

test("a title or link a post would refuse is told in the overlay, and below 992px Preview is still the in-place toggle", async ({ browser }) => {
  const context = await browser.newContext({ ...USER, viewport: { width: 1280, height: 900 } });
  const page = await context.newPage();
  await open(page, "/submit");
  await page.fill("#post-title", "A title");
  await page.fill("#post-body", "Some words");
  await page.evaluate(() => { (document.getElementById("post-URL") as HTMLInputElement).value = "not a link"; });     // the link field a post would refuse
  await openOverlay(page, "create");
  await expect(page.locator("#postPreviewModal .post-preview-sheet")).toContainText("valid link");
  await expect(page.locator("#postPreviewModal .post-preview-card")).toHaveCount(0);
  await page.keyboard.press("Escape");
  await context.close();

  const narrow = await browser.newContext({ ...USER, viewport: { width: 800, height: 900 } });
  const small = await narrow.newPage();
  await open(small, "/submit");
  await small.fill("#post-body", "Some words");
  await small.click("#body-write > .post-toolbar .post-tb-tab:not(.active)");
  await small.waitForSelector("#body-write > .post-preview:not(.d-none) .post-preview-text p");
  expect(await small.evaluate(() => document.getElementById("postPreviewModal"))).toBeNull();     // no overlay was even made
  expect(await small.evaluate(() => getComputedStyle(document.querySelector("#body-write > .post-toolbar .post-tb-tab-write")!).display)).not.toBe("none");
  await narrow.close();
});
