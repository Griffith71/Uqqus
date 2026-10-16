// A Space's own page (templates/space.html, routes/spaces.py, helpers/spaces.py): who is on stage and listening,
// raised hands, the host's controls, the post's comments as its live discussion, and the replay.
//
// Audio goes browser to browser through the site's relay (rtc/mesh.js): the people on stage send their voice to
// everyone in the Space, listeners only receive. A listener has no microphone until they accept an invitation to
// speak. The server's roster says who is in the Space and in which role; nothing is played from anyone it does not
// list on stage. Every name and comment goes in as text, except a comment's stored, sanitized HTML.

import * as sdk from "matrix-js-sdk";
import { ClientEvent } from "matrix-js-sdk";
import { initAsync as initCryptoWasm } from "@matrix-org/matrix-sdk-crypto-wasm";

import { ensureSession, CALL_STORAGE_KEY } from "./token.js";
import { LocalMedia, MediaRefused, levelMeter, resumeAudio } from "./rtc/media.js";
import { Mesh } from "./rtc/mesh.js";

const boot = window.UQQUS_SPACE_BOOT;
const byId = (id) => document.getElementById(id);
const STAGE = new Set(["host", "cohost", "speaker"]);
const ROLE_LABEL = { host: "Host", cohost: "Co-host", speaker: "Speaker" };

const state = {
  client: null, me: null, mesh: null, media: null, joined: false, ended: false,
  data: null, heartbeat: null, streams: new Map(), meters: new Map(), audios: new Map(),
  lastComment: null, recorder: null, chunks: [], mix: null,
};

// --- the server -------------------------------------------------------------------------------------------------
async function api(action, fields = {}) {
  const body = new URLSearchParams({ formkey: boot.formkey, device_id: state.me ? state.me.deviceId : "", ...fields });
  const resp = await fetch(`/api/spaces/${boot.spaceId}/${action}`, {
    method: "POST", credentials: "same-origin", body, headers: { Accept: "application/json" },
  });
  let data = {};
  try {
    data = await resp.json();
  } catch (e) {
    data = {};
  }
  if (!resp.ok) throw new Error(data.error || "That didn't work. Try again.");
  return data;
}

function say(text, bad) {
  const el = byId("space-status");
  el.textContent = text || "";
  el.classList.toggle("bad", !!bad);
}

// --- drawing ------------------------------------------------------------------------------------------------------
function avatar(url, size) {
  const img = document.createElement("img");
  img.alt = "";
  img.width = img.height = size;
  if (typeof url === "string" && /^(https?:\/\/|\/)/.test(url)) img.src = url;
  return img;
}

function myRole() {
  return state.data && state.data.me ? state.data.me.role : "listener";
}

function onStage() {
  return STAGE.has(myRole());
}

function personTile(p, big) {
  const el = document.createElement("button");
  el.type = "button";
  el.className = `space-person${big ? " big" : ""}`;
  el.dataset.user = String(p.id);
  el.dataset.username = p.username;
  const face = document.createElement("span");
  face.className = "space-face";
  face.appendChild(avatar(p.profile_url, big ? 64 : 44));
  if (p.hand) {
    const hand = document.createElement("i");
    hand.className = "fas fa-hand space-hand-mark";
    hand.setAttribute("aria-label", "Hand raised");
    face.appendChild(hand);
  }
  const name = document.createElement("span");
  name.className = "space-person-name";
  name.textContent = p.id === boot.userId ? "You" : p.shown_name;
  el.append(face, name);
  if (ROLE_LABEL[p.role]) {
    const role = document.createElement("span");
    role.className = "space-role";
    role.textContent = ROLE_LABEL[p.role];
    el.appendChild(role);
  }
  el.addEventListener("click", () => openPerson(p));
  return el;
}

