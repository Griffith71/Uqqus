// The call window (templates/call.html, routes/calls.py): a voice or video call in one chat, in a window of its own
// so the rest of the site can be browsed meanwhile. It is a second Matrix device of the account (token.js
// CALL_STORAGE_KEY, its own crypto store), and only one call window runs per browser (a Web Lock).
//
// The server says who is in the call (join / heartbeat, live updates on the call's topic); the browsers connect to
// each other through the relay (rtc/mesh.js) and never to anyone the server does not list. Every name goes in as text.

import * as sdk from "matrix-js-sdk";
import { ClientEvent } from "matrix-js-sdk";
import { initAsync as initCryptoWasm } from "@matrix-org/matrix-sdk-crypto-wasm";

import { ensureSession, CALL_STORAGE_KEY } from "./token.js";
import { LocalMedia, MediaRefused, levelMeter, resumeAudio } from "./rtc/media.js";
import { Mesh } from "./rtc/mesh.js";

const boot = window.UQQUS_CALL_BOOT;
const byId = (id) => document.getElementById(id);

const state = {
  client: null, media: null, mesh: null, callId: null, me: null,
  roster: [], tiles: new Map(), meters: new Map(), connected: new Set(),
  joinedAt: 0, ended: false, events: null, heartbeat: null, ringTimer: null, clock: null,
};

// --- talking to the server -------------------------------------------------------------------------------------
async function api(action, fields = {}) {
  const body = new URLSearchParams({ formkey: boot.formkey, room: boot.roomId, device_id: state.me ? state.me.deviceId : "", ...fields });
  const resp = await fetch(`/api/calls/${action}`, { method: "POST", credentials: "same-origin", body, headers: { Accept: "application/json" } });
  let data = {};
  try {
    data = await resp.json();
  } catch (e) {
    data = {};
  }
  if (!resp.ok) throw new Error(data.error || "The call couldn't continue.");
  return data;
}

// --- drawing -------------------------------------------------------------------------------------------------
function setStatus(text) {
  byId("call-status").textContent = text || "";
}

function fatal(text) {
  byId("call-fatal-text").textContent = text;
  byId("call-fatal").hidden = false;
  byId("call-stage").hidden = true;
  byId("call-controls").hidden = true;
}

function tile(key, info, mine) {
  let el = state.tiles.get(key);
  if (el) return el;
  el = document.createElement("div");
  el.className = "call-tile";
  el.dataset.key = key;
  const video = document.createElement("video");
  video.autoplay = true;
  video.playsInline = true;
  if (mine) video.muted = true;               // never hear yourself
  const audio = document.createElement("audio");
  audio.autoplay = true;
  const face = document.createElement("div");
  face.className = "call-face";
  const img = document.createElement("img");
  img.alt = "";
  if (typeof info.profile_url === "string" && /^(https?:\/\/|\/)/.test(info.profile_url)) img.src = info.profile_url;
  face.appendChild(img);
  const name = document.createElement("div");
  name.className = "call-name";
  name.textContent = mine ? "You" : info.shown_name || "Someone";
  const flag = document.createElement("div");
  flag.className = "call-flag";
  el.append(video, audio, face, name, flag);
  byId("call-tiles").appendChild(el);
  state.tiles.set(key, el);
  layout();
  return el;
}

function layout() {
  const n = state.tiles.size;
  byId("call-tiles").dataset.count = String(Math.min(n, 9));
}

function showStream(key, stream, mine) {
  const el = state.tiles.get(key);
  if (!el) return;
  const video = el.querySelector("video");
  const audio = el.querySelector("audio");
  const hasVideo = stream.getVideoTracks().some((t) => t.enabled && t.readyState === "live");
  if (video.srcObject !== stream) video.srcObject = stream;
  if (!mine && audio.srcObject !== stream) {
    audio.srcObject = stream;
    audio.play().catch((e) => {
      // only a browser that wants a tap before playing sound gets the button
      if (e && e.name === "NotAllowedError") byId("call-unmute-audio").hidden = false;
    });
  }
  video.muted = true;                     // the <audio> plays the sound; the <video> only the picture
  el.classList.toggle("has-video", hasVideo);
  if (!state.meters.has(key)) state.meters.set(key, levelMeter(stream));
}

function dropTile(key) {
  const el = state.tiles.get(key);
  if (el) el.remove();
  state.tiles.delete(key);
  state.meters.delete(key);
  state.connected.delete(key);
  layout();
}

function animateLevels() {
  for (const [key, meter] of state.meters) {
    const el = state.tiles.get(key);
    if (el) el.classList.toggle("speaking", meter() > 0.08);
  }
  if (!state.ended) requestAnimationFrame(animateLevels);
}

function clock() {
  if (!state.joinedAt || !state.connected.size) return;
  const s = Math.floor((Date.now() - state.joinedAt) / 1000);
  const mm = String(Math.floor(s / 60)).padStart(2, "0");
  const ss = String(s % 60).padStart(2, "0");
  setStatus(s >= 3600 ? `${Math.floor(s / 3600)}:${mm.slice(-2)}:${ss}` : `${mm}:${ss}`);
}

