// Settings > Chat apps (templates/settings_chat_apps.html, routes/chat_apps.py): the member's security key, unlocked
// here with their password exactly as the chat page unlocks it (keylock.js: the site never sees the password, the
// pre-key or the key), and their chat devices with Sign out. Text only: nothing here writes HTML.
import { fetchKeyInfo, passwordIsRight, prekeyFromPassword, passwordKey, unlock } from "./keylock.js";
import { CALL_STORAGE_KEY, loadSession } from "./token.js";

const root = document.getElementById("chat-apps");
const formkey = root.dataset.formkey;
const byId = (id) => document.getElementById(id);

// ---- the security key ----

const keyForm = byId("chat-key-form");
if (keyForm) {
  const error = byId("chat-key-error");
  const out = byId("chat-key-out");
  const value = byId("chat-key-value");
  const password = byId("chat-key-password");
  const say = (text) => { error.textContent = text; error.hidden = !text; };

  keyForm.addEventListener("submit", async (e) => {
    e.preventDefault();
    say("");
    const button = byId("chat-key-show");
    button.disabled = true;
    try {
      if (!(await passwordIsRight(formkey, password.value))) { say(window.tr("That password isn't right.")); return; }
      const info = await fetchKeyInfo();
      if (!info.password_blob) { say(window.tr("Open Chat once on this site first, so your key is set up.")); return; }
      let key;
      try {
        key = await unlock(await passwordKey(await prekeyFromPassword(password.value), info.salt), info.password_blob);
      } catch (err) {
        say(window.tr("Your key was locked with an older password. Open Chat on this site and enter your recovery code once; then it opens here."));
        return;
      }
      password.value = "";
      value.value = key;
      out.hidden = false;
    } catch (err) {
      say(err.message || window.tr("Your key can't be shown right now."));
    } finally {
      button.disabled = false;
    }
  });
  byId("chat-key-copy").addEventListener("click", async () => {
    try { await navigator.clipboard.writeText(value.value); byId("chat-key-copy").textContent = window.tr("Copied"); }
    catch (err) { value.select(); }
  });
  byId("chat-key-hide").addEventListener("click", () => {
    value.value = "";
    out.hidden = true;
    byId("chat-key-copy").textContent = window.tr("Copy");
  });
}

// ---- devices ----

const list = byId("chat-devices");
// THE INTERIM (build steps 2 and 3, helpers/chat_signouts.py KEEP_SITE_BROWSERS): this browser's own devices, read from its own storage
// as before. Today's chat page and call window delete their crypto store when their device is signed out, which would destroy room keys
// nothing else holds, so they are labelled "This browser" and offered no Sign out here. The server's `here` can't stand in for it yet:
// today's client asks the token route only when it has no token, so the row's sign-in is often an older one. Build step 4c removes
// this (the client that keeps its stores writes `here` on every start).
const ownDevices = new Set([(loadSession() || {}).device_id, (loadSession(CALL_STORAGE_KEY) || {}).device_id].filter(Boolean));

function when(ts) {
  if (!ts) return window.tr("Not used yet");
  const date = new Date(ts).toLocaleDateString(window.I18N.intl, { day: "numeric", month: "short", year: "numeric" });
  return window.tr("Last used %(date)s", { date });
}

function line(text, cls) {
  const li = document.createElement("li");
  li.className = cls || "text-muted text-small";
  li.textContent = text;
  return li;
}

// asks first; the site's own sheet where the page has it (default.html), the browser's otherwise (this page's shell has none)
function ask(text) {
  if (window.confirmSheet) return window.confirmSheet({ title: window.tr("Sign out"), text, ok: window.tr("Sign out") });
  return Promise.resolve(window.confirm(text));
}

async function signOut(device, button) {
  // "This sign-in" is what the server says (the device was last started by this cookie's sign-in): a copy of the cookie is the same
  // sign-in, so it is listed and can be signed out like any other. This browser's own devices have no button in the interim (above)
  const question = device.here
    ? window.tr("This signs out chat in this browser too, if it is this browser's. It will start again as a new device.")
    : window.tr("Sign out %(device)s? It will need to sign in again to read your chats.", { device: device.name });
  if (!(await ask(question))) return;
  button.disabled = true;
  const body = new URLSearchParams({ formkey, device_id: device.device_id });
  const resp = await fetch("/api/chat/devices/sign_out", { method: "POST", credentials: "same-origin", body });
  if (resp.status === 202) {
    const data = await resp.json().catch(() => ({}));
    const note = document.createElement("span");
    note.className = "text-muted text-small";
    note.textContent = data.message || window.tr("This device will be signed out shortly.");
    button.replaceWith(note);
  } else if (resp.ok) {
    await load();
  } else {
    button.disabled = false;
    window.alert(window.tr("That device couldn't be signed out right now."));
  }
}

async function load() {
  let data;
  try {
    const resp = await fetch("/api/chat/devices", { credentials: "same-origin" });
    data = await resp.json();
    if (!resp.ok) throw new Error(data.error);
  } catch (err) {
    list.replaceChildren(line((err && err.message) || window.tr("Your chat devices can't be listed right now.")));
    return;
  }
  if (!data.devices.length) {
    list.replaceChildren(line(window.tr("No devices yet. Open Chat to start.")));
    return;
  }
  list.replaceChildren(...data.devices.map((device) => {
    const li = document.createElement("li");
    li.className = "d-flex align-items-center justify-content-between py-2 chat-device";
    const text = document.createElement("div");
    const name = document.createElement("div");
    name.className = "font-weight-bold";
    name.textContent = device.name;
    const sub = document.createElement("div");
    sub.className = "text-small text-muted";
    const own = ownDevices.has(device.device_id);
    const kind = own ? window.tr("This browser") : device.here ? window.tr("This sign-in")
      : device.site ? window.tr("A browser on this site") : window.tr("An app");
    // Translators: a chat device's line, e.g. "An app · Last used 3 Oct 2026"
    sub.textContent = window.tr("%(kind)s · %(used)s", { kind, used: when(device.last_seen) });
    text.append(name, sub);
    li.append(text);
    if (own) return li;                  // the interim: never this browser's own device (its store would go with it)
    const button = document.createElement("button");
    button.type = "button";
    button.className = "btn btn-secondary btn-sm";
    button.textContent = window.tr("Sign out");
    button.setAttribute("aria-label", window.tr("Sign out %(device)s", { device: device.name }));
    button.addEventListener("click", () => signOut(device, button));
    li.append(button);
    return li;
  }));
}

load();
