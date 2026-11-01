import { test, expect, chromium, type BrowserContext, type Page } from "@playwright/test";
import AxeBuilder from "@axe-core/playwright";
import { readFileSync, mkdirSync } from "node:fs";
import { THEMES, open } from "./pages";

// The video consent banner (CLAUDE.md "Video consent"). Run scripts/ui/seed_consent.py in the container first (the fixtures have no players).
//   npm run consent
// What a machine can hold it to: on a visitor's first load and scroll, in a place that is asked, EVERY request to another host, EVERY cookie and
// EVERY storage key is on the lists below, and no player frame exists before a press or an Allow; nothing moves when the banner comes or goes; it
// never covers the phone's bottom bar; its buttons weigh the same; and everything it offers does what it says. Player addresses are never really
// fetched (the spec answers them itself), and the ad tag in these tests is a stand-in: automated loads of the real one are invalid traffic.
// The country header is absent on the dev site, which counts as unknown, which means asked. `CF-IPCountry` is sent to try the others.

const ids: Record<string, string> = JSON.parse(readFileSync(new URL("./.fixtures_consent.json", import.meta.url), "utf8"));
const USER = ".auth/viewer.json";

// what a visitor's first load may fetch from elsewhere: the code and icons the privacy page names under "Code and icons". Pictures on other hosts
// are its "Pictures and GIFs", the known gap: the fixtures have none, so one showing up here is a surprise worth reading.
const THIRD_PARTY_ALLOWED = ["cdnjs.cloudflare.com", "code.jquery.com", "stackpath.bootstrapcdn.com", "cdn.jsdelivr.net"];
const PLAYER_HOSTS = ["youtube-nocookie.com", "youtube.com", "facebook.com", "spotify.com", "bandcamp.com", "vimeo.com", "cdn.example.org"];
// the known gap, written down: a video's thumbnail comes from its site with no press. The privacy page says so; the fixture's is the only one.
const THUMBNAIL_HOSTS = ["i.vimeocdn.com"];
const GIF_PIXEL = Buffer.from("R0lGODlhAQABAIAAAAAAAP///yH5BAEAAAAALAAAAAABAAEAAAIBRAA7", "base64");
const COOKIES_ALLOWED = new Set(["session_ruqqus", "ui_lang", "video_consent"]);
const STORAGE_ALLOWED = [/^late_box_h:/];

// where the browser sends the answer so that the server can set the same cookie: nothing goes there until the reader has answered
const ANSWER = "/consent/video";

type Seen = { thirdParty: string[]; players: string[]; thumbnails: string[]; refused: string[]; answers: string[] };

const onHost = (host: string, domain: string) => host === domain || host.endsWith("." + domain);

async function watch(context: BrowserContext): Promise<Seen> {
  const seen: Seen = { thirdParty: [], players: [], thumbnails: [], refused: [], answers: [] };
  await context.route("**/*", async (route) => {
    const url = new URL(route.request().url());
    if (url.pathname === ANSWER) seen.answers.push(route.request().postData() ?? "");
    if (url.hostname === "localhost") return route.continue();
    seen.thirdParty.push(url.hostname);
    if (PLAYER_HOSTS.some((d) => onHost(url.hostname, d))) {
      seen.players.push(url.href);
      return route.fulfill({ status: 200, contentType: "text/html", body: "<!doctype html><title>stand-in player</title>" });
    }
    if (THUMBNAIL_HOSTS.includes(url.hostname)) {
      seen.thumbnails.push(url.href);
      return route.fulfill({ status: 200, contentType: "image/gif", body: GIF_PIXEL });
    }
    if (THIRD_PARTY_ALLOWED.includes(url.hostname)) return route.continue();
    seen.refused.push(url.href);
    return route.abort();
  });
  return seen;
}

async function visitor(browser: any, o: { width?: number; theme?: "light" | "dark"; country?: string; storageState?: string; reducedMotion?: "reduce"; forcedColors?: "active"; rtl?: boolean; hasTouch?: boolean } = {}) {
  const context: BrowserContext = await browser.newContext({
    viewport: { width: o.width ?? 1280, height: 800 }, colorScheme: o.theme ?? "light", storageState: o.storageState, reducedMotion: o.reducedMotion, forcedColors: o.forcedColors, hasTouch: o.hasTouch,
    extraHTTPHeaders: o.country ? { "CF-IPCountry": o.country } : {},
  });
  if (o.rtl) await context.addCookies([{ name: "ui_lang", value: "ar", domain: "localhost", path: "/" }]);
  const seen = await watch(context);
  const page = await context.newPage();
  return { context, page, seen };
}

// the whole page, a screen at a time, as a reader would
async function scrollThrough(page: Page) {
  const height = await page.evaluate(() => document.documentElement.scrollHeight);
  for (let y = 0; y < height; y += 500) {
    await page.evaluate((top) => window.scrollTo(0, top), y);
    await page.waitForTimeout(150);
  }
  await page.waitForTimeout(600);
}

const players = (page: Page) => page.locator(".feed-video-media").count();
const banner = (page: Page) => page.locator("#consent-banner");
const cookieNames = async (context: BrowserContext) => (await context.cookies()).map((c) => c.name);
const consentCookie = async (context: BrowserContext) => (await context.cookies()).find((c) => c.name === "video_consent")?.value;
const attr = (page: Page) => page.evaluate(() => document.documentElement.getAttribute("data-video-consent"));

async function trackShifts(page: Page) {
  await page.addInitScript(() => {
    (window as any).__shifts = [];
    new PerformanceObserver((list) => {
      for (const e of list.getEntries() as any[]) if (!e.hadRecentInput) (window as any).__shifts.push({ value: e.value, sources: (e.sources || []).map((s: any) => s.node && (s.node.id || s.node.className || s.node.nodeName)) });
    }).observe({ type: "layout-shift", buffered: true });
  });
}

function luminance(rgb: number[]) {
  const [r, g, b] = rgb.map((v) => { const c = v / 255; return c <= 0.03928 ? c / 12.92 : Math.pow((c + 0.055) / 1.055, 2.4); });
  return 0.2126 * r + 0.7152 * g + 0.0722 * b;
}
function contrast(a: string, b: string) {
  const parse = (s: string) => s.match(/[\d.]+/g)!.slice(0, 3).map(Number);
  const [x, y] = [luminance(parse(a)), luminance(parse(b))].sort((p, q) => q - p);
  return (x + 0.05) / (y + 0.05);
}

// --- what a visitor's browser does on the first load and scroll, in every combination -------------------------------------------------------------

mkdirSync("shots/consent", { recursive: true });

