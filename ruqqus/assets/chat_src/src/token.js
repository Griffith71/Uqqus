// What this browser keeps of chat, and the one way a page gets a chat session (the chat plan's phase 2, sections 2 and 3).
//
// One plaintext index, `uqqus_chat_devices`, names every chat device this browser has held, per account and kind (`chat`: the chat
// page and its frames; `call`: the call window and a Space joining a call), each with its own crypto store,
// `uqqus-<kind>-<localpart>-<device>`, locked with a 32-byte key the site gives only to a signed-in page of that account
// (/api/chat/token, helpers/chat_store_keys.py). The access token is kept only sealed (seal.js) under a key derived from the store key,
// so what a signed-out browser holds opens for nobody without a sign-in. Nothing here is the server's: it never sees the index.
//
// The index is written only under the Web Lock `uqqus-chat-session`, and the lock is held for the read, the decision and the write,
// never across a network call or a database deletion: a section that needs the site reads, lets go, asks, then takes the lock again,
// reads again and writes only if nothing moved under it (`epoch`, `owner`, the entry's `gen` and `s`). A store is deleted only in the
// cases the plan lists (2.4: a key that can't be derived again, an account deleted here, a store broken for 30 days, the caps of 4
// accounts and 6 removed stores, the legacy unkeyed stores; and, in harvest.js, a `removed` store found empty or whose keys were moved to
// the account's current chat device), under its own lock (`uqqus-store:<prefix>`, held by every page that has it open), and never
// without Web Locks.

import { b64urlDecode, seal, tokenAad, tokenKey, unseal, waitingAad, waitingKey } from "./seal.js";
import { REFRESHED, SITE_TIMEOUT, TRANSIENT, refreshFormkey, refusedKey, timeoutSignal } from "./site.js";

export { refreshFormkey, sitePost, timeoutSignal } from "./site.js";

export const INDEX_KEY = "uqqus_chat_devices";
// First messages that wait in this browser until the other person's chat is ready (index.js), sealed under the account's waiting key.
export const WAITING_KEY = "uqqus_chat_waiting";
const PROBE_KEY = "uqqus_chat_probe";
// What the chat client kept before the index (the dev site only: it was never live): plaintext tokens, and the stores they opened.
export const LEGACY_KEYS = ["ruqqus_chat_session", "ruqqus_call_session"];
const LEGACY_STORES = ["matrix-js-sdk", "uqqus-call"];
const FILTER_KEYS = "mxjssdk_memory_filter_";
const SESSION_LOCK = "uqqus-chat-session";
export const STORE_LOCK = "uqqus-store:";
export const KINDS = ["chat", "call"];
export const MAX_ACCOUNTS = 4;                   // accounts whose stores this browser keeps (2.4 case 6)
export const MAX_REMOVED = 6;                    // removed stores one account keeps here (2.4 case 8)
export const BROKEN_KEEP = 30 * 24 * 60 * 60 * 1000;   // a broken store is kept this long (2.4 case 4)
const DEVICE = /^[A-Za-z0-9_-]{1,64}$/;
const HOUR = 60 * 60 * 1000;
const LIVE = new Set(["in", "out"]);

const tr = (text) => (globalThis.window && typeof window.tr === "function" ? window.tr(text) : text);

// --- what goes wrong, each told apart for the page (index.js, call.js and space.js show each in its own way) -----------------------

// A QR sign-in hasn't given its password yet (helpers/qr_login.py): the site won't hand out a chat session. Nothing stored here is
// touched, whoever's it is.
export class PasswordNeeded extends Error {}
// The site refuses chat to this account (the child lock, or a parent who hasn't switched chat on): its own sentence.
export class ChatRefused extends Error {}
// The site or the network couldn't answer: `retry` says whether trying again can help.
export class ChatUnavailable extends Error {
  constructor(message, { retry = true } = {}) {
    super(message);
    this.retry = retry;
  }
}
// This browser can't keep site data: chat can't start, and no device is made for a page that couldn't remember it (2.5).
export class StorageUnavailable extends Error {}
// The site sign-in is over, or a Log out (or a deletion, or another account) came first while this page waited for its answer.
export class SignedOut extends Error {}
// This page lost chat to another tab (`uqqus-matrix`) while it waited for the site: its answer is dropped, nothing is written, and the
// device the other tab holds is left alone (the page says so itself: index.js holdChatLock).
export class TakenOver extends Error {}

// --- small helpers -------------------------------------------------------------------------------------------------------------

function hasLocks() {
  return !!(globalThis.navigator && navigator.locks && typeof navigator.locks.request === "function");
}

export function underSessionLock(work) {
  return hasLocks() ? navigator.locks.request(SESSION_LOCK, work) : work();
}

// The raw store key is held only until initRustCrypto has it (1.2): every way a page stops before then zeroes it here (keeper.js
// shutdown and openStore, index.js, call.js, space.js).
export function forgetStoreKey(session) {
  if (session && session.storeKey && typeof session.storeKey.fill === "function") session.storeKey.fill(0);
}

function randomHex() {
  return Array.from(crypto.getRandomValues(new Uint8Array(16)), (b) => b.toString(16).padStart(2, "0")).join("");
}

// Made once per page load and sent with every start and every retry of it (the page retries after a 503 or a lost answer): the site
// gives a retry the device it already minted for this page, never a second one (helpers/chat_tokens.py `retried`).
let pageMintKey = null;

function mintKeyOfThisPage() {
  if (!pageMintKey) pageMintKey = randomHex();
  return pageMintKey;
}

