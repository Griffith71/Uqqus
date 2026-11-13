// What happens to the stores this browser kept of an account's older chat devices (the chat plan's phase 2, 2.4 and 8).
//
// A device that was signed out or replaced leaves its crypto store here, marked `removed` in the index (token.js), whatever its kind
// (`chat`, or `call`: the call window's and a Space's) or its `x`: a call device is cross-signed by the chat page, and a chat device
// may have been cross-signed before it was ever marked unlocked, so any of them may hold room keys the key backup doesn't have (a key
// arrives, and the backup loop uploads it seconds later). So a `removed` store is never deleted for what its entry says, only for what
// it holds, and only in two ways from here:
//
// - sweepEmpty (every page, once its own store has opened: the chat page and its frames, the call window, a Space): the store is opened
//   raw and found EMPTY (no room keys, no private cross-signing keys, no backup decryption key): it is marked `empty`, then deleted with
//   nothing imported (2.4 case 3). One that holds anything is marked `held` and kept for the harvest. At most 2 stores a page load.
// - harvest (the chat page only, once chat unlocked here: only the account's current chat device can take the keys): its room keys are
//   exported and imported into the current chat device, whose own backup then uploads them; only after the import has resolved is the
//   store deleted (2.4 case 2). Which keys: when the old store's backup is the current one and the count says every session of it is
//   backed up, none; otherwise every session, imported ones included (after a Start over, or a backup reset elsewhere, the old backup is
//   gone and these keys exist nowhere else). At most 2 stores a page load.
//
// Each store is touched only under its own Web Lock (`uqqus-store:<prefix>`, held by every page that has it open), asked for with
// `ifAvailable`: a store in use, or one another page is sweeping or harvesting, is skipped this time. Without Web Locks nothing is touched
// (gap 17). The store is opened through the crypto wasm directly (StoreHandle.openWithKey, OlmMachine.initFromStore), never through
// MatrixClient.initRustCrypto, whose background requests would run on a token that is gone: this path makes no network call. Its key comes
// from /api/chat/store_keys (the site serves it for the account's own site devices, revoked or not). A store whose key id is no longer
// the site's can never be opened again and goes (2.4 case 1). A store that throws counts a failure (`fails`), once a page load (it isn't
// opened again in that load); at 3 it becomes `broken` and is kept (token.js deletes a broken store 30 days later, 2.4 case 4). A store the site serves no key for is left as it is.
//
// The index is written only under the session lock (token.js withIndex), taken inside the store lock: no page ever waits for a store lock
// while it holds the session lock, so the two can't wait on each other.

import { DeviceId, OlmMachine, StoreHandle, UserId } from "@matrix-org/matrix-sdk-crypto-wasm";
import { b64urlDecode } from "./seal.js";
import { STORE_LOCK, deleteStore, postSite, readIndex, withIndex } from "./token.js";

export const PER_LOAD = 2;                        // stores opened a page load, by the sweep and by the harvest each
export const MAX_FAILS = 3;                       // failures before a store is `broken`
const MAX_KEYS = 4;                               // ids /api/chat/store_keys answers at once (helpers/chat_tokens.py MAX_STORE_KEYS)

let sweptThisLoad = 0;
let harvestedThisLoad = 0;
// Stores that failed in this page load (their prefix): not opened again until the next load, by the sweep or the harvest, and never
// charged a second failure in it, so the three failures that make a store `broken` are three page loads, not one bad moment seen twice.
const failedThisLoad = new Set();

export function hasLocks() {
  return !!(globalThis.navigator && navigator.locks && typeof navigator.locks.request === "function");
}

export function free(thing) {
  try { if (thing && typeof thing.free === "function") thing.free(); } catch (e) { /* freed already */ }
}

// The kept store, opened raw: no network call, no MatrixClient.
export async function openRaw(entry, key) {
  const store = await StoreHandle.openWithKey(entry.p, key);
  try {
    return await OlmMachine.initFromStore(new UserId(entry.u), new DeviceId(entry.d), store);
  } finally {
    free(store);                                  // as the SDK does once the machine has it (rust-crypto/index.js)
  }
}

