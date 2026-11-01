// A device the member signed out (Settings > Chat apps, routes/chat_apps.py) is answered M_UNKNOWN_TOKEN from then
// on. Its keys are gone from the server too, so this browser forgets its session and its crypto store and starts
// again as a new device: the chat page then asks for the password, as any new browser does (crypto.js).
import { HttpApiEvent } from "matrix-js-sdk";
import { loadSession } from "./token.js";

export function startAgainWhenSignedOut(client, storageKey, cryptoDatabasePrefix) {
  let done = false;
  client.on(HttpApiEvent.SessionLoggedOut, async () => {
    if (done) return;
    done = true;
    try { client.stopClient(); } catch (e) { /* already stopped */ }
    // a tab left open when its member logged out hears this up to a sync later, and by then someone else may have
    // signed in here: what is stored is only this device's to clear while it is still this device's
    const stored = loadSession(storageKey);
    if (!stored || stored.device_id === client.getDeviceId()) {
      try { await client.clearStores(cryptoDatabasePrefix ? { cryptoDatabasePrefix } : undefined); } catch (e) { /* nothing stored */ }
      try { localStorage.removeItem(storageKey); } catch (e) { /* storage blocked */ }
    }
    window.location.reload();
  });
  // logged out in another tab (all_js.js logOut), or another account has taken this browser over: this client's token
  // is no longer what is stored, so it stops at once instead of carrying on behind a page that still shows it. The stores
  // stay: the member's own device is taken up again when they sign back in (token.js).
  window.addEventListener("storage", (event) => {
    if (done || event.key !== storageKey) return;
    const stored = loadSession(storageKey);
    if (stored && stored.access_token === client.getAccessToken()) return;
    done = true;
    try { client.stopClient(); } catch (e) { /* already stopped */ }
    window.location.reload();
  });
}
