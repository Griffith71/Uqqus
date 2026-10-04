// Fetches/saves the account's Matrix recovery key from Ruqqus's own
// server (see ruqqus/routes/chat.py's recovery_key routes). This is what
// lets E2EE unlock automatically on any device with no user interaction -
// see crypto.js's ensureEncryptionReady().

export async function fetchStoredRecoveryKey() {
  const resp = await fetch("/api/chat/recovery_key", { credentials: "same-origin" });
  if (!resp.ok) return null;
  const data = await resp.json();
  return data.recovery_key || null;
}

export async function saveRecoveryKey(formkey, encodedKey) {
  const body = new URLSearchParams();
  body.set("formkey", formkey);
  body.set("recovery_key", encodedKey);
  await fetch("/api/chat/recovery_key", {
    method: "POST",
    credentials: "same-origin",
    headers: { "Content-Type": "application/x-www-form-urlencoded" },
    body,
  });
}
