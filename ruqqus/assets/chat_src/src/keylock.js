// The chat recovery key, kept only locked (audit M-03). The site stores two locked copies (GET/POST /api/chat/key):
// one under a key from the member's password, one under a key from a one-time recovery code. Neither the password,
// the code nor the recovery key is ever sent to the site. Must match ruqqus/assets/js/chat_key.js exactly.
//
//   the pre-key   PBKDF2-SHA256(password, "uqqus-chat-prekey-v1", 210,000)   made by the sign-in form
//   password key  PBKDF2-SHA256(pre-key, account salt, 100,000) -> AES-GCM
//   code key      PBKDF2-SHA256(recovery code, "code:" + account salt, 210,000) -> AES-GCM

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

export function takePrekey() {
  try { return localStorage.getItem(PREKEY_STORE); } catch (e) { return null; }
}

export function forgetPrekey() {
  try { localStorage.removeItem(PREKEY_STORE); } catch (e) { /* nothing stored */ }
}

// --- talking to the site ---------------------------------------------------------------------------------------

export async function fetchKeyInfo() {
  const resp = await fetch("/api/chat/key", { credentials: "same-origin" });
  if (!resp.ok) throw new Error("Chat's key isn't available right now.");
  return resp.json();
}

async function post(url, formkey, fields) {
  const body = new URLSearchParams({ formkey, ...fields });
  const resp = await fetch(url, { method: "POST", credentials: "same-origin", body,
                                  headers: { "Content-Type": "application/x-www-form-urlencoded" } });
  if (!resp.ok && resp.status !== 204) throw new Error("That couldn't be saved.");
  return resp.status === 204 ? {} : resp.json();
}

export function saveBlobs(formkey, blobs) {
  return post("/api/chat/key", formkey, blobs);
}

export async function passwordIsRight(formkey, password) {
  const data = await post("/api/chat/check_password", formkey, { password });
  return !!data.ok;
}

export function markReady(formkey) {
  return post("/api/chat/ready", formkey, {}).catch(() => {});
}

export async function fetchLegacyKey() {
  const resp = await fetch("/api/chat/recovery_key", { credentials: "same-origin" });
  if (!resp.ok) return null;
  return (await resp.json()).recovery_key || null;
}

// --- the dialog (text only: nothing here writes HTML) --------------------------------------------------------

export function dialog({ title, text, field, fieldLabel, confirm, secondary, code, check }) {
  // `check(value)` answers an error message (the dialog stays open) or nothing (it closes and resolves {value});
  // the secondary button resolves {secondary: true}
  return new Promise((resolve) => {
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
      out.setAttribute("aria-label", "Your recovery code");
      out.addEventListener("focus", () => out.select());
      box.append(out);
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
      other.addEventListener("click", () => { shade.remove(); resolve({ secondary: true }); });
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
        try { problem = await check(value); } catch (e) { problem = (e && e.message) || "That didn't work."; }
      }
      if (problem) {
        error.textContent = problem;
        error.hidden = false;
        ok.disabled = false;
        return;
      }
      shade.remove();
      resolve({ value });
    });
    if (input) input.addEventListener("keydown", (e) => { if (e.key === "Enter") ok.click(); });
  });
}
