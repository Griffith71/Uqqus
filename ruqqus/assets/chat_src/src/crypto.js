// Chat encryption: cross-signing, secret storage (4S) and the key backup, with the recovery key kept ONLY locked by
// this browser (audit M-03; keylock.js). The site stores a copy locked with a key from the member's password and one
// locked with a one-time recovery code; it can open neither. So:
//
//  - a brand-new account makes its keys here, locks the recovery key with the password and a new recovery code, and
//    shows the code once;
//  - a new device unlocks with the pre-key the sign-in form left (nothing to type), or the old server copy of an
//    account set up before this change (read once, locked, then cleared by the site), or by asking for the password,
//    then the recovery code (after a password reset). With neither, the member can start over (older messages are
//    then unreadable);
//  - once unlocked, the device signs itself and restores the message history from the key backup, which the old code
//    switched on but never read (M-03's "history lost on a new device").
//
// Message keys go only to devices their owner has signed (OnlySignedDevicesIsolationMode, set in index.js), so a
// device the site made for someone, without their key, would receive nothing.

import { decodeRecoveryKey } from "matrix-js-sdk/lib/crypto-api/recovery-key.js";
import { newDeviceLine } from "./lines.js";
import {
  askGrant, closeDialogs, codeKey, dialog as keyDialog, fetchKeyInfo, fetchLegacyKey, forgetPrekey, lock, markCodeSaved, newRecoveryCode, passwordIsRight,
  passwordKey, prekeyFromPassword, saveBlobs, takePrekey, unlock,
} from "./keylock.js";

const cachedKeys = {};

// Why this device is new (lines.js, the chat plan's 6.5): shown once, above the first unlock box this page opens, or as a notice when
// the sign-in's pre-key unlocked without asking.
let pendingLine = null;

// The page stopped while it was unlocking (another tab took chat over, its keeper stopped): its client is stopped, but its access token may
// still work, so a button left on screen could still change the account on the server (Start over deletes the key backup and the secret
// storage, and a client that can't finish would leave the site's locked copies naming a key that is gone). From then on nothing here
// asks, makes keys, resets or saves: `gone()` (ensureEncryptionReady's), asked before each, and stopUnlocking() closes what is open.
// Except once new keys are being put on the server (`keysMade`: set as freshKeys, or Start over's reset, begins): the account then holds
// them, and their only copy is in this page's memory until the locked copies are saved and the recovery code shown. Those steps
// only POST to the site and show text, so they are finished whatever happened to the client: their boxes stay open (keylock.js `keep`)
// and the saves go. Stopped there, the next tab found keys on the server and no copies, and was sent to Start over again; or the code box
// closed unseen after its copy was saved, and the member never got the code.
//
// The account's keys changing on the server (freshKeys, and Start over's resetEncryption before it) is one piece that is never cut short:
// `keysMade` is set as it begins, so a page that stops meanwhile goes on and finishes it, and the page's keeper waits for it before it
// stops the client (`critical`: keeper.js holdFor). Stopped half way, the client's OlmMachine closed under it and left the key backup and
// the secret storage deleted, the site's copies naming the old key, and every Unlock failing on the next tab.
//
// Those copies pending are a state other tabs must see too (`keysPending`, and the Web Lock `uqqus-chat-keys:<mxid>` held from the moment
// new keys begin until their copies are saved and the code seen): a page that stopped there let its store go, and the tab that took
// chat over opened the same store, found keys and no copies yet, and offered Start over; pressed, its new copies were then overwritten
// by the stopped page's late save of the deleted key's, and no device could unlock again. So a page waits for that lock before it
// decides anything ("Finishing chat set-up in another tab…"), and the page's keeper doesn't reload away a page that still holds it
// (keeper.js end: it waits for `keysPending()`). It waits `keysWaitMs` at most (two minutes), then reads the key info afresh: a page that holds
// it can be waiting on a person (the code box, the password), and one the member can't see would never let it go.
//
// A frame nobody sees (the hidden Send-in-chat frame, the side panel's while the panel is closed: index.js inSight) never begins new keys
// and never asks anything (`seen`, NotSeen): a box there could never be answered, and new keys begun there would hold that lock while the
// tab lived, with the code shown to nobody. It answers false with a line saying to open Chat; the chat page, or the panel once it is open
// (its thread's Unlock), does it where the member can see it.
class Stopped extends Error {}
class NotSeen extends Error {}
class StartOverFailed extends Error {}  // said as the page's line (`notify`), with chat left locked: the next Unlock can start over again
class RescueFailed extends Error {}     // the same, for a rescue (rescue.js) that couldn't finish
let seen = () => true;
let keysWaitMs = 120000;
let gone = () => false;
let keysMade = false;
let critical = (work) => work;
let waiting = () => {};
// A Start over that failed after it began (its keys may be half changed: new private keys here, the old ones on the server, the backup gone):
// for the rest of this page the next Unlock goes straight back to Start over, never trusting the keys this device now holds.
let resetUnfinished = false;
let rescueHooks = null;                 // the chat page's rescue.js hooks for this account: {find, master, backup} (null: none on this page)
let rescueUnfinished = false;           // this page began a rescue that hasn't finished (also kept in localStorage: rescueMark)
let rescueOffered = false;              // this page offered a rescue (askCode then doesn't send the member to another browser)
let armKeyReset = () => {};             // the page's keeper.armKeyReset: the key upload of a Start over goes through the site (keeper.js)
let disarmKeyReset = () => {};
export const KEYS_LOCK = "uqqus-chat-keys:";
let keysName = null;                     // KEYS_LOCK + this account's Matrix id
let letKeysGo = null;                    // lets the Web Lock go
let keysSettled = null;                  // resolves once the copies are saved, or the attempt ended
let settleKeys = null;