// What the store holds: its room keys, whether it holds any secret, and the backup it last knew.
async function contents(machine) {
  const counts = await machine.roomKeyCounts();
  const total = Number(counts.total) || 0;
  const backedUp = Number(counts.backedUp) || 0;
  free(counts);
  const status = await machine.crossSigningStatus();
  const secrets = !!(status.hasMaster || status.hasSelfSigning || status.hasUserSigning);
  free(status);
  const backup = await machine.getBackupKeys();
  const version = backup.backupVersion || null;
  const decryption = backup.decryptionKey;
  const decryptionKey = !!decryption;
  free(decryption);
  free(backup);
  return { total, backedUp, version, empty: total === 0 && !secrets && !decryptionKey };
}

// Every session (`() => true`), or none (null): none only when the old store's backup is the one in use now and its count says every
// session it holds is in it. Otherwise everything, imported sessions included: one this store got by import and never uploaded (an
// earlier harvest, keys forwarded by the member's own devices) may have no other copy.
export function exportRule(old, current, total, backedUp) {
  if (old && old === current && backedUp >= total) return null;
  return () => true;
}

// Closed once: a machine the harvest closed before its import isn't closed again by visit().
const closed = new WeakSet();
function close(machine) {
  if (!machine || closed.has(machine)) return;
  closed.add(machine);
  try { if (typeof machine.close === "function") machine.close(); } catch (e) { /* closed already */ }
}

// Whether an export holds any session: its first character after the "[" that isn't white space is not the "]". Read in place, with no copy
// of the string (an export is about 550 characters a session, so a store of tens of thousands of sessions makes several MB).
export function holdsSessions(json) {
  if (typeof json !== "string") return false;
  let i = json.indexOf("[");
  if (i < 0) return false;
  for (i += 1; i < json.length; i += 1) {
    const c = json.charCodeAt(i);
    if (c > 32) return c !== 93;                  // "]"
  }
  return false;
}

// This account's `removed` entries, newest first.
function removedOf(mxid) {
  return readIndex().entries
    .filter((e) => e.u === mxid && e.s === "removed")
    .sort((a, b) => (b.used || b.made || 0) - (a.used || a.made || 0));
}

// Still the same `removed` entry of this account: one that became `forget` (the account is being deleted here) or went is left to whoever
// changed it.
function stillRemoved(entry) {
  return readIndex().entries.some((e) => e.p === entry.p && e.u === entry.u && e.d === entry.d && e.k === entry.k && e.s === "removed");
}

// One change to a `removed` entry, under the session lock, only while it is still `removed`.
function updateRemoved(entry, change) {
  return withIndex((idx) => {
    const e = idx.entries.find((o) => o.p === entry.p && o.u === entry.u && o.d === entry.d && o.k === entry.k);
    if (!e || e.s !== "removed") return false;
    change(e);
    return true;
  }).catch(() => false);
}

function forgetEntry(entry) {
  return withIndex((idx) => {
    idx.entries = idx.entries.filter((o) => !(o.p === entry.p && o.u === entry.u && o.s === "removed"));
  }).catch(() => {});
}

// A failure (it wouldn't open, an export or an import threw): counted at most once a store a page load, and at MAX_FAILS the store is
// `broken` and kept. A broken entry carries no `why`: the next new device says nothing about it (token.js settle tells the reason of a
// store this browser gave up on itself).
async function failed(entry, now) {
  if (failedThisLoad.has(entry.p)) return false;
  failedThisLoad.add(entry.p);
  return updateRemoved(entry, (e) => {
    e.fails = (e.fails || 0) + 1;
    if (e.fails >= MAX_FAILS) {
      e.s = "broken";
      e.bad = now();
      e.why = null;
      e.gen = (e.gen || 0) + 1;
    }
  });
}

// Deleted under the store lock this caller holds: marked first (`mark`: `empty` or `taken`, so a page that dies before the entry goes
// tries the deletion again, never the work), then both databases, then the entry. A delete that failed keeps the marked entry.
async function deleteHeld(entry, mark, now) {
  if (mark && !(await updateRemoved(entry, (e) => { e[mark] = e[mark] || now(); }))) return false;
  if (!(await deleteStore(entry.p))) return false;
  await forgetEntry(entry);
  return true;
}

