import { test, expect, type Browser, type BrowserContext, type Page } from "@playwright/test";
import AxeBuilder from "@axe-core/playwright";
import { mkdirSync } from "node:fs";
import { open } from "./pages";

// QR sign-in's setting in Settings > Security, and how a sign-in was made in Recent sign-ins (CLAUDE.md "QR sign-in"). The dev site has the
// switch on. With UI_OFF_URL, a second copy of the site with it off (see scripts/ui/README.md), the first test also holds that nothing is drawn there:
//   docker exec ruqqus-ruqqus-1 sh -c 'cd /opt/ruqqus/service && PYTHONPATH=. venv/bin/python scripts/ui/seed_qr_settings.py'      before each run
//   npm run qr-settings      (or UI_OFF_URL=http://localhost:8091 npm run qr-settings)
// tests/pg/test_qr_login_setting.py and tests/pg/test_qr_login.py hold the switch off without a copy. Shots go to shots/qr-settings/.

const PASSWORD = "ui-Password-1";
const PHONE = { viewport: { width: 390, height: 844 }, isMobile: true, hasTouch: true } as const;
const DESKTOP = { viewport: { width: 1280, height: 900 } } as const;
const SECTION = "#qr-login";
const FORM = `${SECTION} + p + .settings-section`;
const SHOTS = new URL("./shots/qr-settings/", import.meta.url).pathname;
const OFF_URL = process.env.UI_OFF_URL;

test.describe.configure({ mode: "serial" });
test.setTimeout(300_000);
mkdirSync(SHOTS, { recursive: true });

const signedIn = new Map<string, Awaited<ReturnType<BrowserContext["storageState"]>>>();

// one sign-in per account for the whole run (sign-ins are limited to 6 a minute per address)
async function member(browser: Browser, baseURL: string, name: string, options: object = DESKTOP) {
  if (!signedIn.has(name)) {
    const first = await browser.newContext({ ...DESKTOP, baseURL });
    const form = await first.newPage();
    for (let attempt = 0; ; attempt++) {
      await open(form, "/login");
      await form.fill("#username", name);
      await form.fill("#password", PASSWORD);
      await form.click("#login_button");
      const through = await form.waitForURL((url) => !url.pathname.startsWith("/login"), { timeout: 10_000 }).then(() => true, () => false);
      if (through) break;
      if (attempt === 2) throw new Error(`could not sign in as ${name}`);
      await form.waitForTimeout(61_000);
    }
    signedIn.set(name, await first.storageState());
    await first.close();
  }
  const context = await browser.newContext({ ...options, baseURL, storageState: signedIn.get(name) });
  return { context, page: await context.newPage() };
}

const sideways = (page: Page) => page.evaluate(() => document.documentElement.scrollWidth - document.documentElement.clientWidth);

// every box in the section is inside the card and no text is cut off
async function nothingCut(page: Page) {
  const problems = await page.evaluate((selector) => {
    const card = document.querySelector(selector)!;
    const edge = card.getBoundingClientRect();
    return [...card.querySelectorAll("p, label, input:not([type=hidden]), button, strong")].flatMap((node) => {
      const box = node.getBoundingClientRect();
      const out = box.left < edge.left - 1 || box.right > edge.right + 1;
      const cut = node.scrollWidth > (node as HTMLElement).clientWidth + 1 && getComputedStyle(node).overflow !== "visible";
      return out || cut ? [`${node.tagName} ${node.textContent?.slice(0, 40)}`] : [];
    });
  }, FORM);
  expect(problems).toEqual([]);
}

const serious = async (page: Page, selector: string) =>
  (await new AxeBuilder({ page }).include(selector).withTags(["wcag2a", "wcag2aa"]).analyze()).violations
    .filter((v) => v.impact === "serious" || v.impact === "critical")
    .map((v) => `${v.id}: ${v.nodes.map((n) => n.target.join(" ")).join(", ")}`);

test("with the switch off the setting isn't drawn, and Recent sign-ins still says how", async ({ browser }) => {
  test.skip(!OFF_URL, "needs a second copy of the site with the switch off");
  const context = await browser.newContext({ ...DESKTOP, baseURL: OFF_URL });
  const page = await context.newPage();
  await open(page, "/login");
  await page.fill("#username", "ui_viewer");
  await page.fill("#password", PASSWORD);
  await Promise.all([page.waitForURL((url) => !url.pathname.startsWith("/login")), page.click("#login_button")]);
  await open(page, "/settings/security");
  await expect(page.locator(SECTION)).toHaveCount(0);
  await expect(page.locator('input[name="qr_login_form"]')).toHaveCount(0);
  await expect(page.getByText("QR sign-in")).toHaveCount(0);
  await expect(page.locator(".sign-in-row").first()).toContainText("Password");
  await context.close();
});

