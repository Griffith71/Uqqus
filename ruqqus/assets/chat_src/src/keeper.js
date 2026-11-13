// A running chat client's session (the chat plan's 3.3, 4.4, 6.4 and 6.6): it keeps the access token alive, follows what other tabs do
// to this device in the index, holds the device's store lock, and says what happened when chat stops or can't start.
//
// - fetchFn: the client's fetch. A 401 M_UNKNOWN_TOKEN for the token in use is renewed through the site (never Synapse directly: the
//   refresh token stays on the server) and the same 401 handed back, so the SDK retries with the new token. A token this keeper itself
//   obtained under a minute ago that is refused too is never renewed again, so the SDK's retry can't loop.
// - renew: adopt a newer token another page of this device sealed into the index, or ask the site (`mode=renew`). The index lock is
//   held only to read and to write, never across the site's answer or a backoff, and a Log out (`epoch`) in between drops the answer.
// - watchIndex: another tab logs out, deletes the account here, signs another account in, or replaces this device: this page stops.
//   A rotation by another page (a newer `gen`) is taken without a reload.
// - a page another tab took chat over from (holdChatLock: `stopped`, not `ending`) acts on nothing afterwards: no renewal asks the site
//   after an await, no outcome is acted on, and end() returns at once, so it never reloads and takes chat back.
// - reloadOnce: at most 2 reloads a minute per tab and frame role. A frame in the side panel or the hidden Send-in-chat frame never
//   shows a note of its own: it tells its parent (`uqqus-chat:stopped`) and stops.
// - the store lock `uqqus-store:<prefix>`: taken before the store opens, let go only after the client that opened it was stopped.
//
// Nothing here imports matrix-js-sdk (makeClient is handed it), so it runs under node with fakes (tests/test_chat_keeper.py).

import { tokenAad, seal, unseal } from "./seal.js";
import {
  INDEX_KEY, STORE_LOCK, ChatRefused, ChatUnavailable, PasswordNeeded, SignedOut, StorageUnavailable, StoreFailed, StoreReplaced, TakenOver,
  findEntry, forgetStoreKey, postSite, readIndex, renewBefore, sitePost, withIndex,
} from "./token.js";

// The one request the site makes for the member (routes/chat_key_reset_pages.py): replacing cross-signing keys that already exist,
// which the chat server only takes from the appservice. Only this path, only a POST, only while a grant is armed.
export const KEY_UPLOAD_PATH = "/_matrix/client/v3/keys/device_signing/upload";
export const SIGNING_KEYS_ROUTE = "/api/chat/signing_keys";

export const STOPPED = "uqqus-chat:stopped";      // a frame tells its parent it stopped (side_panels.js, share_chat.js, guild/chat.html)
export const NOTICE = "uqqus-chat:notice";        // a frame's line for its parent to show (a new device, a lost connection)
export const RELOADS_KEY = "uqqus_chat_reloads:";
export const RELOADS_PER_MINUTE = 2;
export const FRESH_MS = 60 * 1000;                // a token obtained this recently and refused again is never renewed again
const ADOPT_LEFT = 2 * 60 * 1000;                 // another page's token is taken only with this long left
const RENEW_GIVE_UP = 2 * 60 * 1000;              // a renewal a 401 asked for gives up after about this long
const BACKOFF = [2000, 5000, 15000, 30000, 60000];
const START_WAITS = [2000, 5000, 15000, 30000];   // a start the site couldn't answer is tried again after these
const LIVE = new Set(["in", "out"]);

const tr = (text) => (globalThis.window && typeof window.tr === "function" ? window.tr(text) : text);
const trn = (one, many, n) => (globalThis.window && typeof window.trn === "function"
  ? window.trn(one, many, n) : (n === 1 ? one : many).replace("%(num)s", String(n)));

// Messages this page held to send once chat unlocked (send_gate.js), lost because the page stops: said, never dropped without a word.
export function unsentLine(n) {
  return trn("A message you wrote here wasn't sent: chat stopped in this tab before it could go.",
    "%(num)s messages you wrote here weren't sent: chat stopped in this tab before they could go.", n);
}

function hasLocks() {
  return !!(globalThis.navigator && navigator.locks && typeof navigator.locks.request === "function");
}

function wait(ms) {
  return new Promise((resolve) => setTimeout(resolve, ms));
}

// --- the page's role -------------------------------------------------------------------------------------------------------------