for (const width of [390, 1280]) for (const theme of THEMES) for (const rtl of [false, true]) {
  const name = `${width}px ${theme}${rtl ? " right to left" : ""}`;

  test(`first load and scroll: nothing from a video site, every request, cookie and key on the list, nothing moves: ${name}`, async ({ browser }) => {
    const { context, page, seen } = await visitor(browser, { width, theme, rtl, hasTouch: width < 992 });
    await trackShifts(page);
    await open(page, "/all");
    expect(await attr(page)).toBe("ask");
    if (rtl) expect(await page.evaluate(() => document.documentElement.dir)).toBe("rtl");
    await expect(banner(page)).toBeVisible();
    await page.screenshot({ path: `shots/consent/${width}-${theme}${rtl ? "-rtl" : ""}.png` });

    const box = await banner(page).boundingBox();
    expect(box!.y + box!.height, "on the screen").toBeLessThanOrEqual(800);
    expect(box!.x).toBeGreaterThanOrEqual(0);
    expect(box!.x + box!.width).toBeLessThanOrEqual(width);
    expect(await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth), "no sideways scroll").toBe(true);

    // the phone's bottom bar: above it, and the bar is there to be above (it slides away only while scrolling down)
    if (width < 992) {
      const bar = await page.locator("#mobile-bottom-navigation-bar").boundingBox();
      expect(bar!.height).toBeGreaterThan(0);
      expect(box!.y + box!.height, "clear of the bottom bar").toBeLessThanOrEqual(bar!.y);
      await expect.poll(() => page.evaluate(() => {
        const r = document.getElementById("consent-banner")!.getBoundingClientRect();
        return document.elementFromPoint(r.x + r.width / 2, r.y + r.height - 4)!.closest("#consent-banner") !== null;
      })).toBe(true);
    }

    // nothing moves: coming and going changes no one's box
    const layout = () => page.evaluate(() => {
      const card = document.querySelector(".posts .card, #posts > *")!.getBoundingClientRect();
      const row = document.getElementById("main-content-row")!.getBoundingClientRect();
      return { height: document.documentElement.scrollHeight, card: [card.x, card.y, card.width, card.height], row: [row.x, row.y, row.width, row.height] };
    });
    const withBanner = await layout();
    await page.evaluate(() => { document.getElementById("consent-banner")!.hidden = true; });
    expect(await layout()).toEqual(withBanner);
    await page.evaluate(() => { document.getElementById("consent-banner")!.hidden = false; });

    // the line on a thumbnail is in the box: the box is the same with it and without it
    const held = page.locator("[data-embed-kind] .embed-consent-note").first();
    await expect(held).toHaveCount(1);
    const boxOf = () => page.evaluate(() => { const r = document.querySelector("[data-embed-kind]")!.getBoundingClientRect(); return [r.width, r.height]; });
    const boxWithNote = await boxOf();
    await page.evaluate(() => document.documentElement.setAttribute("data-video-consent", "yes"));
    expect(await boxOf()).toEqual(boxWithNote);
    await page.evaluate(() => document.documentElement.setAttribute("data-video-consent", "ask"));

    await scrollThrough(page);

    expect(await players(page), "a player frame before a press or an Allow").toBe(0);
    expect(seen.players, "a request to a video site").toEqual([]);
    expect(seen.refused, "a request to a host that isn't on the list").toEqual([]);
    expect([...new Set(seen.thirdParty)].filter((h) => !THIRD_PARTY_ALLOWED.includes(h) && !THUMBNAIL_HOSTS.includes(h))).toEqual([]);

    for (const name of await cookieNames(context)) expect(COOKIES_ALLOWED.has(name), `the cookie ${name}`).toBe(true);
    expect(await consentCookie(context), "no answer until there is one").toBeUndefined();
    expect(seen.answers, "nothing is sent before the reader answers").toEqual([]);
    const keys = await page.evaluate(() => [...Object.keys(localStorage), ...Object.keys(sessionStorage)]);
    for (const key of keys) expect(STORAGE_ALLOWED.some((r) => r.test(key)), `the storage key ${key}`).toBe(true);

    const shifts: { value: number; sources: string[] }[] = await page.evaluate(() => (window as any).__shifts);
    expect(shifts.flatMap((s) => s.sources).filter((s) => String(s).includes("consent")), "the banner caused a layout shift").toEqual([]);
    console.log(`layout shift ${name}: ${shifts.reduce((sum, s) => sum + s.value, 0).toFixed(4)} from [${[...new Set(shifts.flatMap((s) => s.sources))].join(", ")}]`);
    await context.close();
  });

  test(`the banner holds AA, and axe finds nothing in it: ${name}`, async ({ browser }) => {
    const { context, page } = await visitor(browser, { width, theme, rtl });
    await open(page, "/all");
    await expect(banner(page)).toBeVisible();
    const colours = await page.evaluate(() => {
      const banner = document.getElementById("consent-banner")!;
      const c = (s: string) => getComputedStyle(banner.querySelector(s)!);
      return { bg: getComputedStyle(banner).backgroundColor, text: c(".consent-text").color, link: c(".consent-text a").color, button: c("[data-consent-choice]").color };
    });
    for (const key of ["text", "link", "button"] as const) expect(contrast(colours[key], colours.bg), key).toBeGreaterThanOrEqual(4.5);
    const found = (await new AxeBuilder({ page }).include("#consent-banner").analyze()).violations;
    expect(found.filter((v) => v.impact === "serious" || v.impact === "critical").map((v) => v.id)).toEqual([]);
    await context.close();
  });
}

for (const path of ["/", `/post/${ids.youtube}`, `/post/${ids.facebook}`, `/post/${ids.spotify}`, "/@ui_alice"]) {
  test(`first load and scroll of ${path}: nothing from a video site, every request, cookie and key on the list`, async ({ browser }) => {
    const { context, page, seen } = await visitor(browser);
    await open(page, path);
    await scrollThrough(page);
    expect(await players(page)).toBe(0);
    expect(seen.players).toEqual([]);
    expect(seen.refused).toEqual([]);
    expect(seen.answers, "nothing is sent before the reader answers").toEqual([]);
    for (const name of await cookieNames(context)) expect(COOKIES_ALLOWED.has(name), `the cookie ${name}`).toBe(true);
    const keys = await page.evaluate(() => [...Object.keys(localStorage), ...Object.keys(sessionStorage)]);
    for (const key of keys) expect(STORAGE_ALLOWED.some((r) => r.test(key)), `the storage key ${key}`).toBe(true);
    await context.close();
  });
}

// The shortest box of each kind (a music box of 120px, a 16:9 video on the narrowest phone), at the largest text, with the line as it is and as long as
// a machine's German makes it. `long`: the longest line the box has to hold; a language with a longer one is cut off or runs over the button.
const SHORT_BOXES = [
  { name: "a 120px music box", id: "bandcamp", kind: "audio", height: 120 as number | null, long: "Wenn Sie auf Wiedergabe drücken, wird Bandcamp von dort geladen." },
  { name: "a video", id: "youtube", kind: "iframe", height: null, long: "Drücken Sie auf Wiedergabe, um YouTube zu laden, das Cookies setzen, Ihre Adresse sehen und aufzeichnen kann, was Sie sehen." },
];
for (const box of SHORT_BOXES) for (const lang of ["en", "ar"]) {
  test(`in ${box.name} the line doesn't lie across the play button and isn't cut off: ${lang}`, async ({ browser }) => {
    for (const o of [{ width: 320, size: "xlarge" }, { width: 320, size: "" }, { width: 390, size: "xlarge" }, { width: 390, size: "" }, { width: 1280, size: "xlarge" }, { width: 1280, size: "" }]) {
      const { context, page } = await visitor(browser, { width: o.width, hasTouch: o.width < 992 });
      const cookies = [{ name: "ui_lang", value: lang, domain: "localhost", path: "/" }];
      if (o.size) cookies.push({ name: "text_size", value: o.size, domain: "localhost", path: "/" });
      await context.addCookies(cookies);
      await open(page, `/post/${ids[box.id]}`);
      const selector = `[data-embed-kind="${box.kind}"]`;
      const measure = () => page.evaluate((selector) => {
        const box = document.querySelector(selector)!;
        const note = box.querySelector(".embed-consent-note")!;
        const rect = (e: Element) => { const r = e.getBoundingClientRect(); return { top: r.top, bottom: r.bottom, left: r.left, right: r.right, height: r.height }; };
        return { box: rect(box), note: rect(note), glyph: rect(box.querySelector(".embed-lg-play i")!), text: note.textContent!.trim() };
      }, selector);
      const check = async (label: string) => {
        const got = await measure();
        const where = `${label} ${lang} ${o.width}px ${o.size || "default"}: ${JSON.stringify(got)}`;
        if (box.height) expect(got.box.height, where).toBe(box.height);
        expect(got.text.length, where).toBeGreaterThan(10);
        const apart = got.note.left >= got.glyph.right || got.note.right <= got.glyph.left || got.note.top >= got.glyph.bottom || got.note.bottom <= got.glyph.top;
        expect(apart, `the line over the play button, ${where}`).toBe(true);
        expect(got.note.bottom, `the line past the box, ${where}`).toBeLessThanOrEqual(got.box.bottom + 0.5);
        expect(got.glyph.left, where).toBeGreaterThanOrEqual(got.box.left);
        expect(got.glyph.right, where).toBeLessThanOrEqual(got.box.right);
      };
      await check("as it is");
      await page.locator(selector).screenshot({ path: `shots/consent/short-box-${box.id}-${lang}-${o.width}-${o.size || "default"}.png` });
      await page.evaluate(([selector, long]) => { document.querySelector(selector + " .embed-consent-note")!.textContent = long; }, [selector, box.long]);
      await check("as long as a translation makes it");
      // the press still reaches the player
      await page.locator(`${selector} .feed-video-thumb`).click();
      await expect(page.locator(".feed-video-media")).toHaveCount(1);
      await context.close();
    }
  });
}

