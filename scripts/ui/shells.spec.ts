import { test, expect, type Page } from "@playwright/test";
import { open } from "./pages";

// One look across every page shell (the owner, 2026-10-29: "make sure that all of the aesthetic changes have been made consistently across all pages of
// the site as I've noticed for some pages they haven't"). Measured in a real browser, one page per shell:
//   npm run shells
//   main      default.html        /all                       (member)
//   settings  header.html         /settings/profile, /submit, a guild's mod page   (member)
//   help      docs_default.html   /help/faq, /help/terms
//   about     help/about.html     /help/about, /help/donate  (light only, no member bar)
// What they must share: the 56px fixed bar with the ɸ mark (the site's name is its accessible name, never written out), the glass layer behind it,
// 36px targets, the one font, flat buttons, and no sideways scroll, on a phone too. Member bars also share the icon row: 36px targets on a 36px pitch.

const USER = { storageState: ".auth/viewer.json" };

const SHELLS: { name: string; path: string; member: boolean }[] = [
  { name: "main", path: "/all", member: true },
  { name: "settings", path: "/settings/profile", member: true },
  { name: "create post", path: "/submit", member: true },
  { name: "guild mod", path: "/+ui_photography/mod/mods", member: true },
  { name: "help", path: "/help/faq", member: false },
  { name: "legal", path: "/help/terms", member: false },
  { name: "about", path: "/help/about", member: false },
  { name: "donate", path: "/help/donate", member: false },
];

const measure = (page: Page) => page.evaluate(() => {
  const nav = document.getElementById("navbar")!;
  const r = nav.getBoundingClientRect();
  const brand = nav.querySelector(".navbar-brand") as HTMLElement;
  const icons = Array.from(nav.querySelectorAll("#navbar-icon-row .nav-link:not(#dropdownMenuLink)")).filter((e) => e.getBoundingClientRect().width > 0).map((e) => e.getBoundingClientRect());
  const btn = Array.from(document.querySelectorAll(".btn-primary, .btn-black")).find((e) => e.getBoundingClientRect().width > 0);
  const cs = btn && getComputedStyle(btn);
  return {
    height: r.height, position: getComputedStyle(nav).position,
    glass: getComputedStyle(nav, "::before").backdropFilter,
    mark: brand.textContent!.replace(/\s+/g, " ").trim(), label: brand.getAttribute("aria-label"), brandFont: getComputedStyle(brand.querySelector(".brand-main, span")!).fontFamily,
    icons: icons.map((b) => ({ w: b.width, h: b.height, x: b.x })),
    bodyFont: getComputedStyle(document.body).fontFamily,
    btnImage: cs ? cs.backgroundImage : null, btnShadow: cs ? cs.boxShadow : null,
    cssHeight: getComputedStyle(document.documentElement).getPropertyValue("--navbar-height").trim(),
    sideways: document.documentElement.scrollWidth - innerWidth,
    top: document.querySelector("body > :not(script):not(style):not(nav):not(.modal):not(.toast)")?.getBoundingClientRect().top ?? null,
  };
});