// `top`: the chat page, the call window, a Space page. `single`: the guild Chat tab's frame, shown on its page as its own content, so
// it behaves like `top`. `panel`: the side panel's frame (?embed=1). `share`: the hidden Send-in-chat frame (?embed=1&share=1).
export function frameRole(win = globalThis.window) {
  try {
    if (!win || !win.parent || win.parent === win) return "top";
  } catch (e) {
    return "top";
  }
  const query = new URLSearchParams((win.location && win.location.search) || "");
  if (query.get("single")) return "single";
  if (query.get("share")) return "share";
  return "panel";
}

const quiet = (role) => role === "panel" || role === "share";

function tellParent(message) {
  try { window.parent.postMessage(message, window.location.origin); } catch (e) { /* no parent to tell */ }
}

// What a page shows, by role: a frame in the panel or the share sheet hands everything to its parent instead (3.3).
export function uiForRole(role, ui) {
  const page = ui || {};
  if (!quiet(role)) {
    return {
      status: (text) => page.status && page.status(text || ""),
      stop: (text, options = {}) => page.stop && page.stop(text, options),
      banner: (text, options = {}) => page.banner && page.banner(text, options),
      notice: (text) => page.notice && page.notice(text),
    };
  }
  return {
    status: () => {},
    stop: (text, options = {}) => tellParent({ type: STOPPED, why: options.why || "stopped", text: text || "" }),
    banner: (text, options = {}) => tellParent({ type: NOTICE, why: options.why || "", text: text || "" }),
    notice: (text) => tellParent({ type: NOTICE, why: "", text: text || "" }),
  };
}

// At most RELOADS_PER_MINUTE reloads a minute for this tab and role (gap 21). "reload": reloading now; "posted": a quiet frame told
// its parent instead (the parent makes a new frame when it is next shown); "over": over budget, the page says chat stopped.
export function reloadOnce(why, role = frameRole(), { delay = 0, now = Date.now } = {}) {
  if (quiet(role)) {
    tellParent({ type: STOPPED, why: why || "stopped", text: "" });
    return "posted";
  }
  const key = RELOADS_KEY + role;
  let times = [];
  try { times = JSON.parse(sessionStorage.getItem(key) || "[]"); } catch (e) { times = []; }
  if (!Array.isArray(times)) times = [];
  const at = now();
  times = times.filter((t) => typeof t === "number" && at - t < 60 * 1000);
  if (times.length >= RELOADS_PER_MINUTE) {
    // the guild Chat tab's frame says so itself; its page makes it again when the member comes back to the tab (guild/chat.html)
    if (role === "single") tellParent({ type: STOPPED, why: why || "stopped", text: "" });
    return "over";
  }
  times.push(at);
  try { sessionStorage.setItem(key, JSON.stringify(times)); } catch (e) { /* without the budget a reload still happens once */ }
  const go = () => window.location.reload();
  if (delay) setTimeout(go, delay); else go();
  return "reload";
}

function stoppedHere(ui) {
  ui.stop(tr("Chat stopped in this tab."), { why: "stopped", action: tr("Reload"), onAction: () => window.location.reload() });
}

// --- starting: what the page shows when chat can't start (6.6) -------------------------------------------------------------------

// `begin()` is the page's own ensureSession(boot, kind). Answers the session, or null when the page has been told what to show (or is
// reloading). A site or network that couldn't answer is tried again after 2, 5, 15 and 30 s, then offered with a Retry button.
// `stillMine()`: false once another tab took chat over (index.js holdChatLock), whose note this page then shows: nothing more is drawn
// over it and the site isn't asked again, before a status line, a wait, a retry or a Retry button, and after one is pressed.
export async function startSession(begin, ui, { role = frameRole(), sleep = wait, onPassword = null, retry = true, stillMine = () => true } = {}) {
  const shown = uiForRole(role, ui);
  let tries = 0;
  for (;;) {
    if (!stillMine()) return null;
    try {
      const session = await begin();
      shown.status("");
      return session;
    } catch (e) {
      if (!stillMine()) return null;              // lost to another tab while it asked: nothing is drawn over its note, nothing reloads
      if (e instanceof PasswordNeeded) {
        // a page open from before a QR sign-in: drawn again, it is the password form
        if (onPassword) onPassword(e); else if (quiet(role)) shown.stop(e.message, { why: "password" }); else window.location.reload();
        return null;
      }
      if (e instanceof TakenOver) return null;    // another tab took chat over: the page says so itself (index.js holdChatLock)
      if (e instanceof SignedOut) {
        if (reloadOnce("signed_out", role) === "over") stoppedHere(shown);
        return null;
      }
      if (e instanceof StorageUnavailable || e instanceof ChatRefused) {
        shown.stop(e.message, { why: e instanceof ChatRefused ? "refused" : "storage" });
        return null;
      }
      if (e instanceof ChatUnavailable && e.retry && tries < START_WAITS.length) {
        shown.status(tr("Connecting…"));
        await sleep(START_WAITS[tries]);
        tries += 1;
        continue;
      }
      const text = e instanceof ChatUnavailable ? tr("Chat couldn't connect.") : tr("Chat couldn't start in this browser.");
      if (quiet(role) || !retry) {
        shown.stop(text, { why: "unavailable" });
        return null;                              // the parent makes a new frame when it is next shown; a call window is closed
      }
      await new Promise((resolve) => shown.stop(text, { why: "unavailable", action: tr("Retry"), onAction: resolve }));
      if (!stillMine()) return null;
      shown.status(tr("Connecting…"));
      tries = 0;
    }
  }
}

