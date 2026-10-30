import { test, expect, type Page } from "@playwright/test";
import { THEMES, open } from "./pages";

// The light language (CLAUDE.md "The light language"): light is only added along edges and at corners and never over running text, so
// text never loses contrast, and buttons are flat (the owner, 2026-10-29: "remove the shine on buttons"). axe cannot see an overlay, so
// this measures real pixels: a surface is drawn with its text hidden and every pixel inside its text zone is compared with the text's colour.
//   npm run light
// What it holds the site to: a message you sent (running white text) stays AA at every point of the scroll-linked shade it carries, a
// primary button is a flat fill whose hover and press only darken it (the owner's exception: a short white label on the bright fill), the
// reduced-motion / forced-colours / more-contrast settings really switch the effects off, and nothing here animates on its own.

const BUBBLE = `
<div id="light-harness" style="padding:24px;width:360px;background:var(--bg)">
  <div class="chat-msg d-flex justify-content-end mb-2"><div class="px-3 py-2 rounded chat-bubble bg-primary text-white" id="own" style="color:transparent !important;width:260px">Sample text zone sample text</div></div>
  <div class="chat-msg d-flex justify-content-start mb-2"><div class="px-3 py-2 rounded chat-bubble bg-light" id="theirs">Did you see the new feed?</div></div>
  <button type="button" class="btn btn-primary" id="primary" style="color:transparent !important;width:200px;height:40px">Sign Up</button>
</div>`;

async function put(page: Page) {
  await open(page, "/");
  await page.evaluate((m) => { document.body.innerHTML = m; }, BUBBLE);
}

// a thread: the real scroller's classes and many of your bubbles (text hidden, so every pixel is the fill) among theirs
const THREAD = (n: number) => `<div style="position:fixed;inset:0;background:var(--bg)">
  <div id="chat-messages" class="overflow-auto px-3 py-2" style="height:560px">` +
  Array.from({ length: n }, (_, i) => `<div class="d-flex mb-2 chat-msg justify-content-end"><div class="px-3 py-2 rounded chat-bubble bg-primary text-white" id="own${i}" style="color:transparent !important;width:260px">Message ${i} from me</div></div>` +
    `<div class="d-flex mb-2 chat-msg justify-content-start"><div class="px-3 py-2 rounded chat-bubble bg-light" id="their${i}">Their reply ${i}</div></div>`).join("") + `</div></div>`;

async function putThread(page: Page) {
  await open(page, "/");
  await page.evaluate((m) => { document.body.innerHTML = m; }, THREAD(40));
  await page.waitForTimeout(200);
}

// your bubble that sits wholly inside the thread, nearest to `fraction` of its height from the top; returns where it is on the page
async function bubbleNear(page: Page, fraction: number) {
  return page.evaluate((f) => {
    const box = document.getElementById("chat-messages")!.getBoundingClientRect();
    const target = box.top + box.height * f;
    let best: { id: string; x: number; y: number; width: number; height: number; d: number } | null = null;
    for (const el of Array.from(document.querySelectorAll(".chat-bubble.bg-primary"))) {
      const r = el.getBoundingClientRect();
      if (r.top < box.top || r.bottom > box.bottom || r.bottom > innerHeight) continue;
      const d = Math.abs(r.top + r.height / 2 - target);
      if (!best || d < best.d) best = { id: el.id, x: r.x, y: r.y, width: r.width, height: r.height, d };
    }
    return best!;
  }, fraction);
}

async function contrastOfBubble(page: Page, fraction: number): Promise<number> {
  const r = await bubbleNear(page, fraction);
  const png = (await page.screenshot({ clip: { x: r.x, y: r.y, width: r.width, height: r.height } })).toString("base64");
  return lowestWhiteContrastOf(page, png, 14, 8);
}