for (const width of [1280, 390]) {
  test(`${width}px: every shell has the same 56px glass bar with the mark, the one font, flat buttons and no sideways scroll`, async ({ browser }) => {
    const member = await browser.newContext({ ...USER, viewport: { width, height: 800 } });
    const visitor = await browser.newContext({ viewport: { width, height: 800 } });
    const seen: Record<string, Awaited<ReturnType<typeof measure>>> = {};
    for (const shell of SHELLS) {
      const page = await (shell.member ? member : visitor).newPage();
      await open(page, shell.path);
      const m = await measure(page);
      seen[shell.name] = m;
      expect(m.height, `${shell.name}: bar height`).toBeCloseTo(56, 0);
      expect(m.position, `${shell.name}: fixed`).toBe("fixed");
      expect(m.glass, `${shell.name}: the glass layer behind the bar`).toMatch(/blur\(/);
      expect(m.mark, `${shell.name}: the mark`).toMatch(/^ɸ\s*Beta$/i);
      expect(m.label, `${shell.name}: the site's name is the link's accessible name`).toBeTruthy();
      expect(m.brandFont, `${shell.name}: the mark is Noto Sans`).toMatch(/^"?Noto Sans/);
      expect(m.bodyFont, `${shell.name}: body font`).toMatch(/^"?Noto Sans/);
      expect(m.cssHeight, `${shell.name}: the page below starts from the bar's measured height`).toMatch(/^56px$|^calc\(56px/);
      expect(m.sideways, `${shell.name}: sideways scroll`).toBe(0);
      if (m.btnImage !== null) {
        expect(m.btnImage, `${shell.name}: a button is a flat fill`).toBe("none");
        expect(m.btnShadow, `${shell.name}: a button carries no shadow`).toBe("none");
      }
      await page.close();
    }
    if (width >= 768) {
      // the member shells draw the same icon row: 36px targets, a 36px pitch, the same place from the right edge
      const main = seen["main"].icons, shells = ["settings", "create post", "guild mod"];
      for (const name of shells) {
        const icons = seen[name].icons;
        expect(icons.length, `${name}: icons`).toBe(main.length);
        for (const icon of icons) { expect(icon.w, `${name}: icon width`).toBeCloseTo(36, 0); expect(icon.h, `${name}: icon height`).toBeCloseTo(36, 0); }
        const pitches = icons.slice(1).map((icon, i) => Math.round(icon.x - icons[i].x));
        expect(pitches, `${name}: pitch`).toEqual(pitches.map(() => 36));
        expect(Math.round(icons[0].x), `${name}: where the row starts`).toBe(Math.round(main[0].x));
      }
    }
    await member.close();
    await visitor.close();
  });
}

test("phone: the help bar's menu opens into 36px rows and a full-width way back, and closes again", async ({ browser }) => {
  const context = await browser.newContext({ viewport: { width: 390, height: 800 }, hasTouch: true, isMobile: true });
  const page = await context.newPage();
  await open(page, "/help/faq");
  const closed = await page.evaluate(() => document.getElementById("navbar")!.getBoundingClientRect().height);
  expect(closed).toBeCloseTo(56, 0);
  await page.locator("#navbar .navbar-toggler").click();
  await page.waitForTimeout(500);
  const rows = await page.evaluate(() => Array.from(document.querySelectorAll("#siteBarMenu .nav-link, #siteBarMenu .btn")).map((e) => { const b = e.getBoundingClientRect(); return { h: b.height, w: b.width }; }));
  expect(rows.length).toBeGreaterThanOrEqual(3);
  for (const row of rows) { expect(row.h).toBeGreaterThanOrEqual(36); expect(row.w).toBeGreaterThan(300); }
  await page.locator("#navbar .navbar-toggler").click();
  await page.waitForTimeout(500);
  expect(await page.evaluate(() => document.getElementById("navbar")!.getBoundingClientRect().height)).toBeCloseTo(56, 0);
  await context.close();
});

test("the help pages' photo and the marketing pages' rules are the brand's, not the old purple or a tinted grey", async ({ browser }) => {
  const context = await browser.newContext({ viewport: { width: 1280, height: 800 } });
  const page = await context.newPage();
  await open(page, "/help/faq");
  // the jumbotron's picture: mean colour must lean blue, not indigo (the photo used to carry a baked-in purple wash)
  const mean = await page.evaluate(async () => {
    const url = getComputedStyle(document.querySelector(".jumbotron")!).backgroundImage.match(/url\("?([^")]+)"?\)/)![1];
    const img = new Image(); img.src = url; await img.decode();
    const c = document.createElement("canvas"); c.width = 64; c.height = 64;
    const g = c.getContext("2d")!; g.drawImage(img, 0, 0, 64, 64);
    const d = g.getImageData(0, 0, 64, 64).data; let r = 0, gr = 0, b = 0;
    for (let i = 0; i < d.length; i += 4) { r += d[i]; gr += d[i + 1]; b += d[i + 2]; }
    const n = d.length / 4; return [r / n, gr / n, b / n];
  });
  expect(mean[2], "blue channel").toBeGreaterThan(mean[0] + 30);
  expect(mean[1], "green channel above red: blue, not purple").toBeGreaterThan(mean[0] + 10);
  await context.close();
});