// --- the keeper ------------------------------------------------------------------------------------------------------------------

function urlOf(input) {
  if (input && typeof input === "object" && typeof input.url === "string") return input.url;     // a Request
  return String(input);                                                                           // a URL or a string
}

function bearer(headers) {
  if (!headers) return null;
  let value = null;
  if (typeof headers.get === "function") value = headers.get("Authorization");
  else value = headers.Authorization || headers.authorization || null;
  const match = /^Bearer (.+)$/.exec(value || "");
  return match ? match[1] : null;
}

export class SessionKeeper {
  constructor(session, { ui = null, role = frameRole(), fetch: fetchImpl = null, now = Date.now, sleep = wait, random = Math.random } = {}) {
    this.session = session;
    this.boot = session.boot;
    this.me = { u: session.user_id, d: session.device_id, k: session.kind, gen: session.gen || 0 };
    this.tokenKey = session.tokenKey;
    this.exp = session.exp == null ? null : session.exp;
    this.life = session.life == null ? null : session.life;
    this.role = role;
    this.ui = uiForRole(role, ui);
    this.fetchImpl = fetchImpl || ((input, init) => globalThis.fetch(input, init));
    this.now = now;
    this.sleep = sleep;
    this.random = random;
    this.client = null;
    this.reason = null;                           // why the session can't go on, once a renewal decided it (6.2 reads it)
    this.refusal = null;                          // the site's own refusal (need_password, age_locked)
    this.obtained = new Map();                    // token -> when this keeper renewed or adopted it
    this.renewing = null;
    this.timer = null;
    this.storeHold = null;
    this.storeWait = null;
    this.storeOpening = null;
    this.keyWork = null;                          // the account's keys being made or reset on the server (crypto.js `critical`)
    this.closing = null;
    this.stopped = false;
    this.ending = false;
    this.onLost = null;                           // watchSignedOut sets it: what a renewal that failed for good does (6.2)
    this.unsent = null;                           // the page's count of messages still waiting to be sent (index.js: send_gate.js)
    this.keysPending = null;                      // a promise while new keys' copies are still being saved (index.js: crypto.js keysPending)
    this.onStopped = null;                        // told once when the page stops (index.js: the unlock boxes close, crypto.js)
    this.keyResetGrant = null;                    // a Start over's grant, for the one key upload it was asked for (crypto.js startOver)
    this.stopSignal = new Promise((resolve) => { this.signalStopped = resolve; });   // whenStopped(): firstSync's way out
    this.fetchFn = (input, init) => this.fetch(input, init);
    const base = String(session.homeserver_url || "");
    this.base = base.endsWith("/") ? base : base + "/";
  }

  freshlyObtained(token, within = FRESH_MS) {
    const at = this.obtained.get(token);
    return at !== undefined && this.now() - at < within;
  }

  // Start over (crypto.js): the grant the member's password bought, for the next key upload only. Held in this page's memory, never stored.
  armKeyReset(grant) {
    this.keyResetGrant = grant || null;
  }

  disarmKeyReset() {
    this.keyResetGrant = null;
  }

  isKeyUpload(input, init) {
    if (String((init && init.method) || (input && input.method) || "GET").toUpperCase() !== "POST") return false;
    let url;
    try { url = new URL(urlOf(input), globalThis.location ? globalThis.location.href : undefined); } catch (e) { return false; }
    let base;
    try { base = new URL(this.base, globalThis.location ? globalThis.location.href : undefined); } catch (e) { return false; }
    return url.origin === base.origin && url.pathname === base.pathname.replace(/\/$/, "") + KEY_UPLOAD_PATH;
  }

