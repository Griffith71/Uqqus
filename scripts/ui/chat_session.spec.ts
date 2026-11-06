import { test, expect, type BrowserContext, type Page } from "@playwright/test";
import { execSync } from "node:child_process";
import { existsSync, mkdirSync, readFileSync } from "node:fs";

// Two accounts in one browser (CLAUDE.md "Chat encryption"): what one leaves behind must never become the other's.
//   PYTHONPATH=. python scripts/ui/seed_chat_session.py   (in the container), then: npm run chat-session
// The chat, call and Space windows each hold a Matrix session in localStorage and a crypto store in IndexedDB. Logging out has to take the token,
// and a page has to refuse what another account left there (a cookie that ran out never passes through a Log out link).
// Sign-ins are limited to 6 a minute, so each account signs in through the real form once and the tests put a cookie back; the one test
// that needs a real sign-in (the pre-key that unlocks a new device) makes two more, and the refused sign-in's test three (a 429 reads as a sign-in that never leaves /login).
// A Log out ends its sign-in for good on the server (helpers/sign_in_ends.py), so a saved cookie can't be put back after one: each test gets a cookie
// of its own, signed by the app in the container (`docker exec`, RUQQUS_CONTAINER, as money.spec.ts does), as a sign-in would leave it.

test.describe.configure({ mode: "serial" });
test.use({
  launchOptions: { args: ["--use-fake-ui-for-media-stream", "--use-fake-device-for-media-stream"] },
  permissions: ["microphone", "camera"],
});

const PASSWORD = "ui-Password-1";
const A = { name: "ui_chat_a", mxid: "@ruqqus_90703:ruqqus.local" };
const B = { name: "ui_chat_b", mxid: "@ruqqus_90704:ruqqus.local" };
const CHAT_STORES = ["matrix-js-sdk::matrix-sdk-crypto"];
const CALL_STORES = ["uqqus-call::matrix-sdk-crypto"];
type Cookies = Parameters<BrowserContext["addCookies"]>[0];
type Session = { homeserver_url: string; user_id: string; access_token: string; device_id: string };

async function signIn(page: Page, name: string) {
  await page.goto("/login");
  await page.fill("#username", name);
  await page.fill("#password", PASSWORD);
  await Promise.all([page.waitForURL((url) => !url.pathname.startsWith("/login")), page.click("#login_button")]);
}

const cookiesOf = (who: string): Cookies => JSON.parse(readFileSync(`.auth/${who}.json`, "utf8")).cookies;

const CONTAINER = process.env.RUQQUS_CONTAINER || "ruqqus-ruqqus-1";
const minted: Record<string, string[]> = {};

// session cookies for these accounts, each with a session id of its own, signed with the app's own key: what the login route leaves, without
// spending a sign-in
function mintCookies(names: string[], count = 30) {
  const script = `
import json, secrets
from ruqqus.__main__ import app, db_session
from ruqqus.classes import User
signer = app.session_interface.get_signing_serializer(app)
db = db_session()
made = {}
for name in ${JSON.stringify(names)}:
    user = db.query(User).filter_by(username=name).first()
    made[name] = [signer.dumps({"user_id": user.id, "login_nonce": user.login_nonce, "session_id": secrets.token_hex(16), "_permanent": True})
                  for _ in range(${count})]
print("MINTED " + json.dumps(made))
`;
  const out = execSync(`docker exec -i -w /opt/ruqqus/service ${CONTAINER} env PYTHONPATH=. venv/bin/python -`, { input: script, maxBuffer: 16_000_000 }).toString();
  const line = out.split("\n").find((l) => l.startsWith("MINTED "))!;
  Object.assign(minted, JSON.parse(line.slice(7)));
}

async function becomes(context: BrowserContext, who: string) {
  if (!minted[who]?.length) mintCookies([who]);
  const template = cookiesOf(who).find((c) => c.name === "session_ruqqus")!;
  await context.clearCookies();
  await context.addCookies([{ ...template, value: minted[who].shift()! }]);
}

// initRustCrypto's error for a store made for another account or device
const storeRefused = (text: string) => /doesn't match|does not match|mismatch/i.test(text);
const stored = (page: Page, key: string) => page.evaluate((k) => JSON.parse(localStorage.getItem(k) || "null"), key) as Promise<Session | null>;
const databases = (page: Page) => page.evaluate(async () => (await indexedDB.databases()).map((d) => d.name)) as Promise<string[]>;

// asked from the page, as the app asks: whom the chat server takes this token to be (null when it no longer takes it)
const tokenBelongsTo = (page: Page, session: Session) => page.evaluate(async (s) => {
  const answer = await fetch(`${s.homeserver_url}/_matrix/client/v3/account/whoami`, { headers: { Authorization: `Bearer ${s.access_token}` } });
  return answer.ok ? (await answer.json()).user_id : null;
}, session);