function render() {
  const data = state.data;
  if (!data) return;
  const stage = byId("space-stage");
  const listeners = byId("space-listeners");
  stage.replaceChildren(...data.stage.map((p) => personTile(p, true)));
  listeners.replaceChildren(...data.listeners.map((p) => personTile(p, false)));
  byId("space-count").textContent = `${data.stage.length + data.listener_count} in the Space`;
  byId("space-listener-heading").textContent = `Listeners (${data.listener_count})`;

  const me = data.me;
  byId("space-hand").hidden = onStage() || me.invited || !state.joined;
  byId("space-hand").setAttribute("aria-pressed", String(!!me.hand));
  byId("space-hand-label").textContent = me.hand ? "Lower hand" : "Raise hand";
  byId("space-invite").hidden = !me.invited || !state.joined;
  byId("space-mute").hidden = !onStage() || !state.media;
  byId("space-step-down").hidden = !onStage() || myRole() === "host" || !state.joined;
  byId("space-end").hidden = !(boot.isHost || boot.isAdmin) || data.status !== "live";

  const hands = byId("space-hands");
  hands.replaceChildren();
  if (me.may_run && data.hands.length) {
    const title = document.createElement("div");
    title.className = "space-hands-title";
    title.textContent = "Raised hands";
    hands.appendChild(title);
    for (const id of data.hands) {
      const p = data.listeners.find((x) => x.id === id);
      if (!p) continue;
      const row = document.createElement("div");
      row.className = "space-hand-row";
      const name = document.createElement("span");
      name.textContent = p.shown_name;
      const invite = document.createElement("button");
      invite.type = "button";
      invite.className = "space-btn small";
      invite.textContent = "Invite to speak";
      invite.addEventListener("click", () => act("invite", { username: p.username }));
      row.append(name, invite);
      hands.appendChild(row);
    }
  }
}

// a person's sheet: what the host and co-hosts can do to them, and Report for the Space
function openPerson(p) {
  const me = state.data && state.data.me;
  if (!me || !me.may_run || p.id === boot.userId) return;
  const sheet = byId("space-person-sheet");
  byId("space-person-name").textContent = p.shown_name;
  const actions = byId("space-person-actions");
  actions.replaceChildren();
  const add = (label, action, fields, danger) => {
    const b = document.createElement("button");
    b.type = "button";
    b.className = `space-btn${danger ? " danger" : ""}`;
    b.textContent = label;
    b.addEventListener("click", () => {
      sheet.close();
      act(action, fields);
    });
    actions.appendChild(b);
  };
  if (p.role === "listener" && !p.invited) add("Invite to speak", "invite", { username: p.username });
  if (STAGE.has(p.role) && p.role !== "host") {
    add("Ask to mute", "mute", { username: p.username });
    add("Move to listeners", "listen", { username: p.username });
  }
  if (myRole() === "host" && (p.role === "speaker" || p.role === "cohost")) {
    add(p.role === "cohost" ? "Remove as co-host" : "Make co-host", "cohost", { username: p.username });
  }
  if (p.role !== "host") add("Remove from the Space", "remove", { username: p.username }, true);
  sheet.showModal();
}

async function act(action, fields) {
  try {
    const data = await api(action, fields);
    if (data && data.stage) {
      state.data = data;
      render();
      applyRoster();
    }
  } catch (e) {
    say(e.message, true);
  }
}

// --- audio --------------------------------------------------------------------------------------------------------
function peerKey(p) {
  return `${p.mxid}|${p.device_id}`;
}

function applyRoster() {
  if (!state.mesh || !state.data) return;
  const peers = state.data.peers.filter((p) => !(p.id === boot.userId && p.device_id === state.me.deviceId));
  state.mesh.setRoster(peers);
  // only people the roster puts on stage are ever played
  for (const [key, audio] of state.audios) {
    const p = peers.find((x) => peerKey(x) === key);
    audio.muted = !(p && STAGE.has(p.role));
  }
}

function speaking() {
  for (const [key, meter] of state.meters) {
    const loud = meter() > 0.08;
    const userId = key === "me" ? boot.userId : Number((state.data?.peers.find((p) => peerKey(p) === key) || {}).id);
    document.querySelectorAll(`.space-person[data-user="${userId}"]`).forEach((el) => el.classList.toggle("speaking", loud));
  }
  if (!state.ended) requestAnimationFrame(speaking);
}

async function micOn() {
  if (state.media) return;
  try {
    state.media = await LocalMedia.open(false);
  } catch (e) {
    say(e instanceof MediaRefused ? e.message : "The microphone couldn't be started.", true);
    return;
  }
  state.meters.set("me", levelMeter(state.media.stream));
  state.mesh.setMedia(state.media);
  applyRoster();
  recordAdd(state.media.stream);
  render();
}