  // The armed upload, sent to the site with its grant instead of to the chat server, and answered as the chat server would have.
  // The body the SDK sends with an `auth` (a sign-in stage it was asked for) goes without it: the site does that part. The grant is
  // used once; one the site put back (the chat server was down) is armed again for the SDK's retry.
  async uploadThroughSite(init) {
    const grant = this.keyResetGrant;
    this.keyResetGrant = null;
    let keys = null;
    try {
      keys = JSON.parse(typeof init.body === "string" ? init.body : "");
    } catch (e) {
      keys = null;
    }
    const json = (status, body) => new Response(JSON.stringify(body || {}), { status, headers: { "Content-Type": "application/json" } });
    if (!keys || typeof keys !== "object") return json(400, { errcode: "M_BAD_JSON", error: "Not a key upload." });
    delete keys.auth;
    let resp;
    try {
      resp = await sitePost(this.boot, SIGNING_KEYS_ROUTE, { grant, keys: JSON.stringify(keys) });
    } catch (e) {
      this.keyResetGrant = grant;                 // nothing reached the site: the grant is still good
      throw new TypeError("chat offline");
    }
    let body = {};
    try { body = await resp.json(); } catch (e) { body = {}; }
    if (resp.status === 200) return json(200, body);
    if (resp.status === 502 && body && typeof body.matrix_status === "number") return json(body.matrix_status, body.body || {});
    // the site kept it (the chat server was down), or never read it (its rate limit answered first): armed again for the SDK's retry
    if ((body && body.grant === "kept") || resp.status === 429) this.keyResetGrant = grant;
    return json(resp.status >= 500 ? 503 : 403, { errcode: "M_FORBIDDEN", error: (body && body.error) || "", grant: body && body.grant });
  }

  // the client's fetch (createClient({fetchFn})): the SDK calls it with a URL and a plain init whose headers carry the token
  async fetch(input, init = {}) {
    if (this.keyResetGrant && this.isKeyUpload(input, init)) return this.uploadThroughSite(init);
    const res = await this.fetchImpl(input, init);
    if (res.status !== 401 || !urlOf(input).startsWith(this.base)) return res;
    let body = null;
    try { body = await res.clone().json(); } catch (e) { return res; }
    if (!body || body.errcode !== "M_UNKNOWN_TOKEN") return res;
    const used = bearer(init.headers);
    if (!this.client || used !== this.client.getAccessToken()) return res;   // already swapped: the SDK retries by itself
    if (this.freshlyObtained(used)) {
      this.reason = "unusable";                   // the token just renewed was refused too: never renew again (the SDK would loop)
      return res;
    }
    const outcome = await this.renew(used);
    if (outcome === "renewed" || outcome === "adopted") return res;           // a new token: the SDK retries the request with it
    if (outcome === "transient") throw new TypeError("chat offline");         // the SDK's ConnectionError: sync backs off, no log-out
    this.reason = outcome;
    return res;                                   // removed / signed_out / refused / unusable: SessionLoggedOut, then watchSignedOut
  }

  // One renewal at a time per page, whoever asks.
  renew(failed, { proactive = false } = {}) {
    if (!this.renewing) {
      this.renewing = this.renewNow(failed, proactive).finally(() => { this.renewing = null; });
    }
    return this.renewing;
  }

  take(token, gen, exp, life) {
    if (this.client) this.client.setAccessToken(token);
    this.me.gen = gen;
    this.exp = exp == null ? null : exp;
    this.life = life == null ? null : life;
    this.obtained.set(token, this.now());
    this.schedule();
  }

  // under the index lock: adopt a newer token, or say why there is nothing to renew
  async look(failed) {
    return withIndex(async (idx) => {
      const e = findEntry(idx, this.me.u, this.me.d, this.me.k);
      if (!e || e.s === "forget" || idx.owner !== this.me.u || e.s === "out") return { outcome: "signed_out" };
      if (e.s === "removed" || e.s === "broken") return { outcome: "removed" };
      const seen = { epoch: idx.epoch, owner: idx.owner, gen: e.gen || 0 };
      if ((e.gen || 0) > this.me.gen && e.sealed) {
        const opened = await unseal(this.tokenKey, tokenAad(e), e.sealed);
        const left = e.exp == null ? Infinity : e.exp - this.now();
        if (opened && typeof opened.t === "string" && opened.t !== failed && left > ADOPT_LEFT) {
          return { outcome: "adopted", token: opened.t, gen: e.gen, exp: e.exp, life: e.life };
        }
      }
      return { seen };
    });
  }

