import { test, expect, type BrowserContext, type Page } from "@playwright/test";
import { execSync } from "node:child_process";
import { existsSync, readFileSync } from "node:fs";
import {
  PASSWORD, NEW_PASSWORD, INDEX, CONTAINER, type Token, type Entry,
  signIn, becomes, storeRefused, index, entryOf, entryFor, databases, storeOf, tokensOf, tokenOf,
  openChat, unlock, roomBetween, send, groupWith, answerBoxes, setUpChat,
} from "./chat_helpers";

// Two accounts in one browser (CLAUDE.md "Chat encryption"): what one leaves behind must never become the other's.
//   PYTHONPATH=. python scripts/ui/seed_chat_session.py   (in the container), then: npm run chat-session
// The chat, call and Space windows keep their devices in one index (`uqqus_chat_devices`, assets/chat_src/src/token.js), each with a crypto store
// locked with a key only a signed-in page of that account is given, and a token kept only sealed. Logging out takes the sealed tokens and keeps the
// stores (the same account signing back in takes its device up again); another account's page keeps them too and never opens them.
// Sign-ins are limited to 6 a minute, so each account signs in through the real form once and the tests put a cookie back; the one test
// that needs a real sign-in (the pre-key that unlocks a new device) makes two more, and the refused sign-in's test three (a 429 reads as a sign-in that never leaves /login).
// A Log out ends its sign-in for good on the server (helpers/sign_in_ends.py), so a saved cookie can't be put back after one: each test gets a cookie
// of its own, signed by the app in the container (`docker exec`, RUQQUS_CONTAINER, as money.spec.ts does), as a sign-in would leave it.

test.describe.configure({ mode: "serial" });
test.use({
  launchOptions: { args: ["--use-fake-ui-for-media-stream", "--use-fake-device-for-media-stream"] },
  permissions: ["microphone", "camera"],
});

const A = { name: "ui_chat_a", mxid: "@ruqqus_90703:ruqqus.local" };
const B = { name: "ui_chat_b", mxid: "@ruqqus_90704:ruqqus.local" };
const C = { name: "ui_chat_c", mxid: "@ruqqus_90706:ruqqus.local" };       // spec 9 changes its password, and the seed puts it back
const D = { name: "ui_chat_d", mxid: "@ruqqus_90708:ruqqus.local" };       // spec 8 resets its password by email
const E = { name: "ui_chat_e", mxid: "@ruqqus_90709:ruqqus.local" };       // starts chat over after a reset by email (its identity is replaced every run)
const F = { name: "ui_chat_f", mxid: "@ruqqus_90711:ruqqus.local" };       // rescues its chat from this browser's kept store after a reset by email



// The renewal specs need tokens that end within minutes, not the chat server's hour: the dev site reports them as lasting at most
// CHAT_TOKEN_REPORT_SECONDS (90 for these specs, set only on the dev site that runs them: scripts/ui/README.md), so the keeper's timer
// renews each about 30 to 60 s after it came (keeper.js schedule: the end less a third of the life, less up to a minute, never under 30 s)
const SHORT_LIFE = 90_000;
function expectShortTokens(seen: Token[]) {
  const life = seen.find((t) => t.access_token)?.expires_in_ms;
  expect(life, "the dev site must run with CHAT_TOKEN_REPORT_SECONDS=90 (scripts/ui/README.md)").toBeLessThanOrEqual(SHORT_LIFE);
}

// every renewal a context's pages and frames ask the site for (`mode=renew`), with its kind, when it went and when it was answered
type Renewal = { kind: string; device: string; sent: number; done: number; status: number; page: Page | null };
function renewalsOf(context: BrowserContext) {
  const list: Renewal[] = [];
  context.on("request", (request) => {
    if (!request.url().endsWith("/api/chat/token") || request.method() !== "POST") return;
    const form = new URLSearchParams(request.postData() || "");
    if (form.get("mode") !== "renew") return;
    let page: Page | null = null;
    try { page = request.frame().page(); } catch { page = null; }
    const one: Renewal = { kind: form.get("kind") || "", device: form.get("device_id") || "", sent: Date.now(), done: 0, status: 0, page };
    list.push(one);
    request.response().then((answer) => { one.done = Date.now(); one.status = answer ? answer.status() : 0; })
      .catch(() => { one.done = Date.now(); });
  });
  return list;
}
const renewed = (list: Renewal[], kind: string, page?: Page) => list.filter((r) => r.kind === kind && r.status === 200 && (!page || r.page === page));
// one renewal of a kind at a time: each one sent only after the one before it was answered
function expectNoOverlap(list: Renewal[], kind: string) {
  const mine = list.filter((r) => r.kind === kind).sort((a, b) => a.sent - b.sent);
  for (let i = 1; i < mine.length; i += 1) expect(mine[i].sent, `${kind} renewal ${i} went before ${i - 1} was answered`).toBeGreaterThanOrEqual(mine[i - 1].done);
}
// the call window's own clock ("00:42"): the call is still on
const callRunning = async (page: Page) => /^\d+:\d\d/.test((await page.locator("#call-status").innerText().catch(() => "")).trim());

// asked from the page, as the app asks: whom the chat server takes this token to be (null when it no longer takes it). The device is
// the index's entry, the token the one the site answered for it, read off the wire (tokenOf): the index holds it only sealed, so the
// plan's "tokenBelongsTo reads the index" is the entry, never the token itself
const tokenBelongsTo = (page: Page, token: Token) => page.evaluate(async (t) => {
  const answer = await fetch(`${t.homeserver_url}/_matrix/client/v3/account/whoami`, { headers: { Authorization: `Bearer ${t.access_token}` } });
  return answer.ok ? (await answer.json()).user_id : null;
}, token);

// spec 1: a kept store opened in the page as the chat page opens it (matrix-js-sdk's initRustCrypto), with the key the spec chooses: none,
// or 32 zero bytes. store_probe.js is built once with the chat bundle's own esbuild and packages and put in the page as a module; it
// answers "opened" or the error's text.
let storeProbe: string | null = null;
async function openStoreWith(page: Page, entry: Entry, homeserver: string, key: number[] | null) {
  if (storeProbe === null) {
    const chatSrc = new URL("../../ruqqus/assets/chat_src/", import.meta.url).pathname;
    storeProbe = execSync(`"${chatSrc}node_modules/.bin/esbuild" store_probe.js --bundle --format=esm --minify --log-level=error`, {
      cwd: new URL(".", import.meta.url).pathname, env: { ...process.env, NODE_PATH: `${chatSrc}node_modules` }, maxBuffer: 64 * 1024 * 1024,
    }).toString();
  }
  if (!(await page.evaluate(() => typeof (window as unknown as { __openStore?: unknown }).__openStore === "function"))) {
    await page.addScriptTag({ content: storeProbe, type: "module" });
    await page.waitForFunction(() => typeof (window as unknown as { __openStore?: unknown }).__openStore === "function");
  }
  return page.evaluate((args) => (window as unknown as { __openStore(a: unknown): Promise<string> }).__openStore(args),
    { prefix: entry.p, key, userId: entry.u, deviceId: entry.d, homeserver });
}


