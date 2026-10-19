// A Space's own page (templates/space.html, routes/spaces.py, helpers/spaces.py): who is on stage and listening,
// raised hands, the host's controls, video while the Space is small, the board (its discussion: its post takes no
// comments), the live updates the host and co-hosts write, and the replay.
//
// Sound and video go browser to browser through the site's relay (rtc/mesh.js): the people on stage send to everyone
// in the Space, listeners only receive. A listener has no microphone until they accept an invitation to speak, and a
// camera only once on stage, while the server says video is open. The server's roster says who is in the Space, in
// which role and who sends video; nothing is played or shown from anyone it does not list on stage. Every name and
// board message goes in as text; an update or the summary is its stored, sanitized HTML.

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
  client: null, me: null, mesh: null, media: null, joined: false, joining: false, ended: false,
  data: null, heartbeat: null, streams: new Map(), meters: new Map(), audios: new Map(),
  recorder: null, chunks: [], mix: null, videos: new Map(),
  board: { last: 0, quote: null, settings: null },
  updates: { last: 0, thread: null },
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
  byId("space-count").textContent = `${(data.stage_count ?? data.stage.length) + data.listener_count} in the Space`;
  byId("space-listener-heading").textContent = `Listeners (${data.listener_count})`;

  const me = data.me;
  byId("space-hand").hidden = onStage() || me.invited || !state.joined;
  byId("space-hand").setAttribute("aria-pressed", String(!!me.hand));
  byId("space-hand-label").textContent = me.hand ? "Lower hand" : "Raise hand";
  byId("space-invite").hidden = !me.invited || !state.joined;
  byId("space-mute").hidden = !onStage() || !state.media;
  const video = data.video || {};
  const camera = byId("space-camera");
  const cameraOn = !!(state.media && state.media.videoTrack && !state.media.cameraOff);
  camera.hidden = !onStage() || !state.media || !video.allowed;
  camera.disabled = !cameraOn && !video.open;
  camera.setAttribute("aria-pressed", String(cameraOn));
  byId("space-camera-label").textContent = cameraOn ? "Camera off" : "Camera on";
  const note = byId("space-video-note");
  note.textContent = video.allowed && !video.open && video.note ? video.note : "";
  note.hidden = !note.textContent;
  byId("space-step-down").hidden = !onStage() || myRole() === "host" || !state.joined;
  byId("space-end").hidden = !(boot.isHost || boot.isAdmin) || data.status !== "live";

  drawVideos();
  drawRunnerControls();
  drawBoardControls();

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
    document.querySelectorAll(`.space-person[data-user="${userId}"], .space-video[data-user="${userId}"]`)
      .forEach((el) => el.classList.toggle("speaking", loud));
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
  if (state.media.videoTrack) api("video", { on: "0" }).catch(() => {});
  state.mesh.setMedia(null);
  state.media.stop();
  state.media = null;
  state.meters.delete("me");
  applyRoster();
  render();
}

// --- video (only people on stage, only while the server says it is open) ---------------------------------------------
function videoTile(key, stream, name, userId, mine) {
  let tile = state.videos.get(key);
  if (!tile) {
    tile = document.createElement("div");
    tile.className = `space-video${mine ? " mine" : ""}`;
    const video = document.createElement("video");
    video.autoplay = true;
    video.muted = true;                 // the sound plays from the audio element: never twice
    video.playsInline = true;
    const label = document.createElement("span");
    label.className = "space-video-name";
    tile.append(video, label);
    state.videos.set(key, tile);
  }
  tile.dataset.user = String(userId);
  tile.querySelector(".space-video-name").textContent = name;
  const video = tile.querySelector("video");
  if (video.srcObject !== stream) video.srcObject = stream;
  video.play().catch(() => {});
  return tile;
}

function liveVideo(stream) {
  return stream && stream.getVideoTracks().some((t) => t.readyState === "live" && !t.muted);
}

