// A call without a media server: one RTCPeerConnection per other device, always through the site's relay
// (iceTransportPolicy "relay": nobody learns anyone's IP address). Both sides of a pair may make an offer; the
// "perfect negotiation" pattern settles a clash (the polite side gives way). In a Space only the people on stage
// send: `sends(info)` says whether this window sends to a peer, `hears(info)` whether it plays what the peer sends,
// and a pair where neither is true is never connected.
//
// Connections are made only to the devices on the server's roster (setRoster): a signal from anyone else waits a
// short while for the roster to name them, then is dropped.

import { listen, prepare, sendSignal } from "./signalling.js";
import { MAX_AUDIO_BITRATE, MAX_VIDEO_BITRATE } from "./media.js";

const PENDING_MS = 15000;
const PENDING_MAX = 60;

export class Mesh {
  constructor({ client, callId, me, iceServers, media, encrypted, sends, hears, onStream, onPeerState, onPeerGone }) {
    Object.assign(this, { client, callId, me, iceServers, media, encrypted });
    this.sends = sends || (() => true);
    this.hears = hears || (() => true);
    this.onStream = onStream || (() => {});
    this.onPeerState = onPeerState || (() => {});
    this.onPeerGone = onPeerGone || (() => {});
    this.peers = new Map();
    this.pending = new Map();
    this.closed = false;
    this.myKey = `${me.userId}|${me.deviceId}`;
    this.stopListening = listen(client, {
      encrypted, callId, myDevice: me.deviceId,
      accept: (sender, device, content) => this.onSignal(`${sender}|${device}`, content),
    });
    this.media = null;
    this.setMedia(media);
  }

  // the microphone (or none: a Space listener has none until they are invited on stage)
  setMedia(media) {
    if (this.media === media) return;
    this.media = media || null;
    if (media && !media.meshHooked) {
      media.onChange((kind, track) => this.replaceTrack(kind, track));
      media.meshHooked = true;
    }
    for (const peer of this.peers.values()) this.updateSending(peer);
  }

  // entries: [{ mxid, device_id, ...anything the UI wants }], everyone in the call but this window
  async setRoster(entries) {
    if (this.closed) return;
    const wanted = new Map();
    for (const e of entries) {
      const key = `${e.mxid}|${e.device_id}`;
      if (key !== this.myKey && (this.sends(e) || this.hears(e))) wanted.set(key, e);
    }
    for (const key of [...this.peers.keys()]) if (!wanted.has(key)) this.drop(key);
    const fresh = [...wanted.entries()].filter(([key]) => !this.peers.has(key));
    if (fresh.length && this.encrypted) {
      try {
        await prepare(this.client, [...new Set(fresh.map(([, e]) => e.mxid))]);
      } catch (e) {
        // the first message will try again
      }
    }
    for (const [key, info] of wanted) {
      const peer = this.peers.get(key);
      if (peer) {
        peer.info = info;
        this.updateSending(peer);
      } else {
        this.connect(key, info);
      }
    }
  }

  connect(key, info) {
    const pc = new RTCPeerConnection({ iceServers: this.iceServers, iceTransportPolicy: "relay", bundlePolicy: "max-bundle" });
    const peer = {
      key, info, pc,
      userId: info.mxid, deviceId: info.device_id,
      polite: this.myKey > key,
      makingOffer: false, ignoreOffer: false,
      senders: [], candidates: [],
      stream: new MediaStream(),
      chain: Promise.resolve(),
      sending: Promise.resolve(),           // one message at a time to each peer: order matters
    };
    this.peers.set(key, peer);

    pc.ontrack = ({ track }) => {
      if (!peer.stream.getTracks().includes(track)) peer.stream.addTrack(track);
      track.onended = () => peer.stream.removeTrack(track);
      this.onStream(key, peer.stream, peer.info);
    };
    pc.onicecandidate = ({ candidate }) => {
      this.send(peer, { kind: "candidate", candidate: candidate ? candidate.toJSON() : null });
    };
    pc.onnegotiationneeded = async () => {
      try {
        peer.makingOffer = true;
        await pc.setLocalDescription();
        this.send(peer, { kind: "description", description: pc.localDescription.toJSON() });
      } catch (e) {
        // the other side's offer won; theirs is answered instead
      } finally {
        peer.makingOffer = false;
      }
    };
    pc.onconnectionstatechange = () => {
      const state = pc.connectionState;
      if (state === "connected") this.limitBitrate(peer);
      if (state === "failed" && !peer.polite) pc.restartIce();
      this.onPeerState(key, state, peer.info);
    };

    this.updateSending(peer);
    this.onPeerState(key, "new", info);

    const waiting = this.pending.get(key);
    if (waiting) {
      this.pending.delete(key);
      for (const item of waiting) this.onSignal(key, item.content);
    }
  }

