import { test, expect, type Page, type BrowserContext } from "@playwright/test";
import { THEMES, open } from "./pages";

// Glass (CLAUDE.md "Glass"): what floats above the content is frosted, the content never is. axe cannot see a blur, so this measures it in a
// real browser:
//   npm run glass
// What it holds the site to: the blur is REAL (no ancestor of a glass card has opacity, filter or a backdrop-filter of its own, at the start of
// an open as well as at rest: Bootstrap's own fade would break it), it is gone under reduced transparency, more contrast and forced colours
// (the tint alone is then opaque), a feed card's "..." menu is still on top of the next card, and the owner's example (Block) asks in the
// frosted sheet, makes no request until it is told to, can be undone, and leaves no dialog, backdrop or scroll lock behind.

const TARGET = "ui_newcomer";                                                    // an account in the fixtures the viewer may block and unblock

// an element's ancestors that would stop its backdrop-filter from seeing the page
function backdropRoots(selector: string) {
  const el = document.querySelector(selector)!;
  const out: string[] = [];
  for (let e = el.parentElement; e; e = e.parentElement) {
    const s = getComputedStyle(e), why: string[] = [];
    if (+s.opacity < 1) why.push(`opacity ${(+s.opacity).toFixed(2)}`);
    if (s.filter !== "none") why.push("filter");
    if (s.backdropFilter !== "none") why.push("backdrop-filter");
    if (s.mixBlendMode !== "normal") why.push("mix-blend-mode");
    if (s.maskImage !== "none") why.push("mask");
    if (s.clipPath !== "none") why.push("clip-path");
    if (/opacity|filter|mask|backdrop/.test(s.willChange)) why.push(`will-change ${s.willChange}`);
    if (why.length) out.push(`${e.tagName.toLowerCase()}.${e.className.toString().slice(0, 30)} [${why.join(", ")}]`);
  }
  return out;
}

// the luma range of the pixels along a horizontal line through the middle of a clip: about 250 where black and white stripes show through
// sharp, a few where the blur has averaged them away
async function lumaRange(page: Page, clip: { x: number; y: number; width: number; height: number }): Promise<number> {
  const png = (await page.screenshot({ clip })).toString("base64");
  return page.evaluate(async (png) => {
    const img = new Image();
    img.src = `data:image/png;base64,${png}`;
    await img.decode();
    const c = document.createElement("canvas");
    c.width = img.width; c.height = img.height;
    const ctx = c.getContext("2d")!;
    ctx.drawImage(img, 0, 0);
    const row = ctx.getImageData(0, Math.floor(img.height / 2), img.width, 1).data;
    let lo = 255, hi = 0;
    for (let i = 0; i < row.length; i += 4) { const l = 0.2126 * row[i] + 0.7152 * row[i + 1] + 0.0722 * row[i + 2]; lo = Math.min(lo, l); hi = Math.max(hi, l); }
    return hi - lo;
  }, png);
}

async function reducedTransparency(context: BrowserContext, page: Page) {
  // Playwright has no option for it: the DevTools protocol does
  const cdp = await context.newCDPSession(page);
  await cdp.send("Emulation.setEmulatedMedia", { features: [{ name: "prefers-reduced-transparency", value: "reduce" }] });
}

const surfaces = (page: Page) => page.evaluate(() => {
  const read = (sel: string, pseudo?: string) => { const el = document.querySelector(sel); if (!el) return null; const s = getComputedStyle(el, pseudo); return { bf: s.backdropFilter, bg: s.backgroundColor, display: s.display }; };
  return { card: read("#confirmSheet .modal-content"), scrim: read(".modal-backdrop"), menu: read(".dropdown-menu.show"), toast: read("#toast-undo"), navbar: read("#navbar", "::before"), navbarSelf: read("#navbar") };
});