test("the largest text size: nothing is cut off, and it is still above the phone's bar", async ({ browser }) => {
  for (const width of [390, 320]) {
    const { context, page } = await visitor(browser, { width, hasTouch: true });
    await context.addCookies([{ name: "text_size", value: "xlarge", domain: "localhost", path: "/" }]);
    await open(page, "/all");
    await expect(banner(page)).toBeVisible();
    const box = await banner(page).boundingBox();
    const bar = await page.locator("#mobile-bottom-navigation-bar").boundingBox();
    expect(box!.y + box!.height).toBeLessThanOrEqual(bar!.y);
    expect(box!.y).toBeGreaterThanOrEqual(0);
    expect(box!.x + box!.width).toBeLessThanOrEqual(width);
    const clipped = await page.evaluate(() => Array.from(document.querySelectorAll("#consent-banner button:not([hidden]), #consent-banner p:not([hidden])")).filter((e) => e.scrollWidth > e.clientWidth + 1).map((e) => e.textContent));
    expect(clipped).toEqual([]);
    await page.screenshot({ path: `shots/consent/xlarge-${width}.png` });
    await context.close();
  }
});

test("the two buttons weigh the same, at both widths", async ({ browser }) => {
  for (const width of [390, 1280]) {
    const { context, page } = await visitor(browser, { width });
    await open(page, "/all");
    const looks = await page.evaluate(() => Array.from(document.querySelectorAll("[data-consent-choice]")).map((b) => {
      const s = getComputedStyle(b), r = b.getBoundingClientRect();
      return { h: r.height, w: r.width, bg: s.backgroundColor, color: s.color, border: s.borderTopColor, weight: s.fontWeight, size: s.fontSize };
    }));
    const [no, yes] = looks;
    expect({ ...no, w: 0 }).toEqual({ ...yes, w: 0 });
    if (width === 390) expect(Math.abs(no.w - yes.w)).toBeLessThan(1);
    await context.close();
  }
});

// --- what the banner offers ----------------------------------------------------------------------------------------------------------------------

test("a press loads that one player, and it isn't an answer", async ({ browser }) => {
  const { context, page, seen } = await visitor(browser);
  await open(page, `/post/${ids.youtube}`);
  await expect(page.locator(".embed-consent-note").first()).toBeVisible();
  await expect(page.locator(".embed-consent-note").first()).toContainText("Pressing play loads YouTube");
  await page.screenshot({ path: "shots/consent/player.png" });
  await page.locator("[data-embed-kind] .feed-video-thumb").first().click();     // (a click anywhere on the thumbnail plays)
  await expect(page.locator(".feed-video-media")).toHaveCount(1);
  expect(seen.players.length).toBe(1);
  expect(seen.players[0]).toContain("youtube-nocookie.com/embed/");
  expect(seen.players[0]).not.toContain("mute=1");                       // pressed: with sound
  expect(await consentCookie(context)).toBeUndefined();
  expect(seen.answers, "a press sends nothing").toEqual([]);
  await expect(banner(page)).toBeVisible();
  await context.close();
});

test("Allow videos: the frames come at once, the line goes, the next page asks nothing", async ({ browser }) => {
  const { context, page, seen } = await visitor(browser);
  await open(page, `/post/${ids.youtube}`);
  expect(await players(page)).toBe(0);
  await page.locator("[data-consent-choice='yes']").click();
  await expect(banner(page)).toBeHidden();
  expect(await consentCookie(context)).toMatch(/^1\.yes\.\d+$/);
  expect(await attr(page)).toBe("yes");
  await expect(page.locator(".embed-consent-note").first()).toBeHidden();
  await page.locator("[data-embed-kind]").first().scrollIntoViewIfNeeded();
  await expect(page.locator(".feed-video-media")).toHaveCount(1);
  expect(seen.players[0]).toContain("mute=1");                           // started by scrolling: muted
  await open(page, "/all");
  expect(await attr(page)).toBe("yes");
  await expect(banner(page)).toBeHidden();
  await context.close();
});

test("Not now: nothing loads, on this page or the next, and pressing still works", async ({ browser }) => {
  const { context, page, seen } = await visitor(browser);
  await open(page, "/all");
  await page.locator("[data-consent-choice='no']").click();
  await expect(banner(page)).toBeHidden();
  expect(await consentCookie(context)).toMatch(/^1\.no\.\d+$/);
  await scrollThrough(page);
  expect(await players(page)).toBe(0);
  await open(page, `/post/${ids.youtube}`);
  expect(await attr(page)).toBe("no");
  await expect(banner(page)).toBeHidden();
  await expect(page.locator(".embed-consent-note").first()).toBeVisible();
  await scrollThrough(page);
  expect(seen.players).toEqual([]);
  await page.locator("[data-embed-kind] .feed-video-thumb").first().click();
  await expect(page.locator(".feed-video-media")).toHaveCount(1);
  await context.close();
});

test("taking it back removes the frames on this page and the next page draws none", async ({ browser }) => {
  const { context, page, seen } = await visitor(browser);
  await open(page, `/post/${ids.youtube}`);
  await page.locator("[data-consent-choice='yes']").click();
  await page.locator("[data-embed-kind]").first().scrollIntoViewIfNeeded();
  await expect(page.locator(".feed-video-media")).toHaveCount(1);
  await page.locator("[data-consent-open]").first().evaluate((el: HTMLElement) => el.click());
  await expect(banner(page)).toBeVisible();
  await expect(page.locator("[data-consent-choice='yes']")).toHaveAttribute("aria-pressed", "true");
  await expect(page.locator("[data-consent-now='allowed']")).toBeVisible();
  await page.locator("[data-consent-choice='no']").click();
  await expect(page.locator(".feed-video-media")).toHaveCount(0);
  expect(await consentCookie(context)).toMatch(/^1\.no\.\d+$/);
  const before = seen.players.length;
  await page.reload();
  await page.waitForTimeout(600);
  await scrollThrough(page);
  expect(await players(page)).toBe(0);
  expect(seen.players.length).toBe(before);
  await context.close();
});

test("taking it back removes a direct video file too, its address and all, and the thumbnail comes back", async ({ browser }) => {
  const { context, page, seen } = await visitor(browser);
  await answered(context);
  await open(page, `/post/${ids.direct}`);
  await page.locator("[data-embed-kind]").first().scrollIntoViewIfNeeded();
  await expect(page.locator("video.feed-video-media")).toHaveCount(1);
  expect(await page.locator("video.feed-video-media").getAttribute("src")).toBe("https://cdn.example.org/clip.mp4");
  await expect(page.locator(".feed-video-thumb")).toBeHidden();
  await page.locator("[data-consent-open]").first().evaluate((el: HTMLElement) => el.click());
  await page.locator("[data-consent-choice='no']").click();
  await expect(page.locator(".feed-video-media")).toHaveCount(0);
  await expect(page.locator(".feed-video-thumb")).toBeVisible();
  await page.waitForTimeout(700);                                         // (scrolling's own 400ms teardown has nothing to bring back)
  await expect(page.locator(".feed-video-media")).toHaveCount(0);
  // a press still plays it, and Allow gives it back to scrolling
  const before = seen.players.length;
  await page.locator("[data-embed-kind] .feed-video-thumb").first().click();
  await expect(page.locator("video.feed-video-media")).toHaveCount(1);
  expect(seen.players.length).toBeGreaterThanOrEqual(before);
  await context.close();
});