// --- who is in the call ------------------------------------------------------------------------------------------
function applyRoster(peers) {
  state.roster = peers || [];
  const keys = new Set(state.roster.map((p) => `${p.mxid}|${p.device_id}`));
  for (const key of [...state.tiles.keys()]) if (key !== "me" && !keys.has(key)) dropTile(key);
  for (const p of state.roster) tile(`${p.mxid}|${p.device_id}`, p, false);
  state.mesh.setRoster(state.roster);
  if (!state.roster.length && !boot.isGroup && state.connected.size === 0) setStatus(boot.answer ? "Connecting…" : "Ringing…");
  // a voice channel is there whether or not anyone else is
  else if (boot.isVoice && !state.roster.length && state.connected.size === 0) setStatus("You're the only one here.");
}

async function beat() {
  if (state.ended) return;
  try {
    const data = await api("heartbeat");
    if (!data.in_call) return end(data.reason || "The call ended.");
    applyRoster(data.peers);
  } catch (e) {
    // a missed beat is fine: the server allows three
  }
}

// --- ending ---------------------------------------------------------------------------------------------------
async function end(reason, { leave = true } = {}) {
  if (state.ended) return;
  state.ended = true;
  clearInterval(state.heartbeat);
  clearInterval(state.clock);
  clearTimeout(state.ringTimer);
  if (state.events) state.events.close();
  if (state.mesh) state.mesh.close();
  if (state.media) state.media.stop();
  let summary = null;
  if (leave && state.callId) {
    try {
      const data = await api("leave");
      summary = data.summary || null;
    } catch (e) {
      summary = null;
    }
  }
  // the last one out writes the call into the chat ("Voice call · 12 min"), encrypted like any message
  if (summary && state.client) {
    try {
      await state.client.sendMessage(boot.roomId, { msgtype: "m.notice", body: summary });
    } catch (e) {
      // the chat simply has no line for it
    }
  }
  fatal(reason || "Call ended.");
  byId("call-fatal-close").focus();
  setTimeout(() => window.close(), 2500);
}

// --- controls --------------------------------------------------------------------------------------------------
function wireControls() {
  const mute = byId("call-mute");
  const camera = byId("call-camera");
  mute.addEventListener("click", () => {
    state.media.setMuted(!state.media.muted);
    mute.setAttribute("aria-pressed", String(state.media.muted));
    mute.querySelector("i").className = `fas ${state.media.muted ? "fa-microphone-slash" : "fa-microphone"}`;
    mute.querySelector(".call-label").textContent = state.media.muted ? "Unmute" : "Mute";
    state.tiles.get("me")?.classList.toggle("muted", state.media.muted);
  });
  if (!state.media.videoTrack) {
    camera.hidden = true;
  } else {
    camera.addEventListener("click", () => {
      state.media.setCameraOff(!state.media.cameraOff);
      camera.setAttribute("aria-pressed", String(state.media.cameraOff));
      camera.querySelector("i").className = `fas ${state.media.cameraOff ? "fa-video-slash" : "fa-video"}`;
      camera.querySelector(".call-label").textContent = state.media.cameraOff ? "Camera on" : "Camera off";
      showStream("me", state.media.stream, true);
    });
  }
  byId("call-leave").addEventListener("click", () => end("You left the call."));
  byId("call-fatal-close").addEventListener("click", () => window.close());
  byId("call-unmute-audio").addEventListener("click", () => {
    resumeAudio();
    for (const el of state.tiles.values()) el.querySelector("audio")?.play().catch(() => {});
    byId("call-unmute-audio").hidden = true;
  });

  const picker = byId("call-devices");
  const fill = async () => {
    const { audio, video } = await state.media.devices();
    picker.textContent = "";
    const group = (label, list, kind) => {
      if (!list.length) return;
      const og = document.createElement("optgroup");
      og.label = label;
      list.forEach((d, i) => {
        const o = document.createElement("option");
        o.value = `${kind}:${d.deviceId}`;
        o.textContent = d.label || `${label} ${i + 1}`;
        og.appendChild(o);
      });
      picker.appendChild(og);
    };
    group("Microphone", audio, "audio");
    if (state.media.videoTrack) group("Camera", video, "video");
    const current = state.media.audioTrack && state.media.audioTrack.getSettings().deviceId;
    if (current) picker.value = `audio:${current}`;
  };
  picker.addEventListener("focus", () => fill().catch(() => {}), { once: true });
  picker.addEventListener("change", () => {
    const [kind, ...rest] = picker.value.split(":");
    state.media.useDevice(kind, rest.join(":")).then(() => showStream("me", state.media.stream, true)).catch(() => {
      setStatus("That device couldn't be used.");
    });
  });
  fill().catch(() => {});

  window.addEventListener("pagehide", () => {
    if (state.ended || !state.callId) return;
    const form = new FormData();
    form.set("formkey", boot.formkey);
    form.set("room", boot.roomId);
    form.set("device_id", state.me.deviceId);
    navigator.sendBeacon("/api/calls/leave", form);
  });
}