// nothing anywhere in this browser's storage holds a token the site gave, and nothing is signed in
async function expectNoTokenLeft(page: Page, seen: Token[]) {
  const values = await page.evaluate(() => {
    const all: string[] = [];
    for (const store of [localStorage, sessionStorage]) for (let i = 0; i < store.length; i += 1) all.push(`${store.key(i)}=${store.getItem(store.key(i)!)}`);
    return all;
  });
  const raw = seen.map((t) => t.access_token).filter(Boolean) as string[];
  expect(raw.length, "the spec saw the tokens it looks for").toBeGreaterThan(0);
  for (const token of raw) expect(values.filter((v) => v.includes(token))).toEqual([]);
  const idx = await index(page);
  expect(idx?.owner ?? null).toBeNull();
  expect((idx?.entries || []).filter((e) => e.sealed || e.s === "in")).toEqual([]);
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


// read as any script on the signed-out page could read it: the store is locked with the device's key, so its records are ciphertext,
// EXCEPT `tracked_users` (the chat accounts whose devices it follows: this account and the people it chats with), which the crypto
// library keeps in clear (found by the chat plan's raw-dump experiment, every object store of both databases read; the privacy page says so)
const keptAfterLogOut = (page: Page, prefix: string, needles: string[]) => page.evaluate(async ({ prefix, needles }) => {
  const ask = <T,>(request: IDBRequest<T>) => new Promise<T>((resolve, reject) => {
    request.onsuccess = () => resolve(request.result);
    request.onerror = () => reject(request.error);
  });
  const text = (value: unknown) => {
    try { return JSON.stringify(value, (_k, v) => (v instanceof ArrayBuffer || ArrayBuffer.isView(v) ? new TextDecoder("utf-8", { fatal: false }).decode(v as ArrayBuffer) : v)); } catch { return String(value); }
  };
  const hits: Record<string, string[]> = {};
  let coreKeys: string[] = [];
  let roomKeys = 0;
  const names = (await indexedDB.databases()).map((d) => d.name || "").filter((n) => n.startsWith(prefix + "::"));
  for (const name of names) {
    const db = await new Promise<IDBDatabase>((resolve, reject) => {
      const open = indexedDB.open(name);
      open.onsuccess = () => resolve(open.result);
      open.onerror = () => reject(open.error);
    });
    for (const storeName of Array.from(db.objectStoreNames)) {
      const store = () => db.transaction(storeName).objectStore(storeName);
      const keys = (await ask(store().getAllKeys())).map(String);
      const blob = text(await ask(store().getAll())) + text(keys);
      if (storeName === "core") coreKeys = keys;
      if (storeName === "inbound_group_sessions3") roomKeys = keys.length;
      const found = needles.filter((n) => blob.includes(n));
      if (found.length) hits[storeName] = found;
    }
    db.close();
  }
  return { names, coreKeys, roomKeys, hits };
}, { prefix, needles });


// --- the harvest (build step 6, assets/chat_src/src/harvest.js): the stores this browser kept of an account's older devices


// the account's current key backup on the chat server, as a device with this token reads it: its version and how many room keys it holds
const backupOf = (page: Page, token: Token) => page.evaluate(async (t) => {
  const answer = await fetch(`${t.homeserver_url}/_matrix/client/v3/room_keys/version`, { headers: { Authorization: `Bearer ${t.access_token}` } });
  if (!answer.ok) return { version: null as string | null, count: -1 };
  const body = await answer.json();
  return { version: body.version as string, count: Number(body.count) };
}, token);

// the room-key uploads of a context's pages, aborted: a key that arrives then exists only in that device's own store
const UPLOADS = "**/_matrix/client/v3/room_keys/keys**";
async function blockUploads(context: BrowserContext) {
  const blocked = { count: 0 };
  await context.route(UPLOADS, (route) => { blocked.count += 1; return route.abort(); });
  return blocked;
}

// a store gone from this browser: both databases and the index entry (harvest.js: deleted only after its keys were imported, or when found
// empty)
async function expectStoreGone(page: Page, entry: Entry, timeout = 60_000) {
  await expect.poll(async () => {
    const names = await databases(page).catch(() => [entry.p]);
    return names.filter((n) => n.startsWith(entry.p + "::")).length + ((await entryFor(page, entry.u, entry.d)) ? 1 : 0);
  }, { timeout, message: `the store of ${entry.d} goes` }).toBe(0);
}


// a reset link as the forgot-password email carries it (routes/login.py post_forgot), made by the app in the container: no mail is sent
function resetLink(name: string) {
  const script = `
import time
from ruqqus.__main__ import app, db_session
from ruqqus.classes import User
from ruqqus.helpers.security import generate_hash
db = db_session()
user = db.query(User).filter_by(username=${JSON.stringify(name)}).first()
now = int(time.time())
print("LINK /reset?id=%s&time=%s&token=%s" % (user.id, now, generate_hash(f"{user.id}+{now}+forgot+{user.login_nonce}")))
`;
  const out = execSync(`docker exec -i -w /opt/ruqqus/service ${CONTAINER} env PYTHONPATH=. venv/bin/python -`, { input: script }).toString();
  return out.split("\n").find((l) => l.startsWith("LINK "))!.slice(5);
}


test.beforeAll(async ({ browser }) => setUpChat(browser, [A, B, C]));

test("another account's chat device and crypto store are never used and are kept for it, and its waiting messages go", async ({ browser }) => {
  const context = await browser.newContext({ viewport: { width: 1280, height: 800 } });
  const seen = tokensOf(context);
  const page = await context.newPage();
  await becomes(context, A.name);
  await openChat(page, A.mxid);
  const left = (await entryOf(page, A.mxid))!;
  // A's waiting messages, sealed for A (their shape: B's chat can't open them, and doesn't try)
  await page.evaluate((u) => localStorage.setItem("uqqus_chat_waiting", JSON.stringify({ v: 2, u, iv: "aXY", ct: "Y3Q" })), A.mxid);
  // no Log out link: A's cookie just stops counting (it ran out, or the browser was handed over) and B signs in, with A's device still marked in
  await becomes(context, B.name);
  const errors = await openChat(page, B.mxid);
  const now = (await entryOf(page, B.mxid))!;
  expect(now.d).not.toBe(left.d);
  expect(await tokenBelongsTo(page, tokenOf(seen, now)!)).toBe(B.mxid);
  // A's device stays, locked and signed out here: no sealed token, and its store kept for when A signs back in
  const kept = (await entryFor(page, A.mxid, left.d))!;
  expect([kept.s, kept.sealed]).toEqual(["out", undefined]);
  expect(await databases(page)).toContain(storeOf(left));
  expect((await index(page))!.owner).toBe(B.mxid);
  expect(await page.evaluate(() => localStorage.getItem("uqqus_chat_waiting"))).toBeNull();      // A's: gone once B opened chat here
  expect(errors.filter(storeRefused)).toEqual([]);
  await context.close();
});

for (const [shell, path, width] of [["the account menu on a feed", "/", 1280], ["the account menu on settings", "/settings/profile", 1280], ["the account menu on the chat page itself", "/chat", 1280]] as const) {
  test(`Log out in ${shell} takes the sealed token, the pre-key and the waiting messages, and keeps the store`, async ({ browser }) => {
    const context = await browser.newContext({ viewport: { width, height: 800 } });
    const seen = tokensOf(context);
    const page = await context.newPage();
    await becomes(context, A.name);
    await openChat(page, A.mxid);
    const mine = (await entryOf(page, A.mxid))!;
    if (path !== "/chat") await page.goto(path, { waitUntil: "load" });
    await page.evaluate(() => {
      sessionStorage.setItem("uqqus_chat_prekey", "x");
      localStorage.setItem("uqqus_chat_waiting", "{}");
    });
    await pressLogOut(page, ".account-menu [data-logout]");
    await expectNoTokenLeft(page, seen);
    expect((await entryFor(page, A.mxid, mine.d))!.s).toBe("out");
    expect(await databases(page)).toContain(storeOf(mine));
    await context.close();
  });
}

test("Log out in the phone menu of a settings page does the same", async ({ browser }) => {
  const context = await browser.newContext({ viewport: { width: 390, height: 800 }, hasTouch: true });
  const seen = tokensOf(context);
  const page = await context.newPage();
  await becomes(context, A.name);
  await openChat(page, A.mxid);
  await page.goto("/settings/profile", { waitUntil: "load" });
  await pressLogOut(page, "#navbar a[data-logout].nav-link");
  await expectNoTokenLeft(page, seen);
  await context.close();
});

test("after a log out the next account's chat page starts its own device beside the kept one", async ({ browser }) => {
  const context = await browser.newContext({ viewport: { width: 1280, height: 800 } });
  const seen = tokensOf(context);
  const page = await context.newPage();
  await becomes(context, A.name);
  await openChat(page, A.mxid);
  const left = (await entryOf(page, A.mxid))!;
  await page.goto("/settings/profile", { waitUntil: "load" });
  await pressLogOut(page, ".account-menu [data-logout]");
  await becomes(context, B.name);
  const errors = await openChat(page, B.mxid);
  const now = (await entryOf(page, B.mxid))!;
  expect(now.d).not.toBe(left.d);
  expect(await tokenBelongsTo(page, tokenOf(seen, now)!)).toBe(B.mxid);
  expect(errors.filter(storeRefused)).toEqual([]);
  expect((await entryFor(page, A.mxid, left.d))!.s).toBe("out");
  expect(await databases(page)).toContain(storeOf(left));
  await expect(page.locator("#chat-conversation-list")).toBeVisible();
  await context.close();
});

test("the call window and a Space keep a device of their own per account, and never use the other account's", async ({ browser }) => {
  const context = await browser.newContext({ viewport: { width: 1280, height: 800 } });
  const seen = tokensOf(context);
  const page = await context.newPage();
  await becomes(context, A.name);
  await openChat(page, A.mxid);
  const aChat = (await entryOf(page, A.mxid))!;
  const room = await roomBetween(page, B.name);
  expect(room).toBeTruthy();
  await page.goto("/settings/profile", { waitUntil: "load" });

  // A's call window makes the call device
  const call = await context.newPage();
  await call.goto(`/call/${room}?kind=voice`, { waitUntil: "load" });
  await expect.poll(async () => (await entryOf(call, A.mxid, "call"))?.s, { timeout: 30_000 }).toBe("in");
  const aCall = (await entryOf(call, A.mxid, "call"))!;
  expect(aCall.d).not.toBe(aChat.d);
  await expect.poll(async () => (await databases(call)).includes(storeOf(aCall)), { timeout: 30_000 }).toBe(true);
  await call.close();

  // B's opens on what A left, with no chat page in between: a device of B's own, and A's kept, signed out here
  await becomes(context, B.name);
  const bCall = await context.newPage();
  await bCall.goto(`/call/${room}?kind=voice`, { waitUntil: "load" });
  await expect.poll(async () => (await entryOf(bCall, B.mxid, "call"))?.s, { timeout: 30_000 }).toBe("in");
  const bEntry = (await entryOf(bCall, B.mxid, "call"))!;
  expect(bEntry.d).not.toBe(aCall.d);
  expect(await tokenBelongsTo(bCall, tokenOf(seen, bEntry)!)).toBe(B.mxid);
  for (const kept of [aChat, aCall]) expect((await entryFor(bCall, A.mxid, kept.d))!.sealed).toBeUndefined();
  await bCall.close();

  // and a Space on what B's call window left, for A: A's own call device again
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
  await expect.poll(async () => (await entryOf(page, A.mxid, "call"))?.s, { timeout: 30_000 }).toBe("in");
  const spaceEntry = (await entryOf(page, A.mxid, "call"))!;
  expect(spaceEntry.d).toBe(aCall.d);
  expect(await tokenBelongsTo(page, tokenOf(seen, spaceEntry)!)).toBe(A.mxid);
  await context.close();
});

test("a chat left open in another tab stops with its member, and can't take the next account's session with it", async ({ browser }) => {
  const context = await browser.newContext({ viewport: { width: 1280, height: 800 } });
  const seen = tokensOf(context);
  const left = await context.newPage();
  await becomes(context, A.name);
  await openChat(left, A.mxid);

  const other = await context.newPage();
  await other.goto("/settings/profile", { waitUntil: "load" });
  await pressLogOut(other, ".account-menu [data-logout]");
  await expect.poll(() => left.url(), { timeout: 15_000 }).toContain("/login");   // it heard, at once, without a sync

  await becomes(context, B.name);
  await openChat(other, B.mxid);
  const bEntry = (await entryOf(other, B.mxid))!;
  await left.waitForTimeout(3_000);
  expect((await index(other))!.owner).toBe(B.mxid);
  expect((await entryOf(other, B.mxid))!.d).toBe(bEntry.d);
  expect(await tokenBelongsTo(other, tokenOf(seen, bEntry)!)).toBe(B.mxid);
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
  // kept with the account it was typed for and when (assets/js/chat_key.js remember): {v, name, k, at}
  expect(JSON.parse(key || "null")).toEqual({ v: 1, name: A.name.toLowerCase(), k: expect.stringMatching(/^[\w-]{40,}$/), at: expect.any(Number), ref: expect.stringMatching(/^[0-9a-f]{32}$/) });
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

// Spec 4 (first half) of the chat plan: plain Log out keeps the device, so what was written to the account meanwhile is readable at once.
test("the same account signing back in takes up its device, types nothing and reads everything, however soon it logged out", async ({ browser }) => {
  const context = await browser.newContext({ viewport: { width: 1280, height: 800 } });
  const seen = tokensOf(context);
  const page = await context.newPage();
  await signIn(page, A.name);                                                 // the real form: it leaves the pre-key for chat
  await openChat(page, A.mxid);
  const room = await roomBetween(page, B.name);
  const words = `Written just before logging out ${Date.now()}`;
  const unlocked = page.waitForResponse("**/api/chat/ready", { timeout: 30_000 });
  await page.goto(`/chat?room=${encodeURIComponent(room)}`, { waitUntil: "load" });
  await unlocked;
  await send(page, room, words);
  // out within seconds: the key backup uploads up to 10 seconds later, so a store deleted here would lose this message for good
  const before = (await entryOf(page, A.mxid))!;
  await pressLogOut(page, ".account-menu [data-logout]");
  await expectNoTokenLeft(page, seen);
  // what stays on a signed-out browser, readable with no sign-in: the store, LOCKED (its records are ciphertext, so neither the account nor
  // the device is written in it), and the index entry naming them
  const kept = await keptAfterLogOut(page, before.p, [A.mxid, before.d]);
  expect(kept.names.length).toBe(2);                      // the store and its meta database, every object store of both read
  expect(kept.roomKeys).toBeGreaterThan(0);
  // the record NAMES are the library's own and stay readable (`private_identity`, `account`: the chat plan's raw-dump experiment found them
  // in clear), which says what kind of thing is kept, never what it holds
  expect(kept.coreKeys).toContain("private_identity");
  // the device's id is in no record, and the account's only in `tracked_users`, which the library doesn't lock (the database's NAME
  // names both anyway: uqqus-chat-<localpart>-<device>)
  expect(Object.keys(kept.hits), "only tracked_users holds a name in clear").toEqual(["tracked_users"]);
  expect(kept.hits.tracked_users).toEqual([A.mxid]);
  expect((await entryFor(page, A.mxid, before.d))!.s).toBe("out");

  // meanwhile B writes three messages to A
  const elsewhere = await browser.newContext({ viewport: { width: 1280, height: 800 } });
  const bPage = await elsewhere.newPage();
  await becomes(elsewhere, B.name);
  await bPage.goto(`/chat?room=${encodeURIComponent(room)}`, { waitUntil: "load" });
  await unlock(bPage);
  const three = [1, 2, 3].map((n) => `From B while A was out, ${n}, ${Date.now()}`);
  for (const line of three) await send(bPage, room, line);
  await elsewhere.close();

  await signIn(page, A.name);
  const errors = await openChat(page, A.mxid);
  const after = (await entryOf(page, A.mxid))!;
  expect(after.d).toBe(before.d);
  expect(tokenOf(seen, after)!.access_token).not.toBe(tokenOf(seen.slice(0, 1), before)?.access_token ?? "none");
  await expect.poll(() => page.evaluate(() => sessionStorage.getItem("uqqus_chat_prekey")), { timeout: 30_000 }).toBeNull();
  expect(await page.locator(".chat-key-dialog").count()).toBe(0);             // nothing to type
  expect(errors.filter(storeRefused)).toEqual([]);
  await page.goto(`/chat?room=${encodeURIComponent(room)}`, { waitUntil: "load" });
  for (const line of [words, ...three]) await expect(page.locator("#chat-thread-pane")).toContainText(line, { timeout: 30_000 });
  await context.close();
});

// The chat plan's spec 4, second half: what nothing in phase 2 covers (its 8, "not covered"). A room key is shared only with devices that
// exist when it is made; an account with no chat device at all at that moment has nowhere for it to go, and the harvest can't bring back a
// key no store of this browser ever had. Recorded, not fixed (dehydrated devices, MSC3814, are the full fix).
test("a message sent while an account has no chat device at all can't be read afterwards, harvest or not (spec 4, the zero-device gap)", async ({ browser }) => {
  test.setTimeout(300_000);
  const context = await browser.newContext({ viewport: { width: 1280, height: 800 } });
  await becomes(context, C.name, true);
  const page = await context.newPage();
  await openChat(page, C.mxid);
  await unlock(page);
  const old = (await entryOf(page, C.mxid))!;
  await page.goto("/settings/profile", { waitUntil: "load" });            // no chat page runs here while the account has no device

  // every chat device of the account is signed out, from another browser (Settings > Chat apps)
  const elsewhere = await browser.newContext({ viewport: { width: 1280, height: 800 } });
  await becomes(elsewhere, C.name);
  const settings = await elsewhere.newPage();
  await settings.goto("/settings/chat_apps", { waitUntil: "load" });
  const list = () => settings.evaluate(async () => (await (await fetch("/api/chat/devices")).json()).devices.map((d: { device_id: string }) => d.device_id) as string[]);
  for (const device of await list()) {
    const status = await settings.evaluate(async (d) => {
      const form = new FormData();
      form.append("formkey", (window as unknown as { formkey(): string }).formkey());
      form.append("device_id", d);
      return (await fetch("/api/chat/devices/sign_out", { method: "POST", body: form })).status;
    }, device);
    expect([202, 204]).toContain(status);
  }
  await expect.poll(list, { timeout: 60_000 }).toEqual([]);
  await elsewhere.close();

  // B writes to C in a new room while C has no device anywhere
  const bCtx = await browser.newContext({ viewport: { width: 1280, height: 800 } });
  await becomes(bCtx, B.name);
  const bPage = await bCtx.newPage();
  await bPage.goto("/chat", { waitUntil: "load" });
  await unlock(bPage);
  const room = await groupWith(bPage, [C.name]);
  const words = `Sent while C had no device ${Date.now()}`;
  await send(bPage, room, words);
  await bCtx.close();

  // C opens chat again here: a new device, unlocked; the harvest takes what the old store held (it was unlocked, so it held keys) and the
  // store goes; B's message, whose key no device of C's ever had, stays unreadable
  await becomes(context, C.name, true);
  await openChat(page, C.mxid);
  await unlock(page);
  await expectStoreGone(page, old);
  await page.goto(`/chat?room=${encodeURIComponent(room)}`, { waitUntil: "load" });
  await expect(page.locator("#chat-thread-pane")).toContainText("This message can't be read on this device.", { timeout: 60_000 });
  await page.waitForTimeout(5_000);
  const readable = (await page.locator("#chat-thread-pane").innerText()).includes(words);
  console.log(`spec 4: a message sent while the account had no chat device was ${readable ? "READABLE" : "not readable"} afterwards (expected: not)`);
  expect(readable).toBe(false);
  await context.close();
});

test("a device signed out while nobody was signed in here is replaced, and its store, which held nothing yet, goes (sweepEmpty)", async ({ browser }) => {
  const context = await browser.newContext({ viewport: { width: 1280, height: 800 } });
  const seen = tokensOf(context);
  const page = await context.newPage();
  await becomes(context, A.name);
  await openChat(page, A.mxid);
  const left = (await entryOf(page, A.mxid))!;
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
  }, left.d);
  expect(status).toBe(204);
  await elsewhere.close();

  await becomes(context, A.name);
  const errors = await openChat(page, A.mxid);
  const now = (await entryOf(page, A.mxid))!;
  expect(now.d).not.toBe(left.d);
  expect(await tokenBelongsTo(page, tokenOf(seen, now)!)).toBe(A.mxid);
  expect(errors.filter(storeRefused)).toEqual([]);
  // the old device never unlocked here (a minted sign-in leaves no pre-key, and nothing was typed): its store holds no room key, no
  // secret and no backup key, so the page's sweep opens it, finds it empty and deletes it (harvest.js sweepEmpty, the plan's 2.4 case 3).
  // A store that holds anything is kept for the harvest instead (spec 1, spec 5, spec 8)
  await expectStoreGone(page, left);
  expect((await entryOf(page, A.mxid))!.d).toBe(now.d);
  await context.close();
});

// --- spec 5 of the chat plan: removed elsewhere while open here

test("a chat tab whose device is signed out from another browser says why once, starts again as a new device and takes the old store's keys (spec 5)", async ({ browser }) => {
  test.setTimeout(480_000);
  const context = await browser.newContext({ viewport: { width: 1280, height: 800 } });
  const seen = tokensOf(context);
  await becomes(context, A.name);
  const page = await context.newPage();
  await openChat(page, A.mxid);
  await unlock(page);
  const old = (await entryOf(page, A.mxid))!;
  expect(old.x).toBe(1);                                                    // unlocked: its store holds the account's keys
  // B writes in a NEW room (a group B starts with A) while this tab's key uploads are blocked: the message's key is only in this device's
  // store, so the device that replaces it can read the message only through the harvest
  const blocked = await blockUploads(context);
  const bCtx = await browser.newContext({ viewport: { width: 1280, height: 800 } });
  await becomes(bCtx, B.name);
  const bPage = await bCtx.newPage();
  await bPage.goto("/chat", { waitUntil: "load" });
  await unlock(bPage);
  const room = await groupWith(bPage, [A.name]);
  const words = `Only this tab has this key ${Date.now()}`;
  await send(bPage, room, words);
  await bCtx.close();
  await page.goto(`/chat?room=${encodeURIComponent(room)}`, { waitUntil: "load" });
  await expect(page.locator("#chat-thread-pane")).toContainText(words, { timeout: 60_000 });
  await expect.poll(() => blocked.count, { timeout: 30_000, message: "the tab tried to back the new key up" }).toBeGreaterThan(0);
  const backupBefore = await backupOf(page, tokenOf(seen, old)!);
  expect(backupBefore.version).toBeTruthy();
  let loads = 0;
  page.on("framenavigated", (frame) => { if (frame === page.mainFrame()) loads += 1; });

  const elsewhere = await browser.newContext({ viewport: { width: 1280, height: 800 } });
  await becomes(elsewhere, A.name);
  const settings = await elsewhere.newPage();
  await settings.goto("/settings/chat_apps", { waitUntil: "load" });
  const status = await settings.evaluate(async (device) => {
    const form = new FormData();
    form.append("formkey", (window as unknown as { formkey(): string }).formkey());
    form.append("device_id", device);
    return (await fetch("/api/chat/devices/sign_out", { method: "POST", body: form })).status;
  }, old.d);
  expect(status).toBe(204);
  await elsewhere.close();
  await context.unroute(UPLOADS);                                           // the old device is gone: only the new one can upload now

  // its next request to the chat server is refused, its renewal answers that the device is gone, and the page starts again as a new device
  await expect.poll(async () => {
    const e = await entryOf(page, A.mxid);
    return !!(e && e.d !== old.d && e.s === "in" && e.sealed);
  }, { timeout: 150_000 }).toBe(true);
  const line = "This browser was signed out of chat from another device, so chat is starting again here.";
  await expect.poll(() => page.evaluate((text) => document.body.innerText.split(text).length - 1, line).catch(() => 0), { timeout: 30_000 }).toBe(1);
  expect(loads, "it reloads at most once").toBeLessThanOrEqual(1);
  await unlock(page);
  const now = (await entryOf(page, A.mxid))!;
  expect(await tokenBelongsTo(page, tokenOf(seen, now)!)).toBe(A.mxid);
  // the old store held keys (it was unlocked), so the sweep keeps it and the harvest takes them: only then does it go
  await expectStoreGone(page, old);
  expect(loads, "it reloads at most once").toBeLessThanOrEqual(1);
  // the harvest really imported them: B's message, whose key only the old store held, reads on the new device, and the backup takes it
  await page.goto(`/chat?room=${encodeURIComponent(room)}`, { waitUntil: "load" });
  await expect(page.locator("#chat-thread-pane")).toContainText(words, { timeout: 60_000 });
  await expect.poll(async () => (await backupOf(page, tokenOf(seen, now)!)).count, { timeout: 60_000, message: "the backup takes the harvested key" })
    .toBeGreaterThan(backupBefore.count);
  expect((await backupOf(page, tokenOf(seen, now)!)).version).toBe(backupBefore.version);
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
  const seen = tokensOf(context);
  const page = await context.newPage();
  await chatThenSettings(context, page);
  const said: string[] = [];
  page.on("dialog", (dialog) => { said.push(dialog.message()); dialog.accept(); });
  await context.clearCookies();                                               // a password change or "log out everywhere" in another browser
  await pressLogOut(page, ".account-menu [data-logout]");
  await expectNoTokenLeft(page, seen);
  expect(said).toEqual([]);
  await context.close();
});

test("Log out that never reaches the site still clears this browser, says so and leaves the member able to carry on", async ({ browser }) => {
  const context = await browser.newContext({ viewport: { width: 1280, height: 800 } });
  const seen = tokensOf(context);
  const page = await context.newPage();
  await chatThenSettings(context, page);
  const left = (await entryOf(page, A.mxid))!;
  const said: string[] = [];
  page.on("dialog", (dialog) => { said.push(dialog.message()); dialog.accept(); });
  await page.route("**/logout", (route) => route.abort("connectionfailed"));
  const reloaded = page.waitForEvent("load");
  await page.locator(".account-menu [data-logout]").first().evaluate((a: HTMLElement) => a.click());
  await reloaded;
  expect(said.length).toBe(1);
  await expect(page.locator(".account-menu [data-logout]").first()).toBeAttached();   // still signed in, and the page shows it
  // its sealed token is gone all the same (the member asked to leave), and the device is taken up again
  const values = await page.evaluate(() => Object.keys(localStorage).map((k) => localStorage.getItem(k)).join("\n"));
  for (const t of seen) if (t.access_token) expect(values.includes(t.access_token)).toBe(false);
  await page.unroute("**/logout");
  await openChat(page, A.mxid);
  expect((await entryOf(page, A.mxid))?.d).toBe(left.d);
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
  const seen = tokensOf(context);
  const page = await context.newPage();
  await chatThenSettings(context, page);
  page.on("dialog", (dialog) => dialog.accept());
  await page.route("**/logout", async (route) => {
    await route.fetch({ maxRedirects: 0 });
    await route.abort("connectionfailed");
  });
  await pressLogOut(page, ".account-menu [data-logout]");
  await expectNoTokenLeft(page, seen);
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
  // the newest tab takes the chat (the `uqqus-matrix` lock is stolen), as B; the first, A's page, stops and says so, and A's device is kept
  // signed out here (no sealed token), so nothing of A's can run beside B's
  await expect.poll(async () => (await index(second))?.owner, { timeout: 30_000 }).toBe(B.mxid);
  await expect(first.getByText(NOTE)).toBeVisible({ timeout: 30_000 });
  await expect.poll(() => secondSeen.syncs, { timeout: 45_000 }).toBeGreaterThan(0);
  await first.waitForTimeout(8_000);
  const syncsSoFar = firstSeen.syncs;
  await first.waitForTimeout(32_000);                                          // a sync is a long poll of 30 s
  expect(firstSeen.syncs, "the tab that lost the chat lock has no client").toBe(syncsSoFar);
  await expect(second.getByText(NOTE)).toHaveCount(0);
  const a = (await index(second))!.entries.find((e) => e.u === A.mxid)!;
  expect([a.s, a.sealed]).toEqual(["out", undefined]);
  await context.close();
});

test("two tabs opening chat together on a new browser end with one current device, and none is left on Loading", async ({ browser }) => {
  for (let run = 0; run < 5; run += 1) {
    const context = await browser.newContext({ viewport: { width: 1280, height: 800 } });
    const tokens = tokensOf(context);
    // a page that lost chat to the other while the site answered signs the device it was given out with the site (token.js dropMinted:
    // `mode=replace`, `why=taken_over`), whose answer is {replaced: true} and names no device
    const dropped: { device_id: string | null; why: string | null }[] = [];
    context.on("request", (request) => {
      if (!request.url().endsWith("/api/chat/token") || request.method() !== "POST") return;
      const form = new URLSearchParams(request.postData() || "");
      if (form.get("mode") === "replace") dropped.push({ device_id: form.get("device_id"), why: form.get("why") });
    });
    await becomes(context, A.name);
    const [one, two] = [await context.newPage(), await context.newPage()];
    const errors: string[] = [];
    for (const page of [one, two]) page.on("console", (message) => { if (message.type() === "error") errors.push(message.text()); });
    const seen = [syncsOf(one), syncsOf(two)];
    await Promise.all([one.goto("/chat", { waitUntil: "load" }), two.goto("/chat", { waitUntil: "load" })]);
    // one tab takes the lock last and runs; the other says so
    await expect.poll(async () => (await one.getByText(NOTE).count()) + (await two.getByText(NOTE).count()), { timeout: 45_000 }).toBe(1);
    await expect.poll(() => seen[0].syncs + seen[1].syncs, { timeout: 45_000 }).toBeGreaterThan(0);
    // No lock is held across the site's answer (the chat plan's 3.2), so two pages starting at the same moment on a browser with no device may
    // each be given one. Either the page that lost chat to the other while it asked drops its device, signed out with the site and never
    // written to the index (token.js settle's TakenOver, dropMinted), or both are written and the index keeps ONE current device and marks
    // the other `removed`, its store kept (tests/test_chat_index.py test_two_tabs_at_once_...), and the page that held it comes back on the
    // current one.
    const chats = (await index(one))!.entries.filter((e) => e.u === A.mxid && e.k === "chat");
    const current = chats.filter((e) => e.s === "in");
    expect(current.length, `run ${run}: one current device`).toBe(1);
    expect(chats.every((e) => e.s === "in" || e.s === "removed"), `run ${run}`).toBe(true);
    // only answers that carry a device count: a replace's answer names none
    expect(new Set(tokens.filter((t) => t.device_id).map((t) => t.device_id)).size, `run ${run}`).toBeLessThanOrEqual(2);
    for (const gone of dropped) {
      expect(gone.why, `run ${run}: only a device a page dropped is replaced`).toBe("taken_over");
      expect(gone.device_id, `run ${run}: never the current device`).not.toBe(current[0].d);
    }
    expect(errors.filter(storeRefused), `run ${run}`).toEqual([]);
    await context.close();
  }
});

// The plan's 9.1: a store that isn't this device's own (here its recorded identity no longer matches, as a store opened with the wrong key
// or for another device would) repairs itself once, as one new device, and the old store is kept as `broken`.
test("a store that won't open as its own gets a new device once, and the old store is kept", async ({ browser }) => {
  const context = await browser.newContext({ viewport: { width: 1280, height: 800 } });
  const seen = tokensOf(context);
  const page = await context.newPage();
  await becomes(context, A.name);
  await openChat(page, A.mxid);
  const left = (await entryOf(page, A.mxid))!;
  expect(left.ed).toBeTruthy();
  await page.goto("/settings/profile", { waitUntil: "load" });
  await page.evaluate(([key, device]) => {
    const idx = JSON.parse(localStorage.getItem(key)!);
    for (const e of idx.entries) if (e.d === device) e.ed = "not this store's identity";
    localStorage.setItem(key, JSON.stringify(idx));
  }, [INDEX, left.d]);

  const replaced: string[] = [];
  page.on("request", (request) => {
    if (request.url().endsWith("/api/chat/token") && (request.postData() || "").includes("mode=replace")) replaced.push(request.postData()!);
  });
  await page.goto("/chat", { waitUntil: "load" });
  await expect.poll(async () => {
    const e = await entryOf(page, A.mxid);
    return !!(e && e.d !== left.d && e.s === "in" && e.sealed);
  }, { timeout: 60_000 }).toBe(true);
  const now = (await entryOf(page, A.mxid))!;
  expect(replaced.length).toBe(1);
  expect((await entryFor(page, A.mxid, left.d))!.s).toBe("broken");
  expect(await databases(page)).toContain(storeOf(left));
  expect(await tokenBelongsTo(page, tokenOf(seen, now)!)).toBe(A.mxid);
  await context.close();
});

// --- spec 6 of the chat plan: one browser, two accounts in turn

const holdOf = () => {
  const source = readFileSync(new URL("../../ruqqus/templates/partials/ad_hold.html", import.meta.url), "utf8");
  const body = /\{% macro chat_held\(owner\) %\}([\s\S]*?)\{% endmacro %\}/.exec(source)![1];
  return (owner: string | null) => body.replace("{{ owner | tojson }}", JSON.stringify(owner));
};

test("a shared browser: A's devices stay through B's turn, hold the ads for B, give B no keys, and A comes back to them", async ({ browser }) => {
  const context = await browser.newContext({ viewport: { width: 1280, height: 800 } });
  const page = await context.newPage();
  await becomes(context, A.name);
  await openChat(page, A.mxid);
  await unlock(page);
  const aDevice = (await entryOf(page, A.mxid))!;
  const room = await roomBetween(page, B.name);
  await page.goto("/settings/profile", { waitUntil: "load" });
  await pressLogOut(page, ".account-menu [data-logout]");

  await becomes(context, B.name);
  await openChat(page, B.mxid);
  await unlock(page);
  const words = `To A on a shared browser ${Date.now()}`;
  await send(page, room, words);
  expect(await databases(page)).toContain(storeOf(aDevice));
  // the hold as the page would run it (partials/ad_hold.html): another account's devices are here, so Google's tag stays away from B's pages
  const hold = holdOf();
  expect(await page.evaluate((expr) => eval(expr), hold(B.mxid))).toBe(true);
  expect(await page.evaluate((expr) => eval(expr), hold(null))).toBe(true);
  // and the site gives B no key of A's devices
  const keys = await page.evaluate(async (device) => {
    const form = new FormData();
    form.append("formkey", (window as unknown as { formkey(): string }).formkey());
    form.append("device_ids", device);
    return (await fetch("/api/chat/store_keys", { method: "POST", body: form })).json();
  }, aDevice.d);
  expect(keys).toEqual({ keys: {} });
  await page.goto("/settings/profile", { waitUntil: "load" });
  await pressLogOut(page, ".account-menu [data-logout]");

  await becomes(context, A.name);
  const errors = await openChat(page, A.mxid);
  expect((await entryOf(page, A.mxid))!.d).toBe(aDevice.d);
  expect(errors.filter(storeRefused)).toEqual([]);
  await unlock(page);
  await page.goto(`/chat?room=${encodeURIComponent(room)}`, { waitUntil: "load" });
  await expect(page.locator("#chat-thread-pane")).toContainText(words, { timeout: 30_000 });
  await context.close();
});

// --- renewal: the chat plan's specs 1 (its renewal parts, then the harvest), 2 and 3. Tokens end and are renewed through the
// site (keeper.js), which renews them through the chat server's /refresh with the refresh token it keeps sealed (helpers/chat_tokens.py)

test("Log out everywhere from another browser ends a lost laptop's chat at its next request: the sign-in page, the token dead, the store kept, then harvested (spec 1)", async ({ browser }) => {
  test.setTimeout(600_000);
  const laptop = await browser.newContext({ viewport: { width: 1280, height: 800 } });
  const seen = tokensOf(laptop);
  await becomes(laptop, C.name, true);
  const lost = await laptop.newPage();
  await openChat(lost, C.mxid);
  await unlock(lost);
  expectShortTokens(seen);
  const device = (await entryOf(lost, C.mxid))!;

  // the harvest's part (build step 6): B writes in a NEW room (a group B starts with C), whose room key reaches the laptop and nowhere else,
  // because the laptop's key uploads are blocked: when the laptop signs in again, only the harvest can bring that message back
  const blocked = await blockUploads(laptop);
  const bCtx = await browser.newContext({ viewport: { width: 1280, height: 800 } });
  await becomes(bCtx, B.name);
  const bPage = await bCtx.newPage();
  await bPage.goto("/chat", { waitUntil: "load" });
  await unlock(bPage);
  const room = await groupWith(bPage, [C.name]);
  const words = `Only the laptop has this key ${Date.now()}`;
  await send(bPage, room, words);
  await bCtx.close();
  await lost.goto(`/chat?room=${encodeURIComponent(room)}`, { waitUntil: "load" });
  await expect(lost.locator("#chat-thread-pane")).toContainText(words, { timeout: 60_000 });
  await expect.poll(() => blocked.count, { timeout: 30_000, message: "the laptop tried to back the new key up" }).toBeGreaterThan(0);
  const backupBefore = await backupOf(lost, tokenOf(seen, device)!);
  expect(backupBefore.version).toBeTruthy();

  // the member's phone, signed in, never opening chat: it holds no chat device of its own to be spared
  const phone = await browser.newContext({ viewport: { width: 1280, height: 800 } });
  await becomes(phone, C.name);
  const settings = await phone.newPage();
  await settings.goto("/settings/security", { waitUntil: "load" });
  const started = Date.now();
  const status = await settings.evaluate(async (password) => {
    const form = new FormData();
    form.append("formkey", (window as unknown as { formkey(): string }).formkey());
    form.append("password", password);
    return (await fetch("/settings/log_out_all_others", { method: "POST", body: form, redirect: "manual" })).status;
  }, PASSWORD);
  expect([200, 204, 302]).toContain(status);

  // the laptop's next request to the chat server is refused, its renewal through the site is refused (the sign-in is over), and the page
  // is drawn again as the sign-in page: no "Loading" left on screen
  await expect.poll(() => lost.url(), { timeout: 40_000 }).toContain("/login");
  console.log(`spec 1: the laptop was on the sign-in page ${Math.round((Date.now() - started) / 1000)} s after Log out everywhere`);
  // the token it held last (no renewal can be answered after the nonce went up) no longer works on the chat server
  const last = tokenOf(seen, device)!;
  expect(await tokenBelongsTo(settings, last)).toBeNull();
  // and a sync with it, as the client would make, is refused too
  expect(await settings.evaluate(async (t) => (await fetch(`${t.homeserver_url}/_matrix/client/v3/sync?timeout=0`,
    { headers: { Authorization: `Bearer ${t.access_token}` } })).status, last)).toBe(401);
  await expect.poll(async () => settings.evaluate(async () => (await (await fetch("/api/chat/devices")).json()).devices.map((d: { device_id: string }) => d.device_id)),
    { timeout: 60_000 }).not.toContain(device.d);
  // nothing in the laptop's storage holds any token it was given; the entry kept no sealed token; the locked store stays
  const values = await lost.evaluate(() => {
    const all: string[] = [];
    for (const store of [localStorage, sessionStorage]) for (let i = 0; i < store.length; i += 1) all.push(`${store.key(i)}=${store.getItem(store.key(i)!)}`);
    return all;
  });
  for (const token of seen.map((t) => t.access_token).filter(Boolean) as string[]) expect(values.filter((v) => v.includes(token))).toEqual([]);
  expect((await entryFor(lost, C.mxid, device.d))?.sealed).toBeUndefined();
  expect(await databases(lost)).toContain(storeOf(device));
  // the store it kept opens for nobody without its key: no key, or a key of 32 zero bytes, is refused by the crypto library itself
  for (const key of [null, new Array(32).fill(0)]) {
    const opened = await openStoreWith(lost, device, last.homeserver_url, key);
    expect(opened, `opened with ${key ? "32 zero bytes" : "no key"}`).toMatch(/unpickling/i);
  }
  // the control: its real key, which only a signed-in page of the account is given (here the member's phone), opens it, so the two
  // refusals above are the store's own and not the probe failing
  const real = await settings.evaluate(async (d) => {
    const form = new FormData();
    form.append("formkey", (await (await fetch("/api/chat/formkey")).json()).formkey);      // its nonce went up with Log out everywhere
    form.append("device_ids", d);
    return (await (await fetch("/api/chat/store_keys", { method: "POST", body: form })).json()).keys[d]?.key as string | undefined;
  }, device.d);
  expect(real).toBeTruthy();
  expect(await openStoreWith(lost, device, last.homeserver_url, [...Buffer.from(real!, "base64url")])).toBe("opened");
  expect(await databases(lost)).toContain(storeOf(device));
  // the phone opens chat only now, after the laptop's device is gone: a device of its own, which can't read B's message (its key was only
  // in the laptop's store, never on the phone or in the backup), so what the laptop reads below came through the harvest alone
  await openChat(settings, C.mxid);
  await unlock(settings);
  await settings.goto(`/chat?room=${encodeURIComponent(room)}`, { waitUntil: "load" });
  await expect(settings.locator("#chat-thread-pane")).toContainText("This message can't be read on this device.", { timeout: 60_000 });
  await expect(settings.locator("#chat-thread-pane")).not.toContainText(words);
  await phone.close();

  // the laptop is found again and signed in through the real form: a new device, which the sign-in's pre-key unlocks with nothing typed,
  // and which then takes the old store's room keys (harvest.js): B's message, whose key only that store held, can be read, the new
  // device's backup uploads it, and only then does the old store go, within 30 s of the unlock
  await laptop.unroute(UPLOADS);
  await signIn(lost, C.name);
  await openChat(lost, C.mxid);
  const found = (await entryOf(lost, C.mxid))!;
  expect(found.d).not.toBe(device.d);
  // (the old entry is `removed` only for the moment between the new device's start and the harvest: with nothing to type, the harvest
  // can be done before this line runs)
  await expect.poll(async () => (await entryOf(lost, C.mxid))?.x, { timeout: 60_000, message: "the pre-key unlocks the new device" }).toBe(1);
  expect(await lost.locator(".chat-key-dialog").count(), "nothing to type").toBe(0);
  await expectStoreGone(lost, device, 30_000);
  await lost.goto(`/chat?room=${encodeURIComponent(room)}`, { waitUntil: "load" });
  await expect(lost.locator("#chat-thread-pane")).toContainText(words, { timeout: 60_000 });
  await expect.poll(async () => (await backupOf(lost, tokenOf(seen, found)!)).count, { timeout: 60_000, message: "the backup takes the harvested key" })
    .toBeGreaterThan(backupBefore.count);
  expect((await backupOf(lost, tokenOf(seen, found)!)).version).toBe(backupBefore.version);
  // and nothing of another account is left here: no ad hold for C
  const hold = holdOf();
  expect(await lost.evaluate((expr) => eval(expr), hold(C.mxid))).toBe(false);
  await laptop.close();
});

// The chat plan's spec 8 (its 5.5): a reset by email signs chat out everywhere, and the account's password copy stays locked with the old
// password. The new device asks for the recovery code; with it, it unlocks under the same identity, restores the key backup, and the
// harvest brings in what only this browser's old store held (the backup never had it: its upload was blocked); only after that import does
// the old store go. Without it, "Start over?" says what would come back and asks the password, and closing the question keeps the old store.
// Pressing Start over is spec 10's (ui_chat_e), whose identity is replaced every run.
// ui_chat_d's recovery code is kept in .auth/ui_chat_d.code when its chat is first set up, and its password is put back by the seed.
test("a reset by email: the recovery code unlocks a new device, the harvest brings back what only this browser held, and the old store goes only after (spec 8)", async ({ browser }) => {
  test.setTimeout(480_000);
  const CODE = ".auth/ui_chat_d.code";
  const saved = () => (existsSync(CODE) ? readFileSync(CODE, "utf8").trim() : null);
  const laptop = await browser.newContext({ viewport: { width: 1280, height: 800 } });
  const seen = tokensOf(laptop);
  await becomes(laptop, D.name, true);
  const page = await laptop.newPage();
  await openChat(page, D.mxid);
  // set up here the first time (the code is kept), unlocked with the password, or with the code when an earlier run left another password
  await answerBoxes(page, { password: PASSWORD, code: saved(), keep: CODE });
  const old = (await entryOf(page, D.mxid))!;
  expect(old.x).toBe(1);

  // B writes in a new room; the laptop reads it, and its key upload is blocked: the key is only in the laptop's store
  const blocked = await blockUploads(laptop);
  const bCtx = await browser.newContext({ viewport: { width: 1280, height: 800 } });
  await becomes(bCtx, B.name);
  const bPage = await bCtx.newPage();
  await bPage.goto("/chat", { waitUntil: "load" });
  await unlock(bPage);
  const room = await groupWith(bPage, [D.name]);
  const words = `Before the reset ${Date.now()}`;
  await send(bPage, room, words);
  await bCtx.close();
  await page.goto(`/chat?room=${encodeURIComponent(room)}`, { waitUntil: "load" });
  await expect(page.locator("#chat-thread-pane")).toContainText(words, { timeout: 60_000 });
  await expect.poll(() => blocked.count, { timeout: 30_000 }).toBeGreaterThan(0);
  const backupBefore = await backupOf(page, tokenOf(seen, old)!);
  expect(backupBefore.version).toBeTruthy();
  await page.goto("/settings/profile", { waitUntil: "load" });
  await laptop.unroute(UPLOADS);

  // the reset, through the emailed link (made by the app: no mail is sent), in another browser
  const mail = await browser.newContext({ viewport: { width: 1280, height: 800 } });
  const resetPage = await mail.newPage();
  await resetPage.goto(resetLink(D.name), { waitUntil: "load" });
  await expect(resetPage.locator("#reset-chat-note")).toContainText("you'll need your chat recovery code");
  await resetPage.fill("input[name=password]", NEW_PASSWORD);
  await resetPage.fill("input[name=confirm_password]", NEW_PASSWORD);
  await Promise.all([resetPage.waitForLoadState("load"), resetPage.click("form[action='/reset'] input[type=submit]")]);
  await expect(resetPage.locator("body")).toContainText("Password reset successful");
  await mail.close();

  // signed in again (the new password): a new device, whose password doesn't open the copy (locked with the old one). Without the code,
  // the question says what would come back and asks the password; closed, nothing is harvested and the old store stays, `removed`, with `x`
  await becomes(laptop, D.name, true);
  await openChat(page, D.mxid);
  const fresh = (await entryOf(page, D.mxid))!;
  expect(fresh.d).not.toBe(old.d);
  const closed = await answerBoxes(page, { password: NEW_PASSWORD, cancel: true });
  // this browser keeps the old device's store: the rescue is offered first (Not now here: this spec is the recovery code's)
  expect(closed.titles).toEqual(["Unlock your messages", "We found your chat on this browser", "Enter your recovery code", "Start over?"]);
  expect(closed.question.join(" ")).toContain("Your chats will work again on this device");
  expect(closed.question.join(" ")).toContain("Messages this browser could already read will come back here.");
  expect(closed.question.join(" ")).toContain("Enter your password to confirm.");
  await page.waitForTimeout(10_000);
  const kept = (await entryFor(page, D.mxid, old.d))!;
  expect([kept.s, kept.x]).toEqual(["removed", 1]);
  expect(await databases(page)).toContain(storeOf(old));

  // with the code: the same identity, the same backup (restored), and the harvest exports every session of the old store (its count says
  // the backup lacks one) into the new device, whose backup uploads it; only after that import does the old store go
  await page.goto("/chat", { waitUntil: "load" });
  const opened = await answerBoxes(page, { password: NEW_PASSWORD, code: saved() });
  expect(opened.titles).toEqual(["Unlock your messages", "We found your chat on this browser", "Enter your recovery code"]);
  await expectStoreGone(page, old);
  await page.goto(`/chat?room=${encodeURIComponent(room)}`, { waitUntil: "load" });
  await expect(page.locator("#chat-thread-pane")).toContainText(words, { timeout: 60_000 });
  const now = (await entryOf(page, D.mxid))!;
  await expect.poll(async () => (await backupOf(page, tokenOf(seen, now)!)).count, { timeout: 60_000, message: "the backup takes the harvested key" })
    .toBeGreaterThan(backupBefore.count);
  expect((await backupOf(page, tokenOf(seen, now)!)).version).toBe(backupBefore.version);
  await laptop.close();
});

// Spec 10 (the chat plan's phase 3, its "start over finishes"): a reset by email, no recovery code, Start over pressed with the new password.
// The site checks it and makes the key upload for the member (routes/chat_key_reset_pages.py: Synapse asks the appservice no sign-in stage);
// the account gets a new identity and a new recovery code, is told so, the harvest brings back what this browser's old store could read (its
// backup is gone, so every session of it is exported), and B's next message reaches the new identity. A wrong password changes nothing.
test("a reset by email and no recovery code: Start over with the password finishes, says so, and this browser's old messages come back (spec 10)", async ({ browser }) => {
  test.setTimeout(480_000);
  const laptop = await browser.newContext({ viewport: { width: 1280, height: 800 } });
  await becomes(laptop, E.name, true);
  const page = await laptop.newPage();
  await openChat(page, E.mxid);
  // set up here (a first run), or started over (a run before left its copy locked with another password): either way, unlocked
  await answerBoxes(page, { password: PASSWORD, startOver: PASSWORD });
  const old = (await entryOf(page, E.mxid))!;
  expect(old.x).toBe(1);

  const bCtx = await browser.newContext({ viewport: { width: 1280, height: 800 } });
  await becomes(bCtx, B.name);
  const bPage = await bCtx.newPage();
  await bPage.goto("/chat", { waitUntil: "load" });
  await unlock(bPage);
  const room = await groupWith(bPage, [E.name]);
  const words = `Before starting over ${Date.now()}`;
  await send(bPage, room, words);
  await page.goto(`/chat?room=${encodeURIComponent(room)}`, { waitUntil: "load" });
  await expect(page.locator("#chat-thread-pane")).toContainText(words, { timeout: 60_000 });
  await page.goto("/settings/profile", { waitUntil: "load" });

  const mail = await browser.newContext({ viewport: { width: 1280, height: 800 } });
  const resetPage = await mail.newPage();
  await resetPage.goto(resetLink(E.name), { waitUntil: "load" });
  await resetPage.fill("input[name=password]", NEW_PASSWORD);
  await resetPage.fill("input[name=confirm_password]", NEW_PASSWORD);
  await Promise.all([resetPage.waitForLoadState("load"), resetPage.click("form[action='/reset'] input[type=submit]")]);
  await expect(resetPage.locator("body")).toContainText("Password reset successful");
  await mail.close();

  await becomes(laptop, E.name, true);
  await openChat(page, E.mxid);
  // a wrong password on the question changes nothing: the box says so and stays
  const box = page.locator(".chat-key-dialog");
  await expect.poll(async () => {
    const now = (await box.locator("h2").first().innerText().catch(() => "")).trim();
    if (now === "Unlock your messages") {
      await box.locator("input[type=password]").fill(NEW_PASSWORD).catch(() => {});
      await box.getByRole("button", { name: "Unlock" }).click().catch(() => {});
    } else if (now === "We found your chat on this browser") {
      await box.getByRole("button", { name: "Not now" }).click().catch(() => {});      // this spec is Start over's (spec 11 is the rescue)
    } else if (now === "Enter your recovery code") {
      await box.getByRole("button", { name: "I don't have it: start over" }).click().catch(() => {});
    }
    return now;
  }, { timeout: 60_000 }).toBe("Start over?");
  await box.locator("input[type=password]").fill("not-the-password");
  await box.getByRole("button", { name: "Start over" }).click();
  await expect(box).toContainText("That isn't your password.");
  const done = await answerBoxes(page, { password: NEW_PASSWORD, startOver: NEW_PASSWORD });
  expect(done.ended).toBe("unlocked");
  expect(done.titles).toContain("Save your recovery code");
  // told, by a notification, and a staff-only Log entry (read from the database: ui_preview has no site account to draw a system notice with)
  const told = execSync(`docker exec -i -w /opt/ruqqus/service ${CONTAINER} env PYTHONPATH=. venv/bin/python -`, { input: `
from sqlalchemy import text
from ruqqus.__main__ import db_session
db = db_session()
n = db.execute(text("SELECT count(*) FROM notifications n JOIN comments_aux a ON a.id = n.comment_id WHERE n.user_id = 90709 AND a.body LIKE 'Your chat encryption was started over%'")).scalar()
l = db.execute(text("SELECT count(*) FROM log_entries WHERE action = 'chat_keys_reset' AND target_user_id = 90709 AND audience = 2")).scalar()
print("TOLD", n, l)
` }).toString().split("\n").find((line) => line.startsWith("TOLD "))!.split(" ").slice(1).map(Number);
  expect(told[0]).toBeGreaterThan(0);
  expect(told[1]).toBeGreaterThan(0);
  // what this browser's old store could read comes back (the harvest), and the old store goes after
  await page.goto(`/chat?room=${encodeURIComponent(room)}`, { waitUntil: "load" });
  await expect(page.locator("#chat-thread-pane")).toContainText(words, { timeout: 90_000 });
  await expectStoreGone(page, old);
  // and B's next message reaches the new identity
  const after = `After starting over ${Date.now()}`;
  await bPage.goto("/chat", { waitUntil: "load" });
  await unlock(bPage);
  await send(bPage, room, after);
  await page.goto(`/chat?room=${encodeURIComponent(room)}`, { waitUntil: "load" });
  await expect(page.locator("#chat-thread-pane")).toContainText(after, { timeout: 60_000 });
  await bCtx.close();
  await laptop.close();
});

// Spec 11 (the chat plan's phase 3, its "rescue after a reset"): a reset by email, no recovery code, and this browser keeps the store of the
// device the member used before. Signed in again with the new password, chat offers to bring those keys over (rescue.js: they are the keys the
// chat server lists now); with them the old messages read here, a new recovery code is shown, and another browser signing in through the form
// with the new password reads the history with nothing typed.
test("a reset by email, no recovery code: this browser's kept store rescues the chat, with a new code, for every browser (spec 11)", async ({ browser }) => {
  test.setTimeout(480_000);
  const laptop = await browser.newContext({ viewport: { width: 1280, height: 800 } });
  await becomes(laptop, F.name, true);
  const page = await laptop.newPage();
  await openChat(page, F.mxid);
  // set up here (a first run), or started over (a run before left its copy locked with another password)
  await answerBoxes(page, { password: PASSWORD, startOver: PASSWORD });

  const bCtx = await browser.newContext({ viewport: { width: 1280, height: 800 } });
  await becomes(bCtx, B.name);
  const bPage = await bCtx.newPage();
  await bPage.goto("/chat", { waitUntil: "load" });
  await unlock(bPage);
  const room = await groupWith(bPage, [F.name]);
  const words = `Before the rescue ${Date.now()}`;
  await send(bPage, room, words);
  await bCtx.close();
  await page.goto(`/chat?room=${encodeURIComponent(room)}`, { waitUntil: "load" });
  await expect(page.locator("#chat-thread-pane")).toContainText(words, { timeout: 60_000 });
  await page.goto("/settings/profile", { waitUntil: "load" });

  const mail = await browser.newContext({ viewport: { width: 1280, height: 800 } });
  const resetPage = await mail.newPage();
  await resetPage.goto(resetLink(F.name), { waitUntil: "load" });
  await resetPage.fill("input[name=password]", NEW_PASSWORD);
  await resetPage.fill("input[name=confirm_password]", NEW_PASSWORD);
  await Promise.all([resetPage.waitForLoadState("load"), resetPage.click("form[action='/reset'] input[type=submit]")]);
  await expect(resetPage.locator("body")).toContainText("Password reset successful");
  await mail.close();

  await becomes(laptop, F.name, true);
  await openChat(page, F.mxid);
  const rescued = await answerBoxes(page, { password: NEW_PASSWORD, rescue: true });
  expect(rescued.ended).toBe("unlocked");
  expect(rescued.titles).toEqual(["Unlock your messages", "We found your chat on this browser", "Save your recovery code"]);
  await page.goto(`/chat?room=${encodeURIComponent(room)}`, { waitUntil: "load" });
  await expect(page.locator("#chat-thread-pane")).toContainText(words, { timeout: 60_000 });

  // another browser, signed in through the form with the new password: nothing to type, and the history is there
  const desk = await browser.newContext({ viewport: { width: 1280, height: 800 } });
  const deskPage = await desk.newPage();
  await signIn(deskPage, F.name, NEW_PASSWORD);
  await deskPage.goto(`/chat?room=${encodeURIComponent(room)}`, { waitUntil: "load" });
  await expect.poll(async () => (await index(deskPage))?.entries.some((x) => x.s === "in" && x.k === "chat" && x.x), { timeout: 60_000 }).toBe(true);
  expect(await deskPage.locator(".chat-key-dialog").count()).toBe(0);
  await expect(deskPage.locator("#chat-thread-pane")).toContainText(words, { timeout: 60_000 });
  await desk.close();
  await laptop.close();
});

// Spec 12: the recovery code's reminder and Settings > Chat. An account that hasn't said it kept its code sees one line above the chat list;
// "I have it" records only that (routes/chat.py chat_code_saved) and the line doesn't come back; Settings > Chat says when, at a phone's
// width and a desktop's, in both themes, with no sideways scroll.
test("the recovery code reminder goes once the member says they have it, and Settings > Chat says so (spec 12)", async ({ browser }) => {
  test.setTimeout(240_000);
  execSync(`docker exec -i -w /opt/ruqqus/service ${CONTAINER} env PYTHONPATH=. venv/bin/python -`, { input: `
from sqlalchemy import text
from ruqqus.__main__ import db_session
db = db_session()
db.execute(text("UPDATE chat_identities SET code_saved_utc = 0 WHERE user_id = 90703"))
db.commit()
` });
  const ctx = await browser.newContext({ viewport: { width: 1280, height: 800 } });
  await becomes(ctx, A.name);
  const page = await ctx.newPage();
  await page.goto("/chat", { waitUntil: "load" });
  await unlock(page);
  const line = page.locator("#chat-code-reminder");
  await expect(line).toContainText("Have you kept your chat recovery code?", { timeout: 30_000 });
  const told = page.waitForResponse("**/api/chat/code_saved");
  await line.getByRole("button", { name: "I have it" }).click();
  expect((await told).status()).toBe(204);
  await expect(line).toHaveCount(0);
  await page.reload({ waitUntil: "load" });
  await unlock(page);
  await page.waitForTimeout(3_000);
  await expect(page.locator("#chat-code-reminder")).toHaveCount(0);
  for (const [width, scheme] of [[390, "light"], [1280, "dark"]] as const) {
    await page.setViewportSize({ width, height: 800 });
    await page.emulateMedia({ colorScheme: scheme });
    await page.goto("/settings/chat", { waitUntil: "load" });
    await expect(page.locator("#recovery-code")).toContainText("You said you kept your recovery code on");
    await expect(page.locator('#recovery-code a[href="/chat?recovery=new"]')).toBeVisible();
    expect(await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth)).toBe(true);
  }
  await ctx.close();
});