for (const theme of THEMES) {
  test(`a dialog's glass card is blurred from the first frame to the last, and the page behind is blurred and dimmed ${theme}`, async ({ browser }) => {
    const context = await browser.newContext({ colorScheme: theme, storageState: ".auth/viewer.json", viewport: { width: 1000, height: 700 } });
    const page = await context.newPage();
    await open(page, "/all");
    // (not awaited: the promise settles only when the sheet closes)
    await page.evaluate(() => { void window.confirmSheet({ title: "Block @someone?", text: "Text.", ok: "Block" }); });
    const seen: string[][] = [];
    for (const wait of [30, 60, 90, 140, 300]) {
      await page.waitForTimeout(wait);
      seen.push(await page.evaluate(backdropRoots, "#confirmSheet .modal-content"));
    }
    expect(seen.flat(), "an ancestor of the card that would stop its blur").toEqual([]);
    const now = await surfaces(page);
    expect(now.card!.bf).toMatch(/blur\(20px\)/);
    expect(now.scrim!.bf).toMatch(/blur\(6px\)/);
    // the real thing: with the tint taken away and the scrim's own blur off, 40px black and white stripes behind the card must come out averaged
    // (the stripes are a fixed layer above the page and below the scrim (z-index 1040): the page's own opaque backgrounds would hide any on the body)
    await page.evaluate(() => {
      const d = document.createElement("div");
      d.style.cssText = "position:fixed;inset:0;z-index:1035;background:repeating-linear-gradient(90deg,#000 0 20px,#fff 20px 40px)";
      document.body.appendChild(d);
    });
    await page.addStyleTag({ content: ":root { --glass-bg: transparent !important; --glass-fx-scrim: none !important; } .modal-backdrop.show { background-color: transparent !important; } #confirmSheet .modal-content, #confirmSheet .modal-content * { color: transparent !important; box-shadow: none !important; background: transparent !important; }" });
    await page.waitForTimeout(250);
    const box = (await page.locator("#confirmSheet .modal-content").boundingBox())!;
    // (a thin strip in the card's top padding: only what shows through the card is in it)
    const blurred = await lumaRange(page, { x: box.x + 40, y: box.y + 8, width: box.width - 80, height: 4 });
    expect(blurred, "stripes behind the card are blurred away").toBeLessThan(80);
    await page.addStyleTag({ content: "#confirmSheet .modal-content { backdrop-filter: none !important; -webkit-backdrop-filter: none !important; }" });
    await page.waitForTimeout(100);
    const sharp = await lumaRange(page, { x: box.x + 40, y: box.y + 8, width: box.width - 80, height: 4 });
    expect(sharp, "with the blur taken off the stripes show (the measurement can tell the difference)").toBeGreaterThan(180);
    await context.close();
  });
}

test("a feed card's menu is glass, on top of the next card, and no card is a backdrop root; the account menu is glass too", async ({ browser }) => {
  const context = await browser.newContext({ colorScheme: "light", storageState: ".auth/viewer.json", viewport: { width: 1200, height: 900 } });
  const page = await context.newPage();
  await open(page, "/all");
  await page.locator("a[id^=dropdownMoreLink-]").first().click();
  await page.waitForTimeout(450);
  const menu = await page.evaluate(() => {
    const m = document.querySelector(".posts .dropdown-menu.show") as HTMLElement, r = m.getBoundingClientRect();
    const mid = document.elementFromPoint(r.x + r.width / 2, r.y + r.height / 2);
    const cards = Array.from(document.querySelectorAll(".posts > .card")).map((c) => { const s = getComputedStyle(c); return s.filter !== "none" || s.backdropFilter !== "none" || s.transform !== "none" || /paint|layout|strict|content/.test(s.contain) || s.mask !== "none" && s.mask !== "" && !/^none/.test(s.mask); });
    return { bf: getComputedStyle(m).backdropFilter, topmost: m.contains(mid), badCards: cards.filter(Boolean).length };
  });
  expect(menu.bf).toMatch(/blur\(20px\)/);
  expect(menu.topmost).toBe(true);
  expect(menu.badCards).toBe(0);
  expect(await page.evaluate(backdropRoots, ".posts .dropdown-menu.show")).toEqual([]);
  await page.keyboard.press("Escape");
  await page.mouse.click(5, 450);
  // nothing at rest inside the feed carries a backdrop-filter (a blur per card makes a long feed stutter)
  const inFeed = await page.evaluate(() => Array.from(document.querySelectorAll(".posts *")).filter((e) => getComputedStyle(e).backdropFilter !== "none" && getComputedStyle(e).display !== "none" && !e.closest(".dropdown-menu, .modal")).length);
  expect(inFeed).toBe(0);
  // the account menu sits inside the glass bar: its blur sees the PAGE, since the bar's own blur is on a layer behind it
  await page.locator("#accountDropdownMenu").locator("xpath=preceding-sibling::a[1]").click();
  await page.waitForTimeout(450);
  expect(await page.evaluate(backdropRoots, "#accountDropdownMenu")).toEqual([]);
  expect((await surfaces(page)).navbarSelf!.bf).toBe("none");
  await context.close();
});

