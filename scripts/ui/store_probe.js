// For chat_session.spec.ts only (spec 1, the chat plan's phase 2): opens a kept chat store in the page the way the chat page does,
// with matrix-js-sdk's initRustCrypto, but with a key the spec chooses (none, or 32 zero bytes), and says what happened. It proves a
// signed-out browser's store opens for nobody without its key. Built by the spec with the chat bundle's own esbuild and packages
// (ruqqus/assets/chat_src/node_modules), injected as a module, never shipped to the site.
import { initAsync } from "@matrix-org/matrix-sdk-crypto-wasm";
import { MemoryStore, createClient } from "matrix-js-sdk";

window.__openStore = async ({ prefix, key, userId, deviceId, homeserver }) => {
  await initAsync("/assets/js/matrix_sdk_crypto_wasm_bg.wasm");
  // no sync, no network: only the store is opened (a MemoryStore with no localStorage writes nothing either)
  const client = createClient({ baseUrl: homeserver, userId, deviceId, store: new MemoryStore() });
  try {
    await client.initRustCrypto({ storageKey: key == null ? undefined : new Uint8Array(key), cryptoDatabasePrefix: prefix });
    return "opened";
  } catch (e) {
    return String((e && e.message) || e);
  } finally {
    try { client.stopClient(); } catch (e) { /* never started */ }
  }
};
