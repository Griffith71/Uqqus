// How the browsers in a call find each other: offers, answers and network candidates sent as Matrix to-device
// messages, Olm-encrypted in a private call (helpers/calls.py). A message counts only when
//   - it is for this call and this device,
//   - in an encrypted call, it was decrypted (never a clear one), its Olm sender is the user it claims to be from,
//     and the key that sent it is the key the homeserver lists for the device it names,
//   - and that device is on the server's roster of the call (mesh.js), so a device that is not in the call can't
//     start a connection and listen in.

import { ClientEvent } from "matrix-js-sdk";

export const SIGNAL_TYPE = "org.uqqus.call.signal";

const knownKeys = new Map();       // "user|device" -> Curve25519 key, from the homeserver's (device-signed) list

async function deviceKey(client, userId, deviceId) {
  const id = `${userId}|${deviceId}`;
  if (knownKeys.has(id)) return knownKeys.get(id);
  // a device made a moment ago may reach our list a little after its first message: wait for it, briefly
  for (let attempt = 0; attempt < 6; attempt += 1) {
    const devices = await client.getCrypto().getUserDeviceInfo([userId], true);
    const device = devices.get(userId)?.get(deviceId);
    const key = device ? device.getIdentityKey() : null;
    if (key) {
      knownKeys.set(id, key);
      return key;
    }
    await new Promise((resolve) => setTimeout(resolve, 500 * (attempt + 1)));
  }
  return null;
}

export async function prepare(client, userIds) {
  // fetch the devices of everyone in the call before the first message to them (encryption needs their keys)
  if (userIds.length) await client.getCrypto().getUserDeviceInfo(userIds, true);
}

// A device that has only just been made may not be known here yet (its keys reach us through the rooms we share):
// wait for it rather than send into nothing, which encryption would drop without a word.
const KNOWN_TRIES = 8;

export async function sendSignal(client, encrypted, peer, payload) {
  if (encrypted) {
    for (let attempt = 0; attempt < KNOWN_TRIES; attempt += 1) {
      const devices = await client.getCrypto().getUserDeviceInfo([peer.userId], true);
      if (devices.get(peer.userId)?.has(peer.deviceId)) {
        await client.encryptAndSendToDevice(SIGNAL_TYPE, [{ userId: peer.userId, deviceId: peer.deviceId }], payload);
        return;
      }
      await new Promise((resolve) => setTimeout(resolve, 600 * (attempt + 1)));
    }
    throw new Error("That device isn't known yet.");
  } else {
    await client.sendToDevice(SIGNAL_TYPE, new Map([[peer.userId, new Map([[peer.deviceId, payload]])]]));
  }
}

// accept(senderMxid, senderDevice, content) is called for every message that passes; returns a function that stops
export function listen(client, { encrypted, callId, myDevice, accept }) {
  // one message at a time, in the order they came: a wait for a new device must not let a later message pass
  let queue = Promise.resolve();
  const onMessage = (received) => {
    queue = queue.then(() => handle(received)).catch(() => {});
  };
  const handle = async (received) => {
    const message = received && received.message;
    if (!message || message.type !== SIGNAL_TYPE) return;
    const content = message.content || {};
    if (content.call_id !== callId || content.to_device !== myDevice || typeof content.from_device !== "string") return;
    if (encrypted) {
      const info = received.encryptionInfo;
      if (!info || info.sender !== message.sender) return;
      if (info.senderDevice && info.senderDevice !== content.from_device) return;
      try {
        if ((await deviceKey(client, message.sender, content.from_device)) !== info.senderCurve25519KeyBase64) return;
      } catch (e) {
        return;
      }
    }
    accept(message.sender, content.from_device, content);
  };
  client.on(ClientEvent.ReceivedToDeviceMessage, onMessage);
  return () => client.off(ClientEvent.ReceivedToDeviceMessage, onMessage);
}