function hasLocks() {
  return !!(globalThis.navigator && navigator.locks && typeof navigator.locks.request === "function");
}

// New keys are about to go on the server: from here their copies are pending, here and for every tab (see the top).
async function beginKeys() {
  keysMade = true;
  if (!keysSettled) keysSettled = new Promise((resolve) => { settleKeys = resolve; });
  if (letKeysGo || !keysName || !hasLocks()) return;
  let release = null;
  const held = new Promise((resolve) => { release = resolve; });
  await new Promise((granted) => {
    navigator.locks.request(keysName, { ifAvailable: true }, (lock) => {
      granted();
      if (!lock) return undefined;                // another page holds it (it shouldn't: readyOrNot waited): go on without it
      letKeysGo = release;
      return held;
    }).catch(() => granted());
  });
}

// The copies are saved and the code seen, or the attempt ended: nothing is pending any more.
function endKeys() {
  keysMade = false;
  if (letKeysGo) letKeysGo();
  letKeysGo = null;
  if (settleKeys) settleKeys();
  settleKeys = null;
  keysSettled = null;
}

// What the page's keeper waits for before it reloads (keeper.js end): a promise while this page's new keys' copies are pending, else null.
export function keysPending() {
  return keysSettled;
}

// Another page of this account, stopped with its new keys' copies still pending (it holds the lock): wait for it to finish them, then
// read the account's key info afresh, rather than find keys with no copies and offer Start over.
async function otherTabFinishing() {
  if (!keysName || !hasLocks()) return;
  const free = await navigator.locks.request(keysName, { ifAvailable: true }, (lock) => !!lock);
  if (free) return;
  waiting(window.tr("Finishing chat set-up in another tab…"));
  const giveUp = typeof AbortController === "function" ? new AbortController() : null;
  let timer = null;
  try {
    await Promise.race([
      navigator.locks.request(keysName, giveUp ? { signal: giveUp.signal } : {}, () => {}).catch(() => {}),
      new Promise((resolve) => { timer = setTimeout(resolve, keysWaitMs); }),
    ]);
  } finally {
    clearTimeout(timer);
    if (giveUp) giveUp.abort();          // a wait that gave up leaves no request queued behind the other page's lock
    waiting("");
  }
}

function stillHere() {
  if (!keysMade && gone()) throw new Stopped();
}

export function stopUnlocking() {
  closeDialogs();
}

async function dialog(options) {
  const finish = keysMade || !!options.keep;           // a box that finishes what is already on the server (see the top, and showNewCode)
  if (!finish) stillHere();
  if (!finish && !seen()) throw new NotSeen();      // a box nobody can see is never opened (new keys' boxes are, see the top)
  let shown = { ...options, keep: finish };
  if (pendingLine) {
    shown = { ...shown, text: [pendingLine].concat(options.text || []) };
    pendingLine = null;
  }
  const answer = await keyDialog(shown);
  if (answer.closed) throw new Stopped();
  if (!finish) stillHere();
  return answer;
}

function save(site, blobs) {
  stillHere();
  return saveBlobs(site, blobs);
}