  // A page that stopped (another tab took chat over, a sign-out, the page's own end) asks the site nothing more, after any await: its
  // answer would be acted on by a page whose chat is no longer its own. "transient" for it is acted on by nobody (renewSoon, the
  // SessionLoggedOut handler and stayOffline all ask `stopped` after the await).
  async renewNow(failed, proactive) {
    const started = this.now();
    for (let attempt = 0; ; attempt += 1) {
      if (this.stopped) return "transient";
      let looked;
      try {
        looked = await this.look(failed);
      } catch (e) {
        looked = { outcome: "transient" };
      }
      if (this.stopped) return "transient";
      if (looked.outcome === "adopted") {
        this.take(looked.token, looked.gen, looked.exp, looked.life);
        return "adopted";
      }
      if (looked.outcome && looked.outcome !== "transient") return looked.outcome;
      if (!looked.outcome) {
        // renewed or adopted under a minute ago and refused anyway: a page start will find out. Only for a renewal a refusal asked for
        // (the SDK's retry loop): the timer renews a token nobody refused, however new (a token that lasts under three minutes, as the
        // specs' CHAT_TOKEN_REPORT_SECONDS makes them, is renewed less than a minute after it came)
        if (!proactive && this.freshlyObtained(failed)) {
          this.reason = "unusable";
          return "unusable";
        }
        const outcome = await this.ask(looked.seen);
        if (outcome !== "transient") return outcome;
      }
      // the site or the network couldn't answer: wait, holding no lock
      const delay = BACKOFF[Math.min(attempt, BACKOFF.length - 1)];
      const deadline = proactive ? (this.exp == null ? started : this.exp) : started + RENEW_GIVE_UP;
      if (this.stopped || this.now() + delay > deadline) return "transient";
      await this.sleep(delay);
    }
  }

  // the site asked (no lock held), then its answer written under the lock, unless a Log out, a deletion or another page came first
  async ask(seen) {
    const got = await postSite(this.boot, "/api/chat/token", { kind: this.me.k, mode: "renew", device_id: this.me.d });
    const body = got.body || {};
    if (got.signedOut) {
      // the site sign-in is over: a dead token stays as ciphertext no longer than it must
      await withIndex((idx) => {
        const e = findEntry(idx, this.me.u, this.me.d, this.me.k);
        if (e && e.sealed && idx.epoch === seen.epoch) {
          delete e.sealed;
          e.exp = null;
          e.gen = (e.gen || 0) + 1;
        }
      }).catch(() => {});
      return "signed_out";
    }
    if (got.status === 409 && body.device_gone) {
      await withIndex((idx) => {
        const e = findEntry(idx, this.me.u, this.me.d, this.me.k);
        if (e && LIVE.has(e.s) && idx.epoch === seen.epoch) {
          e.s = "removed";
          delete e.sealed;
          e.exp = null;
          e.gen = (e.gen || 0) + 1;
          e.why = typeof body.reason === "string" ? body.reason : "deleted_elsewhere";
        }
      }).catch(() => {});
      return "removed";
    }
    if (got.status === 403 && (body.need_password || body.age_locked)) {
      this.refusal = body;
      return "refused";
    }
    if (got.status !== 200) return "transient";
    if (body.device_id !== this.me.d || typeof body.access_token !== "string" || !body.access_token) return "removed";

    const token = body.access_token;
    const life = typeof body.expires_in_ms === "number" && body.expires_in_ms > 0 ? body.expires_in_ms : null;
    const box = await seal(this.tokenKey, tokenAad(this.me), { t: token });
    let wrote;
    try {
      wrote = await withIndex(async (idx) => {
        const e = findEntry(idx, this.me.u, this.me.d, this.me.k);
        // a Log out, a deletion or another account came first: the new token is dropped, never stored (it ends on Synapse by itself)
        if (idx.epoch !== seen.epoch || idx.owner !== this.me.u) return { outcome: "signed_out" };
        if (!e || e.s === "out" || e.s === "forget") return { outcome: "signed_out" };
        if (e.s !== "in") return { outcome: "removed" };
        if ((e.gen || 0) !== seen.gen && e.sealed) {
          // another page of this device wrote a newer token meanwhile: that one is taken, ours dropped
          const opened = await unseal(this.tokenKey, tokenAad(e), e.sealed);
          if (opened && typeof opened.t === "string") return { outcome: "adopted", token: opened.t, gen: e.gen, exp: e.exp, life: e.life };
        }
        e.sealed = box;
        e.life = life;
        e.exp = life ? this.now() + life : null;  // this browser's own clock, so no skew
        e.gen = (e.gen || 0) + 1;
        return { outcome: "renewed", token, gen: e.gen, exp: e.exp, life };
      });
    } catch (e) {
      return "transient";
    }
    if (wrote.token) this.take(wrote.token, wrote.gen, wrote.exp, wrote.life);
    return wrote.outcome;
  }