function getItem(key) {
  try { return localStorage.getItem(key); } catch (e) { return null; }
}

function removeItem(key) {
  try { if (localStorage.getItem(key) !== null) localStorage.removeItem(key); } catch (e) { /* storage blocked */ }
}

export function prefixFor(kind, mxid, device) {
  const local = /^@([^:]+):/.exec(mxid || "");
  if (!KINDS.includes(kind) || !local || !DEVICE.test(device || "")) throw new Error("not a chat device");
  return `uqqus-${kind}-${local[1]}-${device}`;
}

// --- the index -----------------------------------------------------------------------------------------------------------------

function emptyIndex() {
  return { v: 2, owner: null, epoch: 0, entries: [] };
}

function isEntry(e) {
  return e && typeof e === "object" && typeof e.u === "string" && typeof e.d === "string" && typeof e.k === "string"
    && typeof e.p === "string" && typeof e.s === "string";
}

// The index as stored (`raw`, to write only on a change) and as read. An unreadable one (bad JSON, another version) reads as empty and
// is marked `bad`: it is overwritten only after the site has answered a page of this browser (2.2), and counts as someone else's for the
// ad hold. `unlisted` is an index that replaced an unreadable one (a Log out, a deletion: all_js.js), or this one when the browser can't
// list its databases: stores may be here that no entry names, so the ad hold counts it as someone else's too, until a start here lists
// the databases and gives each store an entry (`settle`, `listedEntries`).
function loadIndex() {
  const raw = getItem(INDEX_KEY);
  if (raw === null) return { idx: emptyIndex(), raw };
  let parsed = null;
  try { parsed = JSON.parse(raw); } catch (e) { parsed = null; }
  if (!parsed || parsed.v !== 2 || !Array.isArray(parsed.entries)) return { idx: { ...emptyIndex(), bad: true }, raw };
  return {
    idx: {
      v: 2,
      owner: typeof parsed.owner === "string" ? parsed.owner : null,
      epoch: Number.isInteger(parsed.epoch) ? parsed.epoch : 0,
      entries: parsed.entries.filter(isEntry),
      ...(parsed.unlisted === true ? { unlisted: true } : {}),
    },
    raw,
  };
}

export function readIndex() {
  return loadIndex().idx;
}

function serialise(idx) {
  return JSON.stringify({ v: 2, owner: idx.owner, epoch: idx.epoch, entries: idx.entries, ...(idx.unlisted ? { unlisted: true } : {}) });
}

// The index changed under a section that read it: another page wrote it meanwhile. Only a writer that couldn't wait for the session lock
// can (a Log out or a deletion after its 3 s, all_js.js underChatSessionLock; any page of a browser with no Web Locks), and a section that
// awaits (settle's and a renewal's unseal) is exactly the one a stuck or suspended tab holds that long.
class IndexMoved extends Error {}

// Compare-and-set: written only while what is stored is still the `raw` this section read, so a write prepared before a Log out or a
// deletion that ran without the lock never undoes it (a sealed token, an owner or an `in` entry put back over them). The caller decides
// again on what is stored now (`withIndex`, `markEntry` and `deleteIfFree` read again; `settle` drops its answer). Asked even when there
// is nothing to write: a section whose answer changes nothing (a reload with its sealed token) still mustn't hand out a token after a Log
// out it didn't see.
function writeIfChanged(idx, raw) {
  if (getItem(INDEX_KEY) !== raw) throw new IndexMoved("the chat index changed meanwhile");
  const next = serialise(idx);
  if (next === raw) return false;                // a plain reload with a fresh token writes nothing, so other tabs hear nothing
  try {
    localStorage.setItem(INDEX_KEY, next);
  } catch (e) {
    throw new StorageUnavailable(tr("Chat needs this browser to keep site data, and it can't here. Chat can't start in this window."));
  }
  return true;
}

// One read-modify-write, read again and decided again on what is stored now when the index changed under it (at most 3 times). `work`
// answers `{ result, write }`: nothing is written when `write` is false.
async function readModifyWrite(work) {
  for (let tries = 0; ; tries += 1) {
    const { idx, raw } = loadIndex();
    const { result, write } = await work(idx);
    if (!write) return result;
    try {
      writeIfChanged(idx, raw);
      return result;
    } catch (e) {
      if (!(e instanceof IndexMoved) || tries >= 2) throw e;
    }
  }
}

export function findEntry(idx, u, d, k) {
  return idx.entries.find((e) => e.u === u && e.d === d && e.k === k) || null;
}

// The device a page of this account and kind uses: `in` or `out` (logged out here, its store kept), the most recently used.
export function currentEntry(idx, u, k) {
  let best = null;
  for (const e of idx.entries) {
    if (e.u !== u || e.k !== k || !LIVE.has(e.s)) continue;
    if (!best || (e.used || 0) > (best.used || 0)) best = e;
  }
  return best;
}

// A change of `s` or `sealed` is what other tabs act on, so it moves `gen`.
function setState(e, s) {
  if (e.s === s && !e.sealed) return;
  e.s = s;
  delete e.sealed;
  e.exp = null;
  e.gen = (e.gen || 0) + 1;
}

// Later pieces (the store's own key once it opened, `x` once chat unlocked) write one entry: under the lock, read again, written on a change.
export function markEntry(ref, fields) {
  return underSessionLock(() => readModifyWrite((idx) => {
    const e = findEntry(idx, ref.u, ref.d, ref.k);
    if (!e) return { result: null, write: false };
    const moves = "s" in fields || "sealed" in fields;
    Object.assign(e, fields);
    if (fields.sealed === undefined && "sealed" in fields) delete e.sealed;
    if (moves) e.gen = (e.gen || 0) + 1;
    return { result: { ...e }, write: true };
  }));
}