export const cryptoCallbacks = {
  cacheSecretStorageKey: (keyId, _keyInfo, privateKey) => {
    cachedKeys[keyId] = privateKey;
  },
  getSecretStorageKey: async ({ keys }) => {
    for (const keyId in keys) {
      if (cachedKeys[keyId]) return [keyId, cachedKeys[keyId]];
    }
    return null;
  },
};

async function authUploadDeviceSigningKeys(makeRequest) {
  try {
    return await makeRequest(null);
  } catch (err) {
    const session = err && err.data && err.data.session;
    if (!session) throw err;
    // Accounts provisioned by the Matrix Application Service never have a password, so m.login.dummy is the only
    // completable auth stage: Synapse accepts it for exactly this case.
    return await makeRequest({ type: "m.login.dummy", session });
  }
}

class StartOver extends Error {}

const START_OVER = window.tr("I don't have it: start over");

async function askPassword(site, info) {
  // answers {recoveryKey, prekey}; throws StartOver; null when the password doesn't open the copy (changed since)
  let found = null;
  const answer = await dialog({
    title: window.tr("Unlock your messages"),
    text: [window.tr("Enter your password to read your messages on this device. It stays in this browser: the site can't read your chats.")],
    field: "password", fieldLabel: window.tr("Password"), confirm: window.tr("Unlock"),
    secondary: info.recovery_blob ? window.tr("Use my recovery code instead") : START_OVER,
    check: async (password) => {
      if (!password) return window.tr("Enter your password.");
      if (!(await passwordIsRight(site, password))) return window.tr("That isn't your password.");
      const prekey = await prekeyFromPassword(password);
      try {
        found = { recoveryKey: await unlock(await passwordKey(prekey, info.salt), info.password_blob), prekey };
      } catch (e) {
        found = { recoveryKey: null, prekey };   // the copy was locked with an older password (a reset)
      }
      return null;
    },
  });
  if (answer.secondary) {
    if (info.recovery_blob) return { useCode: true };
    throw new StartOver();
  }
  return found;
}

// The forgotten-password screen, in the owner's order: the recovery code (the field), a browser where chat was used (only when this one had
// nothing to offer: one that did has just been offered and declined), then Start over (the second button). Closing it leaves chat locked,
// with the thread's Unlock to come back.
async function askCode(info) {
  let recoveryKey = null;
  const answer = await dialog({
    title: window.tr("Enter your recovery code"),
    text: [window.tr("Your password changed since this chat key was locked with it. The recovery code you saved when chat was set up opens it.")]
      .concat(rescueOffered ? [] : [window.tr("No code? Sign in with your new password on a browser where you've used chat: it can bring your chat back.")]),
    field: "text", fieldLabel: window.tr("Recovery code"), confirm: window.tr("Unlock"), secondary: START_OVER,
    check: async (code) => {
      try {
        recoveryKey = await unlock(await codeKey(code, info.salt), info.recovery_blob);
        return null;
      } catch (e) {
        return window.tr("That code doesn't open it. Check for typos.");
      }
    },
  });
  if (answer.secondary) throw new StartOver();
  return recoveryKey;
}

async function getPrekey(site, info, known) {
  // the pre-key to lock the password copy with: from a password typed here (or the sign-in's, which opened this account's copy), the
  // sign-in's of this account when it has no copy yet, or asked for now. With a copy, a sign-in pre-key that reaches here didn't open it
  // (readyOrNot tried it first): the password changed since, so it is never used to lock.
  if (known) return known;
  const stored = info.password_blob ? null : takePrekey(site && site.username, site && site.prekeyRef);
  if (stored) return stored;
  let prekey = null;
  await dialog({
    title: window.tr("Protect your messages"),
    text: [window.tr("Enter your password once. It locks the key to your messages in this browser, so the site can never read them.")],
    field: "password", fieldLabel: window.tr("Password"), confirm: window.tr("Continue"),
    check: async (password) => {
      if (!password) return window.tr("Enter your password.");
      if (!(await passwordIsRight(site, password))) return window.tr("That isn't your password.");
      prekey = await prekeyFromPassword(password);
      return null;
    },
  });
  return prekey;
}