// the lowest contrast of white against any pixel of an element's label zone (its padding box less `ix` x `iy`)
async function lowestWhiteContrast(page: Page, selector: string, ix: number, iy: number): Promise<number> {
  return lowestWhiteContrastOf(page, (await page.locator(selector).screenshot()).toString("base64"), ix, iy);
}

async function lowestWhiteContrastOf(page: Page, png: string, ix: number, iy: number): Promise<number> {
  return page.evaluate(async ({ png, ix, iy }) => {
    const img = new Image();
    img.src = `data:image/png;base64,${png}`;
    await img.decode();
    const canvas = document.createElement("canvas");
    canvas.width = img.width; canvas.height = img.height;
    const ctx = canvas.getContext("2d")!;
    ctx.drawImage(img, 0, 0);
    const { data, width, height } = ctx.getImageData(0, 0, img.width, img.height);
    const lin = (c: number) => { const v = c / 255; return v <= 0.03928 ? v / 12.92 : ((v + 0.055) / 1.055) ** 2.4; };
    let worst = Infinity;
    for (let y = iy; y < height - iy; y++) {
      for (let x = ix; x < width - ix; x++) {
        const i = (y * width + x) * 4;
        const l = 0.2126 * lin(data[i]) + 0.7152 * lin(data[i + 1]) + 0.0722 * lin(data[i + 2]);
        worst = Math.min(worst, 1.05 / (l + 0.05));
      }
    }
    return worst;
  }, { png, ix, iy });
}

for (const theme of THEMES) {
  test(`a message you sent keeps AA behind its text ${theme}`, async ({ browser }) => {
    const context = await browser.newContext({ colorScheme: theme, deviceScaleFactor: 1 });
    const page = await context.newPage();
    await put(page);
    // the corners are rounded: the text zone is inside 14px x 8px
    const worst = await lowestWhiteContrast(page, "#own", 14, 8);
    expect(worst, `white text over the bubble, ${theme}`).toBeGreaterThanOrEqual(4.5);
    await context.close();
  });

  test(`a primary button is a flat fill ${theme}`, async ({ browser }) => {
    const context = await browser.newContext({ colorScheme: theme, deviceScaleFactor: 1 });
    const page = await context.newPage();
    await put(page);
    const style = await page.evaluate(() => {
      const el = document.getElementById("primary")!;
      const s = getComputedStyle(el), before = getComputedStyle(el, "::before"), after = getComputedStyle(el, "::after");
      return { image: s.backgroundImage, overflow: s.overflow, before: before.content, after: after.content, anims: el.getAnimations({ subtree: true }).length };
    });
    expect(style.image).toBe("none");                                  // no sheen, no depth, no rim
    expect(style.overflow).toBe("visible");                            // nothing to clip: a flex row can no longer squeeze it under its label
    expect(["none", "normal"]).toContain(style.before);                // no glint layer
    expect(["none", "normal"]).toContain(style.after);
    expect(style.anims).toBe(0);
    // the same colour all over (a label sits on the owner's bright fill, 2.62:1): the lowest pixel is the fill
    const flat = await lowestWhiteContrast(page, "#primary", 22, 4);
    expect(flat).toBeGreaterThanOrEqual(2.6);
    await context.close();
  });

  test(`your message's shade follows the scroll and keeps AA at every point ${theme}`, async ({ browser }) => {
    const context = await browser.newContext({ colorScheme: theme, deviceScaleFactor: 1, viewport: { width: 700, height: 700 } });
    const page = await context.newPage();
    await putThread(page);
    expect(await page.evaluate(() => CSS.supports("animation-timeline: view()"))).toBe(true);
    for (const top of [0, 640, 1500]) {
      await page.evaluate((t) => { document.getElementById("chat-messages")!.scrollTop = t; }, top);
      await page.waitForTimeout(150);
      const high = await contrastOfBubble(page, 0.12), middle = await contrastOfBubble(page, 0.5), low = await contrastOfBubble(page, 0.88);
      for (const [where, value] of [["top", high], ["middle", middle], ["bottom", low]] as const) {
        expect(value, `white text on your bubble at the ${where} of the thread, scrolled to ${top}, ${theme}`).toBeGreaterThanOrEqual(4.5);
      }
      // darker toward the bottom (10% black takes 4.63:1 to 5.5:1), and it is the same at the same place however far it has scrolled
      expect(low).toBeGreaterThan(high + 0.4);
      expect(low).toBeGreaterThan(middle);
      expect(middle).toBeGreaterThan(high);
    }
    await context.close();
  });
}

