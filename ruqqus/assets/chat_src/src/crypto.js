// Full E2EE bootstrap: cross-signing + 4S (server-side secret storage) +
// key backup. The recovery key itself is never shown to or entered by the
// user - it's generated once, saved automatically via
// POST /api/chat/recovery_key (see recovery.js), and fetched back
// automatically on every subsequent device via GET /api/chat/recovery_key.
// This is a deliberate, informed tradeoff against pure zero-knowledge
// E2EE: Ruqqus's database can decrypt a user's message history given this
// stored value, in exchange for new devices "just working" with no manual
// key management. See ruqqus/classes/chat.py's ChatIdentity docstring.

import { decodeRecoveryKey } from "matrix-js-sdk/lib/crypto-api/recovery-key.js";
import { saveRecoveryKey } from "./recovery.js";

const cachedKeys = {};

export const cryptoCallbacks = {
  cacheSecretStorageKey: (keyId, _keyInfo, privateKey) => {
    cachedKeys[keyId] = privateKey;
  },
  getSecretStorageKey: async ({ keys }) => {
    for (const keyId in keys) {
      if (cachedKeys[keyId]) return [keyId, cachedKeys[keyId]];
    }
    // No prompt, no fallback - if we don't already have the key cached
    // (primed in ensureEncryptionReady from the server-stored value),
    // secret storage just stays locked for this session.
    return null;
  },
};

async function authUploadDeviceSigningKeys(makeRequest) {
  try {
    return await makeRequest(null);
  } catch (err) {
    const session = err && err.data && err.data.session;
    if (!session) throw err;
    // Accounts provisioned by the Matrix Application Service never have a
    // password, so m.login.dummy is the only completable auth stage -
    // Synapse accepts it for exactly this case.
    return await makeRequest({ type: "m.login.dummy", session });
  }
}

export async function ensureEncryptionReady(client, storedRecoveryKey, formkey) {
  const crypto = client.getCrypto();
  const alreadyExists = await crypto.userHasCrossSigningKeys();

  if (!alreadyExists) {
    await crypto.bootstrapCrossSigning({
      setupNewCrossSigning: true,
      authUploadDeviceSigningKeys,
    });
  } else if (!(await crypto.isCrossSigningReady())) {
    await crypto.bootstrapCrossSigning({ authUploadDeviceSigningKeys });
  }

  let generatedKey = null;
  const ssReady = await crypto.isSecretStorageReady();
  if (!ssReady && !alreadyExists) {
    // Brand new account: generate a key, bootstrap with it, then save it
    // server-side so every future device can fetch it automatically.
    await crypto.bootstrapSecretStorage({
      setupNewKeyBackup: true,
      createSecretStorageKey: async () => {
        generatedKey = await crypto.createRecoveryKeyFromPassphrase();
        return generatedKey;
      },
    });
  } else if (!ssReady && storedRecoveryKey) {
    // Existing account, this device doesn't have the key cached yet, but
    // the server gave us the one we saved previously - prime the cache
    // before bootstrapping so getSecretStorageKey resolves instantly.
    try {
      const keyInfo = await client.secretStorage.getDefaultKeyId();
      if (keyInfo) {
        cachedKeys[keyInfo] = decodeRecoveryKey(storedRecoveryKey);
      }
    } catch (e) {
      // malformed/stale stored key - fall through and let bootstrap below
      // proceed without it rather than throwing
    }
    await crypto.bootstrapSecretStorage({});
  } else if (!ssReady) {
    // Secret storage exists remotely but we have no stored key for it -
    // covers the handful of accounts that reached this state under the
    // previous manual-recovery-key flow, before it was never saved
    // server-side. Skip rather than fail the whole boot; basic encrypted
    // messaging still works via per-device Olm.
    console.warn("Chat: secret storage exists but no recovery key is available for this account.");
  }

  if (generatedKey && generatedKey.encodedPrivateKey) {
    await saveRecoveryKey(formkey, generatedKey.encodedPrivateKey);
  }

  await crypto.checkKeyBackupAndEnable();
}