async function showNewCode(site, info, recoveryKey, { oldCodeGone = false } = {}) {
  const code = newRecoveryCode();
  await save(site, { recovery_blob: await lock(await codeKey(code, info.salt), recoveryKey) });
  await dialog({
    title: window.tr("Save your recovery code"),
    text: [window.tr("If you ever forget your password, this code is the only way to read your old messages. Keep it somewhere safe, like a password manager."),
           window.tr("It won't be shown again.")].concat(oldCodeGone ? [window.tr("Your old recovery code no longer works.")] : []),
    code, confirm: window.tr("I've saved it"),
    // its copy is on the site already, so the old code no longer works: the box stays, whatever happens to the page, until the member
    // has seen the code (a page another tab took over closed it unseen, and the reminder was all that was left)
    keep: true,
  });
  await markCodeSaved(site);              // only that they said so, and when (Settings > Chat, the chat page's reminder)
}

async function lockCopies(site, info, recoveryKey, prekey, { needCode, oldCodeGone = false }) {
  const pre = await getPrekey(site, info, prekey);
  await save(site, { password_blob: await lock(await passwordKey(pre, info.salt), recoveryKey) });
  forgetPrekey();
  if (needCode) await showNewCode(site, info, recoveryKey, { oldCodeGone });
  endKeys();                            // both copies saved and the code seen: from here a page that stopped stops
}

async function primeAndRestore(client, recoveryKey) {
  stillHere();
  const crypto = client.getCrypto();
  const keyId = await client.secretStorage.getDefaultKeyId();
  if (keyId) cachedKeys[keyId] = decodeRecoveryKey(recoveryKey);
  await crypto.bootstrapCrossSigning({ authUploadDeviceSigningKeys });    // imports the keys from 4S and signs this device
  await crypto.bootstrapSecretStorage({});
}

async function freshKeys(client) {
  stillHere();
  if (!seen()) throw new NotSeen();     // new keys are begun only where the member sees their boxes (see the top)
  await beginKeys();                    // from here the keys are being changed on the server: finished whatever happens to the page
  return critical(makeKeys(client));
}

// `crossSigning` and `keyBackup` false: Start over's resetEncryption has made both already (one upload of new keys, for the one grant),
// so only the new secret storage, and with it the recovery key, is left to make.
async function makeKeys(client, { crossSigning = true, keyBackup = true } = {}) {
  const crypto = client.getCrypto();
  let generated = null;
  if (crossSigning) await crypto.bootstrapCrossSigning({ setupNewCrossSigning: true, authUploadDeviceSigningKeys });
  await crypto.bootstrapSecretStorage({
    setupNewKeyBackup: keyBackup,
    setupNewSecretStorage: true,
    createSecretStorageKey: async () => {
      generated = await crypto.createRecoveryKeyFromPassphrase();
      return generated;
    },
  });
  return generated.encodedPrivateKey;
}

// The rescue (rescue.js): this browser keeps a store of an earlier chat device of this account holding the keys the chat server lists now.
// Offered only once the password is known not to open the account's copy (a reset): the sign-in's pre-key didn't open it, or the password
// typed here didn't. `typed`: the pre-key of a password typed here just now, which locks the new copy; without it, the box asks the password
// (the sign-in's pre-key is never used to lock: getPrekey), and a password that does open the copy after all goes the ordinary way (answered
// as {recoveryKey, prekey}: nothing is rescued, no code replaced). Answers true once this device is unlocked with the rescued keys; false when
// the member said Not now, nothing was found, or the chat server's backup couldn't be read (nothing is changed then).
async function offerRescue(client, site, info, typed = null) {
  if (!rescueHooks) return false;
  let found = null;
  try { found = await rescueHooks.find(); } catch (e) { found = null; }
  if (!found || !found.bundle) return false;
  let backup;
  try { backup = await rescueHooks.backup(); } catch (e) { return false; }   // can't tell what a rescue would do to the backup: not offered now
  rescueOffered = true;                 // the box opens: the recovery code box then doesn't send the member to another browser
  // the chat server keeps a backup the kept keys can't open: a rescue makes a new one, and what only that backup held is read only with the old code
  const backupLost = !!(backup && !(found.bundle.backup && found.bundle.backup.backup_version === backup.version));
  let prekey = typed;
  let opened = null;
  const answer = await dialog({
    title: window.tr("We found your chat on this browser"),
    text: [window.tr("This browser still holds your chat keys from before your password changed. Use them to read your old messages here and on your other devices?"),
           window.tr("You'll get a new recovery code, and the old one will stop working.")]
      .concat(backupLost ? [window.tr("If you still have your recovery code, use it instead: some older messages are only in the backup it opens.")] : []),
    field: typed ? null : "password", fieldLabel: window.tr("Password"), confirm: window.tr("Use them"), secondary: window.tr("Not now"),
    check: typed ? null : async (password) => {
      if (!password) return window.tr("Enter your password.");
      if (!(await passwordIsRight(site, password))) return window.tr("That isn't your password.");
      prekey = await prekeyFromPassword(password);
      try { opened = await unlock(await passwordKey(prekey, info.salt), info.password_blob); } catch (e) { opened = null; }
      return null;
    },
  });
  if (answer.secondary) return false;
  if (opened) return { recoveryKey: opened, prekey };
  stillHere();
  if (!seen()) throw new NotSeen();
  await beginKeys();                    // the account's secret storage is about to change on the server: never cut short once begun
  const recoveryKey = await critical(rescueWith(client, found));
  await lockCopies(site, info, recoveryKey, prekey, { needCode: true, oldCodeGone: true });
  rescueMark(client, false);
  await afterUnlock(client);
  return true;
}