  // --- the proactive timer, only when the token has an end ------------------------------------------------------------------------

  schedule() {
    if (this.timer) clearTimeout(this.timer);
    this.timer = null;
    if (this.exp == null || this.stopped) return;
    const at = this.exp - renewBefore(this.life) - this.random() * 60 * 1000;
    const delay = Math.max(30 * 1000, at - this.now());
    this.arm(() => this.renewSoon(true), delay);
  }

  // The one timer: a new one always replaces the last (a handle overwritten without clearTimeout would be a second chain of renewals
  // nothing could stop but `stopped`).
  arm(fn, delay) {
    if (this.timer) clearTimeout(this.timer);
    this.timer = setTimeout(fn, delay);
    if (this.timer && typeof this.timer.unref === "function") this.timer.unref();
  }

  async renewSoon(due = false) {
    if (this.stopped || !this.client || this.exp == null) return;
    if (!due && this.exp - this.now() > renewBefore(this.life)) return;
    const outcome = await this.renew(this.client.getAccessToken(), { proactive: true });
    if (this.stopped) return;                     // stopped meanwhile (another tab took chat over): its outcome is nobody's to act on
    if (outcome === "renewed" || outcome === "adopted") return;
    if (outcome === "transient") {
      // nothing answered before the token ends: the 401 path takes over then; look again in a minute meanwhile, asking again then
      // whether it is still due (a token renewed meanwhile, by a 401 or another tab, is left alone)
      this.arm(() => this.renewSoon(false), 60 * 1000);
      return;
    }
    this.reason = outcome;
    try { this.client.stopClient(); } catch (e) { /* stopped already */ }
    if (this.onLost) this.onLost(outcome);
  }

  // --- other tabs (3.3) ------------------------------------------------------------------------------------------------------------

  async onIndex() {
    if (this.stopped || this.ending) return;
    const idx = readIndex();
    const e = findEntry(idx, this.me.u, this.me.d, this.me.k);
    if (!e || e.s === "forget") return this.end("forgotten");
    if (idx.owner === null || e.s === "out") return this.end("logged_out");
    if (idx.owner !== this.me.u) return this.end("other_account");
    if (e.s === "removed" || e.s === "broken") return this.end("removed");
    if ((e.gen || 0) > this.me.gen && e.sealed) {
      const opened = await unseal(this.tokenKey, tokenAad(e), e.sealed);
      if (opened && typeof opened.t === "string" && !this.stopped) this.take(opened.t, e.gen, e.exp, e.life);
    }
    return undefined;
  }

  attach(client) {
    this.client = client;
    this.schedule();
    if (!globalThis.window || typeof window.addEventListener !== "function") return;
    window.addEventListener("storage", (event) => {
      if (event.key === INDEX_KEY || event.key === null) this.onIndex().catch(() => {});
    });
    // looked at once now: another tab's Log out, deletion or sign-in that came between ensureSession's write and this listener (its
    // store clean-up, the waiting list's unseal) fired its event with nobody listening, and nothing else would ever tell this page
    this.onIndex().catch(() => {});
    // timers are throttled in hidden tabs and frozen in sleep: look again when the page comes back
    const soon = () => { this.renewSoon().catch(() => {}); };
    if (globalThis.document && typeof document.addEventListener === "function") {
      document.addEventListener("visibilitychange", () => { if (document.visibilityState === "visible") soon(); });
    }
    window.addEventListener("online", soon);
    window.addEventListener("pageshow", (event) => { if (event && event.persisted) soon(); });
  }

  // --- the store lock (6.4) --------------------------------------------------------------------------------------------------------