// The chat page unlocked this device (crypto.js ensureEncryptionReady answered true): its store now holds the account's secrets and
// room keys. `x` is a hint for later rescue work, never a reason to delete or keep a store (2.2, 2.4 go by what a store holds). Written
// once: a device already marked writes nothing, so other tabs hear nothing.
export async function markUnlocked(session) {
  const ref = session && session.entry;
  if (!ref || ref.k !== "chat" || ref.x) return null;
  const e = await markEntry(ref, { x: 1 });
  if (e) ref.x = 1;
  return e;
}

// One read-modify-write of the index under the session lock, for keeper.js (a renewal, a 409): `work(idx)` may await WebCrypto, never
// the network. Written only on a change, and never over an unreadable index (that waits for the site's next answer to a page start).
// When the index changed while `work` awaited (a Log out that ran without the lock), `work` runs again on what is stored now, so it sees
// the moved `epoch` and drops its answer: it must change nothing but the index it is given.
export function withIndex(work) {
  return underSessionLock(() => readModifyWrite(async (idx) => ({ result: await work(idx), write: !idx.bad })));
}

// --- the stores ----------------------------------------------------------------------------------------------------------------

// Whether the delete went: true on success, and when another tab still has it open (the delete stays queued and whatever opens it next
// waits behind it); false when it failed, so its index entry is kept (the plan's 5.4: a delete that threw leaves the entry `forget`).
function deleteDatabase(name) {
  return new Promise((resolve) => {
    try {
      const request = indexedDB.deleteDatabase(name);
      request.onsuccess = () => resolve(true);
      request.onerror = () => resolve(false);
      request.onblocked = () => resolve(true);
    } catch (e) {
      resolve(typeof indexedDB === "undefined");   // no IndexedDB here: nothing stored; one that threw: still there
    }
  });
}

// True only when both of the store's databases went.
export async function deleteStore(prefix) {
  const done = await Promise.all([deleteDatabase(`${prefix}::matrix-sdk-crypto`), deleteDatabase(`${prefix}::matrix-sdk-crypto-meta`)]);
  return done.every(Boolean);
}

// Whether a device's store is really here (2.6): a device id must never be used with a fresh store, which would make new identity keys
// for a device the chat server already holds keys of. Where the browser can't tell, it says the store is there.
export async function storeExists(prefix) {
  const name = `${prefix}::matrix-sdk-crypto`;
  if (typeof indexedDB === "undefined") return true;
  try {
    if (typeof indexedDB.databases === "function") {
      const list = await indexedDB.databases();
      return list.some((db) => db && db.name === name);
    }
  } catch (e) { /* the probe below */ }
  return new Promise((resolve) => {
    let missing = false;
    let request;
    try { request = indexedDB.open(name); } catch (e) { resolve(true); return; }
    request.onupgradeneeded = (event) => {
      if (event.oldVersion === 0) {
        missing = true;
        try { request.transaction.abort(); } catch (e) { /* aborted already */ }   // the probe never creates the database
      }
    };
    request.onsuccess = () => {
      try { request.result.close(); } catch (e) { /* closed */ }
      resolve(!missing);
    };
    request.onerror = () => resolve(!missing);
  });
}

// The crypto stores this browser has, by database name, or null when it can't list them (no `indexedDB.databases()`, or it threw).
const STORE_NAME = /^uqqus-(chat|call)-([^-:@\s]+)-([A-Za-z0-9_-]{1,64})::matrix-sdk-crypto$/;
// `ed` of an entry given back from a listed database: its store has held keys, but its device key isn't known. Set, so a start that
// finds the store gone replaces the device (2.6: never a fresh store for a device the chat server holds keys for); the store's first
// opening records the real key in its place (openCryptoStore).
export const LISTED_ED = "listed";

async function listStores() {
  if (typeof indexedDB === "undefined") return [];
  if (typeof indexedDB.databases !== "function") return null;
  try {
    const list = await indexedDB.databases();
    return (list || []).map((db) => db && db.name).filter((name) => typeof name === "string");
  } catch (e) {
    return null;
  }
}

// An index that replaced an unreadable one names none of the stores that one did: each store this browser has gets an entry again,
// signed out (`out`, no token), its account's from the database's name (every account here is on the site's one chat server). Its key
// id isn't known (`kid` null), so no key change ever deletes it; the caps count it as the least recently used. Its store is known to have
// held keys (`ed` LISTED_ED), so the store-missing check guards it as for any store opened before. The device this answer serves is
// left to `settle`, which makes its entry as for any other.
function addListedEntries(idx, names, boot, ans, kind) {
  const server = boot.mxid.slice(boot.mxid.indexOf(":") + 1);
  for (const name of names) {
    const found = STORE_NAME.exec(name);
    if (!found) continue;
    const [, k, local, d] = found;
    const u = `@${local}:${server}`;
    const p = `uqqus-${k}-${local}-${d}`;
    if (idx.entries.some((e) => e.p === p)) continue;
    if (u === boot.mxid && k === kind && d === ans.device_id) continue;
    idx.entries.push({ u, d, k, p, kid: null, hs: ans.homeserver_url, s: "out", x: 0, ed: LISTED_ED, made: 0, used: 0, gen: 0, exp: null,
                       life: null, why: null, fails: 0, bad: 0, empty: 0 });
  }
}