// A rescue under way on this device (localStorage RESCUE_MARK + the account and device; only "1", nothing secret): set before the kept keys
// are imported, taken off once the copies are saved and the new code seen. While it is there the next Unlock, here or after a reload, never
// trusts the keys this device holds (they may be imported with nothing after them done) and finishes the rescue instead (finishRescue).
export const RESCUE_MARK = "uqqus_chat_rescue:";
function rescueMarkName(client) {
  return RESCUE_MARK + client.getUserId() + ":" + client.getDeviceId();
}
function rescueMark(client, on) {
  try {
    if (on) localStorage.setItem(rescueMarkName(client), "1"); else localStorage.removeItem(rescueMarkName(client));
  } catch (e) { /* storage refused: the in-page flag below still holds for this page */ }
  rescueUnfinished = on;
}
function rescueMarked(client) {
  if (rescueUnfinished) return true;
  try { return localStorage.getItem(rescueMarkName(client)) === "1"; } catch (e) { return false; }
}

// The kept store's keys into this device. The chat server's master key and backup are read again right before (the member may have taken
// their time over the box, and a Start over elsewhere since makes the keys stale), and the keys must then be exactly that master key's
// (importSecretsBundle checks nothing, so this is the guard). Then finishRescue. Nothing is uploaded until the keys are known to match.
async function rescueWith(client, found) {
  const crypto = client.getCrypto();
  let master = null;
  let backup = null;
  try {
    master = await rescueHooks.master();
    backup = await rescueHooks.backup();
  } catch (e) {
    throw new RescueFailed(window.tr("Chat couldn't bring your keys over. Reload to try again."));
  }
  if (!master || master !== found.master) {
    throw new RescueFailed(window.tr("Those keys are no longer your account's. Use your recovery code, or start over."));
  }
  rescueMark(client, true);
  try {
    await crypto.importSecretsBundle(found.bundle);
    if ((await crypto.getCrossSigningKeyId()) !== master) throw new Error("the keys aren't the account's");
    return await finishRescue(client, backup);
  } catch (e) {
    console.warn("Chat: the rescue couldn't finish", e);           // the library's own words, for a bug report
    throw new RescueFailed(window.tr("Chat couldn't bring your keys over. Reload to try again."));
  }
}

// With the account's keys on this device: it is signed with them directly (crossSignDevice, never bootstrapCrossSigning, which reads the OLD
// secret storage first and so needs its key, which is what was lost), then a new secret storage whose key is the new recovery key holds them
// and the backup key. The backup is kept when this device's backup key opens the server's; a new one is made only when it can't (the member
// was told in the box), never because the server couldn't be asked (`backup` comes from a read that succeeded: a failed one stops the rescue).
async function finishRescue(client, backup) {
  const crypto = client.getCrypto();
  await crypto.crossSignDevice(client.getDeviceId());
  let keep = false;
  if (backup) {
    const trust = await crypto.isKeyBackupTrusted(backup);
    keep = !!(trust && trust.matchesDecryptionKey);
  }
  let generated = null;
  await crypto.bootstrapSecretStorage({
    setupNewSecretStorage: true,
    setupNewKeyBackup: !keep,
    createSecretStorageKey: async () => {
      generated = await crypto.createRecoveryKeyFromPassphrase();
      return generated;
    },
  });
  return generated.encodedPrivateKey;
}