test("scrolling away from a direct video file still keeps it, so scrolling back resumes it", async ({ browser }) => {
  const { context, page } = await visitor(browser);
  await answered(context);
  await open(page, `/post/${ids.direct}`);
  await page.locator("[data-embed-kind]").first().scrollIntoViewIfNeeded();
  await expect(page.locator("video.feed-video-media")).toHaveCount(1);
  await page.evaluate(() => { document.querySelector("[data-embed-kind]")!.setAttribute("style", "margin-bottom: 3000px"); window.scrollTo(0, document.documentElement.scrollHeight); });
  await page.waitForTimeout(800);
  await expect(page.locator("video.feed-video-media")).toHaveCount(1);
  await context.close();
});

// --- a page the browser kept (the back button) ------------------------------------------------------------------------------------------------------
//
// Playwright's own launch switches the back-forward cache off and its headless shell has none, so `goBack()` is a fresh load there and none of this
// would run: these need the full browser with that switch taken away, and each says it really was the kept page.

async function withBackForwardCache(run: (browser: any) => Promise<void>) {
  const browser = await chromium.launch({ channel: "chromium", ignoreDefaultArgs: ["--disable-back-forward-cache"] });
  try {
    await run(browser);
  } finally {
    await browser.close();
  }
}

async function noteKept(page: Page) {
  await page.addInitScript(() => {
    window.addEventListener("pageshow", (event) => { (window as any).__kept = event.persisted; });
  });
}

async function answerElsewhere(page: Page, choice: "no" | "yes") {
  await open(page, "/help/faq");
  await page.locator("[data-consent-open]").first().evaluate((el: HTMLElement) => el.click());
  await page.locator(`[data-consent-choice='${choice}']`).click();
}

async function goBackToKept(page: Page) {
  // (not page.goBack(): it waits for a load event that a page out of the cache never fires)
  await page.evaluate(() => { setTimeout(() => history.back(), 0); });
  await expect.poll(() => page.evaluate(() => (window as any).__kept).catch(() => undefined), { message: "the page really came from the back-forward cache", timeout: 20_000 }).toBe(true);
}

test("the back button: a pressed player goes when the answer was taken back on another page", () => withBackForwardCache(async (browser) => {
  const { context, page, seen } = await visitor(browser);
  await answered(context);
  await noteKept(page);
  await open(page, `/post/${ids.facebook}`);
  await page.locator("[data-embed-kind] .feed-video-thumb").first().click();
  await expect(page.locator("iframe.feed-video-media")).toHaveCount(1);
  expect(seen.players.length).toBe(1);
  await answerElsewhere(page, "no");
  await goBackToKept(page);
  expect(await attr(page)).toBe("no");
  await expect(page.locator(".feed-video-media")).toHaveCount(0);
  await expect(page.locator(".feed-video-thumb").first()).toBeVisible();
  await context.close();
}));

test("the back button: a question answered on another page isn't still being asked", () => withBackForwardCache(async (browser) => {
  for (const choice of ["no", "yes"] as const) {
    const { context, page } = await visitor(browser);
    await noteKept(page);
    await open(page, `/post/${ids.youtube}`);
    await expect(banner(page)).toBeVisible();
    await answerElsewhere(page, choice);
    await goBackToKept(page);
    expect(await attr(page)).toBe(choice);
    await expect(banner(page)).toBeHidden();
    await context.close();
  }
}));

test("the back button: a player pressed while being asked stays when the answer is Not now, as on the page itself", () => withBackForwardCache(async (browser) => {
  const { context, page } = await visitor(browser);
  await noteKept(page);
  await open(page, `/post/${ids.youtube}`);
  await page.locator("[data-embed-kind] .feed-video-thumb").first().click();
  await expect(page.locator(".feed-video-media")).toHaveCount(1);
  await answerElsewhere(page, "no");
  await goBackToKept(page);
  expect(await attr(page)).toBe("no");
  await expect(banner(page)).toBeHidden();
  await expect(page.locator(".feed-video-media")).toHaveCount(1);
  await context.close();
}));

test("the back button: Allow given on another page gives a kept page's held players back to scrolling", () => withBackForwardCache(async (browser) => {
  const { context, page } = await visitor(browser);
  await noteKept(page);
  await open(page, `/post/${ids.youtube}`);
  expect(await players(page)).toBe(0);
  await answerElsewhere(page, "yes");
  await goBackToKept(page);
  await page.locator("[data-embed-kind]").first().scrollIntoViewIfNeeded();
  await expect(page.locator(".feed-video-media")).toHaveCount(1);
  await context.close();
}));

// The cookie's date is usually the server's now, so a browser with a wrong clock can't place it. What a kept page goes by is that the cookie changed.
const WRONG_CLOCKS = [{ name: "30 days slow", days: -30 }, { name: "200 days fast", days: 200 }];

async function clockOff(page: Page, days: number) {
  await page.addInitScript((off) => { const real = Date.now.bind(Date); Date.now = () => real() + off * 86400 * 1000; }, days);
}

// answerElsewhere, and then until the answer on file is the server's, dated by its own clock
async function answerElsewhereThroughTheRoute(context: BrowserContext, page: Page, choice: "no" | "yes") {
  const replied = page.waitForResponse((r) => r.url().endsWith(ANSWER));
  await answerElsewhere(page, choice);
  expect((await replied).status()).toBe(204);
  const real = new Date().getTime() / 1000;
  await expect.poll(async () => Math.abs(Number(((await consentCookie(context)) ?? "").split(".")[2]) - real), { message: "the date on file is the server's" }).toBeLessThan(120);
}

for (const clock of WRONG_CLOCKS) {
  test(`the back button, with a browser's clock ${clock.name}: a yes taken back on another page is taken back here`, () => withBackForwardCache(async (browser) => {
    const { context, page, seen } = await visitor(browser);
    await answered(context);
    await noteKept(page);
    await clockOff(page, clock.days);
    await open(page, `/post/${ids.facebook}`);
    expect(await attr(page)).toBe("yes");
    await page.locator("[data-embed-kind] .feed-video-thumb").first().click();
    await expect(page.locator("iframe.feed-video-media")).toHaveCount(1);
    expect(seen.players.length).toBe(1);
    await answerElsewhereThroughTheRoute(context, page, "no");
    await goBackToKept(page);
    expect(await attr(page)).toBe("no");
    await expect(page.locator(".feed-video-media")).toHaveCount(0);
    await context.close();
  }));

  test(`the back button, with a browser's clock ${clock.name}: a question answered on another page isn't still being asked`, () => withBackForwardCache(async (browser) => {
    for (const choice of ["no", "yes"] as const) {
      const { context, page } = await visitor(browser);
      await noteKept(page);
      await clockOff(page, clock.days);
      await open(page, `/post/${ids.youtube}`);
      await expect(banner(page)).toBeVisible();
      await answerElsewhereThroughTheRoute(context, page, choice);
      await goBackToKept(page);
      expect(await attr(page)).toBe(choice);
      await expect(banner(page)).toBeHidden();
      await context.close();
    }
  }));
}