// Deleted only under the store's own lock, and only when it is free: every page with the store open holds it (its client is stopped
// before it lets go), so a store in use is never deleted, and one whose lock isn't free is tried again at the next start.
async function deleteIfFree(prefix, owner) {
  const done = await navigator.locks.request(STORE_LOCK + prefix, { ifAvailable: true }, async (lock) => {
    if (!lock) return false;
    // a store some page of this account came back to meanwhile (an entry `in` or `out`) is not deleted
    if (readIndex().entries.some((e) => e.p === prefix && e.u === owner && LIVE.has(e.s))) return false;
    return deleteStore(prefix);                  // a delete that failed keeps the entry, for the next start
  }).catch(() => false);
  if (!done) return;
  await underSessionLock(() => readModifyWrite((idx) => {
    idx.entries = idx.entries.filter((e) => e.p !== prefix);
    return { result: null, write: true };
  }));
}

// --- talking to the site -------------------------------------------------------------------------------------------------------

// The chat token POSTs to the site: the form key, no redirect followed (a sign-in that ended answers a redirect to /login, which would read
// as a 200), a timeout, and a stale form key refreshed once (site.js refreshFormkey: only this page's own account's). `signedOut` when the
// site sign-in is over, or the cookie is now another account's; status 0 when nothing answered, or when the refused key couldn't be asked
// for now (a 429, a 5xx, a timeout: refreshFormkey's TRANSIENT), which the keeper's backoff and a start's retry treat as the network's.
// Every other POST: site.js sitePost.
export async function postSite(boot, url, fields) {
  for (let attempt = 0; ; attempt += 1) {
    const body = new URLSearchParams();
    body.set("formkey", boot.formkey);
    for (const [name, value] of Object.entries(fields)) if (value !== undefined && value !== null) body.set(name, value);
    let resp;
    try {
      resp = await fetch(url, {
        method: "POST", credentials: "same-origin", redirect: "manual", signal: timeoutSignal(SITE_TIMEOUT),
        headers: { "Content-Type": "application/x-www-form-urlencoded" }, body,
      });
    } catch (e) {
      return { status: 0, body: {} };
    }
    if (refusedKey(resp)) {
      const refreshed = attempt === 0 ? await refreshFormkey(boot) : null;
      if (refreshed === REFRESHED) continue;
      if (refreshed === TRANSIENT) return { status: 0, body: {} };      // couldn't ask: retried, never read as signed out
      return { status: 401, body: {}, signedOut: true };
    }
    let data = {};
    try { data = (await resp.json()) || {}; } catch (e) { data = {}; }
    return { status: resp.status, body: data };
  }
}

function startFailed() {
  return tr("Could not start chat session");
}

// The site's answer to a page start, or what the page should do instead. Every refusal is thrown before anything stored is read for a
// change or deleted.
function answerOf(got) {
  const body = got.body || {};
  if (got.signedOut) throw new SignedOut(startFailed());
  if (got.status === 403 && body.need_password) throw new PasswordNeeded(body.error || startFailed());
  if (got.status === 403 && (body.age_locked || body.held)) throw new ChatRefused(body.error || startFailed());   // held: the account is on hold (helpers/quarantine.py)
  if (got.status === 200) {
    const ok = typeof body.user_id === "string" && typeof body.device_id === "string" && DEVICE.test(body.device_id)
      && typeof body.homeserver_url === "string" && typeof body.kid === "string" && typeof body.storage_key === "string"
      && typeof body.waiting_key === "string" && (body.access_token === null || typeof body.access_token === "string");
    if (ok) return body;
    throw new ChatUnavailable(startFailed(), { retry: false });
  }
  const retry = got.status === 0 || got.status === 429 || got.status >= 500;
  throw new ChatUnavailable(body.error || startFailed(), { retry });
}

// `mode=replace` mints nothing and answers the same whatever the id was (helpers/chat_tokens.py): the next ask names no device and the
// site mints exactly one. A replace that didn't reach the site leaves the old row on the chat server until the stale-device step signs it
// out (chat_signouts.prune_stale: 90 days unused, unless it is still the account's most recently used chat device, which is never
// signed out that way); the page carries on.
async function replaceDevice(boot, kind, deviceId, why) {
  const got = await postSite(boot, "/api/chat/token", { kind, mode: "replace", device_id: deviceId, why });
  if (got.signedOut) throw new SignedOut(startFailed());
}

export function renewBefore(life) {
  const ten = 10 * 60 * 1000;
  return life ? Math.min(ten, life / 3) : ten;
}

// A device minted for this page's start that the page then drops (it lost chat to another tab meanwhile, a Log out or another account
// came first, the device was taken out of use, storage wouldn't take it) is signed out with the site, best effort: left live, it could
// be the one a credential change in this browser spares (chat_signouts.this_sign_in: the same sign-in, the most recent) in place of
// the device the page that kept chat is using. A device this browser already held is never dropped here.
async function dropMinted(boot, kind, ans) {
  if (!ans || ans.new_device !== true) return;
  try {
    await replaceDevice(boot, kind, ans.device_id, "taken_over");
  } catch (e) {
    // not told (the site sign-in is over): left on the chat server until the stale-device step signs it out (chat_signouts.prune_stale:
    // 90 days unused, unless it is still the account's most recently used chat device, which is never signed out that way)
  }
}

