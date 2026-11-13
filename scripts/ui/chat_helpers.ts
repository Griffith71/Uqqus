import { expect, type Browser, type BrowserContext, type Page } from "@playwright/test";
import { execSync } from "node:child_process";
import { existsSync, mkdirSync, readFileSync, readdirSync, writeFileSync } from "node:fs";

// What the chat specs share (chat_session.spec.ts, chat_history.spec.ts): signing in, cookies minted by the app, the device index,
// opening and unlocking chat, rooms, sending, and the unlock boxes. The accounts are scripts/ui/seed_chat_session.py's.

export const PASSWORD = "ui-Password-1";
export const NEW_PASSWORD = "ui-Password-2";

export type Cookies = Parameters<BrowserContext["addCookies"]>[0];
// what the site answered /api/chat/token: the raw token never lands in storage, so the spec reads it off the wire to look for it there
export type Token = { user_id: string; device_id: string; access_token: string | null; homeserver_url: string; expires_in_ms?: number };
export type Entry = { u: string; d: string; k: string; p: string; s: string; sealed?: string; gen: number; ed: string | null; x: number; why: string | null };
export type Index = { v: number; owner: string | null; epoch: number; entries: Entry[] };
export const INDEX = "uqqus_chat_devices";
export const LIVE = ["in", "out"];

export async function signIn(page: Page, name: string, password = PASSWORD) {
  await page.goto("/login");
  await page.fill("#username", name);
  await page.fill("#password", password);
  await Promise.all([page.waitForURL((url) => !url.pathname.startsWith("/login")), page.click("#login_button")]);
}

export const cookiesOf = (who: string): Cookies => JSON.parse(readFileSync(`.auth/${who}.json`, "utf8")).cookies;

export const CONTAINER = process.env.RUQQUS_CONTAINER || "ruqqus-ruqqus-1";
export const minted: Record<string, string[]> = {};