function micOff() {
  if (!state.media) return;
  state.mesh.setMedia(null);
  state.media.stop();
  state.media = null;
  state.meters.delete("me");
  applyRoster();
  render();
}

// --- the replay (host only, when the Space is recorded) -------------------------------------------------------------
function recordStart() {
  if (!boot.isHost || !boot.record || state.recorder || !window.MediaRecorder) return;
  try {
    const context = new AudioContext();
    const destination = context.createMediaStreamDestination();
    state.mix = { context, destination, added: new Set() };
    const type = ["audio/webm;codecs=opus", "audio/ogg;codecs=opus", "audio/webm"].find((t) => MediaRecorder.isTypeSupported(t));
    state.recorder = new MediaRecorder(destination.stream, type ? { mimeType: type, audioBitsPerSecond: 48000 } : undefined);
    state.recorder.ondataavailable = (e) => { if (e.data && e.data.size) state.chunks.push(e.data); };
    state.recorder.start(10000);
    if (state.media) recordAdd(state.media.stream);
    for (const stream of state.streams.values()) recordAdd(stream);
  } catch (e) {
    state.recorder = null;
    say("This browser can't record the Space.", true);
  }
}

function recordAdd(stream) {
  if (!state.mix || !stream.getAudioTracks().length || state.mix.added.has(stream.id)) return;
  state.mix.context.createMediaStreamSource(stream).connect(state.mix.destination);
  state.mix.added.add(stream.id);
}

async function recordFinish() {
  if (!state.recorder) return;
  const recorder = state.recorder;
  state.recorder = null;
  await new Promise((resolve) => {
    recorder.onstop = resolve;
    recorder.stop();
  });
  const type = recorder.mimeType || "audio/webm";
  const ext = type.includes("ogg") ? "ogg" : "webm";
  const file = new File(state.chunks, `space-${boot.spaceId}.${ext}`, { type: type.split(";")[0] });
  state.chunks = [];
  const box = byId("space-replay");
  box.hidden = false;
  const note = byId("space-replay-text");
  if (!window.MediaUpload) {
    note.textContent = "The replay couldn't be saved here.";
    return offerDownload(file);
  }
  note.textContent = "Saving the replay to your linked storage…";
  try {
    const asset = await window.MediaUpload.upload(file, "audio", {
      onProgress: (n) => { note.textContent = `Saving the replay… ${Math.round((n || 0) * 100)}%`; },
    });
    await api("replay", { media: String(asset.id) });
    note.textContent = "The replay is saved with the post.";
  } catch (e) {
    note.textContent = `The replay wasn't saved: ${e.message || "the upload failed"}. You can download it instead.`;
    offerDownload(file);
  }
}

function offerDownload(file) {
  const link = byId("space-replay-download");
  link.href = URL.createObjectURL(file);
  link.download = file.name;
  link.hidden = false;
}

// --- live comments (the post's own, routes/live_comments.py) --------------------------------------------------------
function drawComments(list) {
  const box = byId("space-comments");
  const atBottom = box.scrollHeight - box.scrollTop - box.clientHeight < 60;
  for (const c of list) {
    if (document.getElementById(`space-comment-${c.id}`)) continue;
    const row = document.createElement("div");
    row.className = "space-comment";
    row.id = `space-comment-${c.id}`;
    const head = document.createElement("div");
    head.className = "space-comment-head";
    head.appendChild(avatar(c.avatar, 24));
    const name = document.createElement("span");
    name.className = "space-comment-name";
    name.textContent = c.author;
    head.appendChild(name);
    const body = document.createElement("div");
    body.className = "space-comment-body";
    body.innerHTML = c.body_html;          // stored sanitized when it was written (helpers/sanitize.py)
    for (const a of body.querySelectorAll("a")) {
      a.target = "_blank";
      a.rel = "nofollow noopener noreferrer";
    }
    row.append(head, body);
    box.appendChild(row);
    state.lastComment = c.id;
  }
  if (atBottom) box.scrollTop = box.scrollHeight;
}

async function loadComments() {
  const after = state.lastComment ? `?after=${encodeURIComponent(state.lastComment)}` : "";
  try {
    const resp = await fetch(`/api/post/${boot.postId}/live_comments${after}`, { credentials: "same-origin" });
    if (!resp.ok) return;
    const data = await resp.json();
    drawComments(data.comments || []);
  } catch (e) {
    // the next event or the next poll tries again
  }
}