// A sealed token with time left is used as it is: the page asks for keys only and the site makes no chat server call. One that doesn't
// expire (`exp` null: given before build step 5 made every token expire) is used the same way, and the site answers such a device's
// key-only start with a token instead (its row holds no refresh token), so it moves to tokens that expire (helpers/chat_tokens.py).
function wantFor(cur, now) {
  return cur && cur.s === "in" && cur.sealed && (cur.exp == null || cur.exp - now > renewBefore(cur.life)) ? "key" : "token";
}

// --- the legacy client's leftovers (2.3) ---------------------------------------------------------------------------------------

// Only after the site answered 200, so a page the site refuses (a QR sign-in, a stale page) touches nothing. Idempotent: the keys go at
// every start (a tab still running the old client may write them again), the old stores once, when this browser had no index yet.
function forgetLegacyKeys() {
  let found = false;
  for (const key of LEGACY_KEYS) {
    if (getItem(key) !== null) found = true;
    removeItem(key);
  }
  try {
    for (let i = localStorage.length - 1; i >= 0; i -= 1) {
      const key = localStorage.key(i);
      if (key && key.startsWith(FILTER_KEYS)) localStorage.removeItem(key);
    }
  } catch (e) { /* storage blocked */ }
  return found;
}

function deleteLegacyStores() {
  return Promise.all(LEGACY_STORES.map((prefix) => deleteStore(prefix)));
}

// --- the caps (2.4 cases 6 and 8) ----------------------------------------------------------------------------------------------

function newest(entries) {
  return entries.reduce((most, e) => Math.max(most, e.used || e.made || 0), 0);
}

// Stores beyond the caps: the signed-in account's are never evicted; that a store taken by a cap may hold keys is the caps' stated cost.
function overTheCaps(idx, owner) {
  const out = new Set();
  const byAccount = new Map();
  for (const e of idx.entries) {
    if (e.s === "forget") continue;
    if (!byAccount.has(e.u)) byAccount.set(e.u, []);
    byAccount.get(e.u).push(e);
  }
  for (const entries of byAccount.values()) {
    const removed = entries.filter((e) => e.s === "removed").sort((a, b) => (b.used || b.made || 0) - (a.used || a.made || 0));
    for (const e of removed.slice(MAX_REMOVED)) out.add(e.p);
  }
  const others = [...byAccount.keys()].filter((u) => u !== owner).sort((a, b) => newest(byAccount.get(a)) - newest(byAccount.get(b)));
  let accounts = byAccount.size;
  for (const u of others) {
    if (accounts <= MAX_ACCOUNTS) break;
    for (const e of byAccount.get(u)) out.add(e.p);
    accounts -= 1;
  }
  return out;
}

// --- ensureSession -------------------------------------------------------------------------------------------------------------

function probeStorage() {
  try {
    localStorage.setItem(PROBE_KEY, "1");
    localStorage.removeItem(PROBE_KEY);
  } catch (e) {
    throw new StorageUnavailable(tr("Chat needs this browser to keep site data, and it can't here. Chat can't start in this window."));
  }
}