test("under reduced transparency, more contrast and forced colours every glass surface is solid and unblurred", async ({ browser }) => {
  for (const mode of ["transparency", "contrast", "forced"] as const) {
    const options = mode === "contrast" ? { contrast: "more" as const } : mode === "forced" ? { forcedColors: "active" as const } : {};
    const context = await browser.newContext({ colorScheme: "light", storageState: ".auth/viewer.json", viewport: { width: 1000, height: 700 }, ...options });
    const page = await context.newPage();
    await open(page, "/all");
    if (mode === "transparency") await reducedTransparency(context, page);
    await page.evaluate(() => { void window.confirmSheet({ title: "Sure?", text: "Text.", ok: "Yes" }); });
    await page.waitForTimeout(500);
    const s = await surfaces(page);
    const alpha = (c: string) => { const m = c.match(/rgba?\(([^)]+)\)/); const p = m![1].split(",").map(Number); return p.length > 3 ? p[3] : 1; };
    expect(s.card!.bf, mode).toBe("none");
    expect(s.scrim!.bf, mode).toBe("none");
    expect(alpha(s.card!.bg), mode).toBe(1);
    expect(s.navbar!.display === "none" || s.navbar!.bf === "none", mode).toBe(true);
    expect(alpha(s.navbarSelf!.bg), mode).toBe(1);
    await context.close();
  }
});

test("the sensitive-content cover is a button the keyboard can use, and the covered content is inert until it is revealed", async ({ browser }) => {
  const context = await browser.newContext({ colorScheme: "light", storageState: ".auth/viewer.json", viewport: { width: 900, height: 700 } });
  const page = await context.newPage();
  await open(page, "/");
  await page.evaluate(() => {
    document.body.innerHTML = `<div class="position-relative sensitive-content-wrap" id="sensitive-media-x" style="width:300px;height:200px">
      <div class="sensitive-blur" inert><a href="#x" id="inner-link">hidden link</a></div>
      <div class="sensitive-overlay" role="button" tabindex="0" onclick="revealSensitive('sensitive-media-x')"
        onkeydown="if (event.key === 'Enter' || event.key === ' ') { event.preventDefault(); revealSensitive('sensitive-media-x'); }"><span class="btn btn-light">View anyway</span></div></div>`;
  });
  expect(await page.evaluate(() => getComputedStyle(document.querySelector(".sensitive-overlay")!).backgroundColor)).toBe("rgba(0, 0, 0, 0.6)");
  expect(await page.evaluate(() => getComputedStyle(document.querySelector(".sensitive-overlay")!).backdropFilter)).toBe("none");     // pixels are blurred, not the backdrop
  await page.focus(".sensitive-overlay");
  // the keyboard's ring is visible and INSIDE the cover (the global ring sits outside it and is clipped by the wrapper)
  const ring = await page.evaluate(() => { const s = getComputedStyle(document.querySelector(".sensitive-overlay")!); return { style: s.outlineStyle, width: s.outlineWidth, offset: s.outlineOffset, colour: s.outlineColor }; });
  expect(ring).toEqual({ style: "solid", width: "2px", offset: "-4px", colour: "rgb(255, 255, 255)" });
  await page.keyboard.press("Enter");
  await page.waitForTimeout(100);
  expect(await page.evaluate(() => ({ revealed: document.getElementById("sensitive-media-x")!.classList.contains("revealed"), inert: document.querySelector(".sensitive-blur")!.hasAttribute("inert") }))).toEqual({ revealed: true, inert: false });
  await context.close();
});