async function openChat(page: Page, mxid: string) {
  const errors: string[] = [];
  const listen = (message: { type(): string; text(): string }) => {
    if (message.type() === "error" || message.type() === "warning") errors.push(message.text());
  };
  page.on("console", listen);
  await page.goto("/chat", { waitUntil: "load" });
  await expect.poll(async () => (await stored(page, "ruqqus_chat_session"))?.user_id, { timeout: 30_000 }).toBe(mxid);
  // the store exists from here on, whether this device is unlocked yet or is asking for the password (a cookie put back has no pre-key)
  await expect.poll(async () => (await databases(page)).includes(CHAT_STORES[0]), { timeout: 30_000 }).toBe(true);
  await page.waitForTimeout(1_500);
  page.off("console", listen);
  return errors;
}

async function expectNoTokenLeft(page: Page) {
  for (const key of ["ruqqus_chat_session", "ruqqus_call_session"]) {
    const session = await stored(page, key);
    expect(session?.access_token).toBeUndefined();
  }
  const left = await page.evaluate(() => [sessionStorage.getItem("uqqus_chat_prekey"), localStorage.getItem("uqqus_chat_prekey"), localStorage.getItem("uqqus_chat_waiting")]);
  expect(left).toEqual([null, null, null]);
}

// the link's own handler, as the menu's click runs it (a chat page's own dialogs could be over the menu); the page comes back signed out
async function pressLogOut(page: Page, link: string) {
  // no wait for the page's own requests to settle: one still on its way when Log out answers can't put the old cookie back any more
  // (helpers/sign_in_ends.py; "a log out can't be undone" below)
  await page.locator(link).first().evaluate((a: HTMLElement) => a.click());
  await expect(page.locator("a[data-logout]")).toHaveCount(0, { timeout: 15_000 });
  await page.waitForLoadState("load");
}

async function roomBetween(page: Page, other: string) {
  return page.evaluate(async (name) => {
    const form = new FormData();
    form.append("formkey", (window as unknown as { formkey(): string }).formkey());
    form.append("username", name);
    const answer = await fetch("/api/chat/start?json=1", { method: "POST", body: form });
    return (await answer.json()).room_id as string;
  }, other);
}

// read as any script on the signed-out page could read it
const keptAfterLogOut = (page: Page) => page.evaluate(async () => {
  const db = await new Promise<IDBDatabase>((resolve, reject) => {
    const open = indexedDB.open("matrix-js-sdk::matrix-sdk-crypto");
    open.onsuccess = () => resolve(open.result);
    open.onerror = () => reject(open.error);
  });
  const ask = <T,>(request: IDBRequest<T>) => new Promise<T>((resolve, reject) => {
    request.onsuccess = () => resolve(request.result);
    request.onerror = () => reject(request.error);
  });
  const store = (name: string) => db.transaction(name).objectStore(name);
  const kept = {
    session: JSON.parse(localStorage.getItem("ruqqus_chat_session") || "null"),
    core: (await ask(store("core").getAllKeys())).map(String),
    backup: await ask(store("backup_keys").count()),
    roomKeys: await ask(store("inbound_group_sessions3").count()),
  };
  db.close();
  return kept;
});

const keysLocked = (page: Page) => page.evaluate(async () => {
  const info = await (await fetch("/api/chat/key")).json();
  return !!(info.password_blob && info.recovery_blob);
});

// Each account's chat is set up once, through the real form and to the end: the first chat page makes its keys, locks them with the pre-key the
// form left and shows the recovery code. A chat set up any other way asks for a password nobody types, and is stuck at "Start over?" afterwards.
test.beforeAll(async ({ browser }) => {
  mkdirSync(".auth", { recursive: true });
  for (const account of [A, B]) {
    const context = await browser.newContext();
    const page = await context.newPage();
    if (existsSync(`.auth/${account.name}.json`)) {                           // a run a minute ago must not spend the minute's sign-ins
      await becomes(context, account.name);
      const answer = await context.request.get("/settings/profile", { maxRedirects: 0 });
      const keys = answer.status() === 200 ? await (await context.request.get("/api/chat/key")).json() : {};
      if (keys.password_blob && keys.recovery_blob) {                         // its chat was set up through the form already
        await context.close();
        continue;
      }
      await context.clearCookies();
    }
    await signIn(page, account.name);
    await page.goto("/chat", { waitUntil: "load" });
    const saved = page.getByRole("button", { name: "I've saved it" });
    await expect.poll(async () => {
      if (await saved.count()) await saved.click();
      return keysLocked(page);
    }, { timeout: 60_000 }).toBe(true);
    await expect.poll(async () => saved.count(), { timeout: 15_000 }).toBe(0);
    await page.waitForTimeout(3_000);                                         // /api/chat/ready, which the other account's messages wait for
    await context.storageState({ path: `.auth/${account.name}.json` });
    await context.close();
  }
});

