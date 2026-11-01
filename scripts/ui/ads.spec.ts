import { test, expect, type Browser, type BrowserContext, type Page } from "@playwright/test";
import AxeBuilder from "@axe-core/playwright";
import { readFileSync, mkdirSync } from "node:fs";

// Ads in a real browser (CLAUDE.md "Ads"). Never Google's tag: automated page loads on a real tag are invalid traffic, so every request to it is
// aborted or answered here, and the client id is made up. The dev site has no AdSense settings, so this runs against a second copy of the site with them:
//   docker compose run -d --rm --no-deps --name ruqqus-ads-spec -p 8090:8090 -e ADSENSE_CLIENT=ca-pub-0000000000000000 -e ADSENSE_SLOT_FEED=1111111111 \
//     -e ADSENSE_SLOT_POST=2222222222 -e ADSENSE_SLOT_SIDEBAR=3333333333 ruqqus /opt/ruqqus/service/venv/bin/gunicorn ruqqus.__main__:app -w 2 -k gevent --bind 0.0.0.0:8090
//   docker exec ruqqus-ads-spec sh -c 'PYTHONPATH=. venv/bin/python scripts/ui/seed_ads.py'      (first; it writes .fixtures_ads.json)
//   UI_BASE_URL=http://localhost:8090 npm run ads
//   docker stop ruqqus-ads-spec
// What a test can't hold: that Google fills anything, honours non-personalised, or shows its message. The tag here is at most a stand-in.

const fixtures = JSON.parse(readFileSync(new URL("./.fixtures_ads.json", import.meta.url), "utf8"));
const TAG = "**://pagead2.googlesyndication.com/**";
const CLEAN = "/+ui_ads_clean?sort=new";
const POST = `/post/${fixtures.text}`;
const SHOTS = new URL("./shots/ads/", import.meta.url).pathname;

type Options = { member?: string; country?: string; width?: number; stored?: Record<string, string>; tab?: Record<string, string>; storageThrows?: boolean; tag?: string };

// a context with the tag's requests counted and aborted (or answered with `tag`: a stand-in's own text)
async function open(browser: Browser, baseURL: string, options: Options = {}) {
  const context = await browser.newContext({
    baseURL,
    viewport: { width: options.width ?? 1280, height: 900 },
    extraHTTPHeaders: options.country ? { "CF-IPCountry": options.country } : {},
  });
  if (options.member) {
    await context.addCookies([{ name: "session_ruqqus", value: fixtures.cookies[options.member], url: baseURL }]);
  }
  const asked: string[] = [];
  await context.route(TAG, (route) => {
    asked.push(route.request().url());
    return options.tag === undefined ? route.abort() : route.fulfill({ status: 200, contentType: "text/javascript", body: options.tag });
  });
  if (options.stored || options.tab) {
    await context.addInitScript(([stored, tab]) => {
      // only until the site's own script has had its say on this load, so a removal it makes can be read back
      if (!sessionStorage.getItem("seeded")) {
        sessionStorage.setItem("seeded", "1");
        for (const [key, value] of Object.entries(stored)) localStorage.setItem(key, value as string);
        for (const [key, value] of Object.entries(tab)) sessionStorage.setItem(key, value as string);
      }
    }, [options.stored ?? {}, options.tab ?? {}]);
  }
  if (options.storageThrows) {
    await context.addInitScript(() => {
      Storage.prototype.getItem = () => { throw new Error("blocked"); };
      Storage.prototype.setItem = () => { throw new Error("blocked"); };
      Storage.prototype.removeItem = () => { throw new Error("blocked"); };
    });
  }
  return { context, asked };
}

async function go(context: BrowserContext, path: string): Promise<Page> {
  const page = await context.newPage();
  await page.goto(path, { waitUntil: "load" });
  // ads.js is deferred and the tag async: past both
  await page.waitForTimeout(250);
  return page;
}

const visibleBoxes = (page: Page) => page.locator(".ad-slot:visible").count();
const pushes = (page: Page) => page.evaluate(() => ((window as any).adsbygoogle || []).length);
const nonPersonal = (page: Page) => page.evaluate(() => ((window as any).adsbygoogle || {}).requestNonPersonalizedAds);
const noSidewaysScroll = (page: Page) => page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth);