test("a new message glints once, and nothing else on the page moves by itself", async ({ browser }) => {
  const context = await browser.newContext({ colorScheme: "light" });
  const page = await context.newPage();
  await put(page);
  // (the shade of your own bubble is a scroll-driven animation: it has no clock and moves only when the thread does)
  const before = await page.evaluate(() => document.getAnimations().filter((a) => /light-(?!ramp)/.test((a as CSSAnimation).animationName || "")).length);
  expect(before).toBe(0);                                              // nothing runs on load
  await page.evaluate(() => document.getElementById("theirs")!.classList.add("is-new"));
  await page.waitForTimeout(30);
  const running = await page.evaluate(() => document.getAnimations().filter((a) => /light-glint/.test((a as CSSAnimation).animationName || "")).length);
  expect(running).toBe(1);
  await page.waitForTimeout(500);
  const after = await page.evaluate(() => document.getAnimations().filter((a) => /light-glint/.test((a as CSSAnimation).animationName || "")).length);
  expect(after).toBe(0);                                               // it played once and stopped
  await context.close();
});

test("reduced motion stops the glint, forced colours drop the effects, more contrast turns the dial to 0", async ({ browser }) => {
  const reduced = await browser.newContext({ reducedMotion: "reduce" });
  let page = await reduced.newPage();
  await put(page);
  await page.evaluate(() => document.getElementById("own")!.classList.add("is-new"));
  await page.waitForTimeout(30);
  expect(await page.evaluate(() => document.getAnimations().filter((a) => /light-/.test((a as CSSAnimation).animationName || "")).length)).toBe(0);
  await reduced.close();

  // and nothing follows the scroll for someone who asked for less motion: every bubble is the same flat blue wherever it sits
  const calm = await browser.newContext({ reducedMotion: "reduce", viewport: { width: 700, height: 700 }, deviceScaleFactor: 1 });
  page = await calm.newPage();
  await putThread(page);
  expect(await page.evaluate(() => getComputedStyle(document.getElementById("own3")!, "::before").content)).toMatch(/^(none|normal)$/);
  const high = await contrastOfBubble(page, 0.12), low = await contrastOfBubble(page, 0.88);
  expect(Math.abs(low - high)).toBeLessThan(0.02);
  await calm.close();

  const forced = await browser.newContext({ forcedColors: "active" });
  page = await forced.newPage();
  await put(page);
  const f = await page.evaluate(() => { const s = getComputedStyle(document.getElementById("own")!); return { img: s.backgroundImage, shadow: s.boxShadow, border: s.borderTopWidth }; });
  expect(f.img).toMatch(/^none(, none)*$/); expect(f.shadow).toBe("none");   // (one `none` per layer: the browser draws no gradient and no shadow) expect(f.border).not.toBe("0px");
  await forced.close();

  const more = await browser.newContext({ contrast: "more" });
  page = await more.newPage();
  await put(page);
  expect((await page.evaluate(() => getComputedStyle(document.documentElement).getPropertyValue("--light-k"))).trim()).toBe("0");
  await more.close();
});

// ---- the real surfaces (phases 2 to 5) ---------------------------------------------------------------------------------