test("the back button: an answer that lapsed stays lapsed on a kept page, when nothing has been answered since", () => withBackForwardCache(async (browser) => {
  const { context, page } = await visitor(browser);
  await context.addCookies([{ name: "video_consent", value: `1.yes.${Math.floor(Date.now() / 1000) - 184 * 86400}`, domain: "localhost", path: "/" }]);
  await noteKept(page);
  await open(page, `/post/${ids.youtube}`);
  expect(await attr(page)).toBe("ask");
  await expect(banner(page)).toBeVisible();
  await open(page, "/help/faq");
  await goBackToKept(page);
  expect(await attr(page)).toBe("ask");
  await expect(banner(page)).toBeVisible();
  expect(await players(page)).toBe(0);
  await context.close();
}));

// Without a kept page the browser's HTTP cache may hold the page, and the cookie the page was drawn from decides whether it is used: pages say `Vary: Cookie`.
// So a new answer always brings a page drawn from it. (No routes in this one: Playwright turns the HTTP cache off for a page that has any.)
test("going back with no kept page: the copy of the page isn't used once the answer has changed, whatever the browser's clock says", async ({ browser }) => {
  const context: BrowserContext = await browser.newContext({ userAgent: "Mozilla/5.0 Chrome/120 ui-consent" });
  const page = await context.newPage();
  await clockOff(page, -30);
  const navigation = () => page.evaluate(() => { const n = performance.getEntriesByType("navigation")[0] as PerformanceNavigationTiming; return { type: n.type, bytes: n.transferSize }; });
  await page.goto("/help/faq", { waitUntil: "load" });
  await page.locator("[data-consent-open]").first().evaluate((el: HTMLElement) => el.click());
  let replied = page.waitForResponse((r) => r.url().endsWith(ANSWER));
  await page.locator("[data-consent-choice='yes']").click();
  await replied;
  await page.goto("/help/rules", { waitUntil: "load" });
  await page.goto("/help/terms", { waitUntil: "load" });
  await page.goBack({ waitUntil: "load" });
  expect(await navigation(), "the answer is the same: the copy is used, so this test can tell").toEqual({ type: "back_forward", bytes: 0 });
  await page.goForward({ waitUntil: "load" });
  await page.locator("[data-consent-open]").first().evaluate((el: HTMLElement) => el.click());
  replied = page.waitForResponse((r) => r.url().endsWith(ANSWER));
  await page.locator("[data-consent-choice='no']").click();
  await replied;
  await page.goBack({ waitUntil: "load" });
  const back = await navigation();
  expect(back.type).toBe("back_forward");
  expect(back.bytes, "drawn again").toBeGreaterThan(0);
  expect(await attr(page)).toBe("no");
  await context.close();
});

test("it is not a dialog: Escape decides nothing, focus goes on past it, and the page behind works", async ({ browser }) => {
  const { context, page, seen } = await visitor(browser);
  await open(page, "/all");
  await expect(banner(page)).toBeVisible();
  // first in the page, so a keyboard meets it before an endless feed
  await page.keyboard.press("Tab");
  expect(await page.evaluate(() => document.activeElement!.closest("#consent-banner") !== null)).toBe(true);
  await page.keyboard.press("Escape");
  await expect(banner(page)).toBeVisible();
  expect(await consentCookie(context)).toBeUndefined();
  expect(seen.answers, "Escape sends nothing").toEqual([]);
  for (let i = 0; i < 5; i++) await page.keyboard.press("Tab");
  expect(await page.evaluate(() => document.activeElement!.closest("#consent-banner") === null), "focus left the banner").toBe(true);
  expect(await page.locator(".modal-backdrop").count()).toBe(0);
  expect(await page.evaluate(() => getComputedStyle(document.body).overflow)).not.toBe("hidden");
  await page.locator("#main-content-row a").first().hover();
  await context.close();
});

test("under reduced motion and forced colours it is still there, still and bordered", async ({ browser }) => {
  for (const o of [{ reducedMotion: "reduce" as const }, { reducedMotion: "reduce" as const, width: 390, hasTouch: true }, { forcedColors: "active" as const }]) {
    const { context, page } = await visitor(browser, o);
    await open(page, "/all");
    await expect(banner(page)).toBeVisible();
    const s = await banner(page).evaluate((el) => { const c = getComputedStyle(el); return { animation: c.animationName, transition: c.transitionDuration, border: c.borderTopWidth, position: c.position }; });
    expect(s).toEqual({ animation: "none", transition: "0s", border: "1px", position: "fixed" });
    await page.screenshot({ path: `shots/consent/${o.reducedMotion ? "reduced-motion" : "forced-colours"}-${o.width ?? 1280}.png` });
    await context.close();
  }
});

// --- who is asked ------------------------------------------------------------------------------------------------------------------------------------

for (const country of ["GB", "DE", "CH", "T1", "XX"]) {
  test(`${country} is asked`, async ({ browser }) => {
    const { context, page } = await visitor(browser, { country });
    await open(page, "/all");
    expect(await attr(page)).toBe("ask");
    await expect(banner(page)).toBeVisible();
    await context.close();
  });
}

test("anywhere else keeps what it had: no question, and a video starts as it scrolls into view", async ({ browser }) => {
  const { context, page, seen } = await visitor(browser, { country: "US" });
  const answer = await page.goto(`/post/${ids.youtube}`, { waitUntil: "load" });
  expect(answer!.headers()["vary"]).toContain("CF-IPCountry");
  expect(await attr(page)).toBeNull();
  await page.waitForTimeout(500);
  await expect(banner(page)).toBeHidden();
  await expect(page.locator(".embed-consent-note").first()).toBeHidden();
  await page.locator("[data-embed-kind]").first().scrollIntoViewIfNeeded();
  await expect(page.locator(".feed-video-media")).toHaveCount(1);
  expect(seen.players[0]).toContain("mute=1");
  await context.close();
});

test("a thumbnail comes from its site with no press, and its player doesn't: the gap the privacy page discloses", async ({ browser }) => {
  const { context, page, seen } = await visitor(browser);
  await open(page, `/post/${ids.vimeo}`);
  await scrollThrough(page);
  expect(seen.thumbnails.length, "the thumbnail's own host").toBeGreaterThan(0);
  expect(seen.players).toEqual([]);
  await open(page, "/help/privacy");
  await expect(page.locator("#videos ~ ul li").first()).toContainText("Its thumbnail can still come from that site");
  await expect(page.locator("#videos ~ ul li").first()).not.toContainText("nothing from a video site loads");
  await context.close();
});

test("six months on, a no is asked again anywhere, and the cookie lasts long enough to tell", async ({ browser }) => {
  const { context, page, seen } = await visitor(browser, { country: "US" });
  await open(page, `/post/${ids.youtube}`);
  await page.locator("[data-consent-open]").first().evaluate((el: HTMLElement) => el.click());
  // (the server's copy lands first, or it would write over the lapsed one below)
  await Promise.all([page.waitForResponse((r) => r.url().endsWith(ANSWER)), page.locator("[data-consent-choice='no']").click()]);
  await expect(page.locator(".feed-video-media")).toHaveCount(0);
  const played = seen.players.length;                                      // (nobody had been asked: the first one started by itself)
  // what the browser was told to keep: a cookie that ended with the answer would be gone by the time it lapsed, and read as no answer at all
  const written = (await context.cookies()).find((c) => c.name === "video_consent")!;
  const reask = 183 * 86400, now = Date.now() / 1000;
  expect(written.expires - now, "kept past the six months").toBeGreaterThan(reask + 180 * 86400);
  // six months on, the cookie is what the browser still holds
  await context.addCookies([{ name: "video_consent", value: `1.no.${Math.floor(now) - 184 * 86400}`, domain: "localhost", path: "/" }]);
  await open(page, `/post/${ids.youtube}`);
  expect(await attr(page)).toBe("ask");
  await expect(banner(page)).toBeVisible();
  await page.locator("[data-embed-kind]").first().scrollIntoViewIfNeeded();
  await page.waitForTimeout(800);
  expect(await players(page)).toBe(0);
  expect(seen.players.length).toBe(played);
  await context.close();
});