test.describe("what a visitor gets", () => {
  test("the tag once, non-personalised, boxes filled and in-feed ads eight cards apart", async ({ browser, baseURL }) => {
    const { context, asked } = await open(browser, baseURL!);
    const page = await go(context, CLEAN);
    expect(asked).toHaveLength(1);
    expect(asked[0]).toContain("client=ca-pub-0000000000000000");
    expect(await nonPersonal(page)).toBe(1);
    const feed = await page.locator("#posts .ad-slot-feed").count();
    expect(feed).toBe(3);                     // 25 cards on the first page
    expect(await page.locator(".ad-slot-sidebar:visible").count()).toBe(1);
    expect(await pushes(page)).toBe(await visibleBoxes(page));
    const gaps = await page.evaluate(() => {
      const kids = Array.from(document.getElementById("posts")!.children);
      let run = 0;
      const out: number[] = [];
      for (const kid of kids) {
        if (kid.classList.contains("ad-slot")) { out.push(run); run = 0; } else if (/^post-/.test(kid.id)) run += 1;
      }
      return out;
    });
    expect(gaps).toEqual([8, 8, 8]);
    mkdirSync(SHOTS, { recursive: true });
    await page.screenshot({ path: SHOTS + "feed-1280.png" });
    await context.close();
  });

  test("a phone: the hidden sidebar's box is never asked for, and nothing scrolls sideways", async ({ browser, baseURL }) => {
    const { context } = await open(browser, baseURL!, { width: 390 });
    const page = await go(context, CLEAN);
    expect(await page.locator(".ad-slot-sidebar:visible").count()).toBe(0);
    expect(await pushes(page)).toBe(await visibleBoxes(page));
    expect(await noSidewaysScroll(page)).toBe(true);
    const filled = await page.locator(".ad-slot-sidebar ins[data-filled-by]").count();
    expect(filled).toBe(0);
    await page.locator("#posts .ad-slot-feed").first().scrollIntoViewIfNeeded();
    await page.screenshot({ path: SHOTS + "feed-390.png" });
    await context.close();
  });

  test("a hidden box is filled when it comes into view", async ({ browser, baseURL }) => {
    const { context } = await open(browser, baseURL!, { width: 390 });
    const page = await go(context, CLEAN);
    await page.setViewportSize({ width: 1280, height: 900 });
    await expect(page.locator(".ad-slot-sidebar ins[data-filled-by]")).toHaveCount(1);
    await context.close();
  });

  test("the post page has its box, and a clean post with a comment is enough", async ({ browser, baseURL }) => {
    const { context, asked } = await open(browser, baseURL!);
    const page = await go(context, POST);
    expect(await page.locator(".ad-slot-post:visible").count()).toBe(1);
    expect(asked).toHaveLength(1);
    await page.screenshot({ path: SHOTS + "post-1280.png" });
    await context.close();
  });

  for (const [name, key] of [["a rated comment", "rated"], ["a picture in a comment", "media"], ["nothing to read", "empty"]]) {
    test(`a post page with ${name} has no box and asks Google for nothing`, async ({ browser, baseURL }) => {
      const { context, asked } = await open(browser, baseURL!);
      const page = await go(context, `/post/${fixtures[key]}`);
      expect(await page.locator(".ad-slot").count()).toBe(0);
      expect(asked).toHaveLength(0);
      await context.close();
    });
  }

  test("one unsafe card on the page: no box, no feed template, no tag", async ({ browser, baseURL }) => {
    const { context, asked } = await open(browser, baseURL!);
    const page = await go(context, "/+ui_ads_unsafe?sort=new");
    expect(await page.locator(".ad-slot").count()).toBe(0);
    expect(await page.locator("#feed-ad").count()).toBe(0);
    expect(await page.locator('[data-ad-safe="0"]').count()).toBe(1);
    expect(asked).toHaveLength(0);
    await context.close();
  });

  test("a search page: no box, no tag", async ({ browser, baseURL }) => {
    const { context, asked } = await open(browser, baseURL!);
    const page = await go(context, "/search?q=fixture");
    expect(await page.locator(".ad-slot").count()).toBe(0);
    expect(asked).toHaveLength(0);
    await context.close();
  });

  test("an unsafe card the continuous feed brings in stops the ads and hides them", async ({ browser, baseURL }) => {
    const { context } = await open(browser, baseURL!);
    const page = await go(context, "/+ui_ads_mixed?sort=new");
    expect(await visibleBoxes(page)).toBeGreaterThan(0);
    expect(await page.locator('[data-ad-safe="0"]').count()).toBe(0);
    await page.evaluate(() => window.scrollTo(0, document.body.scrollHeight));
    await expect(page.locator('[data-ad-safe="0"]')).toHaveCount(1, { timeout: 15_000 });
    await expect(page.locator(".ad-slot:visible")).toHaveCount(0);
    const placed = await page.locator("#posts .ad-slot-feed").count();
    await page.evaluate(() => (window as any).placeFeedAds());
    expect(await page.locator("#posts .ad-slot-feed").count()).toBe(placed);
    await context.close();
  });

  test("however long it scrolls, six in-feed ads on a page at the most", async ({ browser, baseURL }) => {
    const { context } = await open(browser, baseURL!);
    const page = await go(context, CLEAN);
    for (let i = 0; i < 2; i += 1) {
      const before = await page.locator('#posts > [id^="post-"]').count();
      await page.evaluate(() => window.scrollTo(0, document.body.scrollHeight));
      await expect.poll(() => page.locator('#posts > [id^="post-"]').count(), { timeout: 15_000 }).toBeGreaterThan(before);
      await page.waitForTimeout(100);
    }
    expect(await page.locator('#posts > [id^="post-"]').count()).toBe(60);
    expect(await page.locator("#posts .ad-slot-feed").count()).toBe(6);
    await context.close();
  });
});