function wireComments() {
  const form = byId("space-comment-form");
  const input = byId("space-comment-input");
  form.addEventListener("submit", async (event) => {
    event.preventDefault();
    const text = input.value.trim();
    if (!text) return;
    const body = new URLSearchParams({ formkey: boot.formkey, parent_fullname: `t2_${boot.postId}`, body: text });
    input.disabled = true;
    try {
      const resp = await fetch("/api/comment", { method: "POST", credentials: "same-origin", body, headers: { Accept: "application/json" } });
      if (!resp.ok) {
        let data = {};
        try { data = await resp.json(); } catch (e) { data = {}; }
        throw new Error(data.error || "Your comment couldn't be posted.");
      }
      input.value = "";
      loadComments();
    } catch (e) {
      say(e.message, true);
    } finally {
      input.disabled = false;
      input.focus();
    }
  });
  loadComments();
  setInterval(loadComments, 30000);          // a fallback for a missed live event
}

// --- joining and leaving --------------------------------------------------------------------------------------------
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

function inRoom(roomId) {
  return new Promise((resolve) => {
    const started = Date.now();
    const check = () => {
      const room = state.client.getRoom(roomId);
      if ((room && room.getMyMembership() === "join") || Date.now() - started > 10000) return resolve();
      setTimeout(check, 250);
    };
    check();
  });
}

async function join() {
  byId("space-join").disabled = true;
  say("Joining…");
  resumeAudio();
  if (navigator.locks) {
    const got = await new Promise((resolve) => {
      navigator.locks.request("uqqus-call", { ifAvailable: true }, (lock) => {
        resolve(!!lock);
        return lock ? new Promise(() => {}) : null;
      });
    });
    if (!got) {
      byId("space-join").disabled = false;
      return say("A call or a Space is already open in another window.", true);
    }
  }
  try {
    state.client = state.client || (await startMatrix());
    state.me = { userId: state.client.getUserId(), deviceId: state.client.getDeviceId() };
    state.data = await api("join");
  } catch (e) {
    byId("space-join").disabled = false;
    return say(e.message, true);
  }
  let turn = null;
  try {
    turn = await state.client.turnServer();
  } catch (e) {
    turn = null;
  }
  if (!turn || !turn.uris || !turn.uris.length) {
    await api("leave").catch(() => {});
    return say("Spaces are unavailable right now. Try again later.", true);
  }
  // in the Space's room before anyone connects: that is how this browser learns the others' devices
  if (state.data.room_id) await inRoom(state.data.room_id);
  state.joined = true;
  byId("space-live").hidden = false;
  byId("space-join-sheet").hidden = true;
  byId("space-room").hidden = false;
  say("");

  state.mesh = new Mesh({
    client: state.client, callId: `space-${boot.spaceId}`, me: state.me,
    iceServers: [{ urls: turn.uris, username: turn.username, credential: turn.password }],
    media: null, encrypted: true,
    sends: () => onStage(),
    hears: (p) => STAGE.has(p.role),
    onStream: (key, stream) => {
      state.streams.set(key, stream);
      let audio = state.audios.get(key);
      if (!audio) {
        audio = document.createElement("audio");
        audio.autoplay = true;
        byId("space-audio").appendChild(audio);
        state.audios.set(key, audio);
      }
      if (audio.srcObject !== stream) audio.srcObject = stream;
      audio.play().catch(() => { byId("space-play").hidden = false; });
      if (!state.meters.has(key)) state.meters.set(key, levelMeter(stream));
      recordAdd(stream);
      applyRoster();
    },
    onPeerGone: (key) => {
      state.streams.delete(key);
      state.meters.delete(key);
      const audio = state.audios.get(key);
      if (audio) audio.remove();
      state.audios.delete(key);
    },
  });
  if (onStage()) await micOn();
  render();
  applyRoster();
  recordStart();
  requestAnimationFrame(speaking);

  window.UqqusLive.watch(`space:${boot.spaceId}`);
  state.heartbeat = setInterval(beat, 10000);
}

// A busy Space sends many roster changes: ask once for a burst of them, a little apart from everyone else
// (the live endpoints are limited per account: routes/spaces.py).
function soon(fn, base) {
  return () => {
    if (fn.timer) return;
    fn.timer = setTimeout(() => {
      fn.timer = null;
      fn();
    }, base + Math.random() * base);
  };
}
const beatSoon = soon(() => beat(), 1000);
const commentsSoon = soon(() => loadComments(), 600);

