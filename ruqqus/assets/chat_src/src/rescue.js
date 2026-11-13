// Rescue (the chat plan's phase 3): a member who forgot their password reset it, and this browser still keeps a store of one of their
// earlier chat devices (`removed` or `out` in the index, token.js). If that store holds the account's private cross-signing keys, and they are
// the keys the chat server lists NOW (a Start over anywhere since replaced them, and then nothing is offered), the member can bring them
// into the new device: their old messages, and every other browser of theirs, come back with the new password. crypto.js asks first, and
// makes a new recovery code (the old one stops working).
//
// What is read here never leaves the browser: the store is opened raw through the crypto wasm (harvest.js openRaw, no network call), with
// the key /api/chat/store_keys gives for the account's own devices, under the store's own lock asked with `ifAvailable`. Nothing here deletes
// or marks a store: once chat is unlocked, the harvest moves the store's room keys and only then deletes it.

import { UserId } from "@matrix-org/matrix-sdk-crypto-wasm";
import { readIndex } from "./token.js";
import { free, hasLocks, keysFor, openRaw, underStoreLock, zero } from "./harvest.js";

export const MAX_LOOKED_AT = 4;                   // one /api/chat/store_keys call

// The account's key backup as the chat server lists it now: {version, ...}, or null when it has none (a 404). Any other failure throws: a
// rescue never decides about the backup from a request that didn't get an answer.
export async function backupOnServer(client) {
  try {
    return await client.http.authedRequest("GET", "/room_keys/version");
  } catch (e) {
    if (e && (e.httpStatus === 404 || e.errcode === "M_NOT_FOUND")) return null;
    throw e;
  }
}

// The account's master key as the chat server lists it now (its public key), or null.
export async function masterOnServer(client) {
  const mxid = client.getUserId();
  const answer = await client.http.authedRequest("POST", "/keys/query", undefined, { device_keys: { [mxid]: [] } });
  return keyOf(answer && answer.master_keys && answer.master_keys[mxid]);
}

// The one public key of a cross-signing key object ({keys: {"ed25519:X": "X"}}), or null.
export function keyOf(key) {
  const keys = key && typeof key === "object" ? key.keys : null;
  const values = keys && typeof keys === "object" ? Object.values(keys) : [];
  return values.length === 1 && typeof values[0] === "string" ? values[0] : null;
}

// This account's kept stores that might hold its keys, newest first: signed-out ones (`removed`, which the harvest takes once chat is
// unlocked), never this page's own device, never one already found empty or harvested.
export function candidates(session) {
  return readIndex().entries
    .filter((e) => e.u === session.user_id && e.d !== session.device_id && e.s === "removed" && !e.empty && !e.taken)
    .sort((a, b) => (b.used || b.made || 0) - (a.used || a.made || 0))
    .slice(0, MAX_LOOKED_AT);
}

// What one store can give: its secrets bundle (JSON: cross_signing, and backup when it had the backup key) when it holds all three private
// keys, they agree with its own view of the account's identity, and that identity's master key is `master`; else null.
async function bundleOf(machine, entry, master) {
  const status = await machine.crossSigningStatus();
  const all = !!(status.hasMaster && status.hasSelfSigning && status.hasUserSigning);
  free(status);
  if (!all) return null;
  const identity = await machine.getIdentity(new UserId(entry.u));
  if (!identity) return null;
  let same = false;
  try {
    same = identity.isVerified() && keyOf(JSON.parse(identity.masterKey)) === master;
  } finally {
    free(identity);
  }
  if (!same) return null;
  const bundle = await machine.exportSecretsBundle();
  try {
    return bundle.to_json();
  } finally {
    free(bundle);
  }
}

// The first kept store that can rescue this account: {d, bundle, master} (`master`: the master key it matched), or null. `serverMaster()` answers the master key the chat server lists now.
export async function findRescue(session, { serverMaster, open = openRaw, stopped = () => false } = {}) {
  if (!hasLocks() || !session || !session.boot || typeof serverMaster !== "function") return null;
  const kept = candidates(session);
  if (!kept.length) return null;
  let master = null;
  try { master = await serverMaster(); } catch (e) { return null; }
  if (!master) return null;
  const keys = await keysFor(session, kept);
  if (!keys) return null;
  try {
    for (const entry of kept) {
      if (stopped()) return null;
      const key = keys.get(entry.d);
      if (!key) continue;
      if (entry.kid && key.kid !== entry.kid) continue;        // a key that can't open it any more: the harvest deletes it
      const found = await underStoreLock(entry.p, async () => {
        let machine = null;
        try {
          try {
            machine = await open(entry, key.key);
          } finally {
            key.key.fill(0);
          }
          return await bundleOf(machine, entry, master);
        } catch (e) {
          return null;                                           // a store that won't open or read: not this one
        } finally {
          try { if (machine && typeof machine.close === "function") machine.close(); } catch (e) { /* closed */ }
        }
      });
      if (found && found !== "skipped") return { d: entry.d, bundle: found, master };
    }
    return null;
  } finally {
    zero(keys);
  }
}