  // add or take away this window's tracks for one peer (a Space speaker moved on or off stage)
  updateSending(peer) {
    const should = this.media && this.sends(peer.info);
    if (should && !peer.senders.length) {
      for (const track of this.media.stream.getTracks()) peer.senders.push(peer.pc.addTrack(track, this.media.stream));
    } else if (!should && peer.senders.length) {
      for (const sender of peer.senders) {
        try {
          peer.pc.removeTrack(sender);
        } catch (e) {
          // already gone
        }
      }
      peer.senders = [];
    }
  }

  limitBitrate(peer) {
    for (const sender of peer.senders) {
      const kind = sender.track && sender.track.kind;
      if (!kind) continue;
      const params = sender.getParameters();
      if (!params.encodings || !params.encodings.length) params.encodings = [{}];
      params.encodings[0].maxBitrate = kind === "video" ? MAX_VIDEO_BITRATE : MAX_AUDIO_BITRATE;
      sender.setParameters(params).catch(() => {});
    }
  }

  send(peer, body) {
    if (this.closed && body.kind !== "bye") return;
    const payload = { ...body, call_id: this.callId, from_device: this.me.deviceId, to_device: peer.deviceId };
    peer.sending = peer.sending.then(() => sendSignal(this.client, this.encrypted, peer, payload)).catch(() => {});
  }

  onSignal(key, content) {
    const peer = this.peers.get(key);
    if (!peer) {
      // not on the roster (yet): kept briefly in case the roster names them, never acted on before that
      const now = Date.now();
      const list = (this.pending.get(key) || []).filter((item) => now - item.at < PENDING_MS);
      if (list.length < PENDING_MAX) list.push({ at: now, content });
      this.pending.set(key, list);
      return;
    }
    // one message at a time per peer: the order of descriptions and candidates matters
    peer.chain = peer.chain.then(() => this.handle(peer, content)).catch(() => {});
  }

  async handle(peer, content) {
    const { pc } = peer;
    if (content.kind === "bye") {
      this.drop(peer.key);
      return;
    }
    if (content.kind === "description" && content.description) {
      const description = content.description;
      const collision = description.type === "offer" && (peer.makingOffer || pc.signalingState !== "stable");
      peer.ignoreOffer = !peer.polite && collision;
      if (peer.ignoreOffer) return;
      await pc.setRemoteDescription(description);
      for (const candidate of peer.candidates.splice(0)) await pc.addIceCandidate(candidate).catch(() => {});
      if (description.type === "offer") {
        this.updateSending(peer);
        await pc.setLocalDescription();
        this.send(peer, { kind: "description", description: pc.localDescription.toJSON() });
      }
      return;
    }
    if (content.kind === "candidate") {
      if (!pc.remoteDescription) {
        peer.candidates.push(content.candidate || null);
        return;
      }
      try {
        await pc.addIceCandidate(content.candidate || null);
      } catch (e) {
        if (!peer.ignoreOffer) throw e;
      }
    }
  }

  replaceTrack(kind, track) {
    for (const peer of this.peers.values()) {
      for (const sender of peer.senders) {
        if (sender.track && sender.track.kind === kind) sender.replaceTrack(track).catch(() => {});
      }
    }
  }

  drop(key) {
    const peer = this.peers.get(key);
    if (!peer) return;
    this.peers.delete(key);
    try {
      peer.pc.close();
    } catch (e) {
      // closed already
    }
    this.onPeerGone(key, peer.info);
  }

  close() {
    for (const peer of this.peers.values()) this.send(peer, { kind: "bye" });
    for (const key of [...this.peers.keys()]) this.drop(key);
    this.closed = true;
    this.stopListening();
  }
}