test("a chat tab and a call window renew their tokens without a reload, one renewal at a time, and messages keep coming (spec 2)", async ({ browser }) => {
  test.setTimeout(600_000);
  const mineCtx = await browser.newContext({ viewport: { width: 1280, height: 800 } });
  const seen = tokensOf(mineCtx);
  const renewals = renewalsOf(mineCtx);
  await becomes(mineCtx, A.name);
  const chat = await mineCtx.newPage();
  await openChat(chat, A.mxid);
  await unlock(chat);
  expectShortTokens(seen);
  const room = await roomBetween(chat, B.name);
  await chat.goto(`/chat?room=${encodeURIComponent(room)}`, { waitUntil: "load" });

  const theirs = await browser.newContext({ viewport: { width: 1280, height: 800 } });
  await becomes(theirs, B.name);
  const bChat = await theirs.newPage();
  await bChat.goto(`/chat?room=${encodeURIComponent(room)}`, { waitUntil: "load" });
  await unlock(bChat);
  await bChat.goto(`/chat?room=${encodeURIComponent(room)}`, { waitUntil: "load" });

  // a 1:1 call nobody answers ends by itself after 45 s (call.js), so B answers it
  const call = await mineCtx.newPage();
  await call.goto(`/call/${encodeURIComponent(room)}?kind=voice`, { waitUntil: "load" });
  const bCall = await theirs.newPage();
  await bCall.goto(`/call/${encodeURIComponent(room)}?kind=voice&answer=1`, { waitUntil: "load" });
  await expect.poll(() => callRunning(call), { timeout: 60_000 }).toBe(true);
  for (const page of [chat, call]) await page.evaluate(() => { (window as unknown as { __mark: number }).__mark = 42; });
  const callDevice = (await entryOf(call, A.mxid, "call"))!.d;
  const chatDevice = (await entryOf(chat, A.mxid))!.d;

  // four minutes: after every renewal of the chat tab, a message from B still arrives
  const from = Date.now();
  let lines = 0;
  while (Date.now() - from < 240_000) {
    const before = renewed(renewals, "chat").length;
    await expect.poll(() => renewed(renewals, "chat").length, { timeout: 80_000 }).toBeGreaterThan(before);
    lines += 1;
    const words = `After renewal ${lines}, ${Date.now()}`;
    await send(bChat, room, words);
    await expect(chat.locator("#chat-thread-pane")).toContainText(words, { timeout: 30_000 });
  }
  const span = Date.now() - from;
  for (const kind of ["chat", "call"]) {
    const done = renewed(renewals, kind).filter((r) => r.sent >= from - 5_000);
    console.log(`spec 2: ${done.length} ${kind} renewals in ${Math.round(span / 1000)} s`);
    expect(done.length, kind).toBeGreaterThanOrEqual(2);
    expect(done.length, kind).toBeLessThanOrEqual(Math.ceil(span / 30_000));
    expectNoOverlap(renewals, kind);
    expect(renewals.filter((r) => r.kind === kind && r.done && r.status !== 200), `${kind}: every renewal answered`).toEqual([]);
  }
  // no reload, the same devices, the call still on and still syncing with its newest token
  for (const page of [chat, call]) expect(await page.evaluate(() => (window as unknown as { __mark?: number }).__mark), "no reload").toBe(42);
  expect((await entryOf(chat, A.mxid))!.d).toBe(chatDevice);
  expect((await entryOf(call, A.mxid, "call"))!.d).toBe(callDevice);
  expect(renewals.every((r) => r.device === (r.kind === "chat" ? chatDevice : callDevice))).toBe(true);
  const lastCall = renewed(renewals, "call").at(-1)!.done;
  const synced = call.waitForResponse((answer) => /\/_matrix\/client\/v3\/sync/.test(answer.url()) && answer.ok() && Date.now() > lastCall, { timeout: 45_000 });
  await synced;
  expect(await callRunning(call)).toBe(true);

  // a second tab opening the side panel takes the chat over, just after a renewal: the first tab says so and stops, and the frame takes the
  // newest sealed token from the index (its page start asks for the keys only, and no renewal)
  const before = renewed(renewals, "chat").length;
  await expect.poll(() => renewed(renewals, "chat").length, { timeout: 80_000 }).toBeGreaterThan(before);
  const second = await mineCtx.newPage();
  const asked: { mode: string | null; want: string | null }[] = [];
  const answered: Token[] = [];
  second.on("request", (request) => {
    if (request.url().endsWith("/api/chat/token") && request.method() === "POST") {
      const form = new URLSearchParams(request.postData() || "");
      asked.push({ mode: form.get("mode"), want: form.get("want") });
    }
  });
  second.on("response", async (answer) => {
    if (answer.url().endsWith("/api/chat/token") && answer.status() === 200) answered.push(await answer.json().catch(() => ({})));
  });
  const frameSynced = second.waitForResponse((answer) => /\/_matrix\/client\/v3\/sync/.test(answer.url()) && answer.ok(), { timeout: 60_000 });
  await second.goto("/", { waitUntil: "load" });                               // a feed: the navbar's Chat opens the side panel
  await second.locator('[data-side-panel="chat"]:visible').first().click();
  await frameSynced;
  await expect(chat.getByText(NOTE)).toBeVisible({ timeout: 30_000 });
  expect(asked[0]).toEqual({ mode: "boot", want: "key" });
  expect(asked.filter((a) => a.mode === "renew")).toEqual([]);
  expect(answered[0]?.access_token ?? null, "keys only: the frame unseals the token the chat tab renewed").toBeNull();
  expect(answered[0]?.device_id).toBe(chatDevice);
  await theirs.close();
  await mineCtx.close();
});

