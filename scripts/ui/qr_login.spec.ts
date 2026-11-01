import { test, expect, type Browser, type BrowserContext, type Page } from "@playwright/test";
import AxeBuilder from "@axe-core/playwright";
import { execSync } from "node:child_process";
import { mkdirSync } from "node:fs";
import { createRequire } from "node:module";

// QR sign-in, in real browsers with two contexts: a computer and a phone (CLAUDE.md "QR sign-in"). The picture is decoded for real, so what
// a camera would open is what the phone opens. A real phone's camera can't be tried here.
//   docker exec ruqqus-ruqqus-1 sh -c 'cd /opt/ruqqus/service && PYTHONPATH=. venv/bin/python scripts/ui/seed_qr_login.py'      before each run
//   npm run qr-login
// Shots go to shots/qr-login/.

const PASSWORD = "ui-Password-1";
const MEMBER = "ui_qr_member";
const AGENT = "Mozilla/5.0 (Macintosh; Intel Mac OS X 14_5) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/125.0.0.0 Safari/537.36";
const PHONE_AGENT = "Mozilla/5.0 (iPhone; CPU iPhone OS 17_5 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.5 Mobile/15E148 Safari/604.1";
const DESKTOP = { viewport: { width: 1280, height: 900 }, userAgent: AGENT } as const;
const PHONE = { viewport: { width: 390, height: 844 }, isMobile: true, hasTouch: true, userAgent: PHONE_AGENT } as const;
const SHOTS = new URL("./shots/qr-login/", import.meta.url).pathname;
const JSQR = createRequire(import.meta.url).resolve("jsqr/dist/jsQR.js");

test.describe.configure({ mode: "serial" });
test.setTimeout(240_000);
mkdirSync(SHOTS, { recursive: true });

// what a run uses up: the routes' per-minute limits and heat, from one address, are spent long before the checks are
function forgetLimits() {
  execSync(`docker exec ruqqus-ruqqus-1 sh -c 'cd /opt/ruqqus/service && PYTHONPATH=. venv/bin/python scripts/ui/seed_qr_login.py' `, { stdio: "ignore" });
}

let phoneState: Awaited<ReturnType<BrowserContext["storageState"]>> | undefined;

async function signedInPhone(browser: Browser, baseURL: string, options: object = PHONE) {
  if (!phoneState) {
    const first = await browser.newContext({ ...DESKTOP, baseURL });
    const form = await first.newPage();
    await form.goto("/login", { waitUntil: "load" });
    await form.fill("#username", MEMBER);
    await form.fill("#password", PASSWORD);
    await form.click("#login_button");
    await form.waitForURL((url) => !url.pathname.startsWith("/login"));
    phoneState = await first.storageState();
    await first.close();
  }
  const context = await browser.newContext({ ...options, baseURL, storageState: phoneState });
  return { context, page: await context.newPage() };
}

// the picture, read as a camera reads it: nothing here knows the code but what the image says
async function decode(page: Page) {
  await page.addScriptTag({ path: JSQR });
  return page.evaluate(() => {
    const image = document.querySelector<HTMLImageElement>("#qr-image")!;
    const canvas = document.createElement("canvas");
    canvas.width = image.naturalWidth;
    canvas.height = image.naturalHeight;
    const drawing = canvas.getContext("2d")!;
    drawing.drawImage(image, 0, 0);
    const pixels = drawing.getImageData(0, 0, canvas.width, canvas.height);
    // @ts-expect-error from the script tag above
    return window.jsQR(pixels.data, pixels.width, pixels.height)?.data ?? null;
  });
}

// the white margin of the picture on its narrowest side, in modules: a corner's finder square is 7 modules wide, which gives the module's size
async function quietZone(page: Page) {
  return page.evaluate(() => {
    const image = document.querySelector<HTMLImageElement>("#qr-image")!;
    const canvas = document.createElement("canvas");
    canvas.width = image.naturalWidth;
    canvas.height = image.naturalHeight;
    const drawing = canvas.getContext("2d")!;
    drawing.drawImage(image, 0, 0);
    const { data, width, height } = drawing.getImageData(0, 0, canvas.width, canvas.height);
    const dark = (x: number, y: number) => data[(y * width + x) * 4] < 128;
    let left = width, right = -1, top = height, bottom = -1;
    for (let y = 0; y < height; y++) {
      for (let x = 0; x < width; x++) {
        if (!dark(x, y)) continue;
        left = Math.min(left, x); right = Math.max(right, x); top = Math.min(top, y); bottom = Math.max(bottom, y);
      }
    }
    let run = 0;
    while (dark(left + run, top)) run++;
    const module = run / 7;
    return Math.min(left, top, width - 1 - right, height - 1 - bottom) / module;
  });
}

// a phone at the screen with the warning and the credential, on the code the computer just got
async function atTheReadyScreen(phone: Page, address: string) {
  await phone.goto(address, { waitUntil: "load" });
  await phone.waitForFunction(() => document.querySelector(".qr-view.is-current")?.getAttribute("data-qr-view") === "ready");
}