test("Block asks in the sheet first, makes no request until told to, can be undone, and leaves nothing stuck behind", async ({ browser }) => {
  const context = await browser.newContext({ colorScheme: "light", storageState: ".auth/viewer.json", viewport: { width: 1200, height: 800 } });
  const page = await context.newPage();
  const requests: string[] = [];
  page.on("request", (r) => { if (/\/settings\/(un)?block/.test(r.url())) requests.push(`${r.method()} ${new URL(r.url()).pathname}`); });
  const clean = () => page.evaluate(() => ({ shown: document.querySelectorAll(".modal.show").length, backdrops: document.querySelectorAll(".modal-backdrop").length, locked: document.body.classList.contains("modal-open") }));
  try {
    await open(page, "/all");
    const card = page.locator(`.card:has(a[href*="${TARGET}"])`).first();
    const ask = async () => {
      await card.locator("a[id^=dropdownMoreLink-]").first().click();
      await page.waitForTimeout(350);
      await card.locator('a[id^="block-user-"]:not(.d-none)').first().click();
      await page.waitForTimeout(700);
    };
    await ask();
    expect(await page.evaluate(() => document.getElementById("confirm-sheet-title")!.textContent)).toBe(`Block @${TARGET}?`);
    expect(requests).toEqual([]);                                              // asking is not blocking
    await page.locator("#confirmSheet .btn-secondary").click();
    await page.waitForTimeout(600);
    expect(requests).toEqual([]);
    expect(await clean()).toEqual({ shown: 0, backdrops: 0, locked: false });   // Cancel leaves nothing behind
    await ask();
    await page.locator("#confirm-sheet-ok").click();
    await page.waitForTimeout(900);
    expect(requests).toEqual(["POST /settings/block"]);
    expect(await page.evaluate(() => ({ shown: document.getElementById("toast-undo")!.classList.contains("show"), text: document.getElementById("toast-undo-text")!.textContent }))).toEqual({ shown: true, text: `Blocked @${TARGET}.` });
    const reload = page.waitForNavigation({ waitUntil: "load" });
    await page.locator("#toast-undo-button").click();
    await reload;
    expect(requests).toEqual(["POST /settings/block", "POST /settings/unblock"]);
  } finally {
    // never leave the fixture blocked, whatever failed above
    await page.goto(`/@${TARGET}`, { waitUntil: "load" });
    if (await page.locator("#unblock-button").count()) { const done = page.waitForNavigation({ waitUntil: "load" }); await page.locator("#unblock-button").click(); await done; }
    await context.close();
  }
});

test("on a phone the confirm sheet opens over the post's actions sheet and nothing is stuck after Cancel", async ({ browser }) => {
  const context = await browser.newContext({ colorScheme: "light", storageState: ".auth/viewer.json", viewport: { width: 390, height: 780 }, hasTouch: true, isMobile: true });
  const page = await context.newPage();
  const requests: string[] = [];
  page.on("request", (r) => { if (/\/settings\/(un)?block/.test(r.url())) requests.push(r.method()); });
  await open(page, "/all");
  const id = await page.evaluate((t) => { const card = Array.from(document.querySelectorAll(".card")).find((c) => c.querySelector(`a[href*="${t}"]`)); const m = card && card.querySelector('[id^="actionsModal-"]'); return m && m.id; }, TARGET);
  expect(id).toBeTruthy();
  await page.evaluate((id) => window.jQuery("#" + id).modal("show"), id!);
  await page.waitForTimeout(700);
  await page.locator(`#${id} button[id^="block-user-button-"]:not(.d-none)`).first().click();
  await page.waitForTimeout(1200);
  expect(await page.evaluate(() => ({ confirm: document.getElementById("confirmSheet")!.classList.contains("show"), backdrops: document.querySelectorAll(".modal-backdrop").length }))).toEqual({ confirm: true, backdrops: 1 });
  expect(await page.evaluate(backdropRoots, "#confirmSheet .modal-content")).toEqual([]);
  await page.locator("#confirmSheet .btn-secondary").click();
  await page.waitForTimeout(700);
  expect(await page.evaluate(() => ({ open: document.querySelectorAll(".modal.show").length, backdrops: document.querySelectorAll(".modal-backdrop").length, locked: document.body.classList.contains("modal-open") }))).toEqual({ open: 0, backdrops: 0, locked: false });
  expect(requests).toEqual([]);
  await context.close();
});

declare global { interface Window { jQuery: any; confirmSheet: (o: { title?: string; text?: string; ok?: string; icon?: string }) => Promise<boolean>; } }