for (const colorScheme of ["light", "dark"] as const) {
  test(`axe has nothing serious to say about the boxes, ${colorScheme}`, async ({ browser, baseURL }) => {
    const { context } = await open(browser, baseURL!);
    const page = await context.newPage();
    await page.emulateMedia({ colorScheme });
    await page.goto(CLEAN, { waitUntil: "load" });
    await page.waitForTimeout(250);
    const found = (await new AxeBuilder({ page }).include(".ad-slot").analyze()).violations;
    expect(found.filter((v) => v.impact === "serious" || v.impact === "critical").map((v) => v.id)).toEqual([]);
    await context.close();
  });
}

test.describe("a signed-out browser that still holds chat", () => {
  for (const key of ["ruqqus_chat_session", "ruqqus_call_session"]) {
    test(`${key}: nothing goes to Google and the boxes fold, on a feed and a post page, at both widths`, async ({ browser, baseURL }) => {
      for (const width of [1280, 390]) {
        const { context, asked } = await open(browser, baseURL!, { width, stored: { [key]: '{"user_id":"@x:y"}' } });
        for (const path of [CLEAN, POST]) {
          const page = await go(context, path);
          expect(await visibleBoxes(page)).toBe(0);
          expect(await page.locator("#posts .ad-slot-feed").count()).toBe(0);
          expect(await pushes(page)).toBe(0);
          expect(await page.evaluate(() => (window as any).adsHeld)).toBe(true);
          expect(await noSidewaysScroll(page)).toBe(true);
          await page.close();
        }
        expect(asked).toHaveLength(0);
        await context.close();
      }
    });
  }

  test("the boxes never paint: a post page's first paint already has them folded", async ({ browser, baseURL }) => {
    const { context } = await open(browser, baseURL!, { stored: { ruqqus_chat_session: "{}" } });
    const page = await context.newPage();
    // when the parser has gone past the box and the small script after it, and long before anything at the end of <body> has run
    await page.addInitScript(() => {
      new MutationObserver(() => {
        const box = document.querySelector(".ad-slot-post");
        const past = box && box.nextElementSibling && box.nextElementSibling.nextElementSibling;
        if (past && !(window as any).boxAtParse) {
          (window as any).boxAtParse = getComputedStyle(box).display;
          (window as any).tagWasAsked = !!(window as any).loadAdTag;
        }
      }).observe(document, { childList: true, subtree: true });
    });
    await page.goto(POST, { waitUntil: "load" });
    expect(await page.evaluate(() => (window as any).boxAtParse)).toBe("none");
    expect(await page.evaluate(() => (window as any).tagWasAsked)).toBe(false);
    await context.close();
  });

  test("storage that throws counts as nothing held", async ({ browser, baseURL }) => {
    const { context, asked } = await open(browser, baseURL!, { storageThrows: true });
    const page = await go(context, POST);
    expect(asked).toHaveLength(1);
    expect(await page.locator(".ad-slot-post:visible").count()).toBe(1);
    await context.close();
  });

  test("a member's own chat is the accepted risk, and the pre-key goes before the tag runs, this tab's and an older copy", async ({ browser, baseURL }) => {
    const own = JSON.stringify({ user_id: fixtures.mxids.ui_ads_adult, device_id: "MINE" });
    const { context, asked } = await open(browser, baseURL!, {
      member: "ui_ads_adult",
      stored: { ruqqus_chat_session: own, ruqqus_call_session: own, uqqus_chat_prekey: "an older copy" },
      tab: { uqqus_chat_prekey: "a key" },
      tag: "window.prekeyAtTag = [sessionStorage.getItem('uqqus_chat_prekey'), localStorage.getItem('uqqus_chat_prekey')];",
    });
    const page = await go(context, POST);
    expect(asked).toHaveLength(1);
    await expect.poll(() => page.evaluate(() => (window as any).prekeyAtTag)).toEqual([null, null]);
    expect(await page.evaluate(() => localStorage.getItem("ruqqus_chat_session"))).toBe(own);
    expect(await page.locator(".ad-slot-post:visible").count()).toBe(1);
    await context.close();
  });

  // Account A logged out and its keys stayed here. B signing in doesn't make them B's: nothing goes to Google until B opens chat, which clears them.
  for (const key of ["ruqqus_chat_session", "ruqqus_call_session"]) {
    for (const [name, value] of [["another account's", '{"user_id":"@ruqqus_1:ruqqus.local","device_id":"ACCOUNT_A"}'], ["one that names nobody", "{}"]]) {
      test(`${key}, ${name}: a member's page asks Google for nothing and folds its boxes, at both widths`, async ({ browser, baseURL }) => {
        for (const width of [1280, 390]) {
          const { context, asked } = await open(browser, baseURL!, { member: "ui_ads_adult", width, stored: { [key]: value }, tag: "window.tagRan = true;" });
          for (const path of [CLEAN, POST]) {
            const page = await go(context, path);
            expect(await visibleBoxes(page)).toBe(0);
            expect(await page.locator("#posts .ad-slot-feed").count()).toBe(0);
            expect(await page.evaluate(() => (window as any).adsHeld)).toBe(true);
            expect(await page.evaluate(() => (window as any).tagRan)).toBeUndefined();
            expect(await noSidewaysScroll(page)).toBe(true);
            await page.close();
          }
          expect(asked).toHaveLength(0);
          await context.close();
        }
      });
    }
  }

  test("a member's privacy page: the settings link is only a link when another account's chat is here", async ({ browser, baseURL }) => {
    const { context, asked } = await open(browser, baseURL!, { member: "ui_ads_premium", stored: { ruqqus_chat_session: '{"user_id":"@ruqqus_1:ruqqus.local"}' } });
    const page = await go(context, "/help/privacy");
    await page.locator("#ads ~ p [data-ad-consent]").first().click();
    await page.waitForTimeout(200);
    expect(asked).toHaveLength(0);
    await context.close();
  });
});