for (const [label, options] of [["desktop", DESKTOP], ["phone", PHONE]] as const) {
  for (const scheme of ["light", "dark"] as const) {
    test(`on: one button, no password box, nothing cut off (${label}, ${scheme})`, async ({ browser, baseURL }) => {
      const { context, page } = await member(browser, baseURL!, "ui_qr_settings", { ...options, colorScheme: scheme });
      await open(page, "/settings/security");
      await page.locator(SECTION).scrollIntoViewIfNeeded();
      await expect(page.locator(FORM)).toContainText("QR sign-in is on.");
      await expect(page.locator(`${FORM} input[name="password"]`)).toHaveCount(0);
      const button = page.locator(`${FORM} button[type="submit"]`);
      await expect(button).toHaveText("Turn off");
      expect((await button.boundingBox())!.height).toBeGreaterThanOrEqual(label === "phone" ? 44 : 36);
      expect(await sideways(page)).toBeLessThanOrEqual(0);
      await nothingCut(page);
      expect(await serious(page, FORM)).toEqual([]);
      await page.locator(FORM).screenshot({ path: `${SHOTS}on-${label}-${scheme}.png` });
      await context.close();
    });
  }
}

test("off asks for nothing; back on needs the password and tells the member; the row says Password", async ({ browser, baseURL }) => {
  const { context, page } = await member(browser, baseURL!, "ui_qr_settings");
  await open(page, "/settings/security");
  await expect(page.locator(".sign-in-row").first()).toContainText("Password");
  await page.locator(`${FORM} button[type="submit"]`).click();
  await page.waitForURL(/msg=/);
  await expect(page.locator(".alert-success")).toContainText("QR sign-in is off.");
  await expect(page.locator(".alert-success")).toBeInViewport();

  const box = page.locator("#qr-login-password");
  await expect(box).toBeVisible();
  await expect(page.locator(`${FORM} button[type="submit"]`)).toHaveText("Turn on");

  // the browser's own `required` stops an empty box before it is sent
  await page.locator(`${FORM} button[type="submit"]`).click();
  expect(await box.evaluate((input: HTMLInputElement) => input.validity.valueMissing)).toBe(true);
  expect(page.url()).toContain("msg=");

  await box.fill("not the password");
  await page.locator(`${FORM} button[type="submit"]`).click();
  await page.waitForURL(/error=/);
  await expect(page.locator(".alert-danger")).toContainText("Invalid password.");
  await expect(page.locator(FORM)).toContainText("QR sign-in is off.");
  expect(await serious(page, FORM)).toEqual([]);
  await page.locator(FORM).screenshot({ path: `${SHOTS}off-desktop.png` });

  await box.fill(PASSWORD);
  await box.press("Enter");
  await page.waitForURL(/msg=/);
  await expect(page.locator(".alert-success")).toContainText("QR sign-in is on.");
  await expect(page.locator(FORM)).toContainText("Turn off");

  // the bell, not the row: the dev database has no site account (id 1), so the System tab can't draw a notice there.
  // Its words on the page are tests/pg/test_qr_login_setting.py
  await open(page, "/notifications");
  await expect(page.getByRole("link", { name: "Notifications, 1 new" })).toBeVisible();
  await context.close();
});

test("off, on a phone and at the largest text: the box and the button are a target tall and nothing is cut off", async ({ browser, baseURL }) => {
  const { context, page } = await member(browser, baseURL!, "ui_qr_settings", PHONE);
  await context.addCookies([{ name: "text_size", value: "xlarge", url: baseURL! }]);
  await open(page, "/settings/security");
  await page.locator(`${FORM} button[type="submit"]`).click();
  await page.waitForURL(/msg=/);
  await expect(page.locator("#qr-login-password")).toBeVisible();
  expect((await page.locator(`${FORM} button[type="submit"]`).boundingBox())!.height).toBeGreaterThanOrEqual(44);
  expect(await sideways(page)).toBeLessThanOrEqual(0);
  await nothingCut(page);
  await page.locator(FORM).screenshot({ path: `${SHOTS}off-phone-xlarge.png` });
  await page.fill("#qr-login-password", PASSWORD);
  await page.locator(`${FORM} button[type="submit"]`).click();
  await page.waitForURL(/msg=/);
  await context.close();
});

test("right to left", async ({ browser, baseURL }) => {
  const { context, page } = await member(browser, baseURL!, "ui_qr_rtl");
  await open(page, "/settings/security");
  await expect(page.locator("html")).toHaveAttribute("dir", "rtl");
  await page.locator(SECTION).scrollIntoViewIfNeeded();
  const [card, button] = await Promise.all([page.locator(FORM).boundingBox(), page.locator(`${FORM} button[type="submit"]`).boundingBox()]);
  expect(button!.x - card!.x).toBeLessThan(card!.width / 2);        // the button's ml-auto is mirrored to the other end
  expect(await sideways(page)).toBeLessThanOrEqual(0);
  await nothingCut(page);
  await page.locator(FORM).screenshot({ path: `${SHOTS}on-rtl.png` });
  await context.close();
});
