import { test, expect, type Browser, type BrowserContext, type Page } from "@playwright/test";
import AxeBuilder from "@axe-core/playwright";
import { open } from "./pages";

// The age question (CLAUDE.md "Age declaration"; helpers/age_declaration.py, partials/age_question.html, partials/age_notice.html):
//   docker compose exec -T ruqqus env PYTHONPATH=. /opt/ruqqus/service/venv/bin/python scripts/ui/seed_age.py     before each run
//   npm run age
// What it holds it to, in a real browser: sign-up can't be sent without an answer and nothing is answered for them; the notice sits
// in the page's flow, moves and covers nothing, leaves every link working, isn't in a side panel's frame, and isn't scrolling the page
// sideways at 390px; the two choices read the same on sign-up, the notice and Settings; "child" asks again first, and Go back saves
// nothing; the chat page keeps the screen and doesn't carry the notice; both choices are a target tall (32, 44 under a finger); once answered it is gone for good; a child account's word filter and sensitive switch are off in Settings and refused by
// the routes; an admin can change the answer. A real sign-up is tests/pg/test_age_declaration.py: the dev site allows one account an
// hour from its one Docker address, and refuses a headless browser's name.

const PASSWORD = "ui-Password-1";
const PHONE = { viewport: { width: 390, height: 844 }, isMobile: true, hasTouch: true } as const;
const DESKTOP = { viewport: { width: 1280, height: 900 } } as const;
const NOTICE = "#age-notice";
const ADULT = "I'm 18 or over";
const CHILD = "I'm a parent or guardian setting this up for my child (13 to 17)";

test.describe.configure({ mode: "serial" });
test.setTimeout(300_000);

// one sign-in per account for the whole run (sign-ins are limited to 6 a minute per address)
const signedIn = new Map<string, Awaited<ReturnType<BrowserContext["storageState"]>>>();

async function member(browser: Browser, name: string, options: object = DESKTOP) {
  if (!signedIn.has(name)) {
    const first = await browser.newContext(DESKTOP);
    const form = await first.newPage();
    for (let attempt = 0; ; attempt++) {
      await open(form, "/login");
      await form.fill("#username", name);
      await form.fill("#password", PASSWORD);
      await form.click("#login_button");
      const through = await form.waitForURL((url) => !url.pathname.startsWith("/login"), { timeout: 10_000 }).then(() => true, () => false);
      if (through) break;
      if (attempt === 2) throw new Error(`could not sign in as ${name}`);
      await form.waitForTimeout(61_000);     // the limit may be spent by another run against the same site
    }
    signedIn.set(name, await first.storageState());
    await first.close();
  }
  const context = await browser.newContext({ ...options, storageState: signedIn.get(name) });
  return { context, page: await context.newPage() };
}

// the white label on the brand fill is the owner's one exception to AA (CLAUDE.md "Colour contrast")
const notice_axe = (page: Page) => new AxeBuilder({ page }).include(NOTICE).exclude(`${NOTICE} .btn-primary`).withTags(["wcag2a", "wcag2aa"]).analyze();

const sideways = (page: Page) => page.evaluate(() => document.documentElement.scrollWidth - document.documentElement.clientWidth);

test.describe("signing up", () => {
  for (const [label, options] of [["desktop", DESKTOP], ["phone", PHONE]] as const) {
    test(`the form asks, answers nothing and won't go without an answer (${label})`, async ({ browser }) => {
      const context = await browser.newContext(options);
      const page = await context.newPage();
      await open(page, "/signup");
      const radios = page.locator('input[name="age_declaration"]');
      await expect(radios).toHaveCount(2);
      await expect(page.locator('input[name="age_declaration"]:checked')).toHaveCount(0);
      await expect(page.getByText(ADULT, { exact: true })).toBeVisible();
      await expect(page.getByText(CHILD, { exact: true })).toBeVisible();
      await page.fill("#username-register", "someone_new");
      await page.fill("#password-register", "ui-Password-1");
      await page.fill("#password_confirm", "ui-Password-1");
      await page.check("#termsCheck", { force: true });
      await page.locator('label[for="signup-video-no"]').click();   // the video question: this site has no country header, so it asks
      expect(await page.locator("#signup").evaluate((form: HTMLFormElement) => form.checkValidity())).toBe(false);
      await page.getByText(ADULT, { exact: true }).click();
      await expect(page.locator("#signup-age-adult")).toBeChecked();
      expect(await page.locator("#signup").evaluate((form: HTMLFormElement) => form.checkValidity())).toBe(true);
      expect(await sideways(page)).toBeLessThanOrEqual(0);
      const least = label === "phone" ? 44 : 32;
      for (const id of ["signup-age-adult", "signup-age-child"]) {
        expect((await page.locator(`label[for="${id}"]`).boundingBox())!.height, id).toBeGreaterThanOrEqual(least);
      }
      await page.locator("fieldset:has(#signup-age-adult)").screenshot({ path: `shots/age/signup-${label}.png` });
      await context.close();
    });
  }

  test("right to left", async ({ browser }) => {
    const context = await browser.newContext(DESKTOP);
    await context.addCookies([{ name: "ui_lang", value: "ar-XB", url: "http://localhost" }]);
    const page = await context.newPage();
    await open(page, "/signup");
    await expect(page.locator("html")).toHaveAttribute("dir", "rtl");
    const box = (await page.locator('label[for="signup-age-adult"]').boundingBox())!;
    const field = (await page.locator("#username-register").boundingBox())!;
    expect(Math.abs(box.x + box.width - (field.x + field.width))).toBeLessThan(30);   // beside the radio, at the fields' right edge
    await page.locator("fieldset:has(#signup-age-adult)").screenshot({ path: "shots/age/signup-rtl.png" });
    await context.close();
  });
});