test("a tag running in another tab can't read the key a sign-in writes in this one", async ({ browser, baseURL }) => {
  const { context, asked } = await open(browser, baseURL!, {
    tag: `window.seen = [];
          setInterval(function () {
            ["localStorage", "sessionStorage"].forEach(function (name) {
              var value = window[name].getItem("uqqus_chat_prekey");
              if (value) window.seen.push(name);
            });
          }, 20);`,
  });
  const reader = await go(context, POST);
  expect(asked).toHaveLength(1);
  const signing = await context.newPage();
  await signing.goto("/login");
  await signing.fill("#username", "ui_ads_fresh");
  await signing.fill("#password", "ui-Password-1");
  await Promise.all([signing.waitForURL((url) => !url.pathname.startsWith("/login")), signing.click("#login_button")]);
  expect(await signing.evaluate(() => sessionStorage.getItem("uqqus_chat_prekey"))).toMatch(/^[\w-]{40,}$/);
  await reader.waitForTimeout(500);
  expect(await reader.evaluate(() => (window as any).seen.length)).toBe(0);
  // the stand-in really was reading: it finds what is put where it can look
  await signing.evaluate(() => localStorage.setItem("uqqus_chat_prekey", "put there by this spec"));
  await expect.poll(() => reader.evaluate(() => (window as any).seen.length)).toBeGreaterThan(0);
  await signing.evaluate(() => localStorage.removeItem("uqqus_chat_prekey"));
  await context.close();
});

