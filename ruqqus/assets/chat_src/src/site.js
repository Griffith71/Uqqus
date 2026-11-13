// Every POST a chat page, the call window or a Space page makes to the site goes through here (the chat plan's 5.3, row 807, and gap 9).
//
// A credential event that spares this sign-in (Log out everywhere, a password change, two-factor on or off) raises the nonce form keys are
// made from, so a page opened before it holds a key the site now refuses. Its next POST gets a 401 (or a redirect to /login): the page
// asks for the key as it is now (`GET /api/chat/formkey`, routes/chat.py chat_formkey), keeps it in `boot.formkey` (so every later POST,
// and a beacon read at `pagehide`, uses it) and sends the POST again, once. Only the key of the account this page was drawn for is taken:
// a cookie that now belongs to another account (another sign-in in another tab) answers that account's key, which is refused, so the page
// never posts as someone else; it then finds out it is signed out. A GET that couldn't be answered for now (a 429, a 5xx, a timeout) is
// not that: the POST comes back as unavailable and is tried again later, so a call or a Space isn't ended by a moment's refusal.
//
// No imports, so it runs under node (tests/test_chat_site.py) and keylock.js can use it without the rest of the chat client.

// AbortSignal.timeout where the browser has it; an AbortController and a timer where it doesn't (gap 17).
export function timeoutSignal(ms) {
  if (typeof AbortSignal !== "undefined" && typeof AbortSignal.timeout === "function") return AbortSignal.timeout(ms);
  if (typeof AbortController === "undefined") return undefined;
  const controller = new AbortController();
  const timer = setTimeout(() => controller.abort(), ms);
  if (timer && typeof timer.unref === "function") timer.unref();
  return controller.signal;
}

export const SITE_TIMEOUT = 60 * 1000;           // longer than the site's worst case (a 15 s row lock and three 10 s chat server calls)

// `boot`: the page's boot object ({formkey, mxid}), whose form key is replaced in place. A plain form key (a page with no account to
// compare, Settings > Chat apps) is sent as it is and never refreshed.
function holderOf(boot) {
  return boot && typeof boot === "object" ? boot : { formkey: boot, mxid: null };
}

// What asking for the form key found: `REFRESHED` (this page's key replaced in place), `OVER` (the sign-in is over: the GET too was
// refused, or the cookie now belongs to another account, whose key is never taken), or `TRANSIENT` (the site couldn't be asked: a 429,
// a 5xx while the app restarts, an answer that isn't the route's, a timeout or a network error). Only `OVER` means signed out: a
// transient failure is retried like any other (the keeper's backoff, a start's ChatUnavailable retry), never ends the page, and never
// takes a device's sealed token away (the plan's 4.2 and 4.4).
export const REFRESHED = "refreshed";
export const OVER = "over";
export const TRANSIENT = "transient";

// One GET for the form key as it is now. A page with no account to compare (a plain form key: Settings > Chat apps) never asks: its
// refusal stands (`OVER`).
export async function refreshFormkey(boot) {
  const holder = holderOf(boot);
  if (typeof holder.mxid !== "string" || !holder.mxid) return OVER;
  let resp;
  try {
    resp = await fetch("/api/chat/formkey", { method: "GET", credentials: "same-origin", redirect: "manual", signal: timeoutSignal(15000) });
  } catch (e) {
    return TRANSIENT;                              // nothing answered, or not in 15 s
  }
  if (refusedKey(resp)) return OVER;               // the sign-in is over: @auth_required refused the GET too
  if (!resp.ok) return TRANSIENT;                  // 429 (30 a minute per member, shared by every page refreshing after one event), 5xx
  let data;
  try {
    data = await resp.json();
  } catch (e) {
    return TRANSIENT;                              // a proxy's page, a body cut short
  }
  if (!data || typeof data.formkey !== "string" || !data.formkey) return TRANSIENT;
  if (data.mxid !== holder.mxid) return OVER;      // another account's sign-in: this page is signed out, never that account
  holder.formkey = data.formkey;                   // every later POST of this page uses it
  return REFRESHED;
}

// What sitePost hands back when the key couldn't be refreshed for now: a 503 with no body of the route's, so a caller reads it as the
// site being unavailable (try again), never as the sign-in being over.
function unavailable() {
  if (typeof Response === "function") return new Response("{}", { status: 503, headers: { "Content-Type": "application/json" } });
  return { status: 503, ok: false, type: "basic", headers: { get: () => null }, json: async () => ({}), text: async () => "{}" };
}

// What a refused form key looks like: no redirect is followed (a sign-in that ended answers a redirect to /login, which would read as a
// 200, or a 405 once /login sent a signed-in member back to a POST-only address), so it is an opaque redirect, a 3xx or a 401.
export function refusedKey(resp) {
  return resp.type === "opaqueredirect" || resp.status === 401 || (resp.status >= 300 && resp.status < 400);
}

// A POST of `fields` (a form, `formkey` added from `boot`), its Response handed back for the caller to read as it always did. A refused
// form key is refreshed once and the POST sent again; a sign-in that is over comes back as that refusal (not ok); a key that couldn't be
// asked for now comes back as a 503 (unavailable()), never as the refusal. Network failures throw.
export async function sitePost(boot, url, fields = {}, { headers = {}, timeout = SITE_TIMEOUT } = {}) {
  const holder = holderOf(boot);
  for (let attempt = 0; ; attempt += 1) {
    const body = new URLSearchParams();
    body.set("formkey", holder.formkey || "");
    for (const [name, value] of Object.entries(fields || {})) if (value !== undefined && value !== null) body.set(name, value);
    const resp = await fetch(url, {
      method: "POST", credentials: "same-origin", redirect: "manual", signal: timeoutSignal(timeout),
      headers: { "Content-Type": "application/x-www-form-urlencoded", Accept: "application/json", ...headers }, body,
    });
    if (attempt === 0 && refusedKey(resp)) {
      const refreshed = await refreshFormkey(holder);
      if (refreshed === REFRESHED) continue;
      if (refreshed === TRANSIENT) return unavailable();
    }
    return resp;
  }
}