// A rescue that stopped part way on this device (its mark is there): the keys are here, so it is finished, with the password, and a new code.
async function finishMarkedRescue(client, site, info) {
  let prekey = null;
  await dialog({
    title: window.tr("Finish bringing your chat over"),
    text: [window.tr("Bringing your chat keys over stopped part way. Enter your password to finish."),
           window.tr("You'll get a new recovery code, and the old one will stop working.")],
    field: "password", fieldLabel: window.tr("Password"), confirm: window.tr("Finish"),
    check: async (password) => {
      if (!password) return window.tr("Enter your password.");
      if (!(await passwordIsRight(site, password))) return window.tr("That isn't your password.");
      prekey = await prekeyFromPassword(password);
      return null;
    },
  });
  stillHere();
  if (!seen()) throw new NotSeen();
  await beginKeys();
  const recoveryKey = await critical((async () => {
    let backup = null;
    try {
      backup = await rescueHooks.backup();
      return await finishRescue(client, backup);
    } catch (e) {
      console.warn("Chat: the rescue couldn't finish", e);
      throw new RescueFailed(window.tr("Chat couldn't bring your keys over. Reload to try again."));
    }
  })());
  await lockCopies(site, info, recoveryKey, prekey, { needCode: true, oldCodeGone: true });
  rescueMark(client, false);
  await afterUnlock(client);
  return true;
}

// A grant for Start over's key upload (routes/chat_key_reset_pages.py): the chat server takes a replacement of keys that already exist only
// from the site, so the member proves who they are here first. The password box, then the two-factor box when the account has two-factor;
// a wrong answer stays in its box. Answers the grant, or null when the member cancelled.
async function proveForStartOver(site, info, { title, text, confirm }) {
  let password = null;
  let grant = null;
  const first = await dialog({
    title, text, field: "password", fieldLabel: window.tr("Password"), confirm, secondary: window.tr("Cancel"),
    check: async (typed) => {
      if (!typed) return window.tr("Enter your password.");
      if (info.mfa) {
        if (!(await passwordIsRight(site, typed))) return window.tr("That isn't your password.");
        password = typed;
        return null;
      }
      const answer = await askGrant(site, typed);
      if (!answer.grant) return answer.error;
      grant = answer.grant;
      return null;
    },
  });
  if (first.secondary) return null;
  if (grant) return grant;
  const second = await dialog({
    title: window.tr("Enter your two-factor code"),
    text: [window.tr("The code from your authenticator app, or one of your backup codes.")],
    field: "text", fieldLabel: window.tr("Two-factor code"), confirm, secondary: window.tr("Cancel"),
    check: async (code) => {
      if (!code) return window.tr("Enter your two-factor code.");
      const answer = await askGrant(site, password, code);
      if (!answer.grant) return answer.error;
      grant = answer.grant;
      return null;
    },
  });
  password = null;
  return second.secondary ? null : grant;
}

// Start over deletes the account's key backup on the server and makes new keys; it never touches another device's store (5.5). The
// chat server asks a sign-in stage for replacing keys that already exist, which the site's accounts can't give it, so the member's
// password buys a grant FIRST (before anything is deleted: a wrong password changes nothing), and the keeper sends that one upload
// through the site with it (keeper.js armKeyReset). Messages this browser could read come back here: the harvest then exports every
// session of this browser's kept stores into the new device, since their backup is gone.
async function startOver(client, site, info) {
  const crypto = client.getCrypto();
  const grant = await proveForStartOver(site, info, {
    title: window.tr("Start over?"),
    text: [window.tr("Your chats will work again on this device, but messages from before can't be read here (or anywhere without your old key)."),
           window.tr("Messages this browser could already read will come back here."),
           window.tr("This also ends any rescue from a browser where you used chat."),
           window.tr("Enter your password to confirm.")],
    confirm: window.tr("Start over"),
  });
  if (!grant) return null;
  stillHere();
  if (!seen()) throw new NotSeen();
  await beginKeys();                    // the reset and the new keys are one piece (see the top): never cut short once begun
  return critical((async () => {
    armKeyReset(grant);
    try {
      await crypto.resetEncryption(authUploadDeviceSigningKeys);
    } catch (e) {
      // the old keys may be gone already (resetEncryption deletes the backup before it uploads): asked once more, in a box that stays
      // open whatever happens to the page, and the new keys made again; a second failure is said plainly
      disarmKeyReset();
      resetUnfinished = true;
      const again = await proveForStartOver(site, info, {
        title: window.tr("Finish starting over"),
        text: [window.tr("Chat couldn't finish starting over. Enter your password to try again.")],
        confirm: window.tr("Try again"),
      }).catch(() => null);
      if (!again) throw new StartOverFailed(window.tr("Chat couldn't finish starting over. Reload to try again."));
      armKeyReset(again);
      try {
        // the whole reset again, whichever of its steps failed: what is left is deleted, everything made new (the old secret storage
        // may still be there, and new keys can't be put in it without its key, which this page doesn't have)
        await crypto.resetEncryption(authUploadDeviceSigningKeys);
      } catch (e2) {
        throw new StartOverFailed(window.tr("Chat couldn't finish starting over. Reload to try again."));
      } finally {
        disarmKeyReset();
      }
    }
    disarmKeyReset();
    const made = await makeKeys(client, { crossSigning: false, keyBackup: false });
    resetUnfinished = false;
    return made;
  })());
}