test("a token the chat server refuses mid-sync is renewed once, with no reload, on the same device, and messages still come (spec 3)", async ({ browser }) => {
  test.setTimeout(300_000);
  const mineCtx = await browser.newContext({ viewport: { width: 1280, height: 800 } });
  const seen = tokensOf(mineCtx);
  const renewals = renewalsOf(mineCtx);
  await becomes(mineCtx, A.name);
  const chat = await mineCtx.newPage();
  await openChat(chat, A.mxid);
  await unlock(chat);
  expectShortTokens(seen);
  const room = await roomBetween(chat, B.name);
  const device = (await entryOf(chat, A.mxid))!.d;

  const theirs = await browser.newContext({ viewport: { width: 1280, height: 800 } });
  await becomes(theirs, B.name);
  const bChat = await theirs.newPage();
  await bChat.goto(`/chat?room=${encodeURIComponent(room)}`, { waitUntil: "load" });
  await unlock(bChat);
  await bChat.goto(`/chat?room=${encodeURIComponent(room)}`, { waitUntil: "load" });

  // the token this page started with (the keeper never renewed it), refused before its timed renewal (at least 30 s after the start):
  // a token the keeper itself obtained under a minute ago and refused again is never renewed (keeper.js FRESH_MS, the SDK's retry loop),
  // which with the 90 s tokens these specs use is every renewed one
  let refusedAt = 0;
  let startedAt = 0;
  await chat.route("**/_matrix/client/v3/sync**", async (route) => {
    if (refusedAt || !startedAt) return route.continue();
    refusedAt = Date.now();
    await route.fulfill({ status: 401, contentType: "application/json", body: JSON.stringify({ errcode: "M_UNKNOWN_TOKEN", error: "Access token has expired", soft_logout: true }) });
  });
  await chat.goto(`/chat?room=${encodeURIComponent(room)}`, { waitUntil: "load" });
  await chat.evaluate(() => { (window as unknown as { __mark: number }).__mark = 42; });
  // B writes, so the sync waiting on the server returns at once and the next one is the one refused
  startedAt = Date.now();
  const first = `Before the refused sync ${Date.now()}`;
  await send(bChat, room, first);
  await expect(chat.locator("#chat-thread-pane")).toContainText(first, { timeout: 30_000 });
  await expect.poll(() => refusedAt, { timeout: 45_000 }).toBeGreaterThan(0);
  expect(renewals.filter((r) => r.kind === "chat" && r.sent >= startedAt && r.sent < refusedAt), "no timed renewal came first").toEqual([]);
  await chat.waitForTimeout(10_000);
  const after = renewals.filter((r) => r.kind === "chat" && r.sent >= refusedAt);
  expect(after.map((r) => r.status), "one renewal, answered").toEqual([200]);
  expect(after[0].sent - refusedAt).toBeLessThan(5_000);
  const second = `After the refused sync ${Date.now()}`;
  await send(bChat, room, second);
  await expect(chat.locator("#chat-thread-pane")).toContainText(second, { timeout: 30_000 });
  expect(await chat.evaluate(() => (window as unknown as { __mark?: number }).__mark), "no reload").toBe(42);
  expect((await entryOf(chat, A.mxid))!.d).toBe(device);
  expect(await chat.locator(".chat-key-dialog").count()).toBe(0);
  await theirs.close();
  await mineCtx.close();
});