function drawVideos() {
  const box = byId("space-videos");
  const data = state.data;
  const wanted = [];
  if (data && state.joined) {
    if (state.media && state.media.videoTrack && !state.media.cameraOff && data.me.video) {
      wanted.push(videoTile("me", state.media.stream, "You", boot.userId, true));
    }
    for (const p of data.peers || []) {
      const key = peerKey(p);
      const stream = state.streams.get(key);
      if (p.video && STAGE.has(p.role) && liveVideo(stream)) wanted.push(videoTile(key, stream, p.shown_name, p.id, false));
    }
  }
  for (const [key, tile] of state.videos) {
    if (!wanted.includes(tile)) {
      tile.remove();
      state.videos.delete(key);
    }
  }
  box.replaceChildren(...wanted);
  box.hidden = !wanted.length;
}

async function cameraOn() {
  if (!state.media) return;
  try {
    state.data = await api("video", { on: "1" });     // the server says first whether video is open
  } catch (e) {
    return say(e.message, true);
  }
  try {
    await state.media.startCamera();
  } catch (e) {
    api("video", { on: "0" }).catch(() => {});
    return say(e instanceof MediaRefused ? e.message : "The camera couldn't be started.", true);
  }
  state.mesh.syncTracks();
  render();
  applyRoster();
}