test("six months on, a yes where nobody is asked goes back to what everyone there has", async ({ browser }) => {
  const { context, page } = await visitor(browser, { country: "US" });
  await context.addCookies([{ name: "video_consent", value: `1.yes.${Math.floor(Date.now() / 1000) - 184 * 86400}`, domain: "localhost", path: "/" }]);
  await open(page, `/post/${ids.youtube}`);
  expect(await attr(page)).toBeNull();
  await expect(banner(page)).toBeHidden();
  await context.close();
});

test("but a reader anywhere can still say no, and it holds", async ({ browser }) => {
  const { context, page, seen } = await visitor(browser, { country: "US" });
  await open(page, `/post/${ids.youtube}`);
  await page.locator("[data-consent-open]").first().evaluate((el: HTMLElement) => el.click());
  await page.locator("[data-consent-choice='no']").click();
  expect(await consentCookie(context)).toMatch(/^1\.no\.\d+$/);
  await expect(page.locator(".feed-video-media")).toHaveCount(0);
  const before = seen.players.length;
  await page.reload();
  expect(await attr(page)).toBe("no");
  await page.waitForTimeout(800);
  expect(await players(page)).toBe(0);
  expect(seen.players.length).toBe(before);
  await context.close();
});

test("a side panel's frame never draws it", async ({ browser }) => {
  const { context, page } = await visitor(browser, { storageState: USER });
  await open(page, "/notifications?embed=1");
  await expect(banner(page)).toHaveCount(0);
  await context.close();
});

// --- the server sets the same cookie (WebKit keeps one a script wrote for seven days at most: not something Chromium can show, so this holds the route) ---

const dateOf = (value: string | undefined) => Number((value ?? "").split(".")[2]);
const DAY = 86400;

for (const width of [390, 1280]) {
  test(`the answer really arrives through the route, replacing what the script wrote: ${width}px`, async ({ browser }) => {
    const { context, page } = await visitor(browser, { width, hasTouch: width < 992 });
    // the browser's clock is 30 days slow, so the date in the cookie says which of the two wrote it
    await page.addInitScript(() => { const real = Date.now.bind(Date); Date.now = () => real() - 30 * 86400 * 1000; });
    let release!: () => void;
    const held = new Promise<void>((resolve) => { release = resolve; });
    const bodies: string[] = [];
    await page.route(`**${ANSWER}`, async (route) => {
      bodies.push(route.request().postData() ?? "");
      await held;
      await route.continue();
    });
    await open(page, "/help/faq");
    await page.locator("[data-consent-open]").first().evaluate((el: HTMLElement) => el.click());
    const replied = page.waitForResponse((r) => r.url().endsWith(ANSWER));
    await page.locator("[data-consent-choice='yes']").click();
    const now = Date.now() / 1000;

    // written at once by the script, with the browser's date, while the request is still out
    await expect.poll(() => consentCookie(context)).toMatch(/^1\.yes\.\d+$/);
    const script = dateOf(await consentCookie(context));
    expect(Math.abs(script - (now - 30 * DAY)), "the script wrote this one").toBeLessThan(60);
    expect(await attr(page)).toBe("yes");

    release();
    const answer = await replied;
    expect(answer.status()).toBe(204);
    expect(answer.request().method()).toBe("POST");
    expect(bodies).toEqual(["choice=yes"]);
    expect(answer.url().endsWith(ANSWER), "nothing in the address").toBe(true);
    expect(await answer.headerValue("cache-control")).toBe("no-store");
    const set = (await answer.headerValue("set-cookie")) ?? "";
    expect(set.split("\n").length, set).toBe(1);
    expect(set).toMatch(/^video_consent=1\.yes\.\d+; /);
    expect(set).toContain("SameSite=Lax");
    expect(set).not.toMatch(/HttpOnly|Domain=/i);

    await expect.poll(async () => dateOf(await consentCookie(context)), { message: "the server's own clock, not the browser's" }).toBeGreaterThan(now - 60);
    const cookie = (await context.cookies()).find((c) => c.name === "video_consent")!;
    expect(cookie.httpOnly).toBe(false);
    expect(cookie.sameSite).toBe("Lax");
    expect(cookie.path).toBe("/");
    expect(cookie.domain).toBe("localhost");                   // this host's alone
    expect(cookie.secure).toBe(false);                         // the dev site is on http
    expect(Math.abs(cookie.expires - (now + 400 * DAY))).toBeLessThan(120);
    expect(await page.evaluate(() => document.cookie), "the script can still read it").toContain("video_consent=1.yes.");

    await open(page, "/all");
    expect(await attr(page)).toBe("yes");
    await expect(banner(page)).toBeHidden();
    await context.close();
  });
}

for (const failure of ["the network", "a 500", "a 429"]) {
  test(`when ${failure} stops the request nobody is told: the script's cookie stands`, async ({ browser }) => {
    const { context, page } = await visitor(browser);
    const errors: string[] = [];
    page.on("pageerror", (e) => errors.push(String(e)));
    await page.route(`**${ANSWER}`, (route) => failure === "the network" ? route.abort() : route.fulfill({ status: failure === "a 500" ? 500 : 429, body: "" }));
    await open(page, `/post/${ids.youtube}`);
    const sent = page.waitForRequest((r) => r.url().endsWith(ANSWER));
    await page.locator("[data-consent-choice='no']").click();
    await sent;
    await page.waitForTimeout(200);
    expect(errors, "nothing thrown").toEqual([]);
    await expect(banner(page)).toBeHidden();
    await expect(page.locator(".toast.show")).toHaveCount(0);
    expect(await consentCookie(context)).toMatch(/^1\.no\.\d+$/);
    await open(page, "/all");
    expect(await attr(page)).toBe("no");
    await context.close();
  });
}

test("a later answer isn't overtaken: one request at a time, and the last one decides", async ({ browser }) => {
  const { context, page } = await visitor(browser);
  await context.addCookies([{ name: "video_autoplay", value: "tap", domain: "localhost", path: "/" }]);
  let release!: () => void;
  const held = new Promise<void>((resolve) => { release = resolve; });
  const bodies: string[] = [];
  await page.route(`**${ANSWER}`, async (route) => {
    const first = bodies.length === 0;
    bodies.push(route.request().postData() ?? "");
    if (first) await held;
    await route.continue();
  });
  await open(page, "/help/faq");
  const reopen = () => page.locator("[data-consent-open]").first().evaluate((el: HTMLElement) => el.click());
  await reopen();
  await page.locator("[data-consent-choice='yes']").click();
  await reopen();
  await page.locator("[data-consent-choice='no']").click();
  await reopen();
  await page.locator("[data-consent-choice='yes']").click();
  await reopen();
  await page.locator("[data-consent-choice='no']").click();
  await page.waitForTimeout(200);
  expect(bodies, "the rest wait for the first").toEqual(["choice=yes"]);
  expect(await consentCookie(context)).toMatch(/^1\.no\./);
  const second = page.waitForResponse((r) => r.url().endsWith(ANSWER) && r.request().postData() === "choice=no");
  release();
  await second;
  expect(bodies, "only the latest of those waiting").toEqual(["choice=yes", "choice=no"]);
  expect(await consentCookie(context)).toMatch(/^1\.no\./);
  await open(page, "/all");
  expect(await attr(page)).toBe("no");
  await context.close();
});

