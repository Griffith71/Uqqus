import { test, expect, type Browser, type BrowserContext, type Page } from "@playwright/test";
import {
  PASSWORD, NEW_PASSWORD, signIn, becomes, index, entryOf, openChat, unlock, roomBetween, send, groupWith, answerBoxes, setUpChat,
} from "./chat_helpers";

// "Sign in anywhere, see your history" (CLAUDE.md "Chat encryption"): what one browser wrote and read in a 1:1 chat, a group and a
// channel for circles is read in full on a browser that never had chat, signed in through the form with nothing else typed, in a second
// tab of it too; a password changed in Settings re-locks the chat key there, so a third browser signing in with the new password reads
// everything with nothing typed either. Resets, Start over and the rescue are chat_session.spec.ts's (specs 8, 10 and 11).
//   PYTHONPATH=. python scripts/ui/seed_chat_session.py   (in the container), then: npm run chat-history
// ui_chat_h's password is changed and put back through the same form; a run that stopped between the two leaves its chat key locked
// with the other password, and the first browser then starts its chat over (answerBoxes' `startOver`) before anything is written.

test.describe.configure({ mode: "serial" });

const H = { name: "ui_chat_h", mxid: "@ruqqus_90712:ruqqus.local" };
const B = { name: "ui_chat_b", mxid: "@ruqqus_90704:ruqqus.local" };
const VIEW = { viewport: { width: 1280, height: 800 } };

test.beforeAll(async ({ browser }) => setUpChat(browser, [H, B]));

// what the two tests share: the rooms and every line written in each, and the browsers still open
// `fresh`: made by this run, so nothing in it may be unreadable (the 1:1 room is the same every run, and holds what earlier runs wrote
// before one of them started chat over)
const rooms: { name: string; tab: string; id: string; lines: string[]; fresh: boolean }[] = [];
const open: { first?: BrowserContext; firstDevice?: string; second?: BrowserContext; secondTab?: Page } = {};

// chat on this page unlocks by itself (the pre-key the form left): the unlock box never shows, not once
async function readsWithNothingTyped(page: Page) {
  let asked = false;
  await expect.poll(async () => {
    if (await page.locator(".chat-key-dialog").count()) asked = true;
    const e = (await index(page))?.entries.find((x) => x.s === "in" && x.k === "chat");
    return !!(e && e.x);
  }, { timeout: 60_000, message: "chat unlocks" }).toBe(true);
  await page.waitForTimeout(2_000);
  if (await page.locator(".chat-key-dialog").count()) asked = true;
  expect(asked, "nothing was asked").toBe(false);
}

// each conversation opened from the list, as a member opens it (one page load: every load asks the site for the five lists, and a member
// may make 100 requests a minute)
async function readsEverything(page: Page) {
  if (!page.url().includes("/chat")) await page.goto("/chat", { waitUntil: "load" });
  for (const room of rooms) {
    await page.locator(`[data-chat-tab="${room.tab}"]`).first().click();
    await page.locator(`#chat-conversation-list [data-room="${room.id}"]`).click({ timeout: 60_000 });
    for (const line of room.lines) {
      await expect(page.locator("#chat-thread-pane"), room.name).toContainText(line, { timeout: 60_000 });
    }
    if (room.fresh) await expect(page.locator("#chat-thread-pane")).not.toContainText("This message can't be read on this device.");
  }
}

async function changePassword(page: Page, from: string, to: string) {
  await page.goto("/settings/security", { waitUntil: "load" });
  const form = page.locator("form[data-chat-rekey]");
  await form.locator("#new_password").fill(to);
  await form.locator("#cnf_password").fill(to);
  await form.locator("#old_password").fill(from);
  // the form's own script locks the chat key again with the new password before it is sent (assets/js/chat_key.js)
  const sent = page.waitForRequest((r) => r.url().endsWith("/settings/security") && r.method() === "POST");
  const drawn = page.waitForNavigation({ waitUntil: "load" });
  await form.locator("input[type=submit]").click();
  expect((await sent).postData() || "", "the re-locked copy goes with the change").toContain("chat_key_blob=");
  await drawn;
  expect(page.url()).not.toContain("/login");
}

async function devicesOf(page: Page) {
  return page.evaluate(async () => (await (await fetch("/api/chat/devices")).json()).devices.map((d: { device_id: string }) => d.device_id) as string[]);
}

async function newBrowser(browser: Browser) {
  const context = await browser.newContext(VIEW);
  return { context, page: await context.newPage() };
}