// Section B (under the lock): the answer written into the index, unless a Log out, a deletion or another account came first. Returns the
// entry and its token, or `again`: "token" (a sealed token that won't open: ask for a new one) or "restart" (the device answered was
// taken out of use meanwhile).
async function settle(boot, kind, seen, ans, marks, box, tk, why, after, stillMine, listed) {
  // this page lost chat to another tab meanwhile: its answer is dropped, so it never marks the device the other tab holds `removed`
  if (stillMine && !stillMine()) throw new TakenOver(startFailed());
  const { idx, raw } = loadIndex();
  // a Log out or a deletion ran while the answer was on its way: drop it, nothing is written and the token never touches storage
  if (idx.epoch !== seen.epoch) throw new SignedOut(startFailed());
  if (idx.owner && idx.owner !== boot.mxid && idx.owner !== seen.owner) throw new SignedOut(startFailed());
  if (idx.bad || idx.unlisted) {
    // the index this browser had couldn't be read, or replaced one that couldn't: every store here gets its entry back before anything
    // is written over it, so none is lost track of (a deletion finds it, the caps count it, the ad hold sees it). A browser that can't
    // list its databases keeps the index `unlisted`, and the ad hold with it.
    if (listed) {
      addListedEntries(idx, listed, boot, ans, kind);
      delete idx.unlisted;
    } else {
      idx.unlisted = true;
    }
  }
  const now = Date.now();
  const deleting = hasLocks();                   // without Web Locks no store is ever deleted (gap 17)

  for (const m of marks) {
    const e = findEntry(idx, boot.mxid, m.d, kind);
    if (!e) continue;
    if (LIVE.has(e.s)) setState(e, "removed");
    // only a store that can never be opened again goes (2.4 case 1); a missing one's entry is kept `removed` (2.6)
    if (deleting && m.unopenable) after.add(e.p);
  }
  if (ans.previous && typeof ans.previous.device_id === "string") {
    const old = findEntry(idx, boot.mxid, ans.previous.device_id, kind);
    // kept, whatever its kind or `x`, until harvest.js finds it empty or has moved its keys to this account's current chat device (2.4)
    if (old && LIVE.has(old.s)) setState(old, "removed");
    if (typeof ans.previous.reason === "string") why = ans.previous.reason;
  }

  let e = findEntry(idx, boot.mxid, ans.device_id, kind);
  if (e && !LIVE.has(e.s)) return { again: "restart" };
  const fresh = !e;
  if (fresh && !why) {
    // a device this browser gave up on itself (openCryptoStore: its store wouldn't open), or one a running page's renewal found gone
    // (keeper.js: the site answered 409 with the reason, and the page started again naming no device, so the site has no `previous` to
    // give), says why to the next one, once (6.5)
    const told = idx.entries.filter((o) => o.u === boot.mxid && o.k === kind && (o.s === "broken" || o.s === "removed") && o.why)
      .sort((a, b) => (b.bad || b.used || 0) - (a.bad || a.used || 0))[0];
    if (told) {
      why = told.why;
      told.why = null;
    }
  }
  if (fresh) {
    e = {
      u: boot.mxid, d: ans.device_id, k: kind, p: prefixFor(kind, boot.mxid, ans.device_id), kid: ans.kid, hs: ans.homeserver_url,
      s: "in", x: 0, ed: null, made: now, used: now, gen: 0, exp: null, life: null, why: why || null, fails: 0, bad: 0, empty: 0,
    };
    idx.entries.push(e);
  }
  // one current device per account and kind: an older one is now `removed`, its store kept
  for (const other of idx.entries) {
    if (other !== e && other.u === boot.mxid && other.k === kind && LIVE.has(other.s)) setState(other, "removed");
  }

  let token = null;
  const seenGen = seen.cur && seen.cur.d === e.d ? seen.cur.gen : null;
  if (!fresh && e.s === "in" && e.sealed && seenGen !== null && e.gen !== seenGen) {
    // another page of this device sealed a token since this one asked: it is taken, never overwritten
    const opened = await unseal(tk, tokenAad(e), e.sealed);
    if (opened && typeof opened.t === "string") token = opened.t;
  }
  if (!token && ans.access_token) {
    token = ans.access_token;
    e.sealed = box;
    e.life = typeof ans.expires_in_ms === "number" && ans.expires_in_ms > 0 ? ans.expires_in_ms : null;
    e.exp = e.life ? now + e.life : null;        // this browser's own clock, so no skew
    e.gen = (e.gen || 0) + 1;
  } else if (!token) {
    const opened = e.sealed ? await unseal(tk, tokenAad(e), e.sealed) : null;
    if (!opened || typeof opened.t !== "string") return { again: "token" };
    token = opened.t;
  }
  if (e.s !== "in") { e.s = "in"; e.gen = (e.gen || 0) + 1; }
  e.kid = ans.kid;
  e.hs = ans.homeserver_url;
  if (!e.used || now - e.used > HOUR) e.used = now;   // at most hourly: a reload with a fresh token writes nothing (gap 20)
  idx.owner = boot.mxid;

  // another account's entries keep their stores (signing back in takes them up again) but never a token
  for (const o of idx.entries) {
    if (o.u === boot.mxid) continue;
    if (o.s === "in" || o.sealed) {
      if (o.s === "in") o.s = "out";
      delete o.sealed;
      o.exp = null;
      o.gen = (o.gen || 0) + 1;
    }
  }
  if (deleting) {
    // a store whose key can never be derived again (the site's secret was rotated) is never opened again (2.4 case 1); a current device
    // of another kind or account is left to its own page, which replaces it with the site first
    for (const o of idx.entries) if (o.kid && o.kid !== ans.kid && (o.s === "removed" || o.s === "broken")) after.add(o.p);
    // a store given up on 30 days ago (it wouldn't open, or the harvest failed on it 3 times): nobody knows why, and there was time to look
    // at it (2.4 case 4)
    for (const o of idx.entries) if (o.s === "broken" && o.bad && now - o.bad > BROKEN_KEEP) after.add(o.p);
    for (const p of overTheCaps(idx, boot.mxid)) after.add(p);
    for (const o of idx.entries) if (o.s === "forget") after.add(o.p);           // an account deleted here (5.4)
  }
  const legacyHere = forgetLegacyKeys();
  if (deleting && (legacyHere || raw === null || idx.bad)) after.legacy = true;
  delete idx.bad;
  try {
    writeIfChanged(idx, raw);
  } catch (err) {
    if (!(err instanceof IndexMoved)) throw err;
    // the index changed while this section awaited an unseal (a Log out or a deletion that couldn't wait for the lock, another page of a
    // browser with no Web Locks): nothing prepared before it is written. A Log out or another account drops the answer; anything else
    // starts the page again from what is stored now.
    const stored = loadIndex().idx;
    if (stored.epoch !== seen.epoch || (stored.owner && stored.owner !== boot.mxid && stored.owner !== seen.owner)) throw new SignedOut(startFailed());
    after.clear();
    delete after.legacy;
    return { again: "restart" };
  }
  return { entry: { ...e }, token };
}