  // Taken waiting, before the store opens, and held until releaseStore(). Answers false when this page stopped meanwhile (at once:
  // shutdown() ends the wait). Throws StoreFailed after `giveUpMs`; says "Chat is closing in another tab…" after `slowMs`. Neither
  // fires once this page has stopped: what it says then (another tab took chat over, a sign-out) is never drawn over.
  holdStore({ slowMs = 3000, giveUpMs = 30000 } = {}) {
    if (!hasLocks()) {
      this.storeHold = { release() {} };
      return Promise.resolve(!this.stopped);
    }
    return new Promise((resolve, reject) => {
      let settled = false;
      const slow = setTimeout(() => {
        if (!settled && !this.stopped) this.ui.status(tr("Chat is closing in another tab…"));
      }, slowMs);
      const fail = setTimeout(() => {
        if (settled) return;
        settled = true;
        clearTimeout(slow);
        this.storeWait = null;
        if (this.stopped) resolve(false);
        else reject(new StoreFailed(tr("Chat couldn't start in this browser.")));
      }, giveUpMs);
      this.storeWait = () => {
        // shutdown(): the wait ends now, quietly; the lock, if it ever comes, is let go at once
        if (settled) return;
        settled = true;
        clearTimeout(slow);
        clearTimeout(fail);
        resolve(false);
      };
      navigator.locks.request(STORE_LOCK + this.session.prefix, () => {
        this.storeWait = null;
        clearTimeout(slow);
        clearTimeout(fail);
        if (settled || this.stopped) {
          if (!settled) { settled = true; resolve(false); }
          return undefined;                       // given up on, or this page stopped meanwhile: let go at once
        }
        settled = true;
        this.ui.status("");
        let letGo;
        const held = new Promise((done) => { letGo = done; });
        this.storeHold = { release: () => letGo() };
        resolve(true);
        return held;
      }).catch(() => {
        if (!settled) { settled = true; resolve(false); }
      });
    });
  }

  releaseStore() {
    const hold = this.storeHold;
    this.storeHold = null;
    if (hold) hold.release();
  }

  // The store opening, tracked: a page that stops waits for it to settle before it stops the client and lets the store go, or two
  // OlmMachines could open one store. `start` opens it, and is never called once this page has stopped: by then shutdown() has let
  // the store lock go, and the tab that took over may be opening the same store. Answers true once opened, false when it never
  // started. A store given up on (StoreReplaced) starts the page again once.
  opening(start) {
    if (this.stopped) return Promise.resolve(false);
    let promise;
    try {
      promise = Promise.resolve(start());
    } catch (e) {
      promise = Promise.reject(e);
    }
    this.storeOpening = promise.then(() => {}, () => {});
    return promise.then(() => true, async (e) => {
      if (e instanceof StoreReplaced) {
        await this.end("store_unopenable");
        return new Promise(() => {});
      }
      throw e;
    });
  }

  // 6.4 in one place: the store's lock (waited for), then `prepare` (the crypto wasm), then the store, asking between each whether
  // this page has stopped meanwhile (another tab took chat over, a sign-out). Answers true once the store is open, false when this
  // page stopped first: then nothing was opened and the store lock is let go. Throws StoreFailed (the lock never came, or the store
  // wouldn't open) and whatever `prepare` throws, for the page to show. The raw store key is zeroed on every way out (1.2): the store has
  // it by then (openCryptoStore zeroes it as soon as initRustCrypto returns), or it was never opened and never will be.
  async openStore(open, { prepare = null } = {}) {
    try {
      if (!(await this.holdStore())) return false;
      if (this.stopped) {
        await this.shutdown();
        return false;
      }
      if (prepare) await prepare();
      if (this.stopped) return false;             // shutdown() ran with nothing opening: the client stopped, the store lock went
      return await this.opening(open);
    } finally {
      forgetStoreKey(this.session);
    }
  }

  // The account's keys are being made or reset on the server (crypto.js: a first set-up's freshKeys, Start over's resetEncryption and
  // freshKeys). Stopping the client in the middle would close the OlmMachine those calls use, leaving the key backup and the secret
  // storage deleted while the site's locked copies name a key that is gone: shutdown() waits for it to settle before it stops the
  // client (and so before the store lock goes and another tab opens the store). Answers `work`.
  holdFor(work) {
    const settled = Promise.resolve(work).then(() => {}, () => {});
    const before = this.keyWork;
    this.keyWork = before ? before.then(() => settled) : settled;
    return work;
  }

  // Stop in the one safe order: the store's opening settles, the key work under way settles (holdFor), then the client stops (closing
  // the OlmMachine), then the store lock goes. The store key goes with it, once nothing can still be handing it to the store.
  shutdown() {
    if (!this.closing) {
      this.stopped = true;
      this.signalStopped();
      try { if (this.onStopped) this.onStopped(); } catch (e) { /* the page's own clean-up: never stops the shutdown */ }
      if (this.timer) clearTimeout(this.timer);
      this.timer = null;
      if (this.storeWait) this.storeWait();     // a page still waiting for the store lock stops waiting, and draws nothing more
      this.closing = (async () => {
        if (this.storeOpening) await this.storeOpening;
        if (this.keyWork) await this.keyWork;
        forgetStoreKey(this.session);
        try { if (this.client) this.client.stopClient(); } catch (e) { /* stopped already */ }
        this.releaseStore();
      })();
    }
    return this.closing;
  }