// The keys of up to MAX_KEYS devices, or null when the site couldn't be asked (the sign-in ended, the network). A key the site doesn't
// serve (not one of this account's site devices) is simply missing.
export async function keysFor(session, entries) {
  const ids = entries.slice(0, MAX_KEYS).map((e) => e.d);
  if (!ids.length) return new Map();
  const got = await postSite(session.boot, "/api/chat/store_keys", { device_ids: ids.join(",") });
  const served = got && got.status === 200 && got.body && got.body.keys && typeof got.body.keys === "object" ? got.body.keys : null;
  if (!served) return null;
  const keys = new Map();
  for (const d of ids) {
    const k = served[d];
    if (!k || typeof k.key !== "string" || typeof k.kid !== "string") continue;
    let raw;
    try { raw = b64urlDecode(k.key); } catch (e) { continue; }
    if (raw.length === 32) keys.set(d, { key: raw, kid: k.kid }); else raw.fill(0);
  }
  return keys;
}

export function zero(keys) {
  if (keys) for (const k of keys.values()) k.key.fill(0);
}

// The store's lock, if it is free: `work()`'s answer, or "skipped".
export function underStoreLock(prefix, work) {
  return navigator.locks.request(STORE_LOCK + prefix, { ifAvailable: true }, (lock) => (lock ? work() : "skipped"))
    .catch(() => "skipped");
}

// Entries already found empty or harvested whose deletion didn't finish: deleted now, nothing opened.
async function finishDeletions(mxid, now) {
  for (const entry of removedOf(mxid).filter((e) => e.empty || e.taken)) {
    await underStoreLock(entry.p, async () => (stillRemoved(entry) ? deleteHeld(entry, null, now) : false));
  }
}

// One store, under its lock: `inspect(machine, found)` decides what happens to it once opened ("delete": found empty or harvested,
// "keep"). Answers what happened: "skipped", "gone" (deleted), "kept", "failed" or "stopped".
async function visit(entry, key, { open, now, stopped, inspect }) {
  return underStoreLock(entry.p, async () => {
    if (stopped() || !stillRemoved(entry)) return "skipped";
    // a key id that is no longer the site's: this store can never be opened again (2.4 case 1). A store given back from a listed
    // database (`kid` null) is opened with the key the site serves.
    if (entry.kid && key.kid !== entry.kid) return (await deleteHeld(entry, null, now)) ? "gone" : "kept";
    let machine = null;
    try {
      try {
        machine = await open(entry, key.key);
      } finally {
        key.key.fill(0);
      }
      const found = await contents(machine);
      const decided = await inspect(machine, found);
      if (decided.stop) return "stopped";
      close(machine);
      machine = null;
      if (decided.keep) {
        await updateRemoved(entry, (e) => { e.held = e.held || now(); });
        return "kept";
      }
      return (await deleteHeld(entry, decided.mark, now)) ? "gone" : "kept";
    } catch (e) {
      close(machine);
      machine = null;
      // a page that stopped meanwhile (its crypto closing under the import) is not the store's fault: kept, nothing counted
      if (stopped()) return "stopped";
      await failed(entry, now);
      return "failed";
    } finally {
      close(machine);
    }
  });
}

// 2.4 case 3: every page, once its own store has opened. `removed` stores of this account not yet looked at (no `held`), each opened raw:
// one found empty goes, one holding anything is marked `held` for the harvest.
export async function sweepEmpty(session, { open = openRaw, now = Date.now, stopped = () => false } = {}) {
  const outcome = [];
  if (!hasLocks() || !session || !session.boot) return outcome;
  const mxid = session.user_id;
  await finishDeletions(mxid, now);
  const left = PER_LOAD - sweptThisLoad;
  if (left <= 0 || stopped()) return outcome;
  const candidates = removedOf(mxid).filter((e) => !e.empty && !e.taken && !e.held && !failedThisLoad.has(e.p));
  if (!candidates.length) return outcome;
  const keys = await keysFor(session, candidates);
  try {
    if (!keys) return outcome;
    for (const entry of candidates) {
      if (sweptThisLoad >= PER_LOAD || stopped()) break;
      const key = keys.get(entry.d);
      if (!key) continue;
      const what = await visit(entry, key, {
        open, now, stopped,
        inspect: async (machine, found) => (found.empty ? { mark: "empty" } : { keep: true }),
      });
      if (what !== "skipped") sweptThisLoad += 1;
      outcome.push({ d: entry.d, what });
    }
    return outcome;
  } finally {
    zero(keys);
  }
}