async function pressForCode(page: Page) {
  await page.goto("/login", { waitUntil: "load" });
  await page.click("#qr-start");
  await page.waitForFunction(() => !!document.querySelector<HTMLImageElement>("#qr-image")?.naturalWidth);
  const address = await decode(page);
  expect(address).toMatch(/\/login\/qr\/approve#c=[A-Za-z0-9_-]{40,}$/);
  return address as string;
}

const sideways = (page: Page) => page.evaluate(() => document.documentElement.scrollWidth - document.documentElement.clientWidth);
const current = (page: Page) => page.evaluate(() => document.querySelector(".qr-view.is-current")?.getAttribute("data-qr-view"));

// axe answers "can't tell" for text in a stack of views like these, so the colours are measured too: every text on white, at AA,
// but the white label on the site's own button fill (the owner's exception, CLAUDE.md "Colour contrast")
async function readable(page: Page, label: string) {
  const low = await page.evaluate(() => {
    const parts = (colour: string) => colour.match(/[\d.]+/g)!.map(Number);
    const luminance = (rgb: number[]) => {
      const [r, g, b] = rgb.slice(0, 3).map((c) => (c / 255 <= 0.03928 ? c / 255 / 12.92 : ((c / 255 + 0.055) / 1.055) ** 2.4));
      return 0.2126 * r + 0.7152 * g + 0.0722 * b;
    };
    const backdrop = (node: Element | null): number[] => {
      for (let at = node; at; at = at.parentElement) {
        const colour = parts(getComputedStyle(at).backgroundColor);
        if (colour.length === 3 || colour[3] > 0) return colour;
      }
      return [255, 255, 255];
    };
    const out: string[] = [];
    for (const node of document.querySelectorAll("#qr-login *, #qr-approve *, #qr-upgrade *")) {
      const own = [...node.childNodes].some((child) => child.nodeType === 3 && child.textContent!.trim());
      const style = getComputedStyle(node);
      if (!own || style.visibility === "hidden" || style.display === "none" || node.closest("[hidden]")) continue;
      if (node.matches(".btn-primary")) continue;
      const [a, b] = [luminance(parts(style.color)), luminance(backdrop(node))];
      const ratio = (Math.max(a, b) + 0.05) / (Math.min(a, b) + 0.05);
      if (ratio < 4.5) out.push(`${node.tagName}#${node.id} "${node.textContent!.trim().slice(0, 30)}" ${ratio.toFixed(2)}`);
    }
    return out;
  });
  expect(low, label).toEqual([]);
}

async function noSerious(page: Page, label: string) {
  await readable(page, label);
  const { violations } = await new AxeBuilder({ page }).include("#qr-login, #qr-approve, #qr-upgrade").analyze();
  const bad = violations.filter((v) => v.impact === "serious" || v.impact === "critical");
  expect(bad.map((v) => `${v.id}: ${v.nodes.map((n) => n.target).join(" ")}`), label).toEqual([]);
}

test.beforeAll(() => forgetLimits());

test("opening the sign-in page asks for nothing and sets no cookie", async ({ browser, baseURL }) => {
  const context = await browser.newContext({ ...DESKTOP, baseURL });
  const page = await context.newPage();
  const asked: string[] = [];
  page.on("request", (request) => { if (request.url().includes("/login/qr/")) asked.push(request.url()); });
  const answer = await page.goto("/login", { waitUntil: "load" });
  const html = await answer!.text();
  expect(html).not.toContain("#c=");
  expect(html).not.toContain("data:image/png");
  expect(html).not.toMatch(/adsbygoogle|plausible/);
  await page.waitForTimeout(500);
  expect(asked).toEqual([]);
  expect((await context.cookies()).filter((c) => c.name === "session_ruqqus")).toEqual([]);
  await context.close();
});

test("the whole flow: the picture, the phone, Confirm, the limit, the password", async ({ browser, baseURL }) => {
  const computer = await browser.newContext({ ...DESKTOP, baseURL });
  const desk = await computer.newPage();
  const address = await pressForCode(desk);
  expect(address.startsWith(baseURL!)).toBe(true);

  // the box stays white, with the quiet zone drawn in the picture, and is named
  const box = await desk.evaluate(() => {
    const image = document.querySelector("#qr-image")!;
    return { paper: getComputedStyle(document.querySelector(".qr-box")!).backgroundColor, label: image.getAttribute("aria-label"), role: image.getAttribute("role") };
  });
  expect(box.paper).toBe("rgb(255, 255, 255)");
  expect(box.label).toBeTruthy();
  expect(box.role).toBe("img");
  expect(await quietZone(desk), "the margin a camera needs, in the picture's own pixels").toBeGreaterThanOrEqual(4);
  await desk.screenshot({ path: `${SHOTS}desk-waiting.png` });

  // a cookie that ends with the browser, holding a digest and never the code
  const cookie = (await computer.cookies()).find((c) => c.name === "session_ruqqus")!;
  expect(cookie.expires).toBe(-1);

  const { context: phoneContext, page: phone } = await signedInPhone(browser, baseURL!);
  const seen: string[] = [];
  phone.on("request", (request) => seen.push(request.url()));
  await phone.goto(address, { waitUntil: "load" });
  await phone.waitForFunction(() => document.querySelector(".qr-view.is-current")?.getAttribute("data-qr-view") === "ready");

  // gone from the address bar, and never sent to the server
  expect(phone.url()).toBe(`${baseURL}/login/qr/approve`);
  const code = address.split("#c=")[1];
  expect(seen.filter((url) => url.includes(code))).toEqual([]);
  expect(await phone.content()).not.toContain(code);
  expect(await phone.evaluate(() => document.activeElement?.id)).toBe("qr-deny");
  expect(await phone.locator("#qr-deny").evaluate((n) => n.classList.contains("btn-primary"))).toBe(true);
  await expect(phone.locator("#qr-fact-browser")).toHaveText("Chrome on macOS");
  await expect(phone.locator(".qr-reported")).toBeVisible();
  await expect(phone.getByText("Only continue if you started this yourself")).toBeVisible();
  expect(await sideways(phone)).toBeLessThanOrEqual(0);
  await noSerious(phone, "the phone's screen");
  await phone.screenshot({ path: `${SHOTS}phone-ready.png`, fullPage: true });

  // a wrong password says so and the code stands
  await phone.fill("#qr-answer", "not-the-password");
  await phone.click("#qr-approve-button");
  await expect(phone.locator("#qr-error")).toHaveText("That password isn't right.");
  expect(await current(phone)).toBe("ready");

  await phone.fill("#qr-answer", PASSWORD);
  await phone.click("#qr-approve-button");
  await phone.waitForFunction(() => document.querySelector(".qr-view.is-current")?.getAttribute("data-qr-view") === "approved");
  for (const link of ["/settings/security#log-out-everywhere", "/settings/apps", "/settings/chat_apps"]) {
    await expect(phone.locator(`[data-qr-view="approved"] .qr-links a[href="${link}"]`)).toBeVisible();
  }
  await phone.screenshot({ path: `${SHOTS}phone-approved.png`, fullPage: true });

  // Confirm, after showing the name, with focus off it
  const heightBefore = await desk.locator("#qr-panel").evaluate((n) => n.getBoundingClientRect().height);
  await desk.waitForFunction(() => document.querySelector(".qr-view.is-current")?.getAttribute("data-qr-view") === "approved", undefined, { timeout: 15_000 });
  expect(await desk.locator("#qr-panel").evaluate((n) => n.getBoundingClientRect().height)).toBe(heightBefore);
  await expect(desk.locator("#qr-confirm-heading")).toHaveText(`Sign in as @${MEMBER}?`);
  expect(await desk.evaluate(() => document.activeElement?.id)).toBe("qr-confirm-heading");
  await desk.screenshot({ path: `${SHOTS}desk-confirm.png` });
  expect((await computer.cookies()).length && (await desk.evaluate(() => fetch("/me", { redirect: "manual" }).then((r) => r.type)))).toBeTruthy();
  await desk.evaluate(() => { sessionStorage.setItem("uqqus_chat_prekey", "left-over"); localStorage.setItem("uqqus_chat_prekey", "left-over"); });
  await desk.click("#qr-confirm");
  await desk.waitForURL((url) => !url.pathname.startsWith("/login"));
  expect(await desk.evaluate(() => [sessionStorage.getItem("uqqus_chat_prekey"), localStorage.getItem("uqqus_chat_prekey")])).toEqual([null, null]);

  // the limited session: told on every page, and asked for the password
  await expect(desk.locator("#qr-limited-notice")).toBeVisible();
  await desk.screenshot({ path: `${SHOTS}desk-limited.png` });
  for (const path of ["/settings/security", "/settings/chat_apps", "/settings/apps"]) {
    await desk.goto(path, { waitUntil: "load" });
    await expect(desk.locator('form[action="/login/qr/upgrade"]'), path).toBeVisible();
  }

  // the chat page: the password form instead of the client, and another account's data on this browser stays as it is until then
  await desk.evaluate(() => localStorage.setItem("ruqqus_chat_session", JSON.stringify({ user_id: "@ruqqus_someone_else:ruqqus.local", device_id: "OTHER", access_token: "other" })));
  const bundles: string[] = [];
  const tokens: string[] = [];
  desk.on("request", (request) => {
    if (request.url().includes("chat_bundle")) bundles.push(request.url());
    if (request.url().includes("/api/chat/token")) tokens.push(request.url());
  });
  await desk.goto("/chat", { waitUntil: "load" });
  await expect(desk.locator("form[data-chat-prekey]")).toBeVisible();
  await desk.waitForTimeout(500);
  expect(bundles).toEqual([]);
  expect(tokens).toEqual([]);
  expect(JSON.parse((await desk.evaluate(() => localStorage.getItem("ruqqus_chat_session")))!).device_id).toBe("OTHER");
  await noSerious(desk, "the password form");
  await desk.screenshot({ path: `${SHOTS}desk-chat-password.png` });

  // a page already open asks for a token: the clear refusal, which each bundle handles
  const refused = await desk.evaluate(async () => {
    const key = document.querySelector<HTMLInputElement>('input[name="formkey"]')!.value;
    const answer = await fetch("/api/chat/token", { method: "POST", body: new URLSearchParams({ formkey: key }) });
    return { status: answer.status, body: await answer.json() };
  });
  expect(refused.status).toBe(403);
  expect(refused.body.need_password).toBe(true);

  // a wrong password keeps the limit
  await desk.fill("#qr-upgrade-password", "wrong-one");
  await desk.click("#qr-upgrade-button");
  await expect(desk.locator(".alert-danger")).toBeVisible();
  await expect(desk.locator("form[data-chat-prekey]")).toBeVisible();

  // the password, and the chat client starts: this browser's stale session is another account's, so it goes now and not before
  await desk.fill("#qr-upgrade-password", PASSWORD);
  await Promise.all([desk.waitForURL(/\/chat/), desk.click("#qr-upgrade-button")]);
  await expect(desk.locator("#chat-app")).toBeVisible();
  await desk.waitForFunction(() => JSON.parse(localStorage.getItem("ruqqus_chat_session") || "{}").device_id !== "OTHER", undefined, { timeout: 30_000 });
  expect(await desk.locator("#qr-limited-notice").count()).toBe(0);

  await phoneContext.close();
  await computer.close();
});

test("a limited sign-in's call window and Space say so instead of hanging", async ({ browser, baseURL }) => {
  forgetLimits();
  const computer = await browser.newContext({ ...DESKTOP, baseURL });
  const desk = await computer.newPage();
  const address = await pressForCode(desk);
  const { context: phoneContext, page: phone } = await signedInPhone(browser, baseURL!);
  await phone.goto(address, { waitUntil: "load" });
  await phone.waitForFunction(() => document.querySelector(".qr-view.is-current")?.getAttribute("data-qr-view") === "ready");
  await phone.fill("#qr-answer", PASSWORD);
  await phone.click("#qr-approve-button");
  await desk.click("#qr-confirm");
  await desk.waitForURL((url) => !url.pathname.startsWith("/login"));

  // a call window: the password form, with no chat client asked for
  const tokens: string[] = [];
  desk.on("request", (request) => { if (request.url().includes("/api/chat/token")) tokens.push(request.url()); });
  await desk.goto("/call/!someroom:ruqqus.local?kind=voice", { waitUntil: "load" });
  await expect(desk.locator('form[action="/login/qr/upgrade"]')).toBeVisible();

  // a Space with a call, made by the phone's full sign-in
  await phone.goto("/spaces/new", { waitUntil: "load" });
  const made = await phone.evaluate(async () => {
    const form = new FormData();
    form.append("formkey", (window as unknown as { formkey(): string }).formkey());
    form.append("space_kind", "call");
    form.append("title", `${crypto.randomUUID()} ${crypto.randomUUID()}`);        // never alike: similar titles close together suspend the account
    form.append("audience", "0");
    const answer = await fetch("/api/spaces", { method: "POST", body: form });
    return { status: answer.status, body: await answer.json() };
  });
  expect(made.status, JSON.stringify(made.body)).toBe(200);
  await desk.goto(made.body.url, { waitUntil: "load" });
  await expect(desk.locator("#space-password-first")).toBeVisible();
  await expect(desk.locator('#space-password-first a[href^="/login/qr/upgrade"]')).toBeVisible();
  expect(await desk.locator("#space-join").isDisabled()).toBe(true);
  await desk.waitForTimeout(500);
  expect(tokens).toEqual([]);
  await desk.screenshot({ path: `${SHOTS}space-password-first.png` });

  // the chat side panel's frame is only a way out of it: the page around a frame may hold Google's tag, and the form's key would be in its reach
  await desk.goto("/settings/content", { waitUntil: "load" });
  await desk.goto("/", { waitUntil: "load" });
  await desk.locator("[data-side-panel=chat], [data-side-panel='chat']").first().click();
  const frame = desk.frameLocator("#side-panel iframe:visible");
  const way = frame.locator("a#qr-upgrade-link");
  await expect(way).toBeVisible({ timeout: 15_000 });
  await expect(way).toHaveAttribute("target", "_top");
  await expect(frame.locator('input[type="password"]')).toHaveCount(0);
  await desk.screenshot({ path: `${SHOTS}side-panel.png` });
  await Promise.all([desk.waitForURL(/\/login\/qr\/upgrade/), way.click()]);
  expect(new URL(desk.url()).searchParams.get("redirect")).toBe("/chat");

  // on its own page a wrong password is a sentence, not a blank frame, and "Not now" is a target of the ladder
  await desk.fill("#qr-upgrade-password", "wrong-one");
  await desk.click("#qr-upgrade-button");
  await expect(desk.locator(".alert-danger")).toBeVisible();
  const later = await desk.locator(".qr-later a").evaluate((n) => n.getBoundingClientRect().height);
  expect(later).toBeGreaterThanOrEqual(32);
  await Promise.all([desk.waitForURL((url) => url.pathname === "/"), desk.locator(".qr-later a").click()]);
  await phoneContext.close();
  await computer.close();
});

test("a limited sign-in's Follow says it needs the password, and gives the button back at once", async ({ browser, baseURL }) => {
  forgetLimits();
  const computer = await browser.newContext({ ...DESKTOP, baseURL });
  const desk = await computer.newPage();
  const address = await pressForCode(desk);
  const { context: phoneContext, page: phone } = await signedInPhone(browser, baseURL!);
  await atTheReadyScreen(phone, address);
  await phone.fill("#qr-answer", PASSWORD);
  await phone.click("#qr-approve-button");
  await desk.click("#qr-confirm");
  await desk.waitForURL((url) => !url.pathname.startsWith("/login"));

  await desk.goto("/all", { waitUntil: "load" });
  const follow = desk.locator(".wtf-action[data-url^='/api/follow/']").first();
  await expect(follow).toBeVisible({ timeout: 15_000 });
  const label = await follow.textContent();
  const said: string[] = [];
  desk.on("dialog", (dialog) => { said.push(dialog.message()); void dialog.accept(); });
  await follow.click();
  await expect.poll(() => said.length).toBe(1);
  expect(said[0]).toContain("Enter your password");
  // well before the 8 s a reply that never comes would wait
  await expect(follow).toHaveText(label as string, { timeout: 1000 });
  await expect(follow).toBeEnabled();

  // the profile's Follow, which names no text of its own, says the same
  await desk.goto("/@ui_qr_second", { waitUntil: "load" });
  const before = said.length;
  await desk.locator("#button-sub button").click();
  await expect.poll(() => said.length).toBe(before + 1);
  expect(said[before]).toContain("Enter your password");

  await computer.close();
  await phoneContext.close();
});

test("a browser whose sign-in was ended elsewhere can still use a code", async ({ browser, baseURL }) => {
  forgetLimits();
  phoneState = undefined;                                                   // what this test ends
  const old = await signedInPhone(browser, baseURL!, DESKTOP);
  phoneState = undefined;
  const { context: phoneContext, page: phone } = await signedInPhone(browser, baseURL!);
  await phone.goto("/settings/security", { waitUntil: "load" });
  const ended = await phone.evaluate(async (password) => {
    const form = new FormData();
    form.append("formkey", (window as unknown as { formkey(): string }).formkey());
    form.append("password", password);
    return (await fetch("/settings/log_out_all_others", { method: "POST", body: form })).status;
  }, PASSWORD);
  expect(ended).toBe(200);
  phoneState = await phoneContext.storageState();                           // this one took the new number

  const address = await pressForCode(old.page);
  await atTheReadyScreen(phone, address);
  await phone.fill("#qr-answer", PASSWORD);
  await phone.click("#qr-approve-button");
  await old.page.waitForFunction(() => document.querySelector(".qr-view.is-current")?.getAttribute("data-qr-view") === "approved");
  await old.page.click("#qr-confirm");
  await old.page.waitForURL((url) => !url.pathname.startsWith("/login"));
  await expect(old.page.locator("#qr-limited-notice")).toBeVisible();

  await old.context.close();
  await phoneContext.close();
});

test("Not me tells the computer, and a new code works", async ({ browser, baseURL }) => {
  forgetLimits();
  const computer = await browser.newContext({ ...DESKTOP, baseURL });
  const desk = await computer.newPage();
  const address = await pressForCode(desk);
  const { context: phoneContext, page: phone } = await signedInPhone(browser, baseURL!);
  await phone.goto(address, { waitUntil: "load" });
  await phone.waitForFunction(() => document.querySelector(".qr-view.is-current")?.getAttribute("data-qr-view") === "ready");
  await phone.keyboard.press("Enter");                                    // it has the focus
  await phone.waitForFunction(() => document.querySelector(".qr-view.is-current")?.getAttribute("data-qr-view") === "denied");
  expect(await phone.evaluate(() => document.activeElement?.getAttribute("data-qr-view")), "the focus followed").toBe("denied");
  await desk.waitForFunction(() => document.querySelector(".qr-view.is-current")?.getAttribute("data-qr-view") === "denied", undefined, { timeout: 15_000 });
  await expect(desk.locator("#qr-panel .qr-say")).toHaveText("Your phone said no, so nobody was signed in.");
  await desk.locator('[data-qr-view="denied"] [data-qr-new]').click();
  expect(await desk.evaluate(() => document.activeElement?.id), "the pressed button went: the focus has somewhere to be").toBe("qr-back");
  await desk.waitForFunction(() => !!document.querySelector<HTMLImageElement>("#qr-image")?.getAttribute("src"));
  expect(await current(desk)).toBe("waiting");

  // the same code, again: nothing to show
  await phone.goto(address, { waitUntil: "load" });
  await phone.waitForFunction(() => document.querySelector(".qr-view.is-current")?.getAttribute("data-qr-view") === "gone");
  // back to the password, and the panel lets go of the code
  await desk.click("#qr-back");
  await expect(desk.locator("form#login")).toBeVisible();
  await expect(desk.locator("#qr-panel")).toBeHidden();
  await phoneContext.close();
  await computer.close();
});

test("the phone believes only what the route said: a refused or ended answer never reads as done", async ({ browser, baseURL }) => {
  forgetLimits();
  const computer = await browser.newContext({ ...DESKTOP, baseURL });
  const desk = await computer.newPage();
  const address = await pressForCode(desk);
  const { context: phoneContext, page: phone } = await signedInPhone(browser, baseURL!);
  await atTheReadyScreen(phone, address);

  for (const [status, body] of [[429, "<html>Too many requests</html>"], [500, "<html>It broke</html>"]] as const) {
    // the limiter's page, or a fault: Not me stays to be pressed and says it didn't go through
    await phone.route("**/login/qr/deny", (route) => route.fulfill({ status, contentType: "text/html", body }));
    await phone.click("#qr-deny");
    await expect(phone.locator("#qr-deny + .qr-error"), `deny ${status}`).toHaveText("That didn't go through. Wait a moment and try again.");
    expect(await current(phone), `deny ${status}`).toBe("ready");
    await phone.unroute("**/login/qr/deny");
    // the same for the answer, and the code still stands
    await phone.route("**/login/qr/approve", (route) => route.fulfill({ status, contentType: "text/html", body }));
    await phone.fill("#qr-answer", PASSWORD);
    await phone.click("#qr-approve-button");
    await expect(phone.locator("#qr-error"), `approve ${status}`).toHaveText("That didn't go through. Wait a moment and try again.");
    expect(await current(phone), `approve ${status}`).toBe("ready");
    await phone.unroute("**/login/qr/approve");
  }
  await desk.waitForTimeout(3500);
  expect(await current(desk), "nothing was approved or refused").toBe("waiting");

  // the phone's sign-in ends after the page was drawn: the real redirect to /login, which a followed fetch would read as a 200
  await phoneContext.clearCookies();
  await phone.fill("#qr-answer", PASSWORD);
  await phone.click("#qr-approve-button");
  await phone.waitForFunction(() => document.querySelector(".qr-view.is-current")?.getAttribute("data-qr-view") === "signed-out");
  await expect(phone.locator('[data-qr-view="signed-out"] a[href="/login"]')).toBeVisible();
  await desk.waitForTimeout(3500);
  expect(await current(desk)).toBe("waiting");
  await phoneContext.close();

  // and for Not me, which mustn't say the code is dead when it isn't
  const second = await signedInPhone(browser, baseURL!);
  await atTheReadyScreen(second.page, address);
  await second.context.clearCookies();
  await second.page.click("#qr-deny");
  await second.page.waitForFunction(() => document.querySelector(".qr-view.is-current")?.getAttribute("data-qr-view") === "signed-out");
  await desk.waitForTimeout(3500);
  expect(await current(desk), "the code still stands").toBe("waiting");
  await second.context.close();
  await computer.close();
});

test("Not me takes an approval back, Confirm then says so, and once it is used it says it's too late", async ({ browser, baseURL }) => {
  forgetLimits();
  const computer = await browser.newContext({ ...DESKTOP, baseURL });
  const desk = await computer.newPage();
  let address = await pressForCode(desk);
  const { context: phoneContext, page: phone } = await signedInPhone(browser, baseURL!);
  await atTheReadyScreen(phone, address);
  await phone.fill("#qr-answer", PASSWORD);
  await phone.click("#qr-approve-button");
  await phone.waitForFunction(() => document.querySelector(".qr-view.is-current")?.getAttribute("data-qr-view") === "approved");
  await desk.waitForFunction(() => document.querySelector(".qr-view.is-current")?.getAttribute("data-qr-view") === "approved", undefined, { timeout: 15_000 });
  await phone.locator('[data-qr-view="approved"] [data-qr-deny]').click();
  await phone.waitForFunction(() => document.querySelector(".qr-view.is-current")?.getAttribute("data-qr-view") === "denied");
  await desk.click("#qr-confirm");
  await desk.waitForFunction(() => document.querySelector(".qr-view.is-current")?.getAttribute("data-qr-view") === "denied");
  expect(new URL(desk.url()).pathname).toBe("/login");

  // Cancel goes back to the password, and says nothing ran out
  address = await pressForCode(desk);
  await atTheReadyScreen(phone, address);
  await phone.fill("#qr-answer", PASSWORD);
  await phone.click("#qr-approve-button");
  await phone.waitForFunction(() => document.querySelector(".qr-view.is-current")?.getAttribute("data-qr-view") === "approved");
  await desk.waitForFunction(() => document.querySelector(".qr-view.is-current")?.getAttribute("data-qr-view") === "approved", undefined, { timeout: 15_000 });
  await desk.click("#qr-cancel");
  await expect(desk.locator("form#login")).toBeVisible();
  await expect(desk.locator("#qr-panel")).toBeHidden();
  expect(await desk.evaluate(() => document.activeElement?.id)).toBe("qr-start");

  // Confirm refused for a moment: a line to try again, not "run out", and the approval still works
  address = await pressForCode(desk);
  await atTheReadyScreen(phone, address);
  await phone.fill("#qr-answer", PASSWORD);
  await phone.click("#qr-approve-button");
  await phone.waitForFunction(() => document.querySelector(".qr-view.is-current")?.getAttribute("data-qr-view") === "approved");
  await desk.waitForFunction(() => document.querySelector(".qr-view.is-current")?.getAttribute("data-qr-view") === "approved", undefined, { timeout: 15_000 });
  await desk.route("**/login/qr/finish", (route) => route.fulfill({ status: 429, body: "" }));
  await desk.click("#qr-confirm");
  await expect(desk.locator("#qr-confirm-error")).toHaveText("That didn't go through. Wait a moment and try again.");
  expect(await current(desk)).toBe("approved");
  await desk.unroute("**/login/qr/finish");
  await desk.click("#qr-confirm");
  await desk.waitForURL((url) => !url.pathname.startsWith("/login"));

  // the computer is in: the phone can't refuse it now, and doesn't claim to
  await phone.locator('[data-qr-view="approved"] [data-qr-deny]').click();
  await phone.waitForFunction(() => document.querySelector(".qr-view.is-current")?.getAttribute("data-qr-view") === "too-late");
  await expect(phone.locator('[data-qr-view="too-late"] a[href="/settings/security#log-out-everywhere"]')).toBeVisible();
  await phoneContext.close();
  await computer.close();
});

test("an approval in the code's last seconds still reaches Confirm", async ({ browser, baseURL }) => {
  forgetLimits();
  const computer = await browser.newContext({ ...DESKTOP, baseURL });
  const desk = await computer.newPage();
  // the computer's clock says the code ends at 4 s, between the asks at 3 s and 6 s; the server's is still running
  await desk.route("**/login/qr/start", async (route) => {
    const real = await route.fetch();
    await route.fulfill({ response: real, json: { ...(await real.json()), seconds: 4 } });
  });
  const address = await pressForCode(desk);
  const pressed = Date.now();
  const { context: phoneContext, page: phone } = await signedInPhone(browser, baseURL!);
  await atTheReadyScreen(phone, address);
  await phone.fill("#qr-answer", PASSWORD);
  await desk.waitForTimeout(Math.max(0, 4300 - (Date.now() - pressed)));
  await phone.click("#qr-approve-button");
  await phone.waitForFunction(() => document.querySelector(".qr-view.is-current")?.getAttribute("data-qr-view") === "approved");
  await desk.waitForFunction(() => { const view = document.querySelector(".qr-view.is-current")?.getAttribute("data-qr-view"); return view === "approved" || view === "expired"; }, undefined, { timeout: 15_000 });
  expect(await current(desk), "the phone has told someone to press Confirm").toBe("approved");
  await phoneContext.close();
  await computer.close();
});

test("the phone's focus and screen reader: Not me is explained, and Approved comes into view", async ({ browser, baseURL }) => {
  forgetLimits();
  const computer = await browser.newContext({ ...DESKTOP, baseURL });
  const desk = await computer.newPage();
  const address = await pressForCode(desk);
  const size = { viewport: { width: 320, height: 568 }, userAgent: PHONE_AGENT, isMobile: true, hasTouch: true };
  const { context: phoneContext, page: phone } = await signedInPhone(browser, baseURL!, size);
  await phoneContext.addCookies([{ name: "text_size", value: "xlarge", url: baseURL! }]);
  await atTheReadyScreen(phone, address);
  await expect(phone.locator("#qr-deny")).toHaveAccessibleDescription(/Sign in on another screen\?.*Only continue if you started this yourself/);
  await phone.fill("#qr-answer", PASSWORD);
  await phone.locator("#qr-approve-button").tap();
  await phone.waitForFunction(() => document.querySelector(".qr-view.is-current")?.getAttribute("data-qr-view") === "approved");
  expect(await phone.evaluate(() => document.activeElement?.getAttribute("data-qr-view")), "the focus followed").toBe("approved");
  const line = await phone.locator('[data-qr-view="approved"] p').first().evaluate((n) => n.getBoundingClientRect().toJSON());
  expect(line.top, "the next thing to do is on the screen").toBeGreaterThanOrEqual(0);
  expect(line.bottom).toBeLessThanOrEqual(568);
  await phone.screenshot({ path: `${SHOTS}approved-320-xl.png` });
  await phoneContext.close();
  await computer.close();
});

test("with no script: the sign-in page has no button that can't work, and the phone says what to do", async ({ browser, baseURL }) => {
  const plain = await browser.newContext({ ...DESKTOP, baseURL, javaScriptEnabled: false });
  const page = await plain.newPage();
  await page.goto("/login", { waitUntil: "load" });
  await expect(page.locator("#qr-start")).toBeHidden();
  await expect(page.locator("#login_button")).toBeVisible();
  await plain.close();

  await signedInPhone(browser, baseURL!).then(({ context }) => context.close());
  const quiet = await browser.newContext({ ...PHONE, baseURL, javaScriptEnabled: false, storageState: phoneState });
  const phone = await quiet.newPage();
  await phone.goto("/login/qr/approve#c=abcdefghijklmnopqrstuvwxyz0123456789ABCDEFG", { waitUntil: "load" });
  await expect(phone.getByText("Approving a code needs script")).toBeVisible();
  await expect(phone.locator('[data-qr-view="loading"]')).toBeHidden();
  await quiet.close();
});

test("a signed-out phone is told to sign in first, with no way back", async ({ browser, baseURL }) => {
  const context = await browser.newContext({ ...PHONE, baseURL });
  const page = await context.newPage();
  await page.goto("/login/qr/approve#c=abcdefghijklmnopqrstuvwxyz0123456789ABCDEFG", { waitUntil: "load" });
  expect(page.url()).toBe(`${baseURL}/login/qr/approve`);
  await expect(page.getByText("Sign in on this phone first, then scan a new code.")).toBeVisible();
  expect(await page.locator("#qr-approve a.btn").getAttribute("href")).toBe("/login");
  await expect(page.locator("#qr-approve-form")).toHaveCount(0);
  await page.screenshot({ path: `${SHOTS}phone-signed-out.png` });
  await context.close();
});

test("polling: every 3 s, not while hidden, slower after a 429, stops after three, and offers a new code at the end", async ({ browser, baseURL }) => {
  forgetLimits();
  const context = await browser.newContext({ ...DESKTOP, baseURL });
  const page = await context.newPage();
  await page.addInitScript(() => { (window as unknown as { QR_SLOW_MS: number }).QR_SLOW_MS = 1000; });
  let asked = 0;
  let answer429 = false;
  await page.route("**/login/qr/status", async (route) => {
    asked += 1;
    if (answer429) await route.fulfill({ status: 429, body: "{}" });
    else await route.continue();
  });

  await pressForCode(page);
  await page.waitForTimeout(10_000);
  expect(asked).toBeGreaterThanOrEqual(3);
  expect(asked).toBeLessThanOrEqual(4);

  // hidden: no asking; shown: at once
  await page.evaluate(() => {
    Object.defineProperty(document, "hidden", { configurable: true, get: () => (window as unknown as { qrHidden: boolean }).qrHidden });
    (window as unknown as { qrHidden: boolean }).qrHidden = true;
    document.dispatchEvent(new Event("visibilitychange"));
  });
  await page.waitForTimeout(3500);
  const whileHidden = asked;
  await page.waitForTimeout(7000);
  expect(asked).toBe(whileHidden);
  await page.evaluate(() => {
    (window as unknown as { qrHidden: boolean }).qrHidden = false;
    document.dispatchEvent(new Event("visibilitychange"));
  });
  await page.waitForTimeout(300);
  expect(asked).toBeGreaterThan(whileHidden);

  // three 429s and it stops, with a way to start again
  answer429 = true;
  await page.waitForFunction(() => document.querySelector(".qr-view.is-current")?.getAttribute("data-qr-view") === "error", undefined, { timeout: 15_000 });
  const stopped = asked;
  await page.waitForTimeout(4000);
  expect(asked).toBe(stopped);
  await expect(page.locator('[data-qr-view="error"] [data-qr-new]')).toBeVisible();
  answer429 = false;
  await context.close();

  // the end of the code
  const shortened = await browser.newContext({ ...DESKTOP, baseURL });
  const quick = await shortened.newPage();
  await quick.route("**/login/qr/start", async (route) => {
    const real = await route.fetch();
    const body = await real.json();
    await route.fulfill({ response: real, json: { ...body, seconds: 4 } });
  });
  await quick.goto("/login", { waitUntil: "load" });
  await quick.click("#qr-start");
  await quick.waitForFunction(() => document.querySelector(".qr-view.is-current")?.getAttribute("data-qr-view") === "expired", undefined, { timeout: 15_000 });
  await expect(quick.locator('[data-qr-view="expired"] [data-qr-new]')).toBeVisible();
  await quick.locator('[data-qr-view="expired"]').screenshot({ path: `${SHOTS}desk-expired.png` });
  await shortened.close();
});

test("nothing shifts: the page on load, and the panel between its views", async ({ browser, baseURL }) => {
  forgetLimits();
  const context = await browser.newContext({ ...DESKTOP, baseURL });
  const page = await context.newPage();
  await page.addInitScript(() => {
    (window as unknown as { qrShift: number }).qrShift = 0;
    new PerformanceObserver((list) => {
      for (const entry of list.getEntries() as unknown as { hadRecentInput: boolean; value: number }[]) {
        if (!entry.hadRecentInput) (window as unknown as { qrShift: number }).qrShift += entry.value;
      }
    }).observe({ type: "layout-shift", buffered: true });
  });
  await page.goto("/login", { waitUntil: "load" });
  await page.waitForTimeout(800);
  expect(await page.evaluate(() => (window as unknown as { qrShift: number }).qrShift)).toBeLessThan(0.01);

  await page.click("#qr-start");
  await page.waitForFunction(() => !!document.querySelector<HTMLImageElement>("#qr-image")?.naturalWidth);
  const heights = await page.evaluate(() => {
    const panel = document.querySelector("#qr-panel")!;
    return [...panel.querySelectorAll(".qr-view")].map((view) => {
      panel.querySelectorAll(".qr-view").forEach((other) => other.classList.toggle("is-current", other === view));
      return panel.getBoundingClientRect().height;
    });
  });
  expect(new Set(heights).size).toBe(1);
  await context.close();
});

for (const [name, width, colorScheme, reducedMotion, language] of [
  ["320-light", 320, "light", "no-preference", ""], ["390-dark", 390, "dark", "no-preference", ""], ["1280-dark", 1280, "dark", "reduce", ""],
  ["1280-light", 1280, "light", "reduce", ""], ["390-rtl", 390, "light", "no-preference", "ar"], ["1280-rtl", 1280, "dark", "no-preference", "ar"],
] as const) {
  test(`both screens at ${name}`, async ({ browser, baseURL }) => {
    forgetLimits();
    const size = { viewport: { width, height: 844 }, userAgent: width < 768 ? PHONE_AGENT : AGENT, isMobile: width < 768, hasTouch: width < 768, colorScheme, reducedMotion };
    const context = await browser.newContext({ ...size, baseURL });
    if (language) await context.addCookies([{ name: "ui_lang", value: language, url: baseURL! }]);
    const page = await context.newPage();
    const address = await pressForCode(page);
    await expect(page.locator(".qr-box")).toBeVisible();
    // white whatever the theme, square, inside the screen and never mirrored
    const box = await page.locator(".qr-box").evaluate((node) => {
      const rect = node.getBoundingClientRect();
      const image = node.querySelector("img")!;
      return { paper: getComputedStyle(node).backgroundColor, width: rect.width, height: rect.height, left: rect.left, right: rect.right, transform: getComputedStyle(image).transform };
    });
    expect(box.paper).toBe("rgb(255, 255, 255)");
    expect(Math.abs(box.width - box.height)).toBeLessThanOrEqual(1);
    expect(box.left).toBeGreaterThanOrEqual(0);
    expect(box.right).toBeLessThanOrEqual(width);
    expect(box.transform).toBe("none");
    expect(await sideways(page)).toBeLessThanOrEqual(0);
    const dir = await page.evaluate(() => document.documentElement.dir);
    expect(dir).toBe(language === "ar" ? "rtl" : "ltr");
    // a press target of the ladder
    const smallest = await page.locator("#qr-back").evaluate((n) => n.getBoundingClientRect().height);
    expect(smallest).toBeGreaterThanOrEqual(width < 768 ? 44 : 32);
    await noSerious(page, `the panel ${name}`);
    await page.screenshot({ path: `${SHOTS}panel-${name}.png`, fullPage: true });

    const { context: phoneContext, page: phone } = await signedInPhone(browser, baseURL!, size);
    if (language) await phoneContext.addCookies([{ name: "ui_lang", value: language, url: baseURL! }]);
    await phone.goto(address, { waitUntil: "load" });
    await phone.waitForFunction(() => document.querySelector(".qr-view.is-current")?.getAttribute("data-qr-view") === "ready");
    expect(await sideways(phone)).toBeLessThanOrEqual(0);
    await noSerious(phone, `the phone's screen ${name}`);
    for (const id of ["#qr-deny", "#qr-approve-button"]) {
      expect(await phone.locator(id).evaluate((n) => n.getBoundingClientRect().height), id).toBeGreaterThanOrEqual(width < 768 ? 44 : 32);
    }
    await phone.screenshot({ path: `${SHOTS}approve-${name}.png`, fullPage: true });
    await phoneContext.close();
    await context.close();
  });
}