test("another account's chat session, crypto store and waiting messages are never used", async ({ browser }) => {
  const context = await browser.newContext({ viewport: { width: 1280, height: 800 } });
  const page = await context.newPage();
  await becomes(context, A.name);
  await openChat(page, A.mxid);
  const left = (await stored(page, "ruqqus_chat_session"))!;
  await page.evaluate(() => localStorage.setItem("uqqus_chat_waiting", JSON.stringify({ "!room:x": [{ text: "from A", at: 1 }] })));
  // no Log out link: A's cookie just stops counting (it ran out, or the browser was handed over) and B signs in, with A's token still stored
  await becomes(context, B.name);
  const errors = await openChat(page, B.mxid);
  const now = (await stored(page, "ruqqus_chat_session"))!;
  expect(now.user_id).toBe(B.mxid);
  expect(now.device_id).not.toBe(left.device_id);
  expect(await tokenBelongsTo(page, now)).toBe(B.mxid);
  expect(await page.evaluate(() => localStorage.getItem("uqqus_chat_waiting"))).toBeNull();
  expect(errors.filter(storeRefused)).toEqual([]);
  await context.close();
});

for (const [shell, path, width] of [["the account menu on a feed", "/", 1280], ["the account menu on settings", "/settings/profile", 1280], ["the account menu on the chat page itself", "/chat", 1280]] as const) {
  test(`Log out in ${shell} takes the token, the pre-key and the waiting messages`, async ({ browser }) => {
    const context = await browser.newContext({ viewport: { width, height: 800 } });
    const page = await context.newPage();
    await becomes(context, A.name);
    await openChat(page, A.mxid);
    if (path !== "/chat") await page.goto(path, { waitUntil: "load" });
    await page.evaluate(() => {
      sessionStorage.setItem("uqqus_chat_prekey", "x");
      localStorage.setItem("uqqus_chat_waiting", "{}");
    });
    await pressLogOut(page, ".account-menu [data-logout]");
    await expectNoTokenLeft(page);
    await context.close();
  });
}

test("Log out in the phone menu of a settings page does the same", async ({ browser }) => {
  const context = await browser.newContext({ viewport: { width: 390, height: 800 }, hasTouch: true });
  const page = await context.newPage();
  await becomes(context, A.name);
  await openChat(page, A.mxid);
  await page.goto("/settings/profile", { waitUntil: "load" });
  await pressLogOut(page, "#navbar a[data-logout].nav-link");
  await expectNoTokenLeft(page);
  await context.close();
});

test("after a log out the next account's chat page clears the device and starts its own", async ({ browser }) => {
  const context = await browser.newContext({ viewport: { width: 1280, height: 800 } });
  const page = await context.newPage();
  await becomes(context, A.name);
  await openChat(page, A.mxid);
  const left = (await stored(page, "ruqqus_chat_session"))!;
  await page.goto("/settings/profile", { waitUntil: "load" });
  await pressLogOut(page, ".account-menu [data-logout]");
  await becomes(context, B.name);
  const errors = await openChat(page, B.mxid);
  const now = (await stored(page, "ruqqus_chat_session"))!;
  expect(now.device_id).not.toBe(left.device_id);
  expect(await tokenBelongsTo(page, now)).toBe(B.mxid);
  expect(errors.filter(storeRefused)).toEqual([]);
  await expect(page.locator("#chat-conversation-list")).toBeVisible();
  await context.close();
});