test.describe("a member from before the question", () => {
  test("the notice is in the page's flow, covers nothing, and the page works without an answer", async ({ browser }) => {
    const { context, page } = await member(browser, "ui_age_old");
    await open(page, "/all");
    const notice = page.locator(NOTICE);
    await expect(notice).toBeVisible();
    expect(await notice.evaluate((el) => getComputedStyle(el).position)).toBe("static");
    await expect(page.locator(".modal.show")).toHaveCount(0);
    await expect(page.getByRole("dialog")).toHaveCount(0);
    expect(await page.evaluate(() => document.activeElement === document.body), "it never takes the focus").toBe(true);
    await expect(notice.getByText(ADULT, { exact: true })).toBeVisible();
    await expect(notice.getByText(CHILD, { exact: true })).toBeVisible();
    await expect(notice.getByRole("link", { name: "Terms" })).toHaveAttribute("href", "/help/terms");
    await page.screenshot({ path: "shots/age/notice-1280-light.png" });

    // nothing is answered by using the site, and the links still lead on
    await page.goto("/browse");
    expect(new URL(page.url()).pathname).toBe("/browse");
    await expect(page.locator(NOTICE)).toBeVisible();

    const axe = await notice_axe(page);
    expect(axe.violations.filter((v) => ["serious", "critical"].includes(v.impact ?? ""))).toEqual([]);

    // a page with a layout of its own still lays out under it
    await open(page, "/notifications");
    await expect(page.locator(NOTICE)).toBeVisible();
    expect(await sideways(page)).toBeLessThanOrEqual(0);
    await page.screenshot({ path: "shots/age/notice-1280-notifications.png" });
    await context.close();
  });

  test("dark, with the same check", async ({ browser }) => {
    const { context, page } = await member(browser, "ui_age_old", { ...DESKTOP, colorScheme: "dark" });
    await open(page, "/all");
    await expect(page.locator(NOTICE)).toBeVisible();
    const axe = await notice_axe(page);
    expect(axe.violations.filter((v) => ["serious", "critical"].includes(v.impact ?? ""))).toEqual([]);
    await page.screenshot({ path: "shots/age/notice-1280-dark.png" });
    await context.close();
  });

  // the chat page is exactly the screen between the bars: a notice above it would push a conversation's box off the screen
  for (const [label, options, bottomBar] of [["desktop", DESKTOP, 0], ["phone", PHONE, 56]] as const) {
    test(`the chat page keeps the screen and doesn't carry the notice (${label})`, async ({ browser }) => {
      const { context, page } = await member(browser, "ui_age_old", options);
      await open(page, "/chat");
      await expect(page.locator(NOTICE)).toHaveCount(0);
      const box = (await page.locator("#chat-app").boundingBox())!;
      const height = options.viewport.height;
      expect(box.y + box.height).toBeLessThanOrEqual(height - bottomBar + 1);
      expect(await page.evaluate(() => document.documentElement.scrollHeight - window.innerHeight)).toBeLessThanOrEqual(1);
      await page.screenshot({ path: `shots/age/chat-${label}.png` });
      await context.close();
    });
  }

  test("a phone: nothing runs off the screen, and a target is big enough for a finger", async ({ browser }) => {
    const { context, page } = await member(browser, "ui_age_old", PHONE);
    for (const path of ["/all", "/browse", "/notifications"]) {
      await open(page, path);
      await expect(page.locator(NOTICE), path).toBeVisible();
      expect(await sideways(page), path).toBeLessThanOrEqual(0);
      const box = (await page.locator(NOTICE).boundingBox())!;
      expect(box.x, path).toBeGreaterThanOrEqual(0);
      expect(box.x + box.width, path).toBeLessThanOrEqual(390);
      await page.screenshot({ path: `shots/age/notice-390${path.replace("/", "-")}.png` });
    }
    await open(page, "/all");
    for (const target of [`${NOTICE} label[for="age-notice-adult"]`, `${NOTICE} label[for="age-notice-child"]`, `${NOTICE} button[type=submit]`]) {
      expect((await page.locator(target).boundingBox())!.height, target).toBeGreaterThanOrEqual(44);
    }
    // the radio is beside its first line, whatever the row's padding
    const label = (await page.locator(`${NOTICE} label[for="age-notice-adult"]`).boundingBox())!;
    const radio = await page.locator(`${NOTICE} label[for="age-notice-adult"]`).evaluate((el) => {
      const dot = getComputedStyle(el, "::before");
      return { top: parseFloat(dot.top), height: parseFloat(dot.height) };
    });
    expect(Math.abs(radio.top + radio.height / 2 - label.height / 2)).toBeLessThanOrEqual(1);
    // the whole row answers, not only the words
    await page.locator(`${NOTICE} label[for="age-notice-adult"]`).click({ position: { x: label.width - 4, y: 4 } });
    await expect(page.locator("#age-notice-adult")).toBeChecked();
    await context.close();
  });

  test("a desktop: both choices and Save are at least a control tall", async ({ browser }) => {
    const { context, page } = await member(browser, "ui_age_old");
    await open(page, "/all");
    for (const target of [`${NOTICE} label[for="age-notice-adult"]`, `${NOTICE} label[for="age-notice-child"]`, `${NOTICE} button[type=submit]`]) {
      expect((await page.locator(target).boundingBox())!.height, target).toBeGreaterThanOrEqual(32);
    }
    await page.locator(NOTICE).screenshot({ path: "shots/age/notice-targets-desktop.png" });
    await context.close();
  });

  test("not in a side panel's frame", async ({ browser }) => {
    const { context, page } = await member(browser, "ui_age_old");
    for (const path of ["/notifications?embed=1", "/all?embed=1", "/chat?embed=1"]) {
      await open(page, path);
      await expect(page.locator(NOTICE), path).toHaveCount(0);
    }
    await context.close();
  });

  test("the largest Text size cuts nothing off", async ({ browser }) => {
    const { context, page } = await member(browser, "ui_age_old");
    await context.addCookies([{ name: "text_size", value: "xlarge", url: "http://localhost" }]);
    await open(page, "/all");
    const clipped = await page.locator(`${NOTICE} *`).evaluateAll((els) => els.filter((el) => el.scrollWidth > el.clientWidth + 1 && getComputedStyle(el).display !== "inline").length);
    expect(clipped).toBe(0);
    await page.locator(NOTICE).screenshot({ path: "shots/age/notice-xlarge.png" });
    await context.close();
  });

  test("right to left", async ({ browser }) => {
    const { context, page } = await member(browser, "ui_age_rtl");
    await open(page, "/all");
    await expect(page.locator("html")).toHaveAttribute("dir", "rtl");
    const notice = (await page.locator(NOTICE).boundingBox())!;
    const label = (await page.locator('label[for="age-notice-adult"]').boundingBox())!;
    expect(notice.x + notice.width - (label.x + label.width)).toBeLessThan(40);   // starts at the right-hand edge
    await page.screenshot({ path: "shots/age/notice-rtl.png" });
    await context.close();
  });

  test("answering 18 or over ends it for good, on the page they were on", async ({ browser }) => {
    const { context, page } = await member(browser, "ui_age_old");
    await open(page, "/browse");
    await page.locator(NOTICE).getByText(ADULT, { exact: true }).click();
    await page.locator(`${NOTICE} button[type=submit]`).click();
    await page.waitForLoadState("load");
    expect(new URL(page.url()).pathname).toBe("/browse");
    await expect(page.locator(NOTICE)).toHaveCount(0);
    await open(page, "/all");
    await expect(page.locator(NOTICE)).toHaveCount(0);

    await open(page, "/settings/content");
    await expect(page.locator("#age-declaration")).toContainText("marked 18 or over since");
    await expect(page.locator("#age-declaration form")).toHaveCount(0);
    await expect(page.locator("#filterlevel")).toBeEnabled();
    await page.locator("#age-declaration").screenshot({ path: "shots/age/settings-adult.png" });
    expect((await new AxeBuilder({ page }).include("#age-declaration").withTags(["wcag2a", "wcag2aa"]).analyze()).violations).toEqual([]);

    const again = await page.evaluate(async () => {
      const form = new URLSearchParams({ formkey: (document.querySelector('input[name="formkey"]') as HTMLInputElement)?.value ?? "", age_declaration: "2", confirm: "1" });
      return (await fetch("/settings/age_declaration", { method: "POST", body: form })).status;
    });
    expect(again).toBe(409);
    await context.close();
  });

  test("answering child asks first; Go back saves nothing; then it is locked", async ({ browser }) => {
    const { context, page } = await member(browser, "ui_age_child");
    await open(page, "/all");
    await page.locator(NOTICE).getByText(CHILD, { exact: true }).click();
    await page.locator(`${NOTICE} button[type=submit]`).click();
    await expect(page.getByRole("heading", { name: "Is this a child's account?" })).toBeVisible();
    await expect(page.locator(NOTICE)).toHaveCount(0);
    await expect(page.getByText("stay on the Child word filter")).toBeVisible();
    await page.screenshot({ path: "shots/age/confirm-1280.png" });

    await page.getByRole("link", { name: "Go back" }).click();
    await page.waitForLoadState("load");
    await expect(page.locator(NOTICE), "nothing was saved").toBeVisible();

    await page.locator(NOTICE).getByText(CHILD, { exact: true }).click();
    await page.locator(`${NOTICE} button[type=submit]`).click();
    await page.getByRole("button", { name: "Yes, this is a child's account" }).click();
    await page.waitForLoadState("load");
    await expect(page.locator(NOTICE)).toHaveCount(0);

    await open(page, "/settings/content");
    await expect(page.locator("#age-declaration")).toContainText("This is a child account");
    await expect(page.locator("#filterlevel")).toBeDisabled();
    await expect(page.locator("#filterlevel")).toHaveValue("2");
    await expect(page.locator("#filterlevel-save")).toBeHidden();
    await expect(page.locator("#all-shows-sensitive")).toBeDisabled();
    await page.locator(".settings-section").first().screenshot({ path: "shots/age/settings-child.png" });
    await expect(page.locator("#filterlevel-words"), "the list isn't shown at this level, so no link to a refusal").toHaveCount(0);
    expect((await new AxeBuilder({ page }).include("#age-declaration").withTags(["wcag2a", "wcag2aa"]).analyze()).violations).toEqual([]);

    const key = await page.evaluate(() => (window as any).formkey?.() ?? "");
    const refused = await page.evaluate(async ({ key, password }) => {
      const send = async (path: string, fields: Record<string, string>) =>
        (await fetch(path, { method: "POST", body: new URLSearchParams({ formkey: key, ...fields }) })).status;
      return [await send("/settings/profile", { filter_level: "1", password }), await send("/api/vue/settings/profile", { filter_level: "0", password }),
              await send("/settings/all_shows_sensitive", { on: "true" })];
    }, { key, password: PASSWORD });
    expect(refused).toEqual([403, 403, 403]);
    await context.close();
  });

  test("an admin changes the answer, and the member can lower the filter themselves after", async ({ browser }) => {
    const { context, page } = await member(browser, "ui_age_admin");
    await open(page, "/@ui_age_child");
    const form = page.locator('form[action="/admin/age_declaration"]').first();
    await expect(form.locator('select[name="answer"]')).toHaveValue("2");
    await form.locator('select[name="answer"]').selectOption("1");
    await form.locator('input[name="reason"]').fill("a grown-up, their own account");
    await form.screenshot({ path: "shots/age/admin-form.png" });
    page.once("dialog", (dialog) => dialog.accept());
    await form.locator('input[type="submit"]').click();
    await page.waitForLoadState("load");
    await expect(page.locator('form[action="/admin/age_declaration"] select[name="answer"]').first()).toHaveValue("1");
    await context.close();

    const child = await member(browser, "ui_age_child");
    await open(child.page, "/settings/content");
    await expect(child.page.locator("#age-declaration")).toContainText("marked 18 or over since");
    await expect(child.page.locator("#filterlevel")).toBeEnabled();
    await expect(child.page.locator("#filterlevel")).toHaveValue("2");
    await child.context.close();
  });
});