// session cookies for these accounts, each with a session id of its own, signed with the app's own key: what the login route leaves, without
// spending a sign-in
export function mintCookies(names: string[], count = 30) {
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

// A Log out ends its sign-in for good on the server, so a context never reuses a cookie: each `becomes` puts a fresh one in
export async function becomes(context: BrowserContext, who: string, fresh = false) {
  if (fresh || !minted[who]?.length) mintCookies([who]);
  // the cookie's name, domain and flags, from an account set up through the form (ui_chat_d never is: spec 8 sets its chat up itself)
  const template = cookiesOf(existsSync(`.auth/${who}.json`) ? who : readdirSync(".auth").find((f) => f.endsWith(".json"))!.replace(/\.json$/, "")).find((c) => c.name === "session_ruqqus")!;
  await context.clearCookies();
  await context.addCookies([{ ...template, value: minted[who].shift()! }]);
}

// initRustCrypto's error for a store made for another account or device, or opened with another key
export const storeRefused = (text: string) => /doesn't match|does not match|mismatch|unpickling/i.test(text);
// null while the page is between documents (a chat page that starts again reloads itself)
export const index = (page: Page) => (page.evaluate((k) => JSON.parse(localStorage.getItem(k) || "null"), INDEX) as Promise<Index | null>).catch(() => null);
// this account's live device of a kind (the one it signs in with here): `in` while signed in, `out` after a Log out
export async function entryOf(page: Page, mxid: string, kind = "chat"): Promise<Entry | undefined> {
  const idx = await index(page);
  return idx?.entries.find((e) => e.u === mxid && e.k === kind && LIVE.includes(e.s));
}
export const entryFor = async (page: Page, mxid: string, device: string) => (await index(page))?.entries.find((e) => e.u === mxid && e.d === device);
export const databases = (page: Page) => page.evaluate(async () => (await indexedDB.databases()).map((d) => d.name)) as Promise<string[]>;
export const storeOf = (entry: Entry) => `${entry.p}::matrix-sdk-crypto`;

// every token the site answered in this context, from every page and frame
export function tokensOf(context: BrowserContext) {
  const seen: Token[] = [];
  context.on("response", async (answer) => {
    if (!answer.url().endsWith("/api/chat/token") || answer.status() !== 200) return;
    try { seen.push(await answer.json()); } catch { /* a page that went away */ }
  });
  return seen;
}
export const tokenOf = (seen: Token[], entry: Entry) => [...seen].reverse().find((t) => t.device_id === entry.d && t.access_token);

export async function openChat(page: Page, mxid: string) {
  const errors: string[] = [];
  const listen = (message: { type(): string; text(): string }) => {
    if (message.type() === "error" || message.type() === "warning") errors.push(message.text());
  };
  page.on("console", listen);
  await page.goto("/chat", { waitUntil: "load" });
  await expect.poll(async () => {
    const e = await entryOf(page, mxid);
    return !!(e && e.s === "in" && e.sealed);
  }, { timeout: 30_000 }).toBe(true);
  // the store exists from here on, whether this device is unlocked yet or is asking for the password (a minted cookie has no pre-key)
  const entry = (await entryOf(page, mxid))!;
  await expect.poll(async () => (await databases(page)).includes(storeOf(entry)), { timeout: 30_000 }).toBe(true);
  await page.waitForTimeout(1_500);
  page.off("console", listen);
  return errors;
}

// a device with no pre-key in its tab asks for the password once; a first device shows its recovery code
export async function unlock(page: Page) {
  const saved = page.getByRole("button", { name: "I've saved it" });
  const field = page.locator(".chat-key-dialog input[type=password]");
  const ready = page.waitForResponse("**/api/chat/ready", { timeout: 60_000 }).catch(() => null);
  await expect.poll(async () => {
    if (await saved.count()) await saved.click().catch(() => {});
    if (await field.count()) {
      await field.fill(PASSWORD);
      await page.getByRole("button", { name: "Unlock" }).click().catch(() => {});
    }
    const e = (await index(page))?.entries.find((x) => x.s === "in" && x.k === "chat");
    return !!(e && e.x);
  }, { timeout: 60_000 }).toBe(true);
  await ready;
}

export async function roomBetween(page: Page, other: string) {
  return page.evaluate(async (name) => {
    const form = new FormData();
    form.append("formkey", (window as unknown as { formkey(): string }).formkey());
    form.append("username", name);
    const answer = await fetch("/api/chat/start?json=1", { method: "POST", body: form });
    return (await answer.json()).room_id as string;
  }, other);
}

export async function send(page: Page, room: string, words: string) {
  if (!page.url().includes(encodeURIComponent(room))) await page.goto(`/chat?room=${encodeURIComponent(room)}`, { waitUntil: "load" });
  // the thread opens once the page's lists have come (index.js boot.initialRoom), which can take longer than an action's 15 s
  await page.locator("#chat-composer-input").waitFor({ timeout: 60_000 });
  await page.locator("#chat-composer-input").fill(words);
  const sent = page.waitForResponse((answer) => /\/send\/m\.room\.encrypted\//.test(answer.url()) && answer.ok(), { timeout: 60_000 });
  await page.locator("#chat-composer-input").press("Enter");
  await sent;
}

export const keysLocked = (page: Page) => page.evaluate(async () => {
  const info = await (await fetch("/api/chat/key")).json();
  return !!(info.password_blob && info.recovery_blob);
});

// a group that B starts with these accounts (who follow B both ways: scripts/ui/seed_chat_session.py), so they are in it at once: a NEW
// room, whose room key no older device of theirs ever had
export async function groupWith(page: Page, names: string[]) {
  return page.evaluate(async (people) => {
    const form = new FormData();
    form.append("formkey", (window as unknown as { formkey(): string }).formkey());
    form.append("name", `Harvest ${Date.now() % 100000}`);
    form.append("usernames", people.join(" "));
    const answer = await fetch("/api/chat/groups", { method: "POST", body: form });
    const body = await answer.json();
    if (!answer.ok) throw new Error(`group: ${answer.status} ${JSON.stringify(body)}`);
    return body.room_id as string;
  }, names);
}

// The unlock boxes, each answered once (and again only if it is still there 10 s later: the password check is limited to 10 a minute,
// and a run of 429s puts the address in time out for an hour): the password typed (a new device's "Unlock your messages", or a first
// set-up's "Protect your messages"), the recovery code typed when the copy was locked with another password (or, with `cancel`, "I don't
// have it: start over" and then Cancel on the "Start over?" question; with `startOver`, the same question answered with that password,
// which the site checks before it makes the key upload: routes/chat_key_reset_pages.py), a first set-up's code kept in `keep` (a file) when
// it is shown. "We found your chat on this browser" (the rescue, rescue.js) is answered Use them with `rescue`, else Not now. Answers the
// boxes' titles in order, the question's lines, and how it ended.
export type Boxes = { titles: string[]; question: string[]; ended: "unlocked" | "cancelled" };
export async function answerBoxes(page: Page, { password, code = null, cancel = false, keep = null, startOver = null, rescue = false }:
  { password: string; code?: string | null; cancel?: boolean; keep?: string | null; startOver?: string | null; rescue?: boolean }): Promise<Boxes> {
  const box = page.locator(".chat-key-dialog");
  const titles: string[] = [];
  let question: string[] = [];
  let acted = { title: "", at: 0 };
  let ended: Boxes["ended"] | null = null;
  await expect.poll(async () => {
    if (ended === "cancelled" && !(await box.count())) return ended;
    const e = (await index(page))?.entries.find((x) => x.s === "in" && x.k === "chat");
    if (e && e.x && !(await box.count())) return (ended = "unlocked");
    const now = (await box.locator("h2").first().innerText().catch(() => "")).trim();
    if (now && titles[titles.length - 1] !== now) titles.push(now);
    if (!now || (acted.title === now && Date.now() - acted.at < 10_000)) return now;
    acted = { title: now, at: Date.now() };
    if (now === "Unlock your messages" || now === "Protect your messages") {
      await box.locator("input[type=password]").fill(password).catch(() => {});
      await box.getByRole("button", { name: now === "Protect your messages" ? "Continue" : "Unlock" }).click().catch(() => {});
    } else if (now === "Enter your recovery code") {
      if (cancel || startOver) {
        await box.getByRole("button", { name: "I don't have it: start over" }).click().catch(() => {});
      } else {
        if (!code) throw new Error("the key is locked with another password and its recovery code isn't kept: give the account a new id (seed_chat_session.py)");
        await box.locator("input:not([type=password])").first().fill(code).catch(() => {});
        await box.getByRole("button", { name: "Unlock" }).click().catch(() => {});
      }
    } else if (now === "We found your chat on this browser") {
      if (rescue) {
        await box.locator("input[type=password]").fill(password).catch(() => {});
        await box.getByRole("button", { name: "Use them" }).click().catch(() => {});
      } else {
        await box.getByRole("button", { name: "Not now" }).click().catch(() => {});
      }
    } else if (now === "Start over?") {
      if (!cancel && !startOver) throw new Error("asked to start over");
      question = await box.locator("p").allInnerTexts();
      if (startOver) {
        await box.locator("input[type=password]").fill(startOver).catch(() => {});
        await box.getByRole("button", { name: "Start over" }).click().catch(() => {});
      } else {
        await box.getByRole("button", { name: "Cancel" }).click().catch(() => {});
        ended = "cancelled";
      }
    } else if (now === "Save your recovery code") {
      const shown = await box.locator("input[readonly]").first().inputValue().catch(() => "");
      if (keep && /^[A-Z2-9]{5}(-[A-Z2-9]{5}){4}$/.test(shown)) writeFileSync(keep, shown);
      await page.getByRole("button", { name: "I've saved it" }).click().catch(() => {});
    }
    return now;
  }, { timeout: 120_000 }).toMatch(/^(unlocked|cancelled)$/);
  return { titles, question, ended: ended! };
}

// Each account's chat is set up once, through the real form and to the end: the first chat page makes its keys, locks them with the pre-key the
// form left and shows the recovery code. A chat set up any other way asks for a password nobody types, and is stuck at "Start over?" afterwards.
export async function setUpChat(browser: Browser, accounts: { name: string }[]) {
  mkdirSync(".auth", { recursive: true });
  for (const account of accounts) {
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
    // the copies can be saved before the code is shown: press it whenever it shows, until it is gone
    await expect.poll(async () => {
      if (await saved.count()) await saved.click().catch(() => {});
      return saved.count();
    }, { timeout: 30_000 }).toBe(0);
    await page.waitForTimeout(3_000);                                         // /api/chat/ready, which the other account's messages wait for
    await context.storageState({ path: `.auth/${account.name}.json` });
    await context.close();
  }
}