// --- starting --------------------------------------------------------------------------------------------------
async function startMatrix() {
  const session = await ensureSession(boot, CALL_STORAGE_KEY);
  const client = sdk.createClient({
    baseUrl: session.homeserver_url, accessToken: session.access_token, userId: session.user_id, deviceId: session.device_id,
  });
  await initCryptoWasm("/assets/js/matrix_sdk_crypto_wasm_bg.wasm");
  await client.initRustCrypto({ cryptoDatabasePrefix: "uqqus-call" });
  await new Promise((resolve) => {
    const onSync = (s) => {
      if (s === "PREPARED" || s === "SYNCING") {
        client.off(ClientEvent.Sync, onSync);
        resolve();
      }
    };
    client.on(ClientEvent.Sync, onSync);
    client.startClient({ initialSyncLimit: 1, lazyLoadMembers: true }).catch(() => {});
  });
  return client;
}

async function run() {
  setStatus("Starting…");
  byId("call-title").textContent = boot.label;
  state.client = await startMatrix();
  state.me = { userId: state.client.getUserId(), deviceId: state.client.getDeviceId() };

  let joined;
  try {
    joined = await api("join", { kind: boot.kind });
  } catch (e) {
    return fatal(e.message);
  }
  state.callId = joined.call_id;

  try {
    state.media = await LocalMedia.open(!!joined.video);
  } catch (e) {
    await api("leave").catch(() => {});
    return fatal(e instanceof MediaRefused ? e.message : "The microphone couldn't be started.");
  }
  if (boot.kind === "video" && !joined.video) setStatus("Video is off in this call: you're on audio.");
  else if (state.media.cameraProblem) setStatus("The camera couldn't be started: you're on audio.");

  let turn;
  try {
    turn = await state.client.turnServer();
  } catch (e) {
    turn = null;
  }
  if (!turn || !turn.uris || !turn.uris.length) {
    await api("leave").catch(() => {});
    state.media.stop();
    return fatal("Calls are unavailable right now. Try again later.");
  }

  const mine = tile("me", { shown_name: "You", profile_url: boot.avatar }, true);
  showStream("me", state.media.stream, true);
  mine.classList.add("me");

  state.mesh = new Mesh({
    client: state.client,
    callId: state.callId,
    me: state.me,
    iceServers: [{ urls: turn.uris, username: turn.username, credential: turn.password }],
    media: state.media,
    encrypted: true,
    onStream: (key, stream) => showStream(key, stream, false),
    onPeerState: (key, s) => {
      const el = state.tiles.get(key);
      if (el) el.querySelector(".call-flag").textContent = s === "connected" ? "" : s === "failed" ? "Couldn't connect" : "Connecting…";
      if (s === "connected") {
        state.connected.add(key);
        if (!state.joinedAt) state.joinedAt = Date.now();
        clearTimeout(state.ringTimer);
      } else if (s === "failed" || s === "closed") {
        state.connected.delete(key);
      }
    },
    onPeerGone: (key) => dropTile(key),
  });
  state.meters.set("me", levelMeter(state.media.stream));
  wireControls();
  applyRoster(joined.peers);

  state.events = new EventSource(`/api/live/events?topics=call:${encodeURIComponent(state.callId)}`);
  // one ask for a burst of roster changes (the heartbeat route is limited per account)
  let rosterTimer = null;
  state.events.addEventListener("call.roster", () => {
    if (rosterTimer) return;
    rosterTimer = setTimeout(() => {
      rosterTimer = null;
      beat();
    }, 500 + Math.random() * 500);
  });
  state.events.addEventListener("call.ended", (e) => {
    let reason = "The call ended.";
    try {
      const data = JSON.parse(e.data).data || {};
      if (data.reason === "declined") reason = "They declined the call.";
    } catch (err) {
      // the default reason
    }
    end(reason, { leave: false });
  });
  state.heartbeat = setInterval(beat, (joined.heartbeat_seconds || 10) * 1000);
  state.clock = setInterval(clock, 1000);
  requestAnimationFrame(animateLevels);

  // a 1:1 call nobody answers ends by itself
  if (!boot.isGroup && !joined.peers.length) {
    state.ringTimer = setTimeout(() => {
      if (!state.connected.size) end("No answer.");
    }, (joined.ring_seconds || 45) * 1000);
  }
}

async function main() {
  if (!boot) return;
  if (boot.refusal) return fatal(boot.refusal);          // e.g. a message request not accepted yet
  if (navigator.locks) {
    const got = await new Promise((resolve) => {
      navigator.locks.request("uqqus-call", { ifAvailable: true }, (lock) => {
        resolve(!!lock);
        return lock ? new Promise(() => {}) : null;      // held for as long as this window lives
      });
    });
    if (!got) return fatal("A call is already open in another window.");
  }
  try {
    await run();
  } catch (e) {
    fatal("The call couldn't start. Close this window and try again.");
    if (state.callId) api("leave").catch(() => {});
  }
}

main();