test.describe("who gets what", () => {
  const cases: [string, string | undefined, string | undefined, boolean][] = [
    ["an adult in the US", "ui_ads_adult", "US", true],
    ["an adult in the UK", "ui_ads_adult", "GB", false],
    ["an adult in Germany", "ui_ads_adult", "DE", false],
    ["an adult in Switzerland", "ui_ads_adult", "CH", false],
    ["an adult whose country isn't known", "ui_ads_adult", undefined, false],
    ["an adult on Tor", "ui_ads_adult", "T1", false],
    ["an undeclared member in the US", "ui_ads_undeclared", "US", false],
    ["a visitor in the US", undefined, "US", false],
  ];
  for (const [name, member, country, personal] of cases) {
    test(`${name}: ${personal ? "personalised" : "non-personalised"}`, async ({ browser, baseURL }) => {
      const { context, asked } = await open(browser, baseURL!, { member, country });
      const page = await go(context, POST);
      expect(asked).toHaveLength(1);
      expect(await nonPersonal(page)).toBe(personal ? undefined : 1);
      await context.close();
    });
  }

  for (const member of ["ui_ads_child", "ui_ads_premium"]) {
    test(`${member}: no box and no tag on a feed or a post, and on the privacy page only after pressing the link`, async ({ browser, baseURL }) => {
      const { context, asked } = await open(browser, baseURL!, {
        member,
        // what Google's script does: runs what is queued once its API is ready
        tag: `window.googlefc = window.googlefc || {}; window.googlefc.showRevocationMessage = function () { window.reopened = true; };
              setTimeout(function () { (window.googlefc.callbackQueue || []).forEach(function (q) { if (q.CONSENT_API_READY) q.CONSENT_API_READY(); }); }, 100);`,
      });
      for (const path of [CLEAN, POST]) {
        const page = await go(context, path);
        expect(await page.locator(".ad-slot").count()).toBe(0);
        await page.close();
      }
      expect(asked).toHaveLength(0);
      const page = await go(context, "/help/privacy");
      expect(asked).toHaveLength(0);
      await page.locator("#ads ~ p [data-ad-consent]").first().click();
      await expect.poll(() => asked.length).toBe(1);
      await expect.poll(() => page.evaluate(() => (window as any).reopened)).toBe(true);
      await context.close();
    });
  }

  test("a visitor's privacy page loads the tag; held there, the link is only a link", async ({ browser, baseURL }) => {
    const plain = await open(browser, baseURL!);
    await go(plain.context, "/help/privacy");
    expect(plain.asked).toHaveLength(1);
    await plain.context.close();
    const held = await open(browser, baseURL!, { stored: { ruqqus_chat_session: "{}" } });
    const page = await go(held.context, "/help/privacy");
    await page.locator("#ads ~ p [data-ad-consent]").first().click();
    await page.waitForTimeout(200);
    expect(held.asked).toHaveLength(0);
    await held.context.close();
  });
});

test.describe("the consent banner and the tag", () => {
  const banner = (page: Page) => page.locator("#consent-banner");
  const ready = (after: number | null) => (after === null ? "" :
    `setTimeout(function () { (window.googlefc && window.googlefc.callbackQueue || []).forEach(function (q) { if (q.CONSENT_DATA_READY) q.CONSENT_DATA_READY(); }); }, ${after});`);

  test("with the tag loaded by script the banner waits for Google's message", async ({ browser, baseURL }) => {
    const { context } = await open(browser, baseURL!, { tag: ready(1500) });
    const page = await go(context, CLEAN);
    await expect(banner(page)).toBeHidden();
    await expect(banner(page)).toBeVisible({ timeout: 4000 });
    await context.close();
  });

  test("a tag that never says so doesn't lose the question: five seconds at the most", async ({ browser, baseURL }) => {
    const { context } = await open(browser, baseURL!, { tag: ready(null) });
    const page = await go(context, CLEAN);
    await expect(banner(page)).toBeHidden();
    await expect(banner(page)).toBeVisible({ timeout: 6000 });
    await context.close();
  });

  for (const [name, options, path] of [
    ["a page with no tag", {}, "/+ui_ads_unsafe?sort=new"],
    ["a held browser", { stored: { ruqqus_chat_session: "{}" } }, CLEAN],
  ] as [string, Options, string][]) {
    test(`${name} doesn't wait`, async ({ browser, baseURL }) => {
      const { context, asked } = await open(browser, baseURL!, options);
      const page = await go(context, path);
      await expect(banner(page)).toBeVisible({ timeout: 1000 });
      expect(asked).toHaveLength(0);
      await context.close();
    });
  }
});

test("a password sign-in holds the ads, and the pre-key stays for chat", async ({ browser, baseURL }) => {
  const { context, asked } = await open(browser, baseURL!);
  const page = await context.newPage();
  await page.goto("/login");
  await page.fill("#username", "ui_ads_fresh");
  await page.fill("#password", "ui-Password-1");
  await Promise.all([page.waitForURL((url) => !url.pathname.startsWith("/login")), page.click("#login_button")]);
  await page.goto(POST, { waitUntil: "load" });
  await page.waitForTimeout(250);
  expect(await page.locator(".ad-slot").count()).toBe(0);
  expect(asked).toHaveLength(0);
  expect(await page.evaluate(() => sessionStorage.getItem("uqqus_chat_prekey"))).not.toBeNull();
  await context.close();
});