// 2.4 case 2 and section 8: the chat page, after chat unlocked here (crypto.js ensureEncryptionReady answered true). `client` is the
// page's MatrixClient, whose crypto takes the keys. Every `removed` store of this account, of either kind and whatever its `x`.
// `hold` is the page keeper's holdFor (keeper.js): the import writes the current device's store, and closing its OlmMachine doesn't stop
// an import already under way (the wasm's future owns its own handle on the machine and the store), so a page that stops meanwhile (another
// tab taking chat over) must stop its client, and let the store lock go, only once the import has settled, or the tab that took over
// would open the store beside a machine still writing it.
export async function harvest(client, session, { open = openRaw, now = Date.now, stopped = () => false, hold = (work) => work } = {}) {
  const outcome = [];
  if (!hasLocks() || !session || !session.boot || !client) return outcome;
  const crypto = typeof client.getCrypto === "function" ? client.getCrypto() : null;
  if (!crypto) return outcome;
  const mxid = session.user_id;
  await finishDeletions(mxid, now);
  if (harvestedThisLoad >= PER_LOAD || stopped()) return outcome;
  const candidates = removedOf(mxid).filter((e) => !e.empty && !e.taken && !failedThisLoad.has(e.p));
  if (!candidates.length) return outcome;
  const keys = await keysFor(session, candidates);
  try {
    if (!keys) return outcome;
    for (const entry of candidates) {
      if (harvestedThisLoad >= PER_LOAD || stopped()) break;
      const key = keys.get(entry.d);
      if (!key) continue;
      const what = await visit(entry, key, {
        open, now, stopped,
        inspect: async (machine, found) => {
          if (found.empty) return { mark: "empty" };
          if (stopped()) return { stop: true };
          const current = await crypto.getActiveSessionBackupVersion();
          const rule = exportRule(found.version, current, found.total, found.backedUp);
          if (rule && found.total > 0) {
            const json = await machine.exportRoomKeys(rule);
            // the old machine is closed before the import (the JSON holds the keys, and the store itself stays until the import has
            // resolved), so the two machines and the string are never all in memory at once
            close(machine);
            if (holdsSessions(json)) {
              // this page stopped meanwhile (another tab took chat over, a sign-out): its crypto is closing, so nothing is imported, and
              // the store is kept for the next page
              if (stopped()) return { stop: true };
              // held by the keeper (asked in the same turn as the check above, so a shutdown that has begun never misses it): its
              // shutdown() waits for the import before it stops the client and lets the current store's lock go
              await hold(crypto.importRoomKeysAsJson(json));
              // stopped while it ran: the keys are in the current device's store all the same (its backup uploads them the next time a
              // page opens it), so the old store may still go; only this page's stopping crypto isn't asked anything more
              if (!stopped()) nudgeBackup(crypto);
            }
          }
          return { mark: "taken" };               // only now, the import resolved: the store may go
        },
      });
      if (what !== "skipped") harvestedThisLoad += 1;
      outcome.push({ d: entry.d, what });
    }
    return outcome;
  } finally {
    zero(keys);
  }
}

// Imported keys aren't marked as backed up, so the device's backup loop uploads them; whether an import alone restarts the loop isn't
// known (the SDK restarts it when the OlmMachine reports updated room keys), so it is asked once more. A private member of the SDK's
// RustCrypto, pinned by tests/test_chat_harvest.py; a loop already running ignores it.
function nudgeBackup(crypto) {
  try {
    const manager = crypto.backupManager;
    if (manager && typeof manager.maybeUploadKey === "function") Promise.resolve(manager.maybeUploadKey()).catch(() => {});
  } catch (e) { /* the loop starts when the next key arrives */ }
}

