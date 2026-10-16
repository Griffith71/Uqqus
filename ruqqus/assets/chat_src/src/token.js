// Browser-owned Matrix session. The access token + device_id live ONLY in
// this browser's localStorage - the server never persists either (see
// ruqqus/helpers/matrix_client.py's module docstring). Reusing the same
// device_id across calls keeps this browser as a single stable Olm/Megolm
// device instead of minting a new one every page load.

const STORAGE_KEY = "ruqqus_chat_session";
// The call window (call.js) is a second device of the same account, with its own crypto store, so a call never
// fights the chat page over one device's keys.
export const CALL_STORAGE_KEY = "ruqqus_call_session";

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

export async function ensureSession(boot, storageKey = STORAGE_KEY) {
  const existing = loadSession(storageKey);
  if (existing && existing.access_token && existing.user_id) {
    return existing;
  }

  const body = new URLSearchParams();
  body.set("formkey", boot.formkey);
  if (existing && existing.device_id) {
    body.set("device_id", existing.device_id);
  }

  const resp = await fetch("/api/chat/token", {
    method: "POST",
    credentials: "same-origin",
    headers: { "Content-Type": "application/x-www-form-urlencoded" },
    body,
  });
  if (!resp.ok) {
    throw new Error("Could not start chat session");
  }
  const data = await resp.json();
  const session = {
    homeserver_url: data.homeserver_url,
    user_id: data.user_id,
    access_token: data.access_token,
    device_id: data.device_id,
  };
  saveSession(session, storageKey);
  return session;
}