// (a real navigation can't hold the first request open for the test: Playwright's hold ends with the page, so the first test fires the event itself)
test("an answer still waiting goes as the page is left, and isn't sent twice", async ({ browser }) => {
  const { context, page } = await visitor(browser);
  let release!: () => void;
  const held = new Promise<void>((resolve) => { release = resolve; });
  const bodies: string[] = [];
  await page.route(`**${ANSWER}`, async (route) => {
    bodies.push(route.request().postData() ?? "");
    if (bodies.length > 1) return route.continue();
    await held;
    await route.abort();                                        // the first never lands
  });
  await open(page, "/help/faq");
  const reopen = () => page.locator("[data-consent-open]").first().evaluate((el: HTMLElement) => el.click());
  await reopen();
  await page.locator("[data-consent-choice='yes']").click();
  await reopen();
  await page.locator("[data-consent-choice='no']").click();
  await page.waitForTimeout(100);
  expect(bodies).toEqual(["choice=yes"]);
  const landed = page.waitForResponse((r) => r.url().endsWith(ANSWER) && r.request().postData() === "choice=no");
  await page.evaluate(() => window.dispatchEvent(new Event("pagehide")));
  await landed;
  expect(bodies).toEqual(["choice=yes", "choice=no"]);
  release();
  await page.waitForTimeout(200);
  expect(bodies, "not again when the first comes back").toEqual(["choice=yes", "choice=no"]);
  expect(await consentCookie(context)).toMatch(/^1\.no\./);
  await context.close();
});

test("going to another page with an answer waiting ends on the later answer", async ({ browser }) => {
  const { context, page } = await visitor(browser);
  let release!: () => void;
  const held = new Promise<void>((resolve) => { release = resolve; });
  let first = true;
  await page.route(`**${ANSWER}`, async (route) => {
    if (!first) return route.continue();
    first = false;
    await held;
    await route.abort().catch(() => {});
  });
  await open(page, "/help/faq");
  const reopen = () => page.locator("[data-consent-open]").first().evaluate((el: HTMLElement) => el.click());
  await reopen();
  await page.locator("[data-consent-choice='yes']").click();
  await reopen();
  await page.locator("[data-consent-choice='no']").click();
  await open(page, "/help/rules");
  release();
  await page.waitForTimeout(300);
  expect(await consentCookie(context)).toMatch(/^1\.no\./);
  await open(page, "/all");
  expect(await attr(page)).toBe("no");
  await context.close();
});

test("another site's form can't answer for the reader", async ({ browser }) => {
  const { context, page } = await visitor(browser);
  await page.route("http://evil.example/**", (route) => route.fulfill({
    contentType: "text/html",
    body: `<!doctype html><form id="f" method="post" action="http://localhost${ANSWER}"><input name="choice" value="yes"></form>`,
  }));
  await page.goto("http://evil.example/");
  const refused = page.waitForResponse((r) => r.url().endsWith(ANSWER));
  await page.evaluate(() => (document.getElementById("f") as HTMLFormElement).submit());
  const answer = await refused;
  expect(answer.status()).toBe(403);
  expect(answer.request().headers()["origin"]).toBe("http://evil.example");
  expect(await answer.headerValue("set-cookie")).toBeNull();
  expect(await consentCookie(context)).toBeUndefined();
  await open(page, "/all");
  expect(await attr(page)).toBe("ask");
  await context.close();
});

test("reopening the banner and closing it sends nothing, and only an answer does", async ({ browser }) => {
  const { context, page, seen } = await visitor(browser);
  await answered(context);
  await open(page, "/help/faq");
  await expectReopens(page, page.locator("[data-consent-open]").first());
  expect(seen.answers).toEqual([]);
  await context.close();
});

// --- Cookie choices, everywhere the plan names ------------------------------------------------------------------------------------------------------

async function expectReopens(page: Page, link: ReturnType<Page["locator"]>) {
  await expect(banner(page)).toBeHidden();
  await link.click();
  await expect(banner(page)).toBeVisible();
  expect(await page.evaluate(() => document.activeElement!.getAttribute("data-consent-choice")), "focus is in the banner").toBe("no");
  await page.locator("[data-consent-close]").click();
  await expect(banner(page)).toBeHidden();
}

// (each with an answer already given, so the banner isn't open by itself)
async function answered(context: BrowserContext) {
  await context.addCookies([{ name: "video_consent", value: `1.yes.${Math.floor(Date.now() / 1000)}`, domain: "localhost", path: "/" }]);
}

test("the footer, the privacy page", async ({ browser }) => {
  const { context, page } = await visitor(browser);
  await answered(context);
  await open(page, "/all");
  await expectReopens(page, page.locator("#main-content-row [data-consent-open], .sticky-63 [data-consent-open]").first());
  await open(page, "/help/privacy");
  await expect(page.locator("#videos")).toHaveText("Videos from other sites");
  await expectReopens(page, page.locator("#article [data-consent-open], .container [data-consent-open]").first());
  await page.locator("#article [data-consent-open], .container [data-consent-open]").first().click();
  await page.screenshot({ path: "shots/consent/privacy.png" });
  await context.close();
});

test("the account menu has no link, on both shells", async ({ browser }) => {
  const { context, page } = await visitor(browser, { storageState: USER });
  await answered(context);
  for (const path of ["/all", "/settings/content"]) {
    await open(page, path);
    await page.locator("#dropdownMenuLink").click();
    await expect(page.locator(".account-menu .dropdown-item").first()).toBeVisible();
    await expect(page.locator(".account-menu [data-consent-open]")).toHaveCount(0);
  }
  await context.close();
});

test("sign-up asks, so the new member's first page has no banner", async ({ browser }) => {
  const { context, page } = await visitor(browser);
  await open(page, "/signup");
  const form = page.locator("#signup");
  await expect(page.locator("#signup-video-yes")).not.toBeChecked();
  await expect(page.locator("#signup-video-no")).not.toBeChecked();
  await page.locator('label[for="signup-age-adult"]').click();
  await page.locator('label[for="termsCheck"]').click();
  await page.locator("#username-register").fill("someone_new");
  await page.locator("#password-register").fill("a-Password-1");
  await page.locator("#password_confirm").fill("a-Password-1");
  const stillMissing = () => form.evaluate((el: HTMLFormElement) => Array.from(el.elements).filter((e: any) => e.willValidate && !e.validity.valid).map((e: any) => e.name));
  expect(await stillMissing()).toContain("video_consent");
  await page.locator('label[for="signup-video-no"]').click();
  expect(await stillMissing()).not.toContain("video_consent");
  await page.locator("fieldset:has(#signup-video-yes)").screenshot({ path: "shots/consent/signup.png" });
  await context.close();
});

test("sign-up doesn't ask someone who has already answered", async ({ browser }) => {
  const { context, page } = await visitor(browser);
  await answered(context);
  await open(page, "/signup");
  await expect(page.locator("#signup-video-yes")).toHaveCount(0);
  await context.close();
});

test("Settings > Content > Video and audio", async ({ browser }) => {
  const { context, page } = await visitor(browser, { storageState: USER });
  await answered(context);
  await open(page, "/settings/content");
  const row = page.locator(".settings-section [data-consent-open]");
  await row.scrollIntoViewIfNeeded();
  await page.screenshot({ path: "shots/consent/settings.png" });
  await expectReopens(page, row);
  await context.close();
});

// what the banner says is true now: scrolling may not start a player for any of these, whatever the answer
for (const o of [{ name: "reduced motion", reducedMotion: "reduce" as const }, { name: "Tap to play", tap: true }]) {
  test(`a reopened banner says a video waits for a press, with ${o.name}, and it does`, async ({ browser }) => {
    const { context, page } = await visitor(browser, { reducedMotion: o.reducedMotion });
    await answered(context);
    if (o.tap) await context.addCookies([{ name: "video_autoplay", value: "tap", domain: "localhost", path: "/" }]);
    await open(page, `/post/${ids.youtube}`);
    await page.locator("[data-consent-open]").first().evaluate((el: HTMLElement) => el.click());
    await expect(page.locator("[data-consent-now='wait']")).toBeVisible();
    await expect(page.locator("[data-consent-now='allowed']")).toBeHidden();
    await expect(page.locator("[data-consent-choice='yes']")).toHaveAttribute("aria-pressed", "true");
    await page.locator("[data-consent-close]").click();
    await page.locator("[data-embed-kind]").first().scrollIntoViewIfNeeded();
    await page.waitForTimeout(1000);
    expect(await players(page)).toBe(0);
    await context.close();
  });
}