test("the call window and a Space leave nothing of the other account either", async ({ browser }) => {
  const context = await browser.newContext({ viewport: { width: 1280, height: 800 } });
  const page = await context.newPage();
  await becomes(context, A.name);
  await openChat(page, A.mxid);
  const room = await roomBetween(page, B.name);
  expect(room).toBeTruthy();
  await page.goto("/settings/profile", { waitUntil: "load" });

  // A's call window makes the call device
  const call = await context.newPage();
  await call.goto(`/call/${room}?kind=voice`, { waitUntil: "load" });
  await expect.poll(async () => (await stored(call, "ruqqus_call_session"))?.user_id, { timeout: 30_000 }).toBe(A.mxid);
  await expect.poll(async () => (await databases(call)).includes(CALL_STORES[0]), { timeout: 30_000 }).toBe(true);
  const aCall = (await stored(call, "ruqqus_call_session"))!;
  await call.close();

  // B's opens on what A left, with no chat page in between
  await becomes(context, B.name);
  const bCall = await context.newPage();
  await bCall.goto(`/call/${room}?kind=voice`, { waitUntil: "load" });
  await expect.poll(async () => (await stored(bCall, "ruqqus_call_session"))?.user_id, { timeout: 30_000 }).toBe(B.mxid);
  const session = (await stored(bCall, "ruqqus_call_session"))!;
  expect(session.device_id).not.toBe(aCall.device_id);
  expect(await tokenBelongsTo(bCall, session)).toBe(B.mxid);
  expect(await stored(bCall, "ruqqus_chat_session")).toBeNull();              // A's chat device went with it
  await bCall.close();

  // and a Space on what B's call window left, for A
  await becomes(context, A.name);
  await page.goto("/spaces/new", { waitUntil: "load" });
  const made = await page.evaluate(async () => {
    const form = new FormData();
    form.append("formkey", (window as unknown as { formkey(): string }).formkey());
    form.append("space_kind", "call");
    form.append("title", `${crypto.randomUUID()} ${crypto.randomUUID()}`);        // never alike: similar titles close together suspend the account
    form.append("audience", "0");
    const answer = await fetch("/api/spaces", { method: "POST", body: form });
    return { status: answer.status, body: await answer.json() };
  });
  expect(made.status, JSON.stringify(made.body)).toBe(200);
  await page.goto(made.body.url, { waitUntil: "load" });
  await page.locator("#space-start").click();                                // a call's host joins as they start it
  await expect.poll(async () => (await stored(page, "ruqqus_call_session"))?.user_id, { timeout: 30_000 }).toBe(A.mxid);
  const spaceSession = (await stored(page, "ruqqus_call_session"))!;
  expect(spaceSession.device_id).not.toBe(session.device_id);
  expect(await tokenBelongsTo(page, spaceSession)).toBe(A.mxid);
  await context.close();
});

test("a chat left open in another tab stops with its member, and can't take the next account's session with it", async ({ browser }) => {
  const context = await browser.newContext({ viewport: { width: 1280, height: 800 } });
  const left = await context.newPage();
  await becomes(context, A.name);
  await openChat(left, A.mxid);

  const other = await context.newPage();
  await other.goto("/settings/profile", { waitUntil: "load" });
  await pressLogOut(other, ".account-menu [data-logout]");
  await expect.poll(() => left.url(), { timeout: 15_000 }).toContain("/login");   // it heard, at once, without a sync

  await becomes(context, B.name);
  await openChat(other, B.mxid);
  const bSession = (await stored(other, "ruqqus_chat_session"))!;
  await left.waitForTimeout(3_000);
  expect((await stored(other, "ruqqus_chat_session"))?.user_id).toBe(B.mxid);
  expect(await tokenBelongsTo(other, bSession)).toBe(B.mxid);
  await context.close();
});