// --- spec 9 of the chat plan: a credential change spares the chat of the sign-in that made it (the owner's decision 2)

for (const event of ["a password change", "Log out everywhere"] as const) {
  test(`${event} in another tab spares this chat tab (same device, no reload, a fresh form key) and signs the other browser out`, async ({ browser }) => {
    test.setTimeout(300_000);
    const here = await browser.newContext({ viewport: { width: 1280, height: 800 } });
    const seen = tokensOf(here);
    await becomes(here, C.name, true);
    const chat = await here.newPage();
    await openChat(chat, C.mxid);
    await unlock(chat);
    expectShortTokens(seen);
    const mine = (await entryOf(chat, C.mxid))!;
    await chat.evaluate(() => { (window as unknown as { __mark: number }).__mark = 42; });

    // another browser of the same account, with chat open
    const there = await browser.newContext({ viewport: { width: 1280, height: 800 } });
    await becomes(there, C.name);
    const far = await there.newPage();
    await openChat(far, C.mxid);
    const farDevice = (await entryOf(far, C.mxid))!.d;

    // a third browser shows a guild's Chat tab: its frame (`single`, framed as guild/chat.html frames it) is a chat client of its own
    const third = await browser.newContext({ viewport: { width: 1280, height: 800 } });
    await becomes(third, C.name);
    const tab = await third.newPage();
    await tab.goto("/settings/profile", { waitUntil: "load" });
    await tab.evaluate(() => {
      const frame = document.createElement("iframe");
      frame.id = "guild-chat-frame";
      frame.src = "/chat?embed=1&single=1";
      frame.style.cssText = "width: 800px; height: 600px";
      document.body.prepend(frame);
    });
    await expect.poll(async () => !!(await entryOf(tab, C.mxid))?.ed, { timeout: 60_000 }).toBe(true);

    const settings = await here.newPage();
    await settings.goto("/settings/security", { waitUntil: "load" });
    const status = await settings.evaluate(async ([which, oldPassword, newPassword]) => {
      const form = new FormData();
      form.append("formkey", (window as unknown as { formkey(): string }).formkey());
      if (which === "Log out everywhere") {
        form.append("password", oldPassword);
        return (await fetch("/settings/log_out_all_others", { method: "POST", body: form, redirect: "manual" })).status;
      }
      form.append("old_password", oldPassword);
      form.append("new_password", newPassword);
      form.append("cnf_password", newPassword);
      return (await fetch("/settings/security", { method: "POST", body: form, redirect: "manual" })).type === "opaqueredirect" ? 302 : 0;
    }, [event, PASSWORD, NEW_PASSWORD]);
    expect([200, 204, 302]).toContain(status);

    // the chat tab's next timed renewal (its token ends within 90 s here: expectShortTokens) goes with the form key it was drawn with (now
    // stale: refused), fetches the current one and sends again, on the same device
    // (the stale key's answer is a redirect the page reads as an opaque one, `redirect: "manual"`, so it shows as a request with no answer here)
    const site: string[] = [];
    chat.on("request", (request) => { if (request.url().endsWith("/api/chat/token")) site.push("token asked"); });
    chat.on("response", (answer) => {
      if (answer.url().endsWith("/api/chat/token")) site.push(`token ${answer.status()}`);
      if (answer.url().endsWith("/api/chat/formkey")) site.push(`formkey ${answer.status()}`);
    });
    const SEQUENCE = "token asked,formkey 200,token asked,token 200";
    const shown = () => site.filter((x) => !/^token [23]0[0-9]$/.test(x) || x === "token 200").join(",");
    await expect.poll(shown, { timeout: 120_000 }).toContain(SEQUENCE);
    // nothing else first, except a renewal sent before the change and answered after it (its request went before this listened)
    expect(shown().slice(0, shown().indexOf(SEQUENCE)), shown()).toMatch(/^(token 200,)?$/);
    await chat.waitForTimeout(3_000);
    expect(await chat.evaluate(() => (window as unknown as { __mark?: number }).__mark), "no reload").toBe(42);
    expect((await entryOf(chat, C.mxid))!.d).toBe(mine.d);
    expect(await chat.locator(".chat-key-dialog").count()).toBe(0);

    // the other browser's device is gone from the chat server (the account's own list, asked by a site route)
    await expect.poll(async () => settings.evaluate(async () => (await (await fetch("/api/chat/devices")).json()).devices.map((d: { device_id: string }) => d.device_id)),
      { timeout: 60_000 }).not.toContain(farDevice);
    expect(await settings.evaluate(async () => (await (await fetch("/api/chat/devices")).json()).devices.map((d: { device_id: string }) => d.device_id))).toContain(mine.d);

    // the guild Chat tab's frame of a browser signed out says so in the frame, never a blank one: the sign-in page may not be framed,
    // so the frame's own reload used to end on the browser's error page (routes/errors.py error_401 now answers a frame itself)
    const frameText = async () => {
      const frame = await (await tab.$("#guild-chat-frame"))?.contentFrame();
      if (!frame || frame.url().startsWith("chrome-error:")) return "blank";
      return frame.evaluate(() => (document.body ? document.body.innerText : "")).catch(() => "");
    };
    await expect.poll(frameText, { timeout: 90_000 }).toContain("Sign in again to carry on.");
    await third.close();

    if (event === "a password change") {                                     // put the password back, from the same sign-in
      await settings.goto("/settings/security", { waitUntil: "load" });
      await settings.evaluate(async ([oldPassword, newPassword]) => {
        const form = new FormData();
        form.append("formkey", (window as unknown as { formkey(): string }).formkey());
        form.append("old_password", newPassword);
        form.append("new_password", oldPassword);
        form.append("cnf_password", oldPassword);
        await fetch("/settings/security", { method: "POST", body: form, redirect: "manual" });
      }, [PASSWORD, NEW_PASSWORD]);
    }
    await there.close();
    await here.close();
  });
}