async function beat() {
  if (!state.joined || state.ended) return;
  try {
    const data = await api("heartbeat");
    if (!data.in_space) return finish(data.reason || (data.status === "ended" ? "This Space has ended." : "You're no longer in this Space."));
    const wasOnStage = onStage();
    state.data = data;
    if (onStage() && !wasOnStage) await micOn();
    if (!onStage() && wasOnStage) micOff();
    render();
    applyRoster();
  } catch (e) {
    // a missed beat is fine
  }
}

async function leave(reason) {
  if (state.joined && !state.ended) await api("leave").catch(() => {});
  finish(reason || "You left the Space.");
}

async function finish(reason) {
  if (state.ended) return;
  state.ended = true;
  clearInterval(state.heartbeat);
  if (state.mesh) state.mesh.close();
  if (state.media) state.media.stop();
  for (const audio of state.audios.values()) audio.remove();
  byId("space-room").hidden = true;
  byId("space-live").hidden = true;
  byId("space-ended").hidden = false;
  byId("space-ended-text").textContent = reason;
  await recordFinish();
}

// --- starting the page ----------------------------------------------------------------------------------------------
function wire() {
  byId("space-join").addEventListener("click", join);
  byId("space-leave").addEventListener("click", () => leave());
  byId("space-hand").addEventListener("click", () => act("hand", { up: state.data && state.data.me.hand ? "0" : "1" }));
  byId("space-accept-speak").addEventListener("click", async () => {
    await act("accept");
    if (onStage()) await micOn();
  });
  byId("space-decline-speak").addEventListener("click", () => act("listen"));
  byId("space-step-down").addEventListener("click", async () => {
    await act("listen");
    micOff();
  });
  byId("space-mute").addEventListener("click", () => {
    if (!state.media) return;
    state.media.setMuted(!state.media.muted);
    byId("space-mute").setAttribute("aria-pressed", String(state.media.muted));
    byId("space-mute-label").textContent = state.media.muted ? "Unmute" : "Mute";
  });
  byId("space-end").addEventListener("click", async () => {
    if (!window.confirm("End this Space for everyone?")) return;
    try {
      await api("end");
      finish("You ended the Space.");
    } catch (e) {
      say(e.message, true);
    }
  });
  byId("space-play").addEventListener("click", () => {
    resumeAudio();
    for (const audio of state.audios.values()) audio.play().catch(() => {});
    byId("space-play").hidden = true;
  });
  const start = byId("space-start");
  if (start) {
    start.addEventListener("click", async () => {
      start.disabled = true;
      try {
        await api("start");
        boot.status = "live";
        await join();
      } catch (e) {
        start.disabled = false;
        say(e.message, true);
      }
    });
  }
  byId("space-report").addEventListener("click", async () => {
    const reason = window.prompt("What's wrong with this Space? (optional)") ;
    if (reason === null) return;
    try {
      await api("report", { reason });
      say("Thanks: the site's admins will take a look.");
    } catch (e) {
      say(e.message, true);
    }
  });
  window.addEventListener("pagehide", () => {
    if (!state.joined || state.ended) return;
    const form = new FormData();
    form.set("formkey", boot.formkey);
    form.set("device_id", state.me.deviceId);
    navigator.sendBeacon(`/api/spaces/${boot.spaceId}/leave`, form);
  });

  const live = window.UqqusLive;
  live.watch(`post:${boot.postId}`);
  live.on("comment.new", commentsSoon);
  live.on("space.roster", beatSoon);
  live.on("space.started", () => { if (!state.joined) window.location.reload(); });
  live.on("space.ended", () => finish("This Space has ended."));
  live.on("space.invited", beatSoon);
  live.on("space.listening", () => beat().then(() => micOff()));
  live.on("space.removed", () => finish("You were removed from this Space."));
  live.on("space.mute", () => {
    if (!state.media) return;
    state.media.setMuted(true);
    byId("space-mute").setAttribute("aria-pressed", "true");
    byId("space-mute-label").textContent = "Unmute";
    say("The host asked you to mute.");
  });
}

function main() {
  if (!boot) return;
  wire();
  wireComments();
}

main();
