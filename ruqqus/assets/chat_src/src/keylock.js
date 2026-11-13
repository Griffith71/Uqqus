// The chat recovery key, kept only locked (audit M-03). The site stores two locked copies (GET/POST /api/chat/key):
// one under a key from the member's password, one under a key from a one-time recovery code. Neither the password,
// the code nor the recovery key is ever sent to the site. Must match ruqqus/assets/js/chat_key.js exactly.
//
//   the pre-key   PBKDF2-SHA256(password, "uqqus-chat-prekey-v1", 210,000)   made by the sign-in form
//   password key  PBKDF2-SHA256(pre-key, account salt, 100,000) -> AES-GCM
//   code key      PBKDF2-SHA256(recovery code, "code:" + account salt, 210,000) -> AES-GCM

import { sitePost } from "./site.js";

const PREKEY_LABEL = "uqqus-chat-prekey-v1";
const PREKEY_ROUNDS = 210000;
const KEK_ROUNDS = 100000;
const CODE_ROUNDS = 210000;
export const PREKEY_STORE = "uqqus_chat_prekey";
const enc = new TextEncoder();

function b64url(bytes) {
  let s = "";
  new Uint8Array(bytes).forEach((b) => { s += String.fromCharCode(b); });
  return btoa(s).replace(/\+/g, "-").replace(/\//g, "_").replace(/=+$/, "");
}

function unb64url(text) {
  const s = atob(text.replace(/-/g, "+").replace(/_/g, "/") + "===".slice((text.length + 3) % 4));
  return Uint8Array.from(s, (c) => c.charCodeAt(0));
}

async function pbkdf2(secretBytes, saltBytes, rounds) {
  const base = await crypto.subtle.importKey("raw", secretBytes, "PBKDF2", false, ["deriveBits"]);
  return crypto.subtle.deriveBits({ name: "PBKDF2", hash: "SHA-256", salt: saltBytes, iterations: rounds }, base, 256);
}

async function aesKey(bits) {
  return crypto.subtle.importKey("raw", bits, "AES-GCM", false, ["encrypt", "decrypt"]);
}

export async function prekeyFromPassword(password) {
  return b64url(await pbkdf2(enc.encode(password), enc.encode(PREKEY_LABEL), PREKEY_ROUNDS));
}

export async function passwordKey(prekey, salt) {
  return aesKey(await pbkdf2(unb64url(prekey), enc.encode(salt), KEK_ROUNDS));
}

function normaliseCode(code) {
  return String(code || "").toUpperCase().replace(/[^A-Z2-9]/g, "");
}

export async function codeKey(code, salt) {
  return aesKey(await pbkdf2(enc.encode(normaliseCode(code)), enc.encode(`code:${salt}`), CODE_ROUNDS));
}

export async function lock(key, text) {
  const iv = crypto.getRandomValues(new Uint8Array(12));
  const ct = new Uint8Array(await crypto.subtle.encrypt({ name: "AES-GCM", iv }, key, enc.encode(text)));
  const out = new Uint8Array(12 + ct.length);
  out.set(iv, 0);
  out.set(ct, 12);
  return b64url(out);
}

export async function unlock(key, blob) {
  const raw = unb64url(blob);
  const pt = await crypto.subtle.decrypt({ name: "AES-GCM", iv: raw.slice(0, 12) }, key, raw.slice(12));
  return new TextDecoder().decode(pt);
}

// 25 characters from an alphabet without look-alikes (no 0/O, 1/I/L), shown as five groups: about 125 bits
const CODE_ALPHABET = "ABCDEFGHJKMNPQRSTUVWXYZ23456789";
export function newRecoveryCode() {
  const bytes = crypto.getRandomValues(new Uint8Array(25));
  const chars = Array.from(bytes, (b) => CODE_ALPHABET[b % CODE_ALPHABET.length]).join("");
  return chars.match(/.{5}/g).join("-");
}

// How a pre-key names its account (chat_key.js accountName): what was typed in the sign-in form's username box, or the account a signed-in
// form is for, lowercased and without a leading "@".
export function accountName(name) {
  return String(name || "").trim().replace(/^@+/, "").toLowerCase();
}

// In this tab's sessionStorage (chat_key.js): shared with a frame in this tab, the side panel's chat, never with another tab. Kept as
// {v: 1, name, k, at, ref}: the pre-key `k`, the account it was typed for and the id of the sign-in that typed it. Answered only to a page
// of that account (`account`, the chat boot's username) and of that sign-in (`ref`, the chat boot's prekeyRef: the id the form sent with
// the password, which the sign-in kept, helpers/session_cookie.py PREKEY_REF_KEY). One left in this tab by an account that logged out in
// another tab, before someone else signed in there, is another account's password, and locking this account's recovery key with it
// would let that password open this account's chats. One left by an earlier sign-in of this same account may be from a password changed
// since anywhere (another browser's Settings, a reset by email, a parent's new password for a child), which ended that sign-in: this
// browser signed in again with a new id, so that pre-key is never used, and the old password can't open the account's chats. Anything
// else (another account's, another sign-in's, one without an id, the older bare form, one that can't be read, a sign-in typed with an
// email address) is removed and chat asks.
// So is one made before the last password change this browser sent (`at`, chat_key.js `uqqus_chat_prekey_before`, which every tab
// sees): a change made here keeps the sign-in (and so its id), so the id alone wouldn't tell.
export function takePrekey(account, ref) {
  let raw = null;
  try { raw = sessionStorage.getItem(PREKEY_STORE); } catch (e) { return null; }
  if (!raw) return null;
  let held = null;
  try { held = JSON.parse(raw); } catch (e) { held = null; }
  const wanted = accountName(account);
  const sameSignIn = typeof ref === "string" && !!ref && held && held.ref === ref;
  if (held && held.v === 1 && typeof held.k === "string" && held.k && wanted && held.name === wanted && sameSignIn
      && madeSinceChange(held)) return held.k;
  forgetPrekey();
  return null;
}

export const PASSWORD_CHANGED_KEY = "uqqus_chat_prekey_before";

function madeSinceChange(held) {
  let changed = 0;
  try { changed = Number(localStorage.getItem(PASSWORD_CHANGED_KEY)) || 0; } catch (e) { changed = 0; }
  return !changed || (Number(held.at) || 0) > changed;
}

export function forgetPrekey() {
  try { sessionStorage.removeItem(PREKEY_STORE); } catch (e) { /* nothing stored */ }
  try { localStorage.removeItem(PREKEY_STORE); } catch (e) { /* an older copy, from before it moved */ }
}

// --- talking to the site ---------------------------------------------------------------------------------------

export async function fetchKeyInfo() {
  const resp = await fetch("/api/chat/key", { credentials: "same-origin" });
  if (!resp.ok) throw new Error(window.tr("Chat's key isn't available right now."));
  return resp.json();
}

// `site`: the page's boot object (a stale form key is refreshed in place and the POST sent again: site.js), or a plain form key on a page
// with nothing to refresh (Settings > Chat apps).
async function post(url, site, fields) {
  const resp = await sitePost(site, url, fields);
  if (!resp.ok && resp.status !== 204) throw new Error(window.tr("That couldn't be saved."));
  return resp.status === 204 ? {} : resp.json();
}

export function saveBlobs(site, blobs) {
  return post("/api/chat/key", site, blobs);
}

export async function passwordIsRight(site, password) {
  const data = await post("/api/chat/check_password", site, { password });
  return !!data.ok;
}

// Start over's proof (routes/chat_key_reset_pages.py): the password, and the two-factor or backup code when the account has two-factor,
// for a one-time grant. Answers {grant} or {error, field} ("password" or "otp"): a refusal the box shows beside its field.
export async function askGrant(site, password, otp) {
  let resp;
  try {
    resp = await sitePost(site, "/api/chat/key_reset/grant", otp ? { password, otp } : { password });
  } catch (e) {
    return { error: window.tr("That didn't work.") };
  }
  let body = {};
  try { body = await resp.json(); } catch (e) { body = {}; }
  if (resp.ok && body && typeof body.grant === "string" && body.grant) return { grant: body.grant };
  return { error: (body && body.error) || window.tr("That didn't work."), field: body && body.field };
}

// The member says they kept their recovery code (only the time is kept: routes/chat.py chat_code_saved).
export function markCodeSaved(site) {
  return post("/api/chat/code_saved", site, {}).catch(() => {});
}

export function markReady(site) {
  return post("/api/chat/ready", site, {}).catch(() => {});
}

export async function fetchLegacyKey() {
  const resp = await fetch("/api/chat/recovery_key", { credentials: "same-origin" });
  if (!resp.ok) return null;
  return (await resp.json()).recovery_key || null;
}

// --- the dialog (text only: nothing here writes HTML) --------------------------------------------------------

// The dialogs open now, each with what closes it unanswered.
const openDialogs = new Set();

// The page stopped (another tab took chat over, its keeper stopped): every open dialog goes, answered {closed: true}, so no button in it
// can act for a page whose client has stopped (crypto.js reads that as the page having stopped).
export function closeDialogs() {
  for (const close of [...openDialogs]) close();
}

export function dialog({ title, text, field, fieldLabel, confirm, secondary, code, check, keep = false }) {
  // `check(value)` answers an error message (the dialog stays open) or nothing (it closes and resolves {value});
  // the secondary button resolves {secondary: true}; closeDialogs() resolves {closed: true}, except a dialog opened with `keep`, which
  // only its own buttons close (crypto.js: what must be finished once new keys are on the server)
  return new Promise((done) => {
    let answered = false;
    const resolve = (answer) => {
      if (answered) return;
      answered = true;
      openDialogs.delete(close);
      shade.remove();
      done(answer);
    };
    const close = () => resolve({ closed: true });
    if (!keep) openDialogs.add(close);
    const shade = document.createElement("div");
    shade.className = "chat-key-dialog";
    shade.setAttribute("role", "dialog");
    shade.setAttribute("aria-modal", "true");
    const box = document.createElement("div");
    box.className = "chat-key-box";
    const h = document.createElement("h2");
    h.className = "h5";
    h.id = `chat-key-title-${Date.now()}`;
    h.textContent = title;
    shade.setAttribute("aria-labelledby", h.id);
    box.append(h);
    for (const line of [].concat(text || [])) {
      const p = document.createElement("p");
      p.className = "text-small";
      p.textContent = line;
      box.append(p);
    }
    if (code) {
      const out = document.createElement("input");
      out.type = "text";
      out.readOnly = true;
      out.value = code;
      out.className = "form-control chat-key-code mb-2";
      out.setAttribute("aria-label", window.tr("Your recovery code"));
      out.addEventListener("focus", () => out.select());
      box.append(out);
      // Copy and Download, made here in the browser: the code never goes to the site
      const keepRow = document.createElement("div");
      keepRow.className = "d-flex flex-wrap mb-2";
      keepRow.style.gap = "0.5rem";
      const copy = document.createElement("button");
      copy.type = "button";
      copy.className = "btn btn-secondary btn-sm";
      copy.textContent = window.tr("Copy");
      copy.addEventListener("click", async () => {
        try {
          await navigator.clipboard.writeText(code);
          copy.textContent = window.tr("Copied");
        } catch (e) {
          out.focus();
        }
      });
      const save = document.createElement("button");
      save.type = "button";
      save.className = "btn btn-secondary btn-sm";
      save.textContent = window.tr("Download");
      save.addEventListener("click", () => {
        try {
          const file = new Blob([window.tr("Your chat recovery code") + "\n" + code + "\n"], { type: "text/plain" });
          const link = document.createElement("a");
          link.href = URL.createObjectURL(file);
          link.download = "chat-recovery-code.txt";
          document.body.append(link);
          link.click();
          link.remove();
          setTimeout(() => URL.revokeObjectURL(link.href), 1000);
        } catch (e) { /* the code is still on screen to copy by hand */ }
      });
      keepRow.append(copy, save);
      box.append(keepRow);
    }
    let input = null;
    if (field) {
      const label = document.createElement("label");
      label.className = "text-small d-block";
      label.textContent = fieldLabel;
      input = document.createElement("input");
      input.type = field;
      input.className = "form-control mt-1";
      input.autocomplete = field === "password" ? "current-password" : "off";
      label.append(input);
      box.append(label);
    }
    const error = document.createElement("p");
    error.className = "text-danger small mb-1";
    error.setAttribute("role", "alert");
    error.hidden = true;
    box.append(error);
    const row = document.createElement("div");
    row.className = "d-flex flex-wrap mt-2";
    row.style.gap = "0.5rem";
    const ok = document.createElement("button");
    ok.type = "button";
    ok.className = "btn btn-primary";
    ok.textContent = confirm;
    row.append(ok);
    if (secondary) {
      const other = document.createElement("button");
      other.type = "button";
      other.className = "btn btn-link text-muted";
      other.textContent = secondary;
      other.addEventListener("click", () => resolve({ secondary: true }));
      row.append(other);
    }
    box.append(row);
    shade.append(box);
    document.body.append(shade);
    (input || ok).focus();
    ok.addEventListener("click", async () => {
      ok.disabled = true;
      const value = input ? input.value : null;
      let problem = null;
      if (check) {
        try { problem = await check(value); } catch (e) { problem = (e && e.message) || window.tr("That didn't work."); }
      }
      if (problem) {
        error.textContent = problem;
        error.hidden = false;
        ok.disabled = false;
        return;
      }
      resolve({ value });
    });
    if (input) input.addEventListener("keydown", (e) => { if (e.key === "Enter") ok.click(); });
  });
}