// --- deleting an account signs out through a form, not a Log out link

const GONE = { name: "ui_chat_gone", mxid: "@ruqqus_90705:ruqqus.local" };

test("deleting an account takes its chat devices out of the browser, leaves another account's, and a wrong password takes nothing", async ({ browser }) => {
  const context = await browser.newContext({ viewport: { width: 1280, height: 800 } });
  const page = await context.newPage();
  await page.goto("/login");
  await page.fill("#username", GONE.name);
  await page.fill("#password", PASSWORD);
  await page.click("#login_button");
  await page.waitForURL((url) => !url.pathname.startsWith("/login"), { timeout: 15_000 }).catch(() => {});
  test.skip(page.url().includes("/login"), `${GONE.name} has been deleted already: run scripts/ui/seed_chat_session.py again`);

  await page.goto("/settings/security", { waitUntil: "load" });
  const entry = (u: string, d: string, k: string) => ({ u, d, k, p: `uqqus-${k}-${u.slice(1).split(":")[0]}-${d}`, kid: "k1", hs: "http://localhost:8008",
    s: "out", x: 1, ed: null, made: 1, used: 1, gen: 1, exp: null, life: null, why: null, fails: 0, bad: 0, empty: 0 });
  const entries = [entry(GONE.mxid, "UQGONECHAT00", "chat"), entry(GONE.mxid, "UQGONECALL00", "call"), entry(A.mxid, "UQKEPTCHAT00", "chat")];
  await page.evaluate(async ([key, list]) => {
    localStorage.setItem(key as string, JSON.stringify({ v: 2, owner: null, epoch: 4, entries: list }));
    localStorage.setItem("ruqqus_chat_session", JSON.stringify({ user_id: "@x:s", access_token: "t", device_id: "D" }));
    sessionStorage.setItem("uqqus_chat_prekey", "PREKEY");
    localStorage.setItem("uqqus_chat_waiting", JSON.stringify({ "!room:x": [{ text: "plain text", at: 1 }] }));
    localStorage.setItem("density", "compact");
    const names = (list as { p: string }[]).flatMap((e) => [`${e.p}::matrix-sdk-crypto`, `${e.p}::matrix-sdk-crypto-meta`]);
    for (const name of names) {
      await new Promise<void>((resolve) => {
        const open = indexedDB.open(name);
        open.onsuccess = () => { open.result.close(); resolve(); };
      });
    }
  }, [INDEX, entries] as const);
  const everything = () => page.evaluate(async (key) => ({
    keys: ["ruqqus_chat_session", "uqqus_chat_waiting"].filter((k) => localStorage.getItem(k) !== null)
      .concat(sessionStorage.getItem("uqqus_chat_prekey") === null ? [] : ["uqqus_chat_prekey"]),
    devices: (JSON.parse(localStorage.getItem(key) || "{}").entries || []).map((e: { d: string }) => e.d).sort(),
    stores: (await indexedDB.databases()).map((d) => d.name).filter((name) => /matrix-sdk-crypto/.test(name || "")).sort(),
  }), INDEX);
  const send = (password: string) => page.evaluate((typed) => {
    const form = document.getElementById("deleteAccountForm") as HTMLFormElement;
    (form.querySelector("#delete-password") as HTMLInputElement).value = typed;
    form.submit();
  }, password);

  await Promise.all([page.waitForLoadState("load"), page.waitForResponse("**/settings/delete_account"), send("not the password")]);
  await page.waitForTimeout(500);
  const held = await everything();
  expect(held.keys.length).toBe(3);
  expect(held.devices.length).toBe(3);
  expect(held.stores.length).toBe(6);

  await Promise.all([page.waitForResponse("**/settings/delete_account"), page.waitForEvent("domcontentloaded"), send(PASSWORD)]);
  const kept = entries[2].p;
  await expect.poll(async () => JSON.stringify(await everything()), { timeout: 15_000 })
    .toBe(JSON.stringify({ keys: [], devices: ["UQKEPTCHAT00"], stores: [`${kept}::matrix-sdk-crypto`, `${kept}::matrix-sdk-crypto-meta`] }));
  expect(await page.evaluate(() => localStorage.getItem("density"))).toBe("compact");
  await expect(page.locator("a[data-logout]")).toHaveCount(0);
  await expect(page.locator("h1")).toBeVisible();
  await page.screenshot({ path: "reports/account-deleted-1280.png" });
  await page.setViewportSize({ width: 390, height: 800 });
  await page.screenshot({ path: "reports/account-deleted-390.png" });
  await context.close();
});