  // Resolves once this page has stopped (shutdown): every way, a reload or not.
  whenStopped() {
    return this.stopSignal;
  }

  // This page's chat ends: stopped in order, then started again once (or, over the budget, "Chat stopped in this tab."). Messages the
  // page still held to send (send_gate.js) would go with a reload: then it stops and says so, and the member reloads when they choose.
  // New keys whose locked copies are still being saved, or whose recovery code is on screen (crypto.js keysPending), would go with a
  // reload too, and leave the account with keys no copy opens (or a code nobody saw): the reload waits until they are finished. Those
  // steps only POST to the site and show text, so the stopped client isn't needed for them.
  async end(why, { notice = null } = {}) {
    // already ending; or stopped without ending (another tab took chat over: index.js holdChatLock), whose page says so itself and must
    // never reload, or it would take chat back from the tab the member just opened
    if (this.ending || this.stopped) return;
    this.ending = true;
    let unsent = 0;
    try { unsent = this.unsent ? Number(this.unsent()) || 0 : 0; } catch (e) { unsent = 0; }
    let pending = null;
    try { pending = this.keysPending ? this.keysPending() : null; } catch (e) { pending = null; }
    await this.shutdown();
    if (pending) {
      try { await pending; } catch (e) { /* the attempt ended: nothing is pending any more */ }
    }
    if (notice) this.ui.notice(notice);
    if (unsent > 0) {
      this.ui.stop(unsentLine(unsent), { why, action: tr("Reload"), onAction: () => window.location.reload() });
      return;
    }
    if (reloadOnce(why, this.role, { delay: notice && !quiet(this.role) ? 1500 : 0 }) === "over") stoppedHere(this.ui);
  }
}

// One chat client per browser (`uqqus-matrix`, taken with steal): they would share one device and its crypto store, and two at once
// can lose keys. The newest tab (or side panel) takes over. The one it took over from is told (`lost`, which draws its note), then its
// keeper (if it has one yet) stops in the safe order: the store it may still be opening settles, its client stops, its store lock goes
// (shutdown), so the tab that took over, waiting on that store lock, never opens the store beside a machine still writing it.
// Without Web Locks it resolves at once.
export const CHAT_LOCK = "uqqus-matrix";

export function holdChatLock({ keeper = () => null, lost = () => {} } = {}) {
  if (!hasLocks()) return Promise.resolve();
  return new Promise((resolve) => {
    navigator.locks
      .request(CHAT_LOCK, { steal: true }, () => {
        resolve();
        return new Promise(() => {});            // held for as long as this page lives
      })
      .catch(() => {
        lost();
        const k = keeper();
        if (k) k.shutdown();
      });
  });
}

// The client started and its first sync waited for (the call window, a Space): true once it synced (PREPARED or SYNCING), false when
// the page stopped first (a renewal the site refused, over the reload budget, another tab's Log out): the stopped client never syncs,
// so without this way out the page would wait for ever (a Space's Join held disabled with its uqqus-call lock). `syncEvent` is the
// SDK's ClientEvent.Sync, handed in (nothing here imports matrix-js-sdk).
export function firstSync(client, keeper, syncEvent, options = {}) {
  return new Promise((resolve) => {
    if (keeper.stopped) {
      resolve(false);
      return;
    }
    let done = false;
    const onSync = (state) => {
      if (state === "PREPARED" || state === "SYNCING") finish(true);
    };
    function finish(value) {
      if (done) return;
      done = true;
      try { client.off(syncEvent, onSync); } catch (e) { /* gone with the client */ }
      resolve(value);
    }
    client.on(syncEvent, onSync);
    keeper.whenStopped().then(() => finish(false));
    try {
      Promise.resolve(client.startClient(options)).catch(() => {});
    } catch (e) { /* a client that won't start never syncs: only the page stopping ends the wait */ }
  });
}

// The one way a chat page makes its client: the keeper's fetchFn, no localStorage store (no mxjssdk_memory_filter_* keys), and the
// keeper watching the index and the token's end. `page`: { ui, role } (keeper.js uiForRole).
export function makeClient(sdk, session, extra = {}, page = {}) {
  const keeper = new SessionKeeper(session, page);
  const client = sdk.createClient({
    baseUrl: session.homeserver_url,
    accessToken: session.accessToken,
    userId: session.user_id,
    deviceId: session.device_id,
    fetchFn: keeper.fetchFn,
    store: new sdk.MemoryStore(),
    ...extra,
  });
  keeper.attach(client);
  return { client, keeper };
}