async function cameraOff(reason) {
  if (!state.media || !state.media.videoTrack) return;
  state.media.stopCamera();
  if (state.mesh) state.mesh.syncTracks();
  try {
    state.data = await api("video", { on: "0" });
  } catch (e) {
    // the next heartbeat says what the server has
  }
  if (reason) say(reason);
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

// --- shared drawing helpers ------------------------------------------------------------------------------------------
function clock(utc) {
  try {
    return new Date(utc * 1000).toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" });
  } catch (e) {
    return "";
  }
}

function smallButton(label, fn) {
  const b = document.createElement("button");
  b.type = "button";
  b.textContent = label;
  b.addEventListener("click", fn);
  return b;
}

async function postForm(url, fields = {}) {
  const resp = await fetch(url, {
    method: "POST", credentials: "same-origin", headers: { Accept: "application/json" },
    body: new URLSearchParams({ formkey: boot.formkey, ...fields }),
  });
  if (resp.status === 204) return {};
  let data = {};
  try {
    data = await resp.json();
  } catch (e) {
    data = {};
  }
  if (!resp.ok) throw new Error(data.error || "That didn't work. Try again.");
  return data;
}

async function getJson(url) {
  const resp = await fetch(url, { credentials: "same-origin", headers: { Accept: "application/json" } });
  if (!resp.ok) throw new Error("not available");
  return resp.json();
}

function live() {
  return (state.data ? state.data.status : boot.status) === "live" && !state.ended;
}

// --- the board: the Space's discussion (routes/spaces.py `space_messages`) -------------------------------------------
const BOARD_KEEP = 400;
const ROLE_NAME = { host: "Host", cohost: "Co-host" };

function boardNote(text, bad) {
  const el = byId("space-board-note");
  el.textContent = text || "";
  el.style.color = bad ? "#ff8a8a" : "";
}

function canWrite() {
  return live() && (state.joined || (state.data && state.data.me && state.data.me.may_run));
}

function drawBoardControls() {
  const form = byId("space-board-form");
  form.hidden = !canWrite();
  if (!live()) {
    boardNote(state.ended || boot.status === "ended" ? "The board is closed: this Space has ended." : "The board opens when the Space starts.");
  } else if (!canWrite()) {
    boardNote("Join the Space to write on its board.");
  } else if (byId("space-board-note").textContent.startsWith("Join the Space") || byId("space-board-note").textContent.startsWith("The board")) {
    boardNote("");
  }
  const settings = state.board.settings;
  const slow = settings ? settings.slow_seconds : 0;
  byId("space-slow-label").textContent = slow && live() ? `Slow mode · ${settings.slow_label}` : "";
  const may = !!(state.data && state.data.me && state.data.me.may_moderate) && live();
  byId("space-slow-wrap").hidden = !may;
  const select = byId("space-slow");
  if (may && !select.options.length) {
    for (const [seconds, label] of boot.slowChoices || []) {
      const option = document.createElement("option");
      option.value = String(seconds);
      option.textContent = seconds ? `Slow · ${label}` : "Slow mode off";
      select.appendChild(option);
    }
  }
  if (may) select.value = String(slow || 0);
  if (may) byId("space-slow-label").textContent = "";
  for (const el of document.querySelectorAll(".board-quote-btn")) el.hidden = !canWrite();
}

function setQuote(m) {
  state.board.quote = m ? m.id : null;
  byId("space-quote").hidden = !m;
  byId("space-quote-text").textContent = m ? `${m.author}: ${m.body || ""}` : "";
  if (m) byId("space-board-input").focus();
}

function drawMessage(m) {
  const row = document.createElement("div");
  row.className = "board-msg";
  row.id = `space-msg-${m.id}`;
  const head = document.createElement("div");
  head.className = "board-head";
  head.appendChild(avatar(m.avatar, 24));
  const name = document.createElement("span");
  name.className = "board-name";
  name.textContent = m.mine ? "You" : m.author;
  head.appendChild(name);
  if (ROLE_NAME[m.role]) {
    const role = document.createElement("span");
    role.className = "board-role";
    role.textContent = ROLE_NAME[m.role];
    head.appendChild(role);
  }
  const time = document.createElement("span");
  time.className = "board-time";
  time.textContent = clock(m.created_utc);
  head.appendChild(time);
  row.appendChild(head);
  if (m.quote) {
    const quote = document.createElement("div");
    quote.className = "board-quote";
    if (m.quote.gone) {
      quote.classList.add("hidden-text");
      quote.textContent = "A message that isn't there any more";
    } else if (m.quote.hidden) {
      quote.classList.add("hidden-text");
      quote.textContent = `${m.quote.author}: hidden by your word filter`;
    } else {
      quote.textContent = `${m.quote.author}: ${m.quote.body}`;
    }
    row.appendChild(quote);
  }
  const body = document.createElement("div");
  body.className = "board-body";
  if (m.hidden) {
    body.classList.add("hidden-text");
    body.textContent = "Hidden by your word filter";
  } else {
    body.textContent = m.body;
  }
  row.appendChild(body);
  const actions = document.createElement("div");
  actions.className = "board-actions";
  if (!m.hidden) {
    const quote = smallButton("Quote", () => setQuote(m));
    quote.className = "board-quote-btn";
    quote.hidden = !canWrite();
    actions.appendChild(quote);
  }
  if (m.mine) {
    actions.appendChild(smallButton("Delete", async () => {
      if (!window.confirm("Delete this message?")) return;
      try {
        await postForm(`/api/space_messages/${m.id}/delete`);
        row.remove();
      } catch (e) {
        boardNote(e.message, true);
      }
    }));
  } else {
    actions.appendChild(smallButton("Report", async () => {
      const reason = window.prompt("What's wrong with this message? (optional)");
      if (reason === null) return;
      try {
        await postForm(`/api/space_messages/${m.id}/report`, { reason });
        boardNote("Thanks: the site's admins will take a look.");
      } catch (e) {
        boardNote(e.message, true);
      }
    }));
  }
  if (m.may_remove) {
    actions.appendChild(smallButton("Remove", async () => {
      if (!window.confirm("Remove this message from the board for everyone?")) return;
      try {
        await postForm(`/api/space_messages/${m.id}/remove`);
        row.remove();
      } catch (e) {
        boardNote(e.message, true);
      }
    }));
  }
  row.appendChild(actions);
  return row;
}

function drawMessages(list) {
  const box = byId("space-board");
  const atBottom = box.scrollHeight - box.scrollTop - box.clientHeight < 80;
  for (const m of list) {
    state.board.last = Math.max(state.board.last, m.id);
    if (document.getElementById(`space-msg-${m.id}`)) continue;
    box.appendChild(drawMessage(m));
  }
  while (box.children.length > BOARD_KEEP) box.firstElementChild.remove();
  byId("space-board-empty").hidden = box.children.length > 0;
  if (atBottom) box.scrollTop = box.scrollHeight;
}

async function loadBoard() {
  try {
    const data = await getJson(`/api/spaces/${boot.spaceId}/messages${state.board.last ? `?after=${state.board.last}` : ""}`);
    state.board.settings = data.board || state.board.settings;
    drawMessages(data.messages || []);
    drawBoardControls();
  } catch (e) {
    // the next event or the next poll tries again
  }
}

function wireBoard() {
  const form = byId("space-board-form");
  const input = byId("space-board-input");
  form.addEventListener("submit", async (event) => {
    event.preventDefault();
    const text = input.value.trim();
    if (!text) return;
    input.disabled = true;
    try {
      const fields = { body: text };
      if (state.board.quote) fields.quote = String(state.board.quote);
      const data = await postForm(`/api/spaces/${boot.spaceId}/messages`, fields);
      input.value = "";
      setQuote(null);
      boardNote("");
      drawMessages(data.messages || []);
      const box = byId("space-board");
      box.scrollTop = box.scrollHeight;
    } catch (e) {
      boardNote(e.message, true);
    } finally {
      input.disabled = false;
      input.focus();
    }
  });
  byId("space-quote-cancel").addEventListener("click", () => setQuote(null));
  byId("space-slow").addEventListener("change", async (event) => {
    try {
      const data = await postForm(`/api/spaces/${boot.spaceId}/slow`, { seconds: event.target.value });
      state.board.settings = data.board;
      drawBoardControls();
    } catch (e) {
      boardNote(e.message, true);
    }
  });
  loadBoard();
  setInterval(loadBoard, 30000);          // a fallback for a missed live event
}

// --- live updates: the host and co-hosts write them (the live-thread machinery, keyed by the post) -------------------
function mayRun() {
  return !!(state.data && state.data.me && state.data.me.may_run);
}

function drawRunnerControls() {
  const runner = mayRun() && live();
  byId("space-update-form").hidden = !runner;
  byId("space-summary-edit").hidden = !runner || !byId("space-summary-form").hidden;
  if (!runner) byId("space-summary-form").hidden = true;
}

function drawThread(thread) {
  if (!thread) return;
  state.updates.thread = thread;
  byId("space-updates-live").hidden = !thread.live;
  const summary = byId("space-summary");
  if (thread.summary_hidden) {
    summary.textContent = "The summary is hidden by your word filter.";
    summary.hidden = false;
  } else if (thread.summary_html) {
    summary.innerHTML = thread.summary_html;          // stored sanitized when it was written (helpers/sanitize.py)
    for (const a of summary.querySelectorAll("a")) {
      a.target = "_blank";
      a.rel = "nofollow noopener noreferrer";
    }
    summary.hidden = false;
  } else {
    summary.replaceChildren();
    summary.hidden = true;
  }
}

function drawUpdate(u) {
  const row = document.createElement("article");
  row.className = `update${u.struck ? " struck" : ""}`;
  row.id = `space-update-${u.id}`;
  const meta = document.createElement("div");
  meta.className = "update-meta";
  const time = document.createElement("span");
  time.textContent = clock(u.created_utc);
  meta.appendChild(time);
  if (u.author) {
    const by = document.createElement("span");
    by.textContent = u.author;
    meta.appendChild(by);
  }
  if (u.struck) {
    const tag = document.createElement("span");
    tag.textContent = "Corrected";
    meta.appendChild(tag);
  }
  row.appendChild(meta);
  const body = document.createElement("div");
  body.className = "update-body";
  if (u.hidden) {
    body.textContent = "Hidden by your word filter";
  } else {
    body.innerHTML = u.body_html || "";          // stored sanitized when it was written (helpers/sanitize.py)
    for (const a of body.querySelectorAll("a")) {
      a.target = "_blank";
      a.rel = "nofollow noopener noreferrer";
    }
  }
  row.appendChild(body);
  if (u.may_change) {
    const actions = document.createElement("div");
    actions.className = "board-actions";
    actions.style.paddingLeft = "0";
    actions.appendChild(smallButton(u.struck ? "Undo correction" : "Cross out", async () => {
      try {
        replaceUpdate(await postForm(`/api/live_updates/${u.id}/strike`, { on: u.struck ? "0" : "1" }));
      } catch (e) {
        say(e.message, true);
      }
    }));
    actions.appendChild(smallButton("Delete", async () => {
      if (!window.confirm("Delete this update?")) return;
      try {
        await postForm(`/api/live_updates/${u.id}/delete`);
        replaceUpdate({ id: u.id, gone: true });
      } catch (e) {
        say(e.message, true);
      }
    }));
    row.appendChild(actions);
  }
  return row;
}

function replaceUpdate(u) {
  const old = document.getElementById(`space-update-${u.id}`);
  if (u.gone) {
    if (old) old.remove();
  } else if (old) {
    old.replaceWith(drawUpdate(u));
  }
  byId("space-updates-empty").hidden = byId("space-updates").children.length > 0;
}

async function loadUpdates() {
  try {
    const first = !state.updates.last;
    const data = await getJson(`/api/post/${boot.postId}/live_updates${first ? "" : `?after=${state.updates.last}`}`);
    drawThread(data.thread);
    const box = byId("space-updates");
    // the first page comes newest first; later ones oldest first: the newest is always on top
    for (const u of data.updates || []) {
      state.updates.last = Math.max(state.updates.last, u.id);
      if (document.getElementById(`space-update-${u.id}`)) continue;
      if (first) box.appendChild(drawUpdate(u));
      else box.prepend(drawUpdate(u));
    }
    byId("space-updates-empty").hidden = box.children.length > 0;
  } catch (e) {
    // the next event or the next poll tries again
  }
}

async function refreshUpdate(id) {
  try {
    replaceUpdate(await getJson(`/api/live_updates/${id}`));
  } catch (e) {
    replaceUpdate({ id, gone: true });
  }
}

function wireUpdates() {
  byId("space-update-form").addEventListener("submit", async (event) => {
    event.preventDefault();
    const input = byId("space-update-input");
    const text = input.value.trim();
    if (!text) return;
    input.disabled = true;
    try {
      const u = await postForm(`/api/spaces/${boot.spaceId}/updates`, { body: text });
      input.value = "";
      state.updates.last = Math.max(state.updates.last, u.id);
      if (!document.getElementById(`space-update-${u.id}`)) byId("space-updates").prepend(drawUpdate(u));
      byId("space-updates-empty").hidden = true;
    } catch (e) {
      say(e.message, true);
    } finally {
      input.disabled = false;
    }
  });
  byId("space-summary-edit").addEventListener("click", async () => {
    // the words themselves, to edit, come with the thread for whoever runs it
    try {
      const data = await getJson(`/api/spaces/${boot.spaceId}/summary`);
      byId("space-summary-input").value = data.summary || "";
    } catch (e) {
      byId("space-summary-input").value = "";
    }
    byId("space-summary-form").hidden = false;
    byId("space-summary-edit").hidden = true;
    byId("space-summary-input").focus();
  });
  byId("space-summary-cancel").addEventListener("click", () => {
    byId("space-summary-form").hidden = true;
    drawRunnerControls();
  });
  byId("space-summary-form").addEventListener("submit", async (event) => {
    event.preventDefault();
    try {
      const data = await postForm(`/api/spaces/${boot.spaceId}/summary`, { summary: byId("space-summary-input").value });
      drawThread(data.thread);
      byId("space-summary-form").hidden = true;
      drawRunnerControls();
    } catch (e) {
      say(e.message, true);
    }
  });
  loadUpdates();
  setInterval(loadUpdates, 60000);
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
  state.joining = true;
  try {
    await joinSpace();
  } finally {
    state.joining = false;
  }
}

async function joinSpace() {
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
      for (const track of stream.getVideoTracks()) {
        track.onmute = drawVideos;
        track.onunmute = drawVideos;
      }
      recordAdd(stream);
      applyRoster();
      drawVideos();
    },
    onPeerGone: (key) => {
      state.streams.delete(key);
      state.meters.delete(key);
      const audio = state.audios.get(key);
      if (audio) audio.remove();
      state.audios.delete(key);
      drawVideos();
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
const boardSoon = soon(() => loadBoard(), 400);
const updatesSoon = soon(() => loadUpdates(), 400);

async function beat() {
  if (!state.joined || state.ended) return;
  try {
    const data = await api("heartbeat");
    if (!data.in_space) return finish(data.reason || (data.status === "ended" ? "This Space has ended." : "You're no longer in this Space."));
    const wasOnStage = onStage();
    state.data = data;
    if (onStage() && !wasOnStage) await micOn();
    if (!onStage() && wasOnStage) micOff();
    // the Space grew past what video allows, or the relay's month closed it: cameras off, with the reason
    if (state.media && state.media.videoTrack && data.video && !data.video.open) await cameraOff(data.video.note);
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
  if (state.data) state.data.status = "ended";
  boot.status = "ended";
  drawBoardControls();
  drawRunnerControls();
  drawVideos();
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
  byId("space-camera").addEventListener("click", () => {
    const on = !!(state.media && state.media.videoTrack && !state.media.cameraOff);
    if (on) cameraOff();
    else cameraOn();
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
      state.joining = true;              // the "started" event this sends back must not reload the page
      try {
        await api("start");
        boot.status = "live";
        await join();
      } catch (e) {
        state.joining = false;
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

  const bus = window.UqqusLive;
  bus.watch(`post:${boot.postId}`);
  bus.watch(`space:${boot.spaceId}`);              // the board and the updates live for readers who haven't joined too
  bus.on("space.message", boardSoon);
  bus.on("space.message_gone", (data) => {
    const el = document.getElementById(`space-msg-${Number(data.id)}`);
    if (el) el.remove();
  });
  bus.on("space.board", () => { boardSoon(); beatSoon(); });
  bus.on("live.update", updatesSoon);
  bus.on("live.summary", updatesSoon);
  bus.on("live.changed", (data) => refreshUpdate(Number(data.id)));
  bus.on("space.roster", beatSoon);
  // a reader waiting on a scheduled Space gets the live page; never one that is joining it right now (the host who
  // pressed Start hears this too)
  bus.on("space.started", () => { if (!state.joined && !state.joining) window.location.reload(); });
  bus.on("space.ended", () => finish("This Space has ended."));
  bus.on("space.invited", beatSoon);
  bus.on("space.listening", () => beat().then(() => micOff()));
  bus.on("space.removed", () => finish("You were removed from this Space."));
  bus.on("space.mute", () => {
    if (!state.media) return;
    state.media.setMuted(true);
    byId("space-mute").setAttribute("aria-pressed", "true");
    byId("space-mute-label").textContent = "Unmute";
    say("The host asked you to mute.");
  });
}

async function loadState() {
  // who this reader is in the Space (may they run the updates, moderate the board?) before they join
  try {
    const data = await getJson(`/api/spaces/${boot.spaceId}/state`);
    if (!state.data) state.data = data;
    drawRunnerControls();
    drawBoardControls();
  } catch (e) {
    // the page still reads
  }
}

function main() {
  if (!boot) return;
  wire();
  wireBoard();
  wireUpdates();
  loadState();
}

main();
