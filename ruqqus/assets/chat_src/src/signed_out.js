// A device the member signed out (Settings > Chat apps, routes/chat_apps.py) is answered M_UNKNOWN_TOKEN from then
// on. Its keys are gone from the server too, so this browser forgets its session and its crypto store and starts
// again as a new device: the chat page then asks for the password, as any new browser does (crypto.js).
import { HttpApiEvent } from "matrix-js-sdk";

export function startAgainWhenSignedOut(client, storageKey, cryptoDatabasePrefix) {
  let done = false;
  client.on(HttpApiEvent.SessionLoggedOut, async () => {
    if (done) return;
    done = true;
    try { client.stopClient(); } catch (e) { /* already stopped */ }
    try { await client.clearStores(cryptoDatabasePrefix ? { cryptoDatabasePrefix } : undefined); } catch (e) { /* nothing stored */ }
    try { localStorage.removeItem(storageKey); } catch (e) { /* storage blocked */ }
    window.location.reload();
  });
}
