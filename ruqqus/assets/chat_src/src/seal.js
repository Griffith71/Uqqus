// What this browser keeps of a chat sign-in is sealed (the chat plan's phase 2, section 3.1): the access token in the index
// (token.js, `uqqus_chat_devices`) and the first messages waiting to be sent (`uqqus_chat_waiting`). AES-GCM, under a key DERIVED (HKDF)
// from the key the site hands a signed-in page (/api/chat/token: `storage_key` for a device, `waiting_key` for the account), so one key
// is never used with two algorithms (the Rust store cipher has the store key itself). Nothing here is kept: the keys live in the page's
// memory only, as non-extractable CryptoKeys. The AAD binds each box to its own entry and account, so a box copied into another entry,
// or another account's, doesn't open. No imports: it runs as it is under node 22 (tests/test_chat_seal.py) and in the bundles.

const ENC = new TextEncoder();
const DEC = new TextDecoder();

async function hkdfKey(raw32, info) {
  const base = await crypto.subtle.importKey("raw", raw32, "HKDF", false, ["deriveKey"]);
  return crypto.subtle.deriveKey({ name: "HKDF", hash: "SHA-256", salt: new Uint8Array(0), info: ENC.encode(info) },
    base, { name: "AES-GCM", length: 256 }, false, ["encrypt", "decrypt"]);
}

export const tokenKey = (storeKey) => hkdfKey(storeKey, "uqqus chat token seal v1");
export const waitingKey = (waitingRaw) => hkdfKey(waitingRaw, "uqqus chat waiting v1");

export const tokenAad = (e) => `v2|token|${e.u}|${e.d}|${e.k}`;
export const waitingAad = (u) => `v2|waiting|${u}`;

// base64url without padding, as the site writes the keys (helpers/chat_store_keys.py b64)
export function b64urlEncode(bytes) {
  let text = "";
  for (const byte of bytes) text += String.fromCharCode(byte);
  return btoa(text).replace(/\+/g, "-").replace(/\//g, "_").replace(/=+$/, "");
}

export function b64urlDecode(text) {
  if (typeof text !== "string" || !/^[A-Za-z0-9_-]*$/.test(text)) throw new Error("not base64url");
  const padded = text.replace(/-/g, "+").replace(/_/g, "/") + "===".slice((text.length + 3) % 4);
  const raw = atob(padded);
  const out = new Uint8Array(raw.length);
  for (let i = 0; i < raw.length; i += 1) out[i] = raw.charCodeAt(i);
  return out;
}

export async function seal(key, aad, obj) {
  const iv = crypto.getRandomValues(new Uint8Array(12));
  const ct = await crypto.subtle.encrypt({ name: "AES-GCM", iv, tagLength: 128, additionalData: ENC.encode(aad) }, key,
    ENC.encode(JSON.stringify(obj)));
  return { iv: b64urlEncode(iv), ct: b64urlEncode(new Uint8Array(ct)) };
}

// Any failure (a wrong key, another entry's box, a box cut short, not a box at all) is null: never an exception for the caller to miss.
export async function unseal(key, aad, box) {
  try {
    if (!key || !box || typeof box.iv !== "string" || typeof box.ct !== "string") return null;
    const iv = b64urlDecode(box.iv);
    if (iv.length !== 12) return null;
    const plain = await crypto.subtle.decrypt({ name: "AES-GCM", iv, tagLength: 128, additionalData: ENC.encode(aad) }, key,
      b64urlDecode(box.ct));
    return JSON.parse(DEC.decode(plain));
  } catch (e) {
    return null;
  }
}