test("a reopened banner says the same on a page with no players (help, settings), where the rule is still loaded", async ({ browser }) => {
  for (const o of [{ path: "/help/faq", storageState: undefined }, { path: "/settings/content", storageState: USER }]) {
    const { context, page } = await visitor(browser, { storageState: o.storageState });
    await answered(context);
    await context.addCookies([{ name: "video_autoplay", value: "tap", domain: "localhost", path: "/" }]);
    await open(page, o.path);
    await page.locator("[data-consent-open]").first().evaluate((el: HTMLElement) => el.click());
    await expect(page.locator("[data-consent-now='wait']"), o.path).toBeVisible();
    await expect(page.locator("[data-consent-now='allowed']"), o.path).toBeHidden();
    await context.addCookies([{ name: "video_autoplay", value: "", domain: "localhost", path: "/" }]);
    await open(page, o.path);
    await page.locator("[data-consent-open]").first().evaluate((el: HTMLElement) => el.click());
    await expect(page.locator("[data-consent-now='allowed']"), o.path).toBeVisible();
    await context.close();
  }
});

test("a reopened banner says the players load by themselves where they do", async ({ browser }) => {
  const { context, page } = await visitor(browser);
  await answered(context);
  await open(page, `/post/${ids.youtube}`);
  await page.locator("[data-consent-open]").first().evaluate((el: HTMLElement) => el.click());
  await expect(page.locator("[data-consent-now='allowed']")).toBeVisible();
  await expect(page.locator("[data-consent-now='wait']")).toBeHidden();
  await context.close();
});

test("the phone menu: it folds, so the banner isn't under it", async ({ browser }) => {
  const { context, page } = await visitor(browser, { width: 390, hasTouch: true });
  await answered(context);
  await open(page, "/all");
  await page.locator("#navbar .navbar-toggler").click();
  const link = page.locator(".navbar-phone-menu [data-consent-open], #navbarResponsive [data-consent-open]").first();
  await expect(link).toBeVisible();
  await link.click();
  await expect(banner(page)).toBeVisible();
  await expect(page.locator("#navbarResponsive.show")).toHaveCount(0, { timeout: 2000 });
  const box = await banner(page).boundingBox();
  const hit = await page.evaluate(([x, y]) => document.elementFromPoint(x, y)!.closest("#consent-banner") !== null, [box!.x + box!.width / 2, box!.y + 10]);
  expect(hit, "nothing covers it").toBe(true);
  await page.screenshot({ path: "shots/consent/phone-menu.png" });
  await context.close();
});

// --- Google's message goes first -------------------------------------------------------------------------------------------------------------------

// A stand-in for the tag: dev has no ADSENSE_CLIENT, so it is put into the page here. `fires` is when it says its consent data is ready (null: never).
async function withStandInTag(page: Page, fires: number | null) {
  await page.route("**/pagead/js/adsbygoogle.js*", (route) => route.fulfill({
    contentType: "text/javascript",
    body: fires === null ? "" : `setTimeout(function () { (window.googlefc.callbackQueue || []).forEach(function (q) { if (q.CONSENT_DATA_READY) q.CONSENT_DATA_READY(); }); }, ${fires});`,
  }));
  await page.route("**/all", async (route) => {
    const answer = await route.fetch();
    const html = (await answer.text()).replace("</body>", '<script async src="https://pagead2.googlesyndication.com/pagead/js/adsbygoogle.js?client=ca-pub-0000000000000000"></script></body>');
    await route.fulfill({ response: answer, body: html });
  });
}

test("with the ad tag on the page the banner waits until Google's message is done", async ({ browser }) => {
  const { context, page } = await visitor(browser);
  await withStandInTag(page, 2000);
  await page.goto("/all", { waitUntil: "domcontentloaded" });
  await page.waitForTimeout(1000);
  await expect(banner(page)).toBeHidden();
  await expect(banner(page)).toBeVisible({ timeout: 3000 });
  await context.close();
});

test("and no longer than five seconds, for a tag that never answers", async ({ browser }) => {
  const { context, page } = await visitor(browser);
  await withStandInTag(page, null);
  await page.goto("/all", { waitUntil: "domcontentloaded" });
  await page.waitForTimeout(3500);
  await expect(banner(page)).toBeHidden();
  await expect(banner(page)).toBeVisible({ timeout: 3000 });
  await context.close();
});

// --- the players that aren't on a feed ----------------------------------------------------------------------------------------------------------------

test("a Space's replay waits for a press where the reader is asked", async ({ browser }) => {
  const { context, page, seen } = await visitor(browser, { storageState: USER });
  await open(page, ids.space);
  await expect(page.locator("iframe.space-replay-frame")).toHaveCount(0);
  await expect(banner(page)).toBeVisible();
  await expect(page.locator(".space-replay-wait .embed-consent-note")).toBeVisible();
  await expect(page.locator(".space-replay-wait .embed-consent-note")).toContainText("Pressing play loads YouTube");
  await page.screenshot({ path: "shots/consent/space.png" });
  expect(seen.players).toEqual([]);
  await page.locator(".space-replay-wait .embed-lg-play").click();
  await expect(page.locator(".space-replay-wait iframe")).toHaveCount(1);
  expect(seen.players.length).toBe(1);
  expect(seen.players[0]).toContain("youtube-nocookie.com/embed/");
  await context.close();
});

test("a Space's replay keeps its frame after Allow and where nobody is asked", async ({ browser }) => {
  for (const o of [{ country: "US" }, { allowed: true }] as { country?: string; allowed?: boolean }[]) {
    const { context, page } = await visitor(browser, { storageState: USER, country: o.country });
    if (o.allowed) await answered(context);
    await open(page, ids.space);
    await expect(page.locator("iframe.space-replay-frame")).toHaveCount(1);
    await expect(page.locator(".space-replay-wait")).toHaveCount(0);
    await expect(banner(page)).toBeHidden();
    await context.close();
  }
});

test("taking it back on a Space's page takes its replay down, and the next page draws it waiting", async ({ browser }) => {
  const { context, page } = await visitor(browser, { storageState: USER });
  await answered(context);
  await open(page, ids.space);
  await expect(page.locator("iframe.space-replay-frame")).toHaveCount(1);
  await page.evaluate(() => {
    const link = document.createElement("a");
    link.setAttribute("data-consent-open", "");
    document.body.appendChild(link);
    link.click();
  });
  await page.locator("[data-consent-choice='no']").click();
  await expect(page.locator("iframe.space-replay-frame")).toHaveCount(0);
  await page.reload();
  await expect(page.locator(".space-replay-wait")).toHaveCount(1);
  await context.close();
});

test("a story's video loads as the story comes up once the reader has allowed videos", async ({ browser }) => {
  const { context, page } = await visitor(browser, { storageState: USER });
  await answered(context);
  await open(page, "/@ui_viewer");
  await page.locator("[data-story-user]").first().click();
  await expect(page.locator(".story-frame")).toHaveCount(1);
  await expect(page.locator(".story-video-press")).toHaveCount(0);
  await context.close();
});

test("a story's video waits for a press where the reader is asked", async ({ browser }) => {
  const { context, page, seen } = await visitor(browser, { storageState: USER });
  await open(page, "/@ui_viewer");
  await page.locator("[data-story-user]").first().click();
  await expect(page.locator(".story-video-press")).toBeVisible();
  await page.screenshot({ path: "shots/consent/story.png" });
  await expect(page.locator(".story-frame")).toHaveCount(0);
  expect(seen.players).toEqual([]);
  await page.locator(".story-video-press").click();
  await expect(page.locator(".story-frame")).toHaveCount(1);
  expect(seen.players.length).toBe(1);
  await context.close();
});