test("a 1:1 chat, a group and a channel written on one browser are read in full on a new one, and in its second tab, with nothing typed", async ({ browser }) => {
  test.setTimeout(420_000);
  const stamp = Date.now() % 1_000_000;

  // browser 1: chat as it is used day to day (a sign-in some time ago, so no pre-key in this tab: the password typed once)
  const first = await newBrowser(browser);
  open.first = first.context;
  await becomes(first.context, H.name, true);
  await openChat(first.page, H.mxid);
  await answerBoxes(first.page, { password: PASSWORD, startOver: PASSWORD });
  const firstDevice = (await entryOf(first.page, H.mxid))!.d;
  open.firstDevice = firstDevice;

  const other = await newBrowser(browser);
  await becomes(other.context, B.name, true);
  await openChat(other.page, B.mxid);
  await unlock(other.page);

  const dm = { name: "1:1 chat", tab: "inbox", id: await roomBetween(first.page, B.name), lines: [`H to B ${stamp}`, `B to H ${stamp}`], fresh: false };
  await send(first.page, dm.id, dm.lines[0]);
  await send(other.page, dm.id, dm.lines[1]);

  const group = { name: "group", tab: "group", id: await groupWith(other.page, [H.name]), lines: [`B in the group ${stamp}`, `H in the group ${stamp}`], fresh: true };
  await send(other.page, group.id, group.lines[0]);
  await send(first.page, group.id, group.lines[1]);

  // a channel for circles is end-to-end encrypted and written by its owner's own browser (CLAUDE.md "Broadcast channels")
  const channelId = await first.page.evaluate(async (name) => {
    const form = new FormData();
    form.append("formkey", (window as unknown as { formkey(): string }).formkey());
    form.append("name", name);
    form.append("audience", "4");
    form.append("circles", "friends");
    const answer = await fetch("/api/broadcasts", { method: "POST", body: form });
    const body = await answer.json();
    if (!answer.ok) throw new Error(`channel: ${answer.status} ${JSON.stringify(body)}`);
    return body.room_id as string;
  }, `History ${stamp}`);
  const channel = { name: "channel", tab: "channel", id: channelId, lines: [`First in the channel ${stamp}`, `Second in the channel ${stamp}`], fresh: true };
  for (const line of channel.lines) await send(first.page, channel.id, line);
  rooms.push(dm, group, channel);
  await other.context.close();
  await first.page.waitForTimeout(5_000);           // the key backup uploads what this device holds about 2 s after it holds it

  // browser 2 has never had chat: signed in through the form, it reads everything at once
  const second = await newBrowser(browser);
  open.second = second.context;
  await signIn(second.page, H.name);
  await openChat(second.page, H.mxid);
  await readsWithNothingTyped(second.page);
  expect((await entryOf(second.page, H.mxid))!.d, "a device of its own").not.toBe(firstDevice);
  await readsEverything(second.page);

  // a second tab of it: the same device, nothing typed (its tab has no pre-key; the store is already unlocked)
  const tab = await second.context.newPage();
  open.secondTab = tab;
  await openChat(tab, H.mxid);
  await readsWithNothingTyped(tab);
  await readsEverything(tab);
});

test("a password changed in Settings keeps that browser's chat, and a third browser signing in with the new one reads everything with nothing typed", async ({ browser }) => {
  test.setTimeout(420_000);
  const tab = open.secondTab!;
  const settings = await open.second!.newPage();
  await changePassword(settings, PASSWORD, NEW_PASSWORD);

  // the tab that changed it carries on, on the same device, and what it writes now is read later as well
  const after = `After the password change ${Date.now() % 1_000_000}`;
  await send(tab, rooms[0].id, after);
  rooms[0].lines.push(after);
  expect(await tab.locator(".chat-key-dialog").count()).toBe(0);

  // the other browser's sign-in ended with the change, and its chat device with it
  await expect.poll(() => devicesOf(settings), { timeout: 60_000 }).not.toContain(open.firstDevice);
  await open.first!.close();
  await settings.waitForTimeout(5_000);

  const third = await newBrowser(browser);
  await signIn(third.page, H.name, NEW_PASSWORD);
  await openChat(third.page, H.mxid);
  await readsWithNothingTyped(third.page);
  await readsEverything(third.page);

  // put the password back, through the same form, so the key is locked with it again for the next run
  await changePassword(third.page, NEW_PASSWORD, PASSWORD);
  await open.second!.close();
  await third.context.close();
});
