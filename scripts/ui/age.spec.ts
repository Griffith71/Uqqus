import { test, expect, type Browser, type BrowserContext, type Page } from "@playwright/test";
import AxeBuilder from "@axe-core/playwright";
import { open } from "./pages";

// The age question and the child lock (CLAUDE.md "Age declaration and the child lock"; helpers/age_declaration.py, partials/age_question.html):
//   docker compose exec -T ruqqus env PYTHONPATH=. /opt/ruqqus/service/venv/bin/python scripts/ui/seed_age.py     before each run
//   npm run age
// What it holds it to, in a real browser: sign-up can't be sent without an answer and nothing is answered for them; no page carries a
// notice for a member from before (the owner, 2026-11-05) and Settings > Content asks in the same words as sign-up, with nothing running
// off a phone's screen and both choices a target tall (32, 44 under a finger); "under 18" starts the child lock at once and the lock can
// be lifted by saying 18 or over (no country header: not a strict country); once answered otherwise it is kept for good; a child account's word filter and sensitive switch can't be changed by any of the routes; an admin can
// change the answer. A real sign-up is tests/pg/test_age_declaration.py: the dev site allows one account an
// hour from its one Docker address, and refuses a headless browser's name.

const PASSWORD = "ui-Password-1";
const PHONE = { viewport: { width: 390, height: 844 }, isMobile: true, hasTouch: true } as const;
const DESKTOP = { viewport: { width: 1280, height: 900 } } as const;
const NOTICE = "#age-declaration";
const ADULT = "I'm 18 or over";
const CHILD = "I'm 13 to 17";

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
  // the owner, 2026-11-05: the question belongs to sign-up, so no page carries a notice; Settings > Content still asks
  for (const [label, options] of [["desktop", DESKTOP], ["phone", PHONE]] as const) {
    test(`no page carries a notice, and Settings asks, with nothing running off the screen (${label})`, async ({ browser }) => {
      const { context, page } = await member(browser, "ui_age_old", options);
      for (const path of ["/all", "/notifications", "/browse", "/chat"]) {
        await open(page, path);
        await expect(page.locator("#age-notice"), path).toHaveCount(0);
      }
      await open(page, "/settings/content");
      const asking = page.locator("#age-declaration");
      await expect(asking.getByText(ADULT, { exact: true })).toBeVisible();
      await expect(asking.getByText(CHILD, { exact: true })).toBeVisible();
      await expect(asking.getByRole("link", { name: "Terms" })).toHaveAttribute("href", "/help/terms");
      expect(await sideways(page)).toBeLessThanOrEqual(0);
      for (const target of [`${NOTICE} label[for="age-settings-adult"]`, `${NOTICE} label[for="age-settings-child"]`, `${NOTICE} button[type=submit]`]) {
        const box = (await page.locator(target).boundingBox())!;
        expect(box.height, target).toBeGreaterThanOrEqual(label === "phone" ? 43 : 31);
      }
      await asking.screenshot({ path: `shots/age/settings-${label}.png` });
      expect((await new AxeBuilder({ page }).include("#age-declaration").exclude(`${NOTICE} .btn-primary`).withTags(["wcag2a", "wcag2aa"]).analyze()).violations).toEqual([]);
      await context.close();
    });
  }

  test("answering 18 or over in Settings is kept for good", async ({ browser }) => {
    const { context, page } = await member(browser, "ui_age_old");
    await open(page, "/settings/content");
    await page.locator(`${NOTICE} label[for="age-settings-adult"]`).click();
    await page.locator(`${NOTICE} button[type=submit]`).click();
    await page.waitForLoadState("load");
    expect(new URL(page.url()).pathname).toBe("/settings/content");
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

  test("answering under 18 starts the child lock; the lock can be lifted by saying 18 or over", async ({ browser }) => {
    const { context, page } = await member(browser, "ui_age_child");
    await open(page, "/settings/content");
    await page.locator(`${NOTICE} label[for="age-settings-child"]`).click();
    await page.locator(`${NOTICE} button[type=submit]`).click();
    await page.waitForLoadState("load");
    expect(new URL(page.url()).pathname).toBe("/settings/content");

    await open(page, "/settings/content");
    await expect(page.locator("#age-declaration")).toContainText("Your account has the child lock on");
    await expect(page.locator("#age-confirm")).toContainText("confirm that you're 18 or over");
    await expect(page.locator("#private-posting-switch")).toBeChecked();
    await expect(page.locator("#filterlevel")).toBeDisabled();
    await expect(page.locator("#filterlevel")).toHaveValue("2");
    await expect(page.locator("#filterlevel-save")).toBeHidden();
    await expect(page.locator("#all-shows-sensitive")).toBeDisabled();
    await expect(page.locator('[data-side-panel="chat"]'), "a locked account has no chat").toHaveCount(0);
    await expect(page.locator('[href="/spaces/new"]')).toHaveCount(0);
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
