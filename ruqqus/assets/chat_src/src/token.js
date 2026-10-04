// Browser-owned Matrix session. The access token + device_id live ONLY in
// this browser's localStorage - the server never persists either (see
// ruqqus/helpers/matrix_client.py's module docstring). Reusing the same
// device_id across calls keeps this browser as a single stable Olm/Megolm
// device instead of minting a new one every page load.

const STORAGE_KEY = "ruqqus_chat_session";

export function loadSession() {
  try {
    const raw = localStorage.getItem(STORAGE_KEY);
    return raw ? JSON.parse(raw) : null;
  } catch (e) {
    return null;
  }
}

function saveSession(session) {
  try {
    localStorage.setItem(STORAGE_KEY, JSON.stringify(session));
  } catch (e) {
    // localStorage unavailable (private mode / blocked) - session just
    // won't persist across reloads; nothing else to do.
  }
}

export async function ensureSession(boot) {
  const existing = loadSession();
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
  saveSession(session);
  return session;
}