// The chat session of this page: `kind` is "chat" (the chat page and its frames) or "call" (the call window, a Space joining a call).
// What the browser holds is good only for the account signed in now (boot.mxid, from the page); another account's entries are kept,
// locked, without their tokens. Throws StorageUnavailable, PasswordNeeded, ChatRefused, ChatUnavailable, SignedOut or TakenOver.
// `stillMine()`: whether this page still holds chat (the chat page's `uqqus-matrix`), asked under the lock before the answer is written.
export async function ensureSession(boot, kind = "chat", { stillMine = null } = {}) {
  if (!KINDS.includes(kind)) throw new Error("not a chat kind");
  probeStorage();                                 // before the site is asked: no device for a page that couldn't remember it
  const mintKey = mintKeyOfThisPage();
  const marks = [];
  let why = null;

  for (let restart = 0; restart < 3; restart += 1) {
    // Section A (locked): what this page will ask for
    const seen = await underSessionLock(() => {
      const { idx } = loadIndex();
      const cur = currentEntry(idx, boot.mxid, kind);
      return { epoch: idx.epoch, owner: idx.owner, cur: cur ? { ...cur } : null, unlisted: !!(idx.bad || idx.unlisted) };
    });
    let cur = seen.cur;
    let ask = { device_id: cur ? cur.d : null, want: wantFor(cur, Date.now()) };

    for (let round = 0; round < 4; round += 1) {
      // the network (unlocked)
      const fields = { kind, mode: "boot", want: ask.want, mint_key: mintKey };
      if (ask.device_id) fields.device_id = ask.device_id;
      const ans = answerOf(await postSite(boot, "/api/chat/token", fields));
      if (ans.user_id !== boot.mxid) throw new SignedOut(startFailed());
      const served = cur && ans.device_id === cur.d;
      if (served && round < 3 && cur.kid && ans.kid !== cur.kid) {
        // the site's secret was rotated: this device's store can never be opened again (1.3). The device goes and is never reused.
        await replaceDevice(boot, kind, cur.d, "key_changed");
        marks.push({ d: cur.d, unopenable: true });
        why = "key_changed";
        cur = null;
        ask = { device_id: null, want: "token" };
        continue;
      }
      if (served && round < 3 && cur.ed && !(await storeExists(cur.p))) {
        // its store went (evicted by the browser, cleared by hand): never a fresh store for a device whose keys the server holds (2.6)
        await replaceDevice(boot, kind, cur.d, "store_missing");
        marks.push({ d: cur.d });
        why = "store_missing";
        cur = null;
        ask = { device_id: null, want: "token" };
        continue;
      }

      const storeKey = b64urlDecode(ans.storage_key);
      let done = null;
      let tk = null;
      let wk = null;
      try {
        if (storeKey.length !== 32) throw new ChatUnavailable(startFailed(), { retry: false });
        const waitingRaw = b64urlDecode(ans.waiting_key);
        try {
          tk = await tokenKey(storeKey);
          wk = await waitingKey(waitingRaw);
        } finally {
          waitingRaw.fill(0);
        }
        const box = ans.access_token ? await seal(tk, tokenAad({ u: boot.mxid, d: ans.device_id, k: kind }), { t: ans.access_token }) : null;

        // the databases, listed before the lock, when the index has to be given back the stores it no longer names
        const listed = seen.unlisted ? await listStores() : null;

        // Section B (locked)
        const after = new Set();
        done = await underSessionLock(() => settle(boot, kind, seen, ans, marks, box, tk, why, after, stillMine, listed));
        if (!done.again) {
          // after the lock: the stores that go (2.4), each under its own lock and only when it is free
          if (after.legacy) await deleteLegacyStores();
          for (const prefix of after) await deleteIfFree(prefix, boot.mxid);
        }
      } catch (err) {
        storeKey.fill(0);              // a start that fails (a Log out meanwhile, storage that won't take the index) never drops the key unzeroed (1.2)
        await dropMinted(boot, kind, ans);
        throw err;
      }
      if (done.again === "token" && ask.want !== "token") {
        storeKey.fill(0);
        ask = { device_id: ans.device_id, want: "token" };        // a sealed token that won't open: a new one for the same device, once
        continue;
      }
      if (done.again) {
        storeKey.fill(0);
        await dropMinted(boot, kind, ans);
        break;                                                    // to section A again
      }

      const e = done.entry;
      return {
        kind, user_id: ans.user_id, device_id: ans.device_id, homeserver_url: ans.homeserver_url, prefix: e.p,
        accessToken: done.token, exp: e.exp, life: e.life, gen: e.gen, storeKey, tokenKey: tk, waitingKey: wk, why: e.why || null,
        entry: { u: e.u, d: e.d, k: e.k, p: e.p, gen: e.gen, ed: e.ed, x: e.x },
        boot,                                                     // its form key, refreshed in place (postSite), for the page's later asks
      };
    }
  }
  throw new ChatUnavailable(startFailed(), { retry: true });
}

// --- waiting first messages, sealed under the account's waiting key ------------------------------------------------------------

// Read once when chat starts. A box that won't open, or another account's, goes: the same loss as its removal at Log out.
export async function readWaiting(session) {
  const raw = getItem(WAITING_KEY);
  const { list, ok } = await waitingIn(session, raw);
  if (!ok && getItem(WAITING_KEY) === raw) removeItem(WAITING_KEY);
  return list;
}

// The list a stored box holds, or `ok` false for a box that won't open (or another account's: `theirs`, a box sealed for another
// account, which only a page's start may remove, never a change, see updateWaiting).
async function waitingIn(session, raw) {
  if (!raw) return { list: {}, ok: true };
  let box = null;
  try { box = JSON.parse(raw); } catch (e) { box = null; }
  if (box && box.v === 2 && typeof box.u === "string" && box.u !== session.user_id) return { list: {}, ok: false, theirs: true };
  const list = box && box.v === 2 && box.u === session.user_id ? await unseal(session.waitingKey, waitingAad(session.user_id), box) : null;
  if (!list || typeof list !== "object" || Array.isArray(list)) return { list: {}, ok: false };
  return { list, ok: true };
}

let waitingWrites = Promise.resolve();

async function storeWaiting(session, list) {
  if (!list || !Object.keys(list).length) {
    removeItem(WAITING_KEY);
    return;
  }
  const box = await seal(session.waitingKey, waitingAad(session.user_id), list);
  localStorage.setItem(WAITING_KEY, JSON.stringify({ v: 2, u: session.user_id, iv: box.iv, ct: box.ct }));
}

