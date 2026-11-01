// Browser-owned Matrix session. The access token + device_id live ONLY in
// this browser's localStorage - the server never persists either (see
// ruqqus/helpers/matrix_client.py's module docstring). Reusing the same
// device_id across calls keeps this browser as a single stable Olm/Megolm
// device instead of minting a new one every page load.

// A member's Log out link leaves `user_id` and `device_id` here and takes the token (all_js.js logOut): see ensureSession.
export const STORAGE_KEY = "ruqqus_chat_session";
// The call window (call.js) is a second device of the same account, with its own crypto store, so a call never
// fights the chat page over one device's keys.
export const CALL_STORAGE_KEY = "ruqqus_call_session";
// First messages that wait in this browser until the other person's chat is ready (index.js).
export const WAITING_KEY = "uqqus_chat_waiting";
// Which crypto store goes with which session: matrix-js-sdk's own default name for the chat page, and call.js's and
// space.js's. all_js.js's logOut() has the session keys too (it can't import this; tests/test_chat_session.py keeps them equal).
const CRYPTO_PREFIX = { [STORAGE_KEY]: "matrix-js-sdk", [CALL_STORAGE_KEY]: "uqqus-call" };
// One page at a time reads, makes and clears what is stored here, across every tab and window of this browser (a browser without Web Locks goes without).
const SESSION_LOCK = "uqqus-chat-session";
// The SDK's refusal of a store made for another device or account: nothing else would ever clear it.
const STORE_REFUSED = /doesn't match the account/;
const RETRY_PREFIX = "uqqus_store_retry:";

// A QR sign-in hasn't given its password yet (helpers/qr_login.py): the site won't hand out a chat session. Nothing stored here is
// touched, whoever's it is: this is what each page shows instead of hanging, and the chat page shows the password form anyway.
export class PasswordNeeded extends Error {}

function underSessionLock(work) {
  return globalThis.navigator && navigator.locks ? navigator.locks.request(SESSION_LOCK, work) : work();
}

export function loadSession(storageKey = STORAGE_KEY) {
  try {
    const raw = localStorage.getItem(storageKey);
    return raw ? JSON.parse(raw) : null;
  } catch (e) {
    return null;
  }
}

function saveSession(session, storageKey = STORAGE_KEY) {
  try {
    localStorage.setItem(storageKey, JSON.stringify(session));
  } catch (e) {
    // localStorage unavailable (private mode / blocked) - session just
    // won't persist across reloads; nothing else to do.
  }
}

function deleteDatabase(name) {
  return new Promise((resolve) => {
    try {
      const request = indexedDB.deleteDatabase(name);
      request.onsuccess = request.onerror = () => resolve();
      // another tab still has it open: the delete stays queued and whatever opens it next waits behind it, so this
      // never reads the old keys; it just doesn't hold the page up until that tab lets go
      request.onblocked = () => resolve();
    } catch (e) {
      resolve();                                 // no IndexedDB here: nothing stored
    }
  });
}

async function forgetDevice(storageKey) {
  try { localStorage.removeItem(storageKey); } catch (e) { /* storage blocked */ }
  const prefix = CRYPTO_PREFIX[storageKey];
  await Promise.all([deleteDatabase(`${prefix}::matrix-sdk-crypto`), deleteDatabase(`${prefix}::matrix-sdk-crypto-meta`)]);
}

// What the browser holds is good only for the account signed in now: boot.mxid is that account's Matrix id, from the
// page. Anything else (the Log out link keeps a member's device here without its token, a cookie that ran out passes no
// link at all) belongs to whoever signed in before and is never used, or their keys would read and write under the
// new account's name. The same account takes its device up again, so its keys are still here and nothing is lost.
export function ensureSession(boot, storageKey = STORAGE_KEY) {
  // Two pages opening at once would each make a device: one's session would end up beside the other's store, which nothing can open.
  // Whoever comes second waits here and finds the first one's session.
  return underSessionLock(() => sessionFor(boot, storageKey));
}

async function sessionFor(boot, storageKey) {
  const existing = loadSession(storageKey);
  const mine = existing && existing.user_id === boot.mxid ? existing : null;
  if (mine && mine.access_token) {
    return mine;
  }

  const body = new URLSearchParams();
  body.set("formkey", boot.formkey);
  if (mine && mine.device_id) {
    body.set("device_id", mine.device_id);
  }
  const resp = await fetch("/api/chat/token", {
    method: "POST",
    credentials: "same-origin",
    headers: { "Content-Type": "application/x-www-form-urlencoded" },
    body,
  });
  if (!resp.ok) {
    const refused = await resp.json().catch(() => ({}));
    if (refused.need_password) throw new PasswordNeeded(refused.error);
    throw new Error(window.tr("Could not start chat session"));
  }
  const data = await resp.json();

  // Only once the site has accepted this page's form key: a page left open from before a sign-out has none that
  // counts, so it can't wipe the session of whoever is signed in now. A crypto store can't serve any device but the
  // one it was made for (the SDK refuses it), so a new device always starts without one, whoever's it was.
  const otherKey = storageKey === STORAGE_KEY ? CALL_STORAGE_KEY : STORAGE_KEY;
  const other = loadSession(otherKey);
  const otherIsSomeoneElses = other && other.user_id !== boot.mxid;
  if (!mine || mine.device_id !== data.device_id) await forgetDevice(storageKey);
  if (otherIsSomeoneElses) await forgetDevice(otherKey);
  if ((existing && !mine) || otherIsSomeoneElses) {
    try { localStorage.removeItem(WAITING_KEY); } catch (e) { /* storage blocked */ }
  }

  const session = {
    homeserver_url: data.homeserver_url,
    user_id: data.user_id,
    access_token: data.access_token,
    device_id: data.device_id,
  };
  saveSession(session, storageKey);
  return session;
}

// A store that refuses this session is cleared and the page starts again once, as a new device (the chat page then asks for what any
// new device asks for). The lock means this page's own two tabs can't get here any more; a browser that got here before it did, or one with
// no Web Locks, can. Another page may have made a new device since this client was made: that one is left alone and this page takes it up.
export async function openCryptoStore(client, storageKey = STORAGE_KEY, options = undefined) {
  const retry = RETRY_PREFIX + storageKey;
  try {
    await client.initRustCrypto(options);
    try { sessionStorage.removeItem(retry); } catch (e) { /* storage blocked */ }
  } catch (e) {
    let triedAlready = true;                         // storage blocked: it can't be counted, so it is never looped
    try { triedAlready = !!sessionStorage.getItem(retry); } catch (blocked) { /* as above */ }
    if (!STORE_REFUSED.test(String(e && e.message)) || triedAlready) throw e;
    sessionStorage.setItem(retry, "1");
    await underSessionLock(async () => {
      const stored = loadSession(storageKey);
      if (!stored || stored.device_id === client.getDeviceId()) await forgetDevice(storageKey);
    });
    window.location.reload();
    await new Promise(() => {});                     // nothing more happens on a page that is going away
  }
}