test("hovering and pressing a real primary button only darken it: no animation, no sweep, no glow", async ({ browser }) => {
  const context = await browser.newContext({ colorScheme: "light", viewport: { width: 1280, height: 800 } });
  const page = await context.newPage();
  await open(page, "/");
  const button = page.locator(".btn-primary, .btn-purple").first();
  const box = (await button.boundingBox())!;
  const handle = (await button.elementHandle())!;
  const read = () => page.evaluate((el) => {
    const s = getComputedStyle(el as Element), c = s.backgroundColor.match(/[\d.]+/g)!.map(Number);
    return { luma: 0.2126 * c[0] + 0.7152 * c[1] + 0.0722 * c[2], image: s.backgroundImage, shadow: s.boxShadow, overflow: s.overflow,
             lit: document.getAnimations().filter((a) => /light-/.test((a as CSSAnimation).animationName || "")).length };
  }, handle);
  await page.mouse.move(box.x + box.width + 40, box.y + box.height + 40);
  await page.waitForTimeout(300);
  const rest = await read();
  await page.mouse.move(box.x + box.width / 2, box.y + box.height / 2);
  await page.waitForTimeout(40);
  expect((await read()).lit).toBe(0);                                  // nothing sweeps across it, then or ever
  await page.waitForTimeout(400);
  const hover = await read();
  expect(hover.luma).toBeLessThan(rest.luma);                          // darker, as TikTok, Instagram, Discord and Mastodon do
  expect(hover.image).toBe("none");
  expect(hover.overflow).toBe("visible");
  // a mouse press focuses the button too: it is deeper still, and it is still only a colour
  await page.mouse.down();
  await page.waitForTimeout(250);
  const pressed = await read();
  await page.mouse.up();
  expect(pressed.luma).toBeLessThan(hover.luma);
  expect(pressed.image).toBe("none");
  expect(pressed.shadow).not.toContain("inset");
  expect(pressed.lit).toBe(0);
  await context.close();
});

test("under forced colours a selected chip and a selected tab still differ from an unselected one", async ({ browser }) => {
  const context = await browser.newContext({ forcedColors: "active", viewport: { width: 1280, height: 800 } });
  const page = await context.newPage();
  await open(page, "/browse");
  const chips = await page.evaluate(() => {
    const style = (el: Element) => { const s = getComputedStyle(el); return { outline: s.outlineStyle + " " + s.outlineWidth, weight: s.fontWeight }; };
    const active = document.querySelector(".explore-chip.active")!, plain = document.querySelector(".explore-chip:not(.active)")!;
    const tabActive = document.querySelector(".explore-tab.active")!, tabPlain = document.querySelector(".explore-tab:not(.active)")!;
    return { active: style(active), plain: style(plain), tabActive: getComputedStyle(tabActive).borderBottomColor, tabPlain: getComputedStyle(tabPlain).borderBottomColor };
  });
  expect(chips.active.outline).not.toBe(chips.plain.outline);
  expect(chips.tabActive).not.toBe(chips.tabPlain);
  await context.close();
});

test("the account menu and a '...' menu catch the top edge on a dark page, and a feed card gets nothing at rest", async ({ browser }) => {
  const context = await browser.newContext({ colorScheme: "dark", storageState: ".auth/viewer.json", viewport: { width: 1280, height: 800 } });
  const page = await context.newPage();
  await open(page, "/all");
  await page.locator(".post-actions").first().waitFor();
  await page.locator("#accountDropdownMenu").locator("xpath=preceding-sibling::a[1]").click();
  await page.waitForTimeout(250);
  const menu = await page.evaluate(() => getComputedStyle(document.querySelector(".dropdown-menu.show")!).boxShadow);
  expect(menu).toMatch(/inset/);                                         // the edge, in front of the menu's own ring and shadow
  const card = await page.evaluate(() => { const c = document.querySelector(".post-actions")!.closest(".card")!; const s = getComputedStyle(c); return { shadow: s.boxShadow, image: s.backgroundImage, overflow: s.overflow }; });
  expect(card.shadow).toBe("none");
  expect(card.image).toBe("none");
  await context.close();
});