// chat_key.js writes the pre-key when the form is sent, before the site has answered: the page drawn next has to take it away unless someone signed in
test("a refused sign-in leaves no pre-key, a visitor's pages take one that was left, and a real sign-in keeps it for chat", async ({ browser }) => {
  const context = await browser.newContext({ viewport: { width: 1280, height: 800 } });
  // proves the key was written at all: the page that answers has already taken it by the time anything can look
  await context.addInitScript(() => {
    const write = Storage.prototype.setItem;
    Storage.prototype.setItem = function (key: string, value: string) {
      if (key === "uqqus_chat_prekey") window.sessionStorage.setItem("seen_prekey", "1");
      return write.call(this, key, value);
    };
  });
  const page = await context.newPage();
  const prekey = () => page.evaluate(() => sessionStorage.getItem("uqqus_chat_prekey"));
  const refused = async (name: string, password: string) => {
    await page.goto("/login");
    await page.fill("#username", name);
    await page.fill("#password", password);
    await Promise.all([page.waitForNavigation({ waitUntil: "load" }), page.click("#login_button")]);   // same address: nothing else says the answer has been drawn
    expect(await page.evaluate(() => sessionStorage.getItem("seen_prekey")), "the form wrote it").toBe("1");
    expect(await prekey()).toBeNull();
  };
  await refused("ui_nobody_at_all", "a-mistyped-password");
  await refused(A.name, "the-wrong-password");

  // sent, and the answer never read (a closed tab, a lost connection): the next visitor's page takes it, whichever shell it is
  for (const path of ["/", "/help/privacy", "/signup", "/forgot"]) {
    await page.evaluate(() => {
      sessionStorage.setItem("uqqus_chat_prekey", "LEFT");
      localStorage.setItem("uqqus_chat_prekey", "AN OLDER COPY");
    });
    await page.goto(path);
    expect(await prekey(), path).toBeNull();
    expect(await page.evaluate(() => localStorage.getItem("uqqus_chat_prekey")), path).toBeNull();
  }

  await signIn(page, A.name);
  const key = await prekey();
  expect(key).toMatch(/^[\w-]{40,}$/);
  await page.goto("/help/privacy");
  await page.goto("/");
  expect(await prekey(), "a member's pages leave it for chat").toBe(key);

  // kept for its own tab (assets/js/chat_key.js), because a tag running in another could read it at once in localStorage (ads.spec.ts)
  expect(await page.evaluate(() => localStorage.getItem("uqqus_chat_prekey"))).toBeNull();
  const other = await context.newPage();
  await other.goto("/help/privacy", { waitUntil: "load" });
  expect(await other.evaluate(() => [sessionStorage.getItem("uqqus_chat_prekey"), localStorage.getItem("uqqus_chat_prekey")])).toEqual([null, null]);
  await other.close();
  // and a frame in the tab shares it: the side panel's chat takes it, with nothing to type
  const unlocked = page.waitForResponse("**/api/chat/ready", { timeout: 30_000 });
  await page.locator('[data-side-panel="chat"]:visible').first().click();
  await unlocked;
  await expect.poll(() => prekey(), { timeout: 30_000 }).toBeNull();
  expect(await page.frameLocator("iframe").locator(".chat-key-dialog").count()).toBe(0);
  await context.close();
});

test("chat with no pre-key in its tab, as in a new tab after a sign-in, asks for the password once", async ({ browser }) => {
  const context = await browser.newContext({ viewport: { width: 1280, height: 800 } });
  const page = await context.newPage();
  await becomes(context, A.name);        // signed in, and nothing in this tab's storage: what a tab other than the one the password was typed in has
  await page.goto("/chat", { waitUntil: "load" });
  await expect(page.locator(".chat-key-dialog input[type=password]")).toBeVisible({ timeout: 30_000 });
  await context.close();
});