// Written in the order asked (each seal is asynchronous); an empty list removes the key.
export function writeWaiting(session, list) {
  waitingWrites = waitingWrites.then(async () => {
    try { await storeWaiting(session, list); } catch (e) { /* not kept: sent when possible */ }
  });
  return waitingWrites;
}

// One change to the waiting list, made on what is stored NOW (read and unsealed again), never on a page's own older copy: one tab's
// list never replaces messages another tab added since, and a room's messages go only as they are sent. Changes run in the order asked;
// a room left empty goes; nothing is written when nothing changed. Answers the list as it now is (index.js keeps it to draw from).
// Compare-and-set, like the index: written only while what is stored is still what this change read, so a Log out (which removes the
// list) or another tab's write that came while it unsealed or sealed is never undone; the change is then made again on what is stored now
// (`change` may run more than once, each time on a fresh list), at most 3 times. A box sealed for another account is never written over
// or removed by a change: another account signed in here and opened chat, so this page has stopped, and a send of its that ended late
// taking its message off the list must not take that account's waiting messages with it (all_js.js keeps them too). Answers {} then.
export function updateWaiting(session, change) {
  const next = waitingWrites.then(async () => {
    for (let tries = 0; tries < 3; tries += 1) {
      const raw = getItem(WAITING_KEY);
      const { list, ok, theirs } = await waitingIn(session, raw);
      if (theirs) return {};
      const before = JSON.stringify(list);
      change(list);
      for (const room of Object.keys(list)) if (!Array.isArray(list[room]) || !list[room].length) delete list[room];
      const changed = JSON.stringify(list) !== before || !ok;
      const box = changed && Object.keys(list).length ? await seal(session.waitingKey, waitingAad(session.user_id), list) : null;
      if (getItem(WAITING_KEY) !== raw) continue;
      if (!changed) return list;
      if (box) localStorage.setItem(WAITING_KEY, JSON.stringify({ v: 2, u: session.user_id, iv: box.iv, ct: box.ct }));
      else removeItem(WAITING_KEY);
      return list;
    }
    throw new Error("the waiting list kept changing");
  });
  waitingWrites = next.catch(() => {});
  return next;
}

// --- opening the store ---------------------------------------------------------------------------------------------------------

// The store was given up on (it won't open with its own key, or it isn't this device's): the entry is `broken`, the site was told
// (`mode=replace`, which mints nothing), and the page starts again once (keeper.js), when the site mints exactly one new device.
export class StoreReplaced extends Error {}
// Chat can't start in this browser: a second failure of one store in this tab, or anything else. Shown with "Try again", never an
// endless "Loading…".
export class StoreFailed extends Error {}

// The wasm's own words for a store opened with a key that isn't its own, or holding another account (today's STORE_REFUSED missed them).
const UNPICKLING = /failed to be decrypted while unpickling|doesn't match the account/;
const RETRY_KEY = "uqqus_store_retry:";

function couldntStart() {
  return tr("Chat couldn't start in this browser.");
}

function retriedInTab(prefix) {
  try { return sessionStorage.getItem(RETRY_KEY + prefix) !== null; } catch (e) { return false; }
}

function markRetried(prefix) {
  try { sessionStorage.setItem(RETRY_KEY + prefix, String(Date.now())); } catch (e) { /* the reload budget still bounds it */ }
}

// The store is NOT deleted: kept as `broken`, since nobody knows why it won't open (a start 30 days later deletes it, 2.4 case 4). Once
// per store and tab.
async function giveUp(session, why) {
  if (retriedInTab(session.prefix)) throw new StoreFailed(couldntStart());
  markRetried(session.prefix);
  if (session.entry) await markEntry(session.entry, { s: "broken", why, bad: Date.now(), sealed: undefined, exp: null });
  try {
    await replaceDevice(session.boot, session.kind, session.device_id, why);
  } catch (e) {
    // not told: the next start names no device anyway, and the old row is left on the chat server until the stale-device step signs it
    // out (chat_signouts.prune_stale: 90 days unused, unless it is still the account's most recently used chat device, which is never
    // signed out that way)
  }
  throw new StoreReplaced(why);
}

// The store, opened with its key. The key is zeroed as soon as the store has it, opened or not. The first opening records the device's
// public ed25519 key (also in place of LISTED_ED); every later one checks it (2.6), so a store that isn't this device's own is never used.
export async function openCryptoStore(client, session) {
  let failure = null;
  try {
    await client.initRustCrypto({ storageKey: session.storeKey, cryptoDatabasePrefix: session.prefix });
  } catch (e) {
    failure = e;
  } finally {
    if (session.storeKey && typeof session.storeKey.fill === "function") session.storeKey.fill(0);
  }
  if (failure) {
    if (!UNPICKLING.test(String((failure && failure.message) || ""))) throw new StoreFailed(couldntStart());
    await giveUp(session, "store_unopenable");
  }
  let keys = null;
  try { keys = await client.getCrypto().getOwnDeviceKeys(); } catch (e) { keys = null; }
  const ed = keys && keys.ed25519;
  if (!ed || !session.entry) return;
  if (!session.entry.ed || session.entry.ed === LISTED_ED) {
    try {
      await markEntry(session.entry, { ed });
      session.entry.ed = ed;
    } catch (e) { /* noted at the next opening */ }
    return;
  }
  if (ed !== session.entry.ed) {
    try { client.stopClient(); } catch (e) { /* it closes the store it opened */ }
    await giveUp(session, "store_unopenable");
  }
}