// Answers true once this device can read and send; false when the member left it locked. `why`: why this device is new (6.5);
// `notify(text)` shows that line when nothing was asked. `site`: the page's boot object, whose form key keylock.js refreshes in place
// when a credential event elsewhere made it stale (site.js sitePost). `stopped()`: whether the page has stopped (answers false then, having
// changed nothing more). `hold(work)`: the page's keeper waits for `work` before it stops the client (keeper.js holdFor). `wait(text)`:
// shows a line while another tab finishes this account's new keys' copies ("" takes it away), for `waitMs` at most. `inSight()`: whether the
// member can see this page (a hidden frame begins no keys and asks nothing: false, with `notify` saying to open Chat).
export async function ensureEncryptionReady(client, site, { newCode = false, why = null, notify = null, stopped = () => false,
  hold = (work) => work, wait = () => {}, inSight = () => true, waitMs = 120000, keyReset = null, rescue = null } = {}) {
  pendingLine = newDeviceLine(why);
  rescueHooks = rescue && typeof rescue.find === "function" ? rescue : null;
  rescueOffered = false;
  armKeyReset = keyReset && typeof keyReset.arm === "function" ? keyReset.arm : () => {};
  disarmKeyReset = keyReset && typeof keyReset.disarm === "function" ? keyReset.disarm : () => {};
  gone = stopped;
  critical = hold;
  waiting = wait;
  seen = inSight;
  keysWaitMs = waitMs;
  keysMade = false;
  const mxid = (site && site.mxid) || (typeof client.getUserId === "function" ? client.getUserId() : null);
  keysName = mxid ? KEYS_LOCK + mxid : null;
  try {
    return await readyOrNot(client, site, newCode);
  } catch (e) {
    if (e instanceof Stopped) return false;
    if (e instanceof StartOverFailed || e instanceof RescueFailed) {
      pendingLine = e.message;
      return false;
    }
    if (e instanceof NotSeen) {
      const open = window.tr("Open Chat to finish setting up your messages on this device.");
      pendingLine = pendingLine ? pendingLine + " " + open : open;
      return false;
    }
    throw e;
  } finally {
    endKeys();
    if (pendingLine && notify && !gone()) notify(pendingLine);
    pendingLine = null;
  }
}