test("the same account signing back in takes up its device, types nothing and loses nothing, however soon it logged out", async ({ browser }) => {
  const context = await browser.newContext({ viewport: { width: 1280, height: 800 } });
  const page = await context.newPage();
  await signIn(page, A.name);                                                 // the real form: it leaves the pre-key for chat
  await openChat(page, A.mxid);
  const room = await roomBetween(page, B.name);
  const words = `Written just before logging out ${Date.now()}`;
  const unlocked = page.waitForResponse("**/api/chat/ready", { timeout: 30_000 });   // the box shows before the device is unlocked, and refuses to send till then
  await page.goto(`/chat?room=${encodeURIComponent(room)}`, { waitUntil: "load" });
  await unlocked;
  await page.locator("#chat-composer-input").fill(words);
  const sent = page.waitForResponse((answer) => /\/send\/m\.room\.encrypted\//.test(answer.url()) && answer.ok(), { timeout: 30_000 });
  await page.locator("#chat-composer-input").press("Enter");
  await sent;
  // out within seconds: the key backup uploads up to 10 seconds later, so a store deleted here would lose this message for good
  const before = (await stored(page, "ruqqus_chat_session"))!;
  await pressLogOut(page, ".account-menu [data-logout]");
  await expectNoTokenLeft(page);
  // what stays on a signed-out browser, readable with no sign-in, as CLAUDE.md's limits say: change one, change the other
  const kept = await keptAfterLogOut(page);
  expect(kept.session).toEqual({ user_id: A.mxid, device_id: before.device_id });
  expect(kept.core).toContain("private_identity");
  expect(kept.backup).toBeGreaterThan(0);
  expect(kept.roomKeys).toBeGreaterThan(0);

  await signIn(page, A.name);
  const errors = await openChat(page, A.mxid);
  const after = (await stored(page, "ruqqus_chat_session"))!;
  expect(after.device_id).toBe(before.device_id);
  expect(after.access_token).not.toBe(before.access_token);
  await expect.poll(() => page.evaluate(() => sessionStorage.getItem("uqqus_chat_prekey")), { timeout: 30_000 }).toBeNull();
  expect(await page.locator(".chat-key-dialog").count()).toBe(0);             // nothing to type
  expect(errors.filter(storeRefused)).toEqual([]);
  await page.goto(`/chat?room=${encodeURIComponent(room)}`, { waitUntil: "load" });
  await expect(page.locator("#chat-thread-pane")).toContainText(words, { timeout: 30_000 });
  await context.close();
});

test("a device signed out while nobody was signed in here isn't taken up again", async ({ browser }) => {
  const context = await browser.newContext({ viewport: { width: 1280, height: 800 } });
  const page = await context.newPage();
  await becomes(context, A.name);
  await openChat(page, A.mxid);
  const left = (await stored(page, "ruqqus_chat_session"))!;
  await page.goto("/settings/profile", { waitUntil: "load" });
  await pressLogOut(page, ".account-menu [data-logout]");

  const elsewhere = await browser.newContext({ viewport: { width: 1280, height: 800 } });
  await becomes(elsewhere, A.name);
  const settings = await elsewhere.newPage();
  await settings.goto("/settings/chat_apps", { waitUntil: "load" });
  const status = await settings.evaluate(async (device) => {
    const form = new FormData();
    form.append("formkey", (window as unknown as { formkey(): string }).formkey());
    form.append("device_id", device);
    return (await fetch("/api/chat/devices/sign_out", { method: "POST", body: form })).status;
  }, left.device_id);
  expect(status).toBe(204);
  await elsewhere.close();

  await becomes(context, A.name);
  const errors = await openChat(page, A.mxid);
  const now = (await stored(page, "ruqqus_chat_session"))!;
  expect(now.device_id).not.toBe(left.device_id);
  expect(await tokenBelongsTo(page, now)).toBe(A.mxid);
  expect(errors.filter(storeRefused)).toEqual([]);
  await context.close();
});

// --- pressing Log out when the answer isn't the usual one

async function chatThenSettings(context: BrowserContext, page: Page) {
  await becomes(context, A.name);
  await openChat(page, A.mxid);
  await page.goto("/settings/profile", { waitUntil: "load" });
  await page.evaluate(() => {
    sessionStorage.setItem("uqqus_chat_prekey", "PREKEY");
    localStorage.setItem("uqqus_chat_waiting", JSON.stringify({ "!room:x": [{ text: "plain text", at: 1 }] }));
  });
}

test("Log out on a sign-in that is already gone clears everything and isn't called a failure", async ({ browser }) => {
  const context = await browser.newContext({ viewport: { width: 1280, height: 800 } });
  const page = await context.newPage();
  await chatThenSettings(context, page);
  const said: string[] = [];
  page.on("dialog", (dialog) => { said.push(dialog.message()); dialog.accept(); });
  await context.clearCookies();                                               // a password change or "log out everywhere" in another browser
  await pressLogOut(page, ".account-menu [data-logout]");
  await expectNoTokenLeft(page);
  expect(said).toEqual([]);
  await context.close();
});

test("Log out that never reaches the site still clears this browser, says so and leaves the member able to carry on", async ({ browser }) => {
  const context = await browser.newContext({ viewport: { width: 1280, height: 800 } });
  const page = await context.newPage();
  await chatThenSettings(context, page);
  const left = (await stored(page, "ruqqus_chat_session"))!;
  const said: string[] = [];
  page.on("dialog", (dialog) => { said.push(dialog.message()); dialog.accept(); });
  await page.route("**/logout", (route) => route.abort("connectionfailed"));
  const reloaded = page.waitForEvent("load");
  await page.locator(".account-menu [data-logout]").first().evaluate((a: HTMLElement) => a.click());
  await reloaded;
  expect(said.length).toBe(1);
  await expectNoTokenLeft(page);
  await expect(page.locator(".account-menu [data-logout]").first()).toBeAttached();   // still signed in, and the page shows it
  await page.unroute("**/logout");
  await openChat(page, A.mxid);
  expect((await stored(page, "ruqqus_chat_session"))?.device_id).toBe(left.device_id);
  await context.close();
});

// The old bug (CLAUDE.md "Chat encryption"): an answer of the page's own, worked out before Log out and arriving after it, put the old cookie back and
// the page was signed in again. It still arrives with the old cookie, and the next request refuses it (helpers/sign_in_ends.py).
test("a log out can't be undone by answers that were on their way", async ({ browser }) => {
  const context = await browser.newContext({ viewport: { width: 1280, height: 800 } });
  await becomes(context, A.name);
  const page = await context.newPage();
  await page.goto("/settings/profile", { waitUntil: "load" });
  // asked from another tab, which stays where it is (the tab that logs out reloads, and a reload cancels its own requests)
  const other = await context.newPage();
  await other.goto("/help/privacy", { waitUntil: "load" });
  let asked = 0;
  let release!: () => void;
  const held = new Promise<void>((resolve) => { release = resolve; });
  await other.route("**/api/chat/key", async (route) => {
    const answer = await route.fetch();                                     // answered by the site now, with the old cookie
    asked += 1;
    await held;
    await route.fulfill({ response: answer });                              // and arrives after the Log out
  });
  const answers = other.evaluate(() => Promise.all([0, 1, 2, 3].map(() => fetch("/api/chat/key").then((r) => r.status))));
  await expect.poll(() => asked, { timeout: 15_000 }).toBe(4);
  await pressLogOut(page, ".account-menu [data-logout]");
  release();
  expect(await answers).toEqual([200, 200, 200, 200]);                       // they did arrive, old cookie and all
  for (const path of ["/", "/settings/profile", "/"]) {
    await page.goto(path, { waitUntil: "load" });
    await expect(page.locator("a[data-logout]")).toHaveCount(0);
  }
  await context.close();
});

// Playwright doesn't route a redirect's own request, so a home page answering 500 is tests/test_chat_session.py's; this is the answer being lost after the site acted.
test("Log out whose answer is lost after the site has acted has still logged out", async ({ browser }) => {
  const context = await browser.newContext({ viewport: { width: 1280, height: 800 } });
  const page = await context.newPage();
  await chatThenSettings(context, page);
  page.on("dialog", (dialog) => dialog.accept());
  await page.route("**/logout", async (route) => {
    await route.fetch({ maxRedirects: 0 });
    await route.abort("connectionfailed");
  });
  await pressLogOut(page, ".account-menu [data-logout]");
  await expectNoTokenLeft(page);
  await context.close();
});

// --- two tabs at once

const syncsOf = (page: Page) => {
  const seen = { syncs: 0, mints: 0 };
  page.on("request", (request) => {
    if (/\/_matrix\/client\/v3\/sync/.test(request.url())) seen.syncs += 1;
    if (request.url().endsWith("/api/chat/token")) seen.mints += 1;
  });
  return seen;
};
const NOTE = "Chat is open in another tab.";

test("another account opening chat beside a chat tab leaves one client, not two", async ({ browser }) => {
  const context = await browser.newContext({ viewport: { width: 1280, height: 800 } });
  const first = await context.newPage();
  await becomes(context, A.name);
  await openChat(first, A.mxid);
  await becomes(context, B.name);                                             // no Log out link: the cookie just changes hands
  const firstSeen = syncsOf(first);
  const second = await context.newPage();
  const secondSeen = syncsOf(second);
  await second.goto("/chat", { waitUntil: "load" });
  // the first tab hears its session go and comes back as B, taking the chat from the second
  await expect.poll(async () => (await stored(second, "ruqqus_chat_session"))?.user_id, { timeout: 30_000 }).toBe(B.mxid);
  await expect(second.getByText(NOTE)).toBeVisible({ timeout: 30_000 });
  await first.waitForTimeout(8_000);
  const syncsSoFar = secondSeen.syncs;
  await first.waitForTimeout(32_000);                                          // a sync is a long poll of 30 s
  expect(secondSeen.syncs, "the tab that lost the chat lock has no client").toBe(0);
  expect(syncsSoFar).toBe(0);
  expect(firstSeen.syncs).toBeGreaterThan(0);
  await expect(first.getByText(NOTE)).toHaveCount(0);
  await context.close();
});

test("two tabs opening chat together on a new browser make one device, and none is left on Loading", async ({ browser }) => {
  for (let run = 0; run < 5; run += 1) {
    const context = await browser.newContext({ viewport: { width: 1280, height: 800 } });
    await becomes(context, A.name);
    const [one, two] = [await context.newPage(), await context.newPage()];
    const errors: string[] = [];
    for (const page of [one, two]) page.on("console", (message) => { if (message.type() === "error") errors.push(message.text()); });
    const seen = [syncsOf(one), syncsOf(two)];
    await Promise.all([one.goto("/chat", { waitUntil: "load" }), two.goto("/chat", { waitUntil: "load" })]);
    // one tab takes the lock last and runs; the other says so
    await expect.poll(async () => (await one.getByText(NOTE).count()) + (await two.getByText(NOTE).count()), { timeout: 45_000 }).toBe(1);
    await expect.poll(() => seen[0].syncs + seen[1].syncs, { timeout: 45_000 }).toBeGreaterThan(0);
    expect(seen[0].mints + seen[1].mints, `run ${run}`).toBe(1);
    expect(errors.filter(storeRefused), `run ${run}`).toEqual([]);
    await context.close();
  }
});

test("a browser already stuck with a session and a store of different devices gets out of it by itself", async ({ browser }) => {
  const context = await browser.newContext({ viewport: { width: 1280, height: 800 } });
  const page = await context.newPage();
  await becomes(context, A.name);
  await openChat(page, A.mxid);
  const left = (await stored(page, "ruqqus_chat_session"))!;
  // what the race used to leave: a good token for a device the store wasn't made for
  const other = await page.evaluate(async () => {
    const form = new FormData();
    form.append("formkey", (window as unknown as { formkey(): string }).formkey());
    return (await fetch("/api/chat/token", { method: "POST", body: form })).json();
  });
  expect(other.device_id).not.toBe(left.device_id);
  await page.evaluate((session) => localStorage.setItem("ruqqus_chat_session", JSON.stringify(session)), other);
  await page.goto("/settings/profile", { waitUntil: "load" });

  const seen = syncsOf(page);
  await page.goto("/chat", { waitUntil: "load" });
  await expect.poll(() => seen.syncs, { timeout: 45_000 }).toBeGreaterThan(0);
  const now = (await stored(page, "ruqqus_chat_session"))!;
  expect([left.device_id, other.device_id]).not.toContain(now.device_id);      // a new device, with a store of its own
  expect(await tokenBelongsTo(page, now)).toBe(A.mxid);
  await context.close();
});

// --- deleting an account signs out through a form, not a Log out link

const GONE = "ui_chat_gone";

test("deleting an account takes its chat device out of the browser, and a wrong password takes nothing", async ({ browser }) => {
  const context = await browser.newContext({ viewport: { width: 1280, height: 800 } });
  const page = await context.newPage();
  await page.goto("/login");
  await page.fill("#username", GONE);
  await page.fill("#password", PASSWORD);
  await page.click("#login_button");
  await page.waitForURL((url) => !url.pathname.startsWith("/login"), { timeout: 15_000 }).catch(() => {});
  test.skip(page.url().includes("/login"), `${GONE} has been deleted already: run scripts/ui/seed_chat_session.py again`);

  await page.goto("/settings/security", { waitUntil: "load" });
  await page.evaluate(async () => {
    localStorage.setItem("ruqqus_chat_session", JSON.stringify({ user_id: "@x:s", access_token: "t", device_id: "D" }));
    localStorage.setItem("ruqqus_call_session", JSON.stringify({ user_id: "@x:s", access_token: "t", device_id: "E" }));
    sessionStorage.setItem("uqqus_chat_prekey", "PREKEY");
    localStorage.setItem("uqqus_chat_waiting", JSON.stringify({ "!room:x": [{ text: "plain text", at: 1 }] }));
    localStorage.setItem("density", "compact");
    for (const name of ["matrix-js-sdk::matrix-sdk-crypto", "matrix-js-sdk::matrix-sdk-crypto-meta", "uqqus-call::matrix-sdk-crypto", "uqqus-call::matrix-sdk-crypto-meta"]) {
      await new Promise<void>((resolve) => {
        const open = indexedDB.open(name);
        open.onsuccess = () => { open.result.close(); resolve(); };
      });
    }
  });
  const everything = () => page.evaluate(async () => ({
    keys: ["ruqqus_chat_session", "ruqqus_call_session", "uqqus_chat_waiting"].filter((k) => localStorage.getItem(k) !== null)
      .concat(sessionStorage.getItem("uqqus_chat_prekey") === null ? [] : ["uqqus_chat_prekey"]),
    stores: (await indexedDB.databases()).map((d) => d.name).filter((name) => /matrix-sdk-crypto/.test(name || "")),
  }));
  const send = (password: string) => page.evaluate((typed) => {
    const form = document.getElementById("deleteAccountForm") as HTMLFormElement;
    (form.querySelector("#delete-password") as HTMLInputElement).value = typed;
    form.submit();
  }, password);

  await Promise.all([page.waitForLoadState("load"), page.waitForResponse("**/settings/delete_account"), send("not the password")]);
  await page.waitForTimeout(500);
  const held = await everything();
  expect(held.keys.length).toBe(4);
  expect(held.stores.length).toBe(4);

  await Promise.all([page.waitForResponse("**/settings/delete_account"), page.waitForEvent("domcontentloaded"), send(PASSWORD)]);
  await expect.poll(async () => JSON.stringify(await everything()), { timeout: 10_000 }).toBe(JSON.stringify({ keys: [], stores: [] }));
  expect(await page.evaluate(() => localStorage.getItem("density"))).toBe("compact");
  await expect(page.locator("a[data-logout]")).toHaveCount(0);
  await expect(page.locator("h1")).toBeVisible();
  await page.screenshot({ path: "reports/account-deleted-1280.png" });
  await page.setViewportSize({ width: 390, height: 800 });
  await page.screenshot({ path: "reports/account-deleted-390.png" });
  await context.close();
});
