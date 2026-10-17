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
import {
  codeKey, dialog, fetchKeyInfo, fetchLegacyKey, forgetPrekey, lock, newRecoveryCode, passwordIsRight,
  passwordKey, prekeyFromPassword, saveBlobs, takePrekey, unlock,
} from "./keylock.js";

const cachedKeys = {};

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

const START_OVER = "I don't have it: start over";

async function askPassword(formkey, info) {
  // answers {recoveryKey, prekey}; throws StartOver; null when the password doesn't open the copy (changed since)
  let found = null;
  const answer = await dialog({
    title: "Unlock your messages",
    text: ["Enter your password to read your messages on this device. It stays in this browser: the site can't read your chats."],
    field: "password", fieldLabel: "Password", confirm: "Unlock",
    secondary: info.recovery_blob ? "Use my recovery code instead" : START_OVER,
    check: async (password) => {
      if (!password) return "Enter your password.";
      if (!(await passwordIsRight(formkey, password))) return "That isn't your password.";
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

async function askCode(info) {
  let recoveryKey = null;
  const answer = await dialog({
    title: "Enter your recovery code",
    text: ["Your password changed since this chat key was locked with it. The recovery code you saved when chat was set up opens it."],
    field: "text", fieldLabel: "Recovery code", confirm: "Unlock", secondary: START_OVER,
    check: async (code) => {
      try {
        recoveryKey = await unlock(await codeKey(code, info.salt), info.recovery_blob);
        return null;
      } catch (e) {
        return "That code doesn't open it. Check for typos.";
      }
    },
  });
  if (answer.secondary) throw new StartOver();
  return recoveryKey;
}

async function getPrekey(formkey, known) {
  // the pre-key to lock the password copy with: from sign-in, from a password typed here, or asked for now
  if (known) return known;
  const stored = takePrekey();
  if (stored) return stored;
  let prekey = null;
  await dialog({
    title: "Protect your messages",
    text: ["Enter your password once. It locks the key to your messages in this browser, so the site can never read them."],
    field: "password", fieldLabel: "Password", confirm: "Continue",
    check: async (password) => {
      if (!password) return "Enter your password.";
      if (!(await passwordIsRight(formkey, password))) return "That isn't your password.";
      prekey = await prekeyFromPassword(password);
      return null;
    },
  });
  return prekey;
}

async function showNewCode(formkey, info, recoveryKey) {
  const code = newRecoveryCode();
  await saveBlobs(formkey, { recovery_blob: await lock(await codeKey(code, info.salt), recoveryKey) });
  await dialog({
    title: "Save your recovery code",
    text: ["If you ever forget your password, this code is the only way to read your old messages. Keep it somewhere safe, like a password manager.",
           "It won't be shown again."],
    code, confirm: "I've saved it",
  });
}

async function lockCopies(formkey, info, recoveryKey, prekey, { needCode }) {
  const pre = await getPrekey(formkey, prekey);
  await saveBlobs(formkey, { password_blob: await lock(await passwordKey(pre, info.salt), recoveryKey) });
  forgetPrekey();
  if (needCode) await showNewCode(formkey, info, recoveryKey);
}

async function primeAndRestore(client, recoveryKey) {
  const crypto = client.getCrypto();
  const keyId = await client.secretStorage.getDefaultKeyId();
  if (keyId) cachedKeys[keyId] = decodeRecoveryKey(recoveryKey);
  await crypto.bootstrapCrossSigning({ authUploadDeviceSigningKeys });    // imports the keys from 4S and signs this device
  await crypto.bootstrapSecretStorage({});
}

async function freshKeys(client) {
  const crypto = client.getCrypto();
  let generated = null;
  await crypto.bootstrapCrossSigning({ setupNewCrossSigning: true, authUploadDeviceSigningKeys });
  await crypto.bootstrapSecretStorage({
    setupNewKeyBackup: true,
    setupNewSecretStorage: true,
    createSecretStorageKey: async () => {
      generated = await crypto.createRecoveryKeyFromPassphrase();
      return generated;
    },
  });
  return generated.encodedPrivateKey;
}

async function startOver(client) {
  const crypto = client.getCrypto();
  const ok = await dialog({
    title: "Start over?",
    text: ["Your chats will work again on this device, but messages from before can't be read here (or anywhere without your old key)."],
    confirm: "Start over", secondary: "Cancel",
  });
  if (ok.secondary) return null;
  await crypto.resetEncryption(authUploadDeviceSigningKeys);
  return freshKeys(client);
}

// Answers true once this device can read and send; false when the member left it locked.
export async function ensureEncryptionReady(client, formkey, { newCode = false } = {}) {
  const crypto = client.getCrypto();
  const info = await fetchKeyInfo();

  if (!(await crypto.userHasCrossSigningKeys())) {
    // a brand-new account: its keys are made here and locked straight away
    const recoveryKey = await freshKeys(client);
    await lockCopies(formkey, info, recoveryKey, null, { needCode: true });
    await afterUnlock(client);
    return true;
  }

  const status = await crypto.getCrossSigningStatus();
  const unlockedHere = !!(status.privateKeysCachedLocally && status.privateKeysCachedLocally.selfSigningKey);
  const migrate = info.legacy || !info.password_blob || !info.recovery_blob;

  if (unlockedHere && !migrate && !newCode) {
    await afterUnlock(client);
    return true;
  }

  // this device needs the recovery key: to unlock, to make the locked copies, or for a new recovery code
  let recoveryKey = null;
  let typedPrekey = null;
  try {
    const signedIn = takePrekey();
    if (signedIn && info.password_blob) {
      try {
        recoveryKey = await unlock(await passwordKey(signedIn, info.salt), info.password_blob);
        typedPrekey = signedIn;
      } catch (e) { /* locked with an older password: ask below */ }
    }
    if (!recoveryKey && info.legacy) {
      recoveryKey = await fetchLegacyKey();                    // read once; the site clears it once locked
    }
    if (!recoveryKey && info.password_blob) {
      const answer = await askPassword(formkey, info);
      if (answer && answer.recoveryKey) {
        recoveryKey = answer.recoveryKey;
        typedPrekey = answer.prekey;
      } else if (answer && (answer.useCode || answer.prekey)) {
        typedPrekey = answer.prekey || null;
        recoveryKey = await askCode(info);
      }
    } else if (!recoveryKey && info.recovery_blob) {
      recoveryKey = await askCode(info);
    }
    if (!recoveryKey) {
      // set up before, but its key was never kept anywhere (an interrupted first set-up: M-04)
      recoveryKey = await startOver(client);
      if (!recoveryKey) return false;
      await lockCopies(formkey, info, recoveryKey, typedPrekey, { needCode: true });
      await afterUnlock(client);
      return true;
    }
  } catch (e) {
    if (!(e instanceof StartOver)) throw e;
    recoveryKey = await startOver(client);
    if (!recoveryKey) return false;
    await lockCopies(formkey, info, recoveryKey, typedPrekey, { needCode: true });
    await afterUnlock(client);
    return true;
  }

  if (!unlockedHere) await primeAndRestore(client, recoveryKey);
  // keep the copies current: the password copy (missing, legacy, or locked with an older password) and the code copy
  const needPassword = info.legacy || !info.password_blob || typedPrekey !== null;
  if (needPassword) {
    await lockCopies(formkey, info, recoveryKey, typedPrekey, { needCode: !info.recovery_blob || newCode });
  } else if (!info.recovery_blob || newCode) {
    await showNewCode(formkey, info, recoveryKey);
  }
  forgetPrekey();
  await afterUnlock(client);
  return true;
}

async function afterUnlock(client) {
  const crypto = client.getCrypto();
  try {
    await crypto.loadSessionBackupPrivateKeyFromSecretStorage();
  } catch (e) { /* no backup yet */ }
  await crypto.checkKeyBackupAndEnable();
  // the history: every room key in the backup, in the background (a long backup takes a while)
  crypto.restoreKeyBackup().catch(() => {});
}

// The call and Space windows are other devices of this account made in this same browser (token.js
// CALL_STORAGE_KEY): signed here, so the people they talk to accept their messages.
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