async function readyOrNot(client, site, newCode) {
  const crypto = client.getCrypto();
  await otherTabFinishing();
  stillHere();
  const info = await fetchKeyInfo();
  if (newCode && info.recovery_blob) {
    // asked first: a link to /chat?recovery=new (from Settings > Chat, or from anywhere) must never replace the code by itself
    const sure = await dialog({
      title: window.tr("Make a new recovery code?"),
      text: [window.tr("Your current recovery code will stop working.")],
      confirm: window.tr("Make a new code"), secondary: window.tr("Cancel"),
    });
    if (sure.secondary) newCode = false;
  }

  if (!(await crypto.userHasCrossSigningKeys())) {
    // a brand-new account: its keys are made here and locked straight away
    const recoveryKey = await freshKeys(client);
    await lockCopies(site, info, recoveryKey, null, { needCode: true });
    await afterUnlock(client);
    return true;
  }

  if (rescueHooks && rescueMarked(client)) return finishMarkedRescue(client, site, info);

  if (resetUnfinished) {
    // a Start over that failed in this page: its keys can't be trusted, and nothing but finishing it helps (see resetUnfinished)
    const recoveryKey = await startOver(client, site, info);
    if (!recoveryKey) return false;
    await lockCopies(site, info, recoveryKey, null, { needCode: true });
    await afterUnlock(client);
    return true;
  }

  const status = await crypto.getCrossSigningStatus();
  const unlockedHere = !!(status.privateKeysCachedLocally && status.privateKeysCachedLocally.selfSigningKey);
  const migrate = info.legacy || !info.password_blob || !info.recovery_blob;

  if (unlockedHere && !migrate && !newCode) {
    forgetPrekey();                  // this device kept its keys over a log out, so the sign-in's pre-key has nothing to open
    await afterUnlock(client);
    return true;
  }

  // this device needs the recovery key: to unlock, to make the locked copies, or for a new recovery code
  let recoveryKey = null;
  let typedPrekey = null;
  try {
    const signedIn = takePrekey(site && site.username, site && site.prekeyRef);
    let signedInFailed = false;
    if (signedIn && info.password_blob) {
      try {
        recoveryKey = await unlock(await passwordKey(signedIn, info.salt), info.password_blob);
        typedPrekey = signedIn;
      } catch (e) { signedInFailed = true; /* locked with an older password: ask below */ }
    }
    if (!recoveryKey && info.legacy) {
      recoveryKey = await fetchLegacyKey();                    // read once; the site clears it once locked
    }
    // the copy is locked with another password (a reset): before asking for the recovery code, a store this browser kept may hold the keys
    if (!recoveryKey && signedInFailed && !info.legacy && !newCode) {
      const rescued = await offerRescue(client, site, info);
      if (rescued === true) return true;
      if (rescued && rescued.recoveryKey) {               // the password typed in the box opens the copy after all: the ordinary way
        recoveryKey = rescued.recoveryKey;
        typedPrekey = rescued.prekey;
      }
    }
    if (!recoveryKey && info.password_blob) {
      const answer = await askPassword(site, info);
      if (answer && answer.recoveryKey) {
        recoveryKey = answer.recoveryKey;
        typedPrekey = answer.prekey;
      } else if (answer && (answer.useCode || answer.prekey)) {
        typedPrekey = answer.prekey || null;
        // the password typed here doesn't open the copy (a reset): a kept store may rescue it before the recovery code is asked for
        if (answer.prekey && !signedInFailed && !newCode && (await offerRescue(client, site, info, answer.prekey))) return true;
        recoveryKey = await askCode(info);
      }
    } else if (!recoveryKey && info.recovery_blob) {
      recoveryKey = await askCode(info);
    }
    if (!recoveryKey) {
      // set up before, but its key was never kept anywhere (an interrupted first set-up: M-04)
      recoveryKey = await startOver(client, site, info);
      if (!recoveryKey) return false;
      await lockCopies(site, info, recoveryKey, typedPrekey, { needCode: true });
      await afterUnlock(client);
      return true;
    }
  } catch (e) {
    if (!(e instanceof StartOver)) throw e;
    recoveryKey = await startOver(client, site, info);
    if (!recoveryKey) return false;
    await lockCopies(site, info, recoveryKey, typedPrekey, { needCode: true });
    await afterUnlock(client);
    return true;
  }

  if (!unlockedHere) await primeAndRestore(client, recoveryKey);
  // keep the copies current: the password copy (missing, legacy, or locked with an older password) and the code copy
  const needPassword = info.legacy || !info.password_blob || typedPrekey !== null;
  if (needPassword) {
    await lockCopies(site, info, recoveryKey, typedPrekey, { needCode: !info.recovery_blob || newCode });
  } else if (!info.recovery_blob || newCode) {
    await showNewCode(site, info, recoveryKey);
  }
  forgetPrekey();
  await afterUnlock(client);
  return true;
}

async function afterUnlock(client) {
  stillHere();
  const crypto = client.getCrypto();
  try {
    await crypto.loadSessionBackupPrivateKeyFromSecretStorage();
  } catch (e) { /* no backup yet */ }
  await crypto.checkKeyBackupAndEnable();
  // the history: every room key in the backup, in the background (a long backup takes a while)
  crypto.restoreKeyBackup().catch(() => {});
}

// The call and Space windows are other devices of this account made in this same browser (token.js, the `call` kind in
// the index): signed here, so the people they talk to accept their messages.
export async function signOwnDevices(client, deviceIds) {
  const crypto = client.getCrypto();
  for (const deviceId of deviceIds) {
    if (!deviceId || deviceId === client.getDeviceId()) continue;
    try {
      const status = await crypto.getDeviceVerificationStatus(client.getUserId(), deviceId);
      if (status && status.signedByOwner) continue;
      await crypto.crossSignDevice(deviceId);
    } catch (e) { /* not known yet: signed next time */ }
  }
}
