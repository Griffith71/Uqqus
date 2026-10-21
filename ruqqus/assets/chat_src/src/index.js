import * as sdk from "matrix-js-sdk";
import { ClientEvent, Direction, EventType, RoomEvent, RoomMemberEvent, MatrixEventEvent, ReceiptType } from "matrix-js-sdk";
import { initAsync as initCryptoWasm } from "@matrix-org/matrix-sdk-crypto-wasm";

import { cryptoCallbacks, ensureEncryptionReady, signOwnDevices } from "./crypto.js";
import { markReady } from "./keylock.js";
import { OnlySignedDevicesIsolationMode } from "matrix-js-sdk/lib/crypto-api/index.js";
import { ensureSession, loadSession, CALL_STORAGE_KEY } from "./token.js";
import {
  listConversations, acceptConversation, declineConversation, markRead,
  groupInfo, acceptGroup, declineGroup, markGroupRead,
  postToChannel, leaveChannel, markChannelRead, reportChannelMessage,
  joinGuildChannel, postToGuildChannel, markGuildChannelRead, reportGuildChannelMessage, removeGuildChannelMessage,
  pinGuildChannelMessage, muteGuildChannel, acceptGuildChannelRules, guildChannelPins, searchGuildChannel,
} from "./api.js";
import { renderConversationList, renderThreadShell, renderMessages, setTabBadge } from "./ui.js";
import { initGroups } from "./groups.js";
import { initExtras } from "./chat_extras.js";
import {
  createSenders, mentionPattern, renderMute, renderPins, renderResults, renderSearch, renderStageCard, renderVoiceCard, stopCards,
} from "./guild_channels.js";

const boot = window.RUQQUS_CHAT_BOOT;

const state = {
  client: null,
  tab: "inbox",
  conversations: { inbox: [], group: [], request: [], channel: [], guild: [] },
  active: null, // { conversation, room, isRequest, group, channel, gchannel } - group: a group's members, for its threads; gchannel: a guild channel, with the names of its writers
  thread: null, // { messagesEl, inputEl, setContext, setTyping }
  context: null, // { type: "quote" | "edit", event } - what the box is answering or replacing
  typingSent: 0, // when we last told the room we are typing (0: not typing)
  typingTimer: null,
  renderQueued: false,
  locked: true,  // until this device has unlocked its encryption (crypto.js), nothing is sent from it
};

// --- messages waiting for someone whose chat isn't set up yet (audit M-04) ---------------------------------------
// A message encrypted to someone with no device yet could never be read by them. So it waits here, in this browser,
// and goes as soon as they've opened chat (the conversation list says `ready`).
const WAITING_KEY = "uqqus_chat_waiting";

function loadWaiting() {
  try { return JSON.parse(localStorage.getItem(WAITING_KEY) || "{}") || {}; } catch (e) { return {}; }
}

function saveWaiting(waiting) {
  try { localStorage.setItem(WAITING_KEY, JSON.stringify(waiting)); } catch (e) { /* not kept: sent when possible */ }
}

function waitFor(roomId, text) {
  const waiting = loadWaiting();
  (waiting[roomId] = waiting[roomId] || []).push({ text, at: Date.now() });
  saveWaiting(waiting);
}

async function sendWaiting() {
  if (state.locked) return;
  const waiting = loadWaiting();
  const all = [...state.conversations.inbox, ...state.conversations.request];
  let changed = false;
  for (const roomId of Object.keys(waiting)) {
    const convo = all.find((c) => c.room_id === roomId);
    if (!convo || !convo.other_user || !convo.other_user.ready) continue;
    for (const item of waiting[roomId]) {
      try { await state.client.sendTextMessage(roomId, item.text); } catch (e) { break; }
    }
    delete waiting[roomId];
    changed = true;
  }
  if (changed) saveWaiting(waiting);
}

function waitingNotice(conversation) {
  const count = (loadWaiting()[conversation.room_id] || []).length;
  if (!count || !conversation.other_user) return "";
  return `@${conversation.other_user.username} hasn't opened chat yet. Your ${count === 1 ? "message" : `${count} messages`} will be sent when they do.`;
}

function isMessageLikeEvent(event) {
  return event.getType() === EventType.RoomMessage || event.getType() === EventType.RoomMessageEncrypted;
}

// Reactions and edits are events of their own that point at a message. In an
// encrypted room they arrive as m.room.encrypted, but the relation stays readable.
function isRelationEvent(event) {
  return event.isRelation("m.annotation") || event.isRelation("m.replace");
}

// The other person's Matrix id. Ids here are @<prefix><site user id>:<server>
// (helpers/matrix_client.py mxid_for), so it is ours with the user id swapped.
// The room's member list is no help: it also holds whoever created the room.
function otherMxidOf(conversation) {
  return state.client.getUserId().replace(/\d+:/, `${conversation.other_user.id}:`);
}

// A group as the thread needs it: its members by Matrix id (who wrote each message, who has read it, who
// is typing, whom the viewer blocked).
function indexGroup(info) {
  return { info, byMxid: new Map(info.members.map((m) => [m.mxid, m])) };
}

function waitForRoom(roomId, ms) {
  return new Promise((resolve) => {
    const now = state.client.getRoom(roomId);
    if (now) return resolve(now);
    const onRoom = (room) => {
      if (room.roomId !== roomId) return;
      state.client.off(ClientEvent.Room, onRoom);
      resolve(room);
    };
    state.client.on(ClientEvent.Room, onRoom);
    setTimeout(() => {
      state.client.off(ClientEvent.Room, onRoom);
      resolve(state.client.getRoom(roomId));
    }, ms);
  });
}

// a message the thread shows as a bubble
function isShownMessage(event) {
  return isMessageLikeEvent(event) && !isRelationEvent(event);
}

function textOf(event) {
  // first: on a failure the SDK puts its own error text in the body ("** Unable to decrypt: …")
  if (event.isDecryptionFailure()) return "This message can't be read on this device.";
  const body = event.getContent().body;   // an edited message reads as its latest text
  if (body) return body;
  if (event.isBeingDecrypted() || event.getType() === EventType.RoomMessageEncrypted) return "Decrypting…";
  return "";
}

function reactionsOf(room, event, myMxid) {
  const relations = room.getUnfilteredTimelineSet().relations
    .getChildEventsForEvent(event.getId(), "m.annotation", EventType.Reaction);
  if (!relations) return [];
  return relations.getSortedAnnotationsByKey().map(([key, events]) => ({
    key,
    count: events.size,
    mine: Array.from(events).some((e) => e.getSender() === myMxid),
  }));
}

function myReactionEvent(room, event, key) {
  const relations = room.getUnfilteredTimelineSet().relations
    .getChildEventsForEvent(event.getId(), "m.annotation", EventType.Reaction);
  const mine = relations && relations.getSortedAnnotationsByKey().find(([k]) => k === key);
  return mine ? Array.from(mine[1]).find((e) => e.getSender() === state.client.getUserId()) : null;
}

// a public channel message carries its word-filter rating (routes/broadcasts.py, helpers/broadcasts.SEVERITY_KEY):
// Standard hides 2, Child hides 1 and 2 (helpers/visibility.py), Off nothing
function filteredOut(event) {
  const severity = Number(event.getContent()["org.uqqus.severity"] || 0);
  const level = Number(boot.filterLevel ?? 1);
  return severity > 0 && severity >= 3 - Math.min(level, 2);
}

// Matrix's threads: a message with a relation of type m.thread belongs to the thread of the event it points at
function isThreadMessage(event) {
  return event.getOriginalContent()["m.relates_to"]?.rel_type === "m.thread";
}

function threadRootOf(event) {
  return event.getOriginalContent()["m.relates_to"]?.event_id;
}

function renderActiveThread() {
  const { room, conversation, isRequest, group, channel, gchannel } = state.active;
  const myMxid = state.client.getUserId();
  const memberOf = (event) => (group ? group.byMxid.get(event.getSender()) : null);
  const writerOf = (event) => (channel ? channel.writers.get(event.getSender()) : null);
  // a guild channel: who wrote it, once the site has said (null until then); whom the viewer muted is left out
  const infoOf = (event) => (gchannel && event.getSender() !== myMxid ? gchannel.senders.get(event.getSender()) : null);

  // Pending (locally-echoed, not-yet-sent) events are already included
  // here: the client is created without an explicit pendingEventOrdering
  // override, so the SDK defaults to "chronological" - local echo lives
  // directly in the room's main timeline rather than a separate list.
  // room.getPendingEvents() is only valid under "detached" ordering and
  // throws otherwise.
  const events = room
    .getLiveTimeline()
    .getEvents()
    .filter(isShownMessage);
  const byId = new Map(events.map((event) => [event.getId(), event]));
  // in a guild channel a message that was taken out (by its writer, a guildmaster, or the site removing one that
  // went around its rules) is simply gone, whom the viewer muted is left out, and a message in a thread is shown in
  // its thread, not among the rest
  const shown = gchannel
    ? events.filter((event) => !event.isRedacted() && !isThreadMessage(event) && !(infoOf(event) && infoOf(event).muted))
    : events;
  const threadsLive = new Map();
  if (gchannel) {
    for (const event of events) {
      if (isThreadMessage(event) && !event.isRedacted()) {
        const root = threadRootOf(event);
        threadsLive.set(root, (threadsLive.get(root) || 0) + 1);
      }
    }
  }
  const mine_ = gchannel ? mentionPattern(boot.username) : null;

  // the last message of mine, and whether the other person (in a group: how many of the others) has read it
  const lastMine = [...events].reverse().find((e) => e.getSender() === myMxid && !e.isRedacted() && !e.status);
  let seenId = null;
  let seenBy = 0;
  if (lastMine && group) {
    seenBy = group.info.members.filter((m) => m.joined && m.mxid !== myMxid && room.hasUserReadEvent(m.mxid, lastMine.getId())).length;
    if (seenBy) seenId = lastMine.getId();
  } else if (lastMine && !channel && !gchannel && room.hasUserReadEvent(otherMxidOf(conversation), lastMine.getId())) {
    seenId = lastMine.getId();
  }

  const itemOf = (event) => {
    const deleted = event.isRedacted();
    const relation = event.getOriginalContent()["m.relates_to"];
    // a message in a thread quotes its root only as a fallback: that is not shown as a quote
    const quoted = relation?.rel_type === "m.thread" && relation.is_falling_back ? null : byId.get(relation?.["m.in_reply_to"]?.event_id);
    const mine = event.getSender() === myMxid;
    const author = !mine ? memberOf(event) : null;
    const info = infoOf(event);
    const quotedBlocked = quoted && (memberOf(quoted)?.blocked || (gchannel && infoOf(quoted)?.blocked));
    return {
      eventId: event.getId(),
      mine,
      body: deleted ? "" : textOf(event),
      status: event.status,
      edited: !deleted && !!event.replacingEventId(),
      deleted,
      quote: quoted && !quoted.isRedacted() ? (quotedBlocked ? "A message from someone you blocked" : textOf(quoted)) : null,
      reactions: deleted ? [] : reactionsOf(room, event, myMxid),
      seen: event.getId() === seenId,
      seenBy: group ? seenBy : 0,
      canEdit: mine && !deleted && (!gchannel || gchannel.encrypted) && event.getContent().msgtype === "m.text",
      // a line the call window writes when a call ends ("Voice call · 12 min"): drawn as a note, not a bubble
      notice: !deleted && event.getContent().msgtype === "m.notice",
      // in a group, who wrote it (someone who has left has no row any more); in a channel, which writer
      sender: group && !mine ? (author ? author.shown_name : "Someone who left")
        : channel && !mine ? (writerOf(event) ? writerOf(event).shown_name : "A former helper")
        : gchannel && !mine ? (info ? info.shown_name : "…") : null,
      blocked: !!(author && author.blocked) || !!(info && info.blocked),
      filtered: !!(channel && channel.public && !mine && !deleted && filteredOut(event)) || !!(gchannel && !mine && !deleted && filteredOut(event)),
      reportable: !!(channel && channel.public && !mine && !deleted) || !!(gchannel && !gchannel.encrypted && !mine && !deleted),
      removable: !!(gchannel && gchannel.can_remove && !mine && !deleted),
      senderRole: gchannel && !mine && info ? info.role || null : null,
      // a guild channel: threads under a message, pins, and mentions of the viewer
      threadable: !!(gchannel && !gchannel.encrypted && !deleted && !event.status && !isThreadMessage(event)),
      threadCount: gchannel && !isThreadMessage(event)
        ? Math.max(threadsLive.get(event.getId()) || 0, event.getServerAggregatedRelation("m.thread")?.count || 0) : 0,
      pinnable: !!(gchannel && !gchannel.encrypted && gchannel.can_pin && !deleted && !event.status),
      pinned: !!(gchannel && state.active.pinned && state.active.pinned.has(event.getId())),
      mentionsMe: !!(mine_ && !mine && !deleted && mine_.test(event.getContent().body || "")),
    };
  };
  const items = shown.map(itemOf);
  state.active.itemOf = itemOf;

  // a guild channel is long: Synapse sends the newest events, and older ones are asked for
  const more = gchannel && room.getLiveTimeline().getPaginationToken(Direction.Backward) ? { onLoad: loadEarlier } : null;
  renderMessages(state.thread.messagesEl, items, messageHandlers, more);
  updateTyping();
  if (state.active.threadPanel) renderThreadPanel();

  // Tell the server and the other person once per newest message, not once per draw:
  // our own receipt comes back as a Receipt event that redraws the thread, which
  // would send another receipt, and so on.
  const last = events[events.length - 1];
  if (!isRequest && last && !last.status && state.active.readUpTo !== last.getId()) {
    state.active.readUpTo = last.getId();
    if (!gchannel) state.client.sendReadReceipt(last, ReceiptType.Read).catch(() => {});   // not to a whole guild's channel
    const marked = gchannel ? markGuildChannelRead(boot.formkey, conversation.channel_id)
      : channel ? markChannelRead(boot.formkey, conversation.channel_id)
      : group ? markGroupRead(boot.formkey, conversation.group_id) : markRead(boot.formkey, conversation.conversation_id);
    marked
      .then(() => window.refreshChatBadge?.()) // navbar chat badge (assets/js/chat_badge.js)
      .catch(() => {});
  }
}

// Older messages of a guild channel, keeping the reader where they were
async function loadEarlier() {
  const active = state.active;
  if (!active || !active.gchannel || !state.thread) return;
  const box = state.thread.messagesEl;
  const height = box.scrollHeight;
  const top = box.scrollTop;
  try {
    await state.client.scrollback(active.room, 50);
  } catch (e) {
    window.alert("Couldn't load earlier messages. Try again in a moment.");
  }
  if (state.active !== active) return;
  renderActiveThread();
  box.scrollTop = top + (box.scrollHeight - height);
}

// --- a guild channel's panels: a thread, the pinned messages, search, notifications --------------------------------------------
function closePanels() {
  if (state.active) state.active.threadPanel = null;
}

async function openThread(rootId) {
  const active = state.active;
  if (!active || !active.gchannel || !state.thread) return;
  let older = [];
  try {
    // the thread's messages the live timeline doesn't hold (the server keeps them under their root)
    older = (await state.client.relations(active.room.roomId, rootId, "m.thread", "m.room.message", { limit: 50 })).events || [];
  } catch (e) {
    older = [];
  }
  if (state.active !== active) return;
  const panel = state.thread.openPanel("Thread", () => { active.threadPanel = null; });
  const form = document.createElement("form");
  form.className = "d-flex";
  const input = document.createElement("textarea");
  input.className = "form-control mr-2 chat-composer-input";
  input.rows = 1;
  input.placeholder = "Add to the thread...";
  input.setAttribute("aria-label", "Add to the thread");
  const send = document.createElement("button");
  send.type = "submit";
  send.className = "btn btn-primary align-self-end";
  send.textContent = "Send";
  form.append(input, send);
  if (active.gchannel.may_write) {
    panel.foot.appendChild(form);
    panel.showFoot();
  }
  const submit = () => {
    const text = input.value.trim();
    if (!text) return;
    input.value = "";
    postToGuildChannel(boot.formkey, active.conversation.channel_id, text, null, rootId).catch((e) => {
      window.alert(e.message);
      if (!input.value) input.value = text;
    });
  };
  form.addEventListener("submit", (e) => { e.preventDefault(); submit(); });
  input.addEventListener("keydown", (e) => {
    if (e.key === "Enter" && !e.shiftKey && !e.isComposing) { e.preventDefault(); submit(); }
  });
  active.threadPanel = { rootId, older, panel };
  renderThreadPanel();
  input.focus();
}

const threadHandlers = {
  onReact: (item, key) => messageHandlers.onReact(item, key),
  onReport: (item) => messageHandlers.onReport(item),
  onRemove: (item) => messageHandlers.onRemove(item),
  onDelete: (item) => messageHandlers.onDelete(item),
};

function renderThreadPanel() {
  const active = state.active;
  const tp = active && active.threadPanel;
  if (!tp || !active.itemOf) return;
  const root = active.room.findEventById(tp.rootId);
  const live = active.room.getLiveTimeline().getEvents().filter((e) => isThreadMessage(e) && threadRootOf(e) === tp.rootId);
  const seen = new Set();
  const events = [];
  for (const event of [...tp.older, ...live]) {
    if (seen.has(event.getId())) continue;
    seen.add(event.getId());
    events.push(event);
  }
  events.sort((a, b) => a.getTs() - b.getTs());
  const visible = events.filter((event) => !event.isRedacted() && !(active.gchannel.senders.get(event.getSender())?.muted));
  const items = [];
  if (root && !root.isRedacted()) items.push(active.itemOf(root));
  for (const event of visible) items.push(active.itemOf(event));
  // each is a message among the rest here: no thread of a thread, and no button to start one
  renderMessages(tp.panel.body, items.map((item) => ({ ...item, threadable: false, threadCount: 0, pinnable: false })), threadHandlers);
}

async function loadPins() {
  const active = state.active;
  if (!active || !active.gchannel) return;
  try {
    const pins = await guildChannelPins(active.conversation.channel_id);
    if (state.active === active) {
      active.pinned = new Set(pins.map((p) => p.event_id));
      scheduleRender();
    }
  } catch (e) {
    /* the pins are for the panel to show */
  }
}

async function openPins() {
  const active = state.active;
  if (!active || !active.gchannel || !state.thread) return;
  const panel = state.thread.openPanel("Pinned messages");
  try {
    const pins = await guildChannelPins(active.conversation.channel_id);
    active.pinned = new Set(pins.map((p) => p.event_id));
    const draw = (list) => renderPins(panel.body, list, active.gchannel.can_pin, async (pin) => {
      try {
        await pinGuildChannelMessage(boot.formkey, active.conversation.channel_id, pin.event_id, false);
        active.pinned.delete(pin.event_id);
        draw(list.filter((p) => p.event_id !== pin.event_id));
        scheduleRender();
      } catch (e) {
        window.alert(e.message);
      }
    });
    draw(pins);
  } catch (e) {
    panel.body.textContent = e.message;
  }
}

function openSearch() {
  const active = state.active;
  if (!active || !active.gchannel || !state.thread) return;
  const panel = state.thread.openPanel("Search");
  panel.showFoot();
  renderSearch(panel.body, panel.foot, async (query) => {
    try {
      renderResults(panel.body, await searchGuildChannel(active.conversation.channel_id, query));
    } catch (e) {
      panel.body.textContent = e.message;
    }
  });
}

function openMute() {
  const active = state.active;
  if (!active || !active.gchannel || !state.thread) return;
  const panel = state.thread.openPanel("Notifications");
  const draw = () => renderMute(panel.body, !!active.gchannel.muted, async (hours) => {
    try {
      const answer = await muteGuildChannel(boot.formkey, active.conversation.channel_id, hours);
      active.gchannel.muted = answer.muted;
      active.conversation.muted = answer.muted;
      const icon = document.querySelector("#chat-gc-mute i");
      if (icon) icon.className = `fas ${answer.muted ? "fa-bell-slash" : "fa-bell"}`;
      guildListAt = 0;
      queueRefresh();
      window.refreshChatBadge?.();
      draw();
    } catch (e) {
      window.alert(e.message);
    }
  });
  draw();
}

async function onAcceptRules() {
  const active = state.active;
  if (!active || !active.gchannel) return;
  try {
    await acceptGuildChannelRules(boot.formkey, active.conversation.channel_id);
  } catch (e) {
    window.alert(e.message);
    return;
  }
  // the channel again, now with its box
  guildListAt = 0;
  await refreshLists();
  const fresh = state.conversations.guild.find((c) => c.key === active.conversation.key);
  if (fresh) openConversation(fresh);
}

// Several events usually land together (a message, its reaction, a receipt): draw once.
function scheduleRender() {
  if (!state.active || state.renderQueued) return;
  state.renderQueued = true;
  requestAnimationFrame(() => {
    state.renderQueued = false;
    if (state.active) renderActiveThread();
  });
}

function updateTyping() {
  if (!state.active || !state.thread) return;
  const { room, conversation, group, channel, gchannel } = state.active;
  if (channel || gchannel) return;
  if (!group) {
    const other = room.getMember(otherMxidOf(conversation));
    state.thread.setTyping(other && other.typing ? `@${conversation.other_user.username} is typing…` : null);
    return;
  }
  const me = state.client.getUserId();
  const typing = group.info.members
    .filter((m) => m.mxid !== me && !m.blocked && room.getMember(m.mxid)?.typing)
    .map((m) => m.shown_name);
  let text = null;
  if (typing.length === 1) text = `${typing[0]} is typing…`;
  else if (typing.length === 2) text = `${typing[0]} and ${typing[1]} are typing…`;
  else if (typing.length > 2) text = "Several people are typing…";
  state.thread.setTyping(text);
}

// --- typing: tell the room while there is text in the box ----------------------------
function stopTyping() {
  clearTimeout(state.typingTimer);
  if (state.typingSent && state.active) {
    state.client.sendTyping(state.active.room.roomId, false).catch(() => {});
  }
  state.typingSent = 0;
}

function onTyping(isTyping) {
  if (!state.active || state.active.isRequest || state.active.gchannel) return;      // not told to a whole channel
  if (!isTyping) {
    stopTyping();
    return;
  }
  const now = Date.now();
  if (!state.typingSent || now - state.typingSent > 3000) {
    state.typingSent = now;
    state.client.sendTyping(state.active.room.roomId, true, 6000).catch(() => {});
  }
  clearTimeout(state.typingTimer);
  state.typingTimer = setTimeout(stopTyping, 4000);   // stopped typing
}

// --- quoting, editing, reacting, deleting ---------------------------------------------
function setContext(context) {
  state.context = context;
  if (!state.thread) return;
  if (!context) state.thread.setContext(null);
  else if (context.type === "edit") state.thread.setContext("Editing message");
  else state.thread.setContext(`Quoting: ${textOf(context.event)}`);
}

const messageHandlers = {
  onQuote(item) {
    const event = state.active.room.findEventById(item.eventId);
    if (!event) return;
    setContext({ type: "quote", event });
    state.thread.inputEl.focus();
  },
  onEdit(item) {
    const event = state.active.room.findEventById(item.eventId);
    if (!event) return;
    setContext({ type: "edit", event });
    state.thread.inputEl.value = event.getContent().body || "";
    state.thread.fit?.();
    state.thread.inputEl.focus();
  },
  onReact(item, key) {
    const { room } = state.active;
    const event = room.findEventById(item.eventId);
    if (!event) return;
    const mine = myReactionEvent(room, event, key);
    if (mine) {
      state.client.redactEvent(room.roomId, mine.getId()).catch(() => {});
    } else {
      state.client.sendEvent(room.roomId, EventType.Reaction, {
        "m.relates_to": { rel_type: "m.annotation", event_id: item.eventId, key },
      }).catch(() => {});
    }
  },
  async onReport(item) {
    const { conversation, gchannel } = state.active;
    const reason = window.prompt("What's wrong with this message? (optional)");
    if (reason === null) return;
    try {
      if (gchannel) {
        await reportGuildChannelMessage(boot.formkey, conversation.channel_id, item.eventId, reason);
        window.alert("Thanks: the guild's guildmasters will take a look.");
      } else {
        await reportChannelMessage(boot.formkey, conversation.channel_id, item.eventId, reason);
        window.alert("Thanks: the site's admins will take a look.");
      }
    } catch (e) {
      window.alert(e.message);
    }
  },
  onThread(item) {
    openThread(item.eventId);
  },
  async onPin(item) {
    const { conversation, gchannel } = state.active;
    if (!gchannel) return;
    try {
      const answer = await pinGuildChannelMessage(boot.formkey, conversation.channel_id, item.eventId, !item.pinned);
      state.active.pinned = state.active.pinned || new Set();
      if (answer.pinned) state.active.pinned.add(item.eventId);
      else state.active.pinned.delete(item.eventId);
      scheduleRender();
    } catch (e) {
      window.alert(e.message);
    }
  },
  async onRemove(item) {
    const { conversation } = state.active;
    if (!window.confirm("Remove this message for everyone? The guild's log will say you did.")) return;
    try {
      await removeGuildChannelMessage(boot.formkey, conversation.channel_id, item.eventId);
    } catch (e) {
      window.alert(e.message);
    }
  },
  onDelete(item) {
    if (!window.confirm("Delete this message for everyone?")) return;
    state.client.redactEvent(state.active.room.roomId, item.eventId).catch(() => {});
  },
};

// a voice or stage channel has no thread of messages: a card in its place
function openGuildCard(conversation) {
  stopTyping();
  closePanels();
  state.context = null;
  state.active = null;
  state.thread = null;
  const pane = document.getElementById("chat-thread-pane");
  if (conversation.channel_kind === "voice") renderVoiceCard(pane, conversation, () => startCall(conversation.room_id, "audio"));
  else renderStageCard(pane, conversation);
  document.getElementById("chat-app")?.classList.add("chat-has-room");
}

async function openConversation(conversation) {
  stopCards();
  if (conversation.kind === "guild_channel" && !["text", "announcement"].includes(conversation.channel_kind)) {
    return openGuildCard(conversation);
  }
  const isRequest = state.tab === "request";
  const isGroup = conversation.kind === "group";
  const isChannel = conversation.kind === "channel";
  const isGuildChannel = conversation.kind === "guild_channel";
  if (isGuildChannel && !conversation.in_room) {
    // a guild's channel is joined by opening it: the site checks the person may read it, then puts them in its room
    try {
      await joinGuildChannel(boot.formkey, conversation.channel_id);
      conversation.in_room = true;
    } catch (e) {
      window.alert(e.message);
      return;
    }
  }
  const room = isGroup || isChannel || isGuildChannel ? await waitForRoom(conversation.room_id, 8000) : state.client.getRoom(conversation.room_id);
  if (!room) return;
  const channel = isChannel
    ? { ...conversation, writers: new Map((conversation.writers || []).map((w) => [w.mxid, w])) }
    : null;
  const gchannel = isGuildChannel ? { ...conversation, senders: createSenders(conversation.channel_id, scheduleRender) } : null;

  let group = null;
  if (isGroup) {
    try {
      group = indexGroup(await groupInfo(conversation.group_id));
    } catch (e) {
      return;                       // no longer in it: the list is refreshed by the membership change
    }
  }

  stopTyping();
  closePanels();
  state.context = null;
  state.active = { conversation, room, isRequest, group, channel, gchannel };
  queueMicrotask(() => showThreadNotice(isGroup || isChannel || isGuildChannel ? "" : waitingNotice(conversation)));

  const pane = document.getElementById("chat-thread-pane");
  const inviter = isGroup && conversation.invited_by ? `@${conversation.invited_by.username}` : "Someone";
  state.thread = renderThreadShell(pane, {
    otherUser: conversation.other_user,
    group: group ? { label: group.info.label, size: group.info.size } : null,
    requestText: isGroup ? `${inviter} added you to this group. Accept to read it and write in it.` : null,
    onInfo: group ? () => groupSheets.openInfo(state.active.group.info) : null,
    onCall: isChannel || isGuildChannel ? null : (kind) => startCall(conversation.room_id, kind),
    channel,
    guildChannel: gchannel,
    onSearch: gchannel ? openSearch : null,
    onPins: gchannel ? openPins : null,
    onMute: gchannel ? openMute : null,
    onAcceptRules: gchannel ? onAcceptRules : null,
    onLeave: isChannel ? () => onLeaveChannel(conversation) : null,
    isRequest,
    onAccept: () => onAccept(conversation, room),
    onDecline: () => onDecline(conversation, room),
    onSend: (text) => onSend(room, text),
    onTyping,
    onCancelContext: () => {
      if (state.context && state.context.type === "edit") state.thread.inputEl.value = "";
      setContext(null);
    },
  });

  if (isRequest || isGuildChannel) {
    try {
      await state.client.scrollback(room, 30);
    } catch (e) {
      // best-effort preview - an empty/partial history is an acceptable
      // degraded state here, never a blocking error for the viewer
    }
  }

  renderActiveThread();
  watchCall();
  if (gchannel) loadPins();
}

async function onLeaveChannel(conversation) {
  if (!window.confirm("Leave this channel?")) return;
  try {
    await leaveChannel(boot.formkey, conversation.channel_id);
  } catch (e) {
    window.alert(e.message);
    return;
  }
  closeThread();
  refreshLists().catch(() => {});
}

function onSend(room, text) {
  if (state.active && state.active.gchannel && !state.active.gchannel.encrypted) {
    // a guild channel is written through the site (who may write, slow mode, the word filter), which signs and sends
    // it as you; it is not encrypted, so it needs no unlocking. An encrypted one is written like a group, from here
    const { gchannel } = state.active;
    const quote = state.context && state.context.type === "quote" ? state.context.event.getId() : null;
    setContext(null);
    postToGuildChannel(boot.formkey, gchannel.channel_id, text, quote).catch((e) => {
      window.alert(e.message);
      if (state.thread && !state.thread.inputEl.value) {     // slow mode and the like: what you wrote is not lost
        state.thread.inputEl.value = text;
        state.thread.fit?.();
      }
    });
    return;
  }
  if (state.locked) {
    window.alert("Unlock your messages first: reload the page to enter your password.");
    return;
  }
  const pair = state.active && state.active.conversation && state.active.conversation.other_user;
  if (pair && state.active.conversation.other_user.ready === false && !state.context) {
    waitFor(room.roomId, text);
    showThreadNotice(waitingNotice(state.active.conversation));
    return;
  }
  if (state.active && state.active.channel && state.active.channel.public) {
    // a public channel is written through the site (word filter, daily count, bans), which sends it as you
    setContext(null);
    postToChannel(boot.formkey, state.active.channel.channel_id, text).catch((e) => window.alert(e.message));
    return;
  }
  const context = state.context;
  setContext(null);
  stopTyping();

  let sent;
  if (context && context.type === "edit") {
    // an edit is a new event that replaces the text of the original
    if (text === context.event.getContent().body) return;
    sent = state.client.sendMessage(room.roomId, {
      msgtype: "m.text",
      body: `* ${text}`,
      "m.new_content": { msgtype: "m.text", body: text },
      "m.relates_to": { rel_type: "m.replace", event_id: context.event.getId() },
    });
  } else if (context && context.type === "quote") {
    sent = state.client.sendMessage(room.roomId, {
      msgtype: "m.text",
      body: text,
      "m.relates_to": { "m.in_reply_to": { event_id: context.event.getId() } },
    });
  } else {
    sent = state.client.sendTextMessage(room.roomId, text);
  }
  sent.catch(() => {
    renderActiveThread();
  });
  renderActiveThread();
}

async function onAccept(conversation, room) {
  if (conversation.kind === "group") {
    await acceptGroup(boot.formkey, conversation.group_id);     // the server joins the room for you
  } else {
    await state.client.joinRoom(room.roomId);
    await acceptConversation(boot.formkey, conversation.conversation_id);
  }
  await refreshLists();
  const refreshed = [...state.conversations.inbox, ...state.conversations.group].find((c) => c.key === conversation.key);
  if (refreshed) {
    setTab(refreshed.kind === "group" ? "group" : "inbox");
    openConversation(refreshed);
  }
}

function closeThread() {
  stopTyping();
  stopCards();
  state.active = null;
  state.thread = null;
  document.getElementById("chat-thread-pane").innerHTML =
    '<div class="d-flex align-items-center justify-content-center h-100 text-muted">Select a conversation</div>';
  document.getElementById("chat-app")?.classList.remove("chat-has-room");
}

async function onDecline(conversation, room) {
  if (conversation.kind === "group") {
    await declineGroup(boot.formkey, conversation.group_id);    // the server leaves the room for you
  } else {
    await state.client.leave(room.roomId);
    await declineConversation(boot.formkey, conversation.conversation_id);
  }
  closeThread();
  await refreshLists();
}

// --- calls (routes/calls.py): a window of their own, and the bar when one is going on ---------------------
function startCall(roomId, kind) {
  const url = `/call/${encodeURIComponent(roomId)}?kind=${kind === "video" ? "video" : "audio"}`;
  const win = window.open(url, "uqqus-call", "popup,width=960,height=680");
  if (!win) window.top.location.href = url;          // a blocked popup: open it in this tab
}

let callTimer = null;
function watchCall() {
  clearTimeout(callTimer);
  const active = state.active;
  if (!active || active.isRequest || active.gchannel) return;
  fetch(`/api/calls/status?room=${encodeURIComponent(active.room.roomId)}`, { credentials: "same-origin" })
    .then((r) => (r.ok ? r.json() : null))
    .then((status) => {
      if (state.active === active && state.thread && state.thread.setCall) state.thread.setCall(status);
    })
    .catch(() => {})
    .then(() => {
      if (state.active === active) callTimer = setTimeout(watchCall, 20000);
    });
}

// --- groups: the sheets (groups.js) and keeping an open group's members current --------------------
let groupSheets = null;
let extras = null;                                   // New chat, the Calls tab, the Channels tab's extras

async function openByKey(key, roomId) {
  await refreshLists();
  const tabs = ["inbox", "group", "request", "channel", "guild"];
  const tab = tabs.find((t) => state.conversations[t].some((c) => (key && c.key === key) || c.room_id === roomId));
  if (!tab) return;
  const found = state.conversations[tab].find((c) => (key && c.key === key) || c.room_id === roomId);
  setTab(tab);
  document.getElementById("chat-app")?.classList.add("chat-has-room");
  openConversation(found);
}

function onGroupChanged(info) {
  if (state.active && state.active.group && state.active.group.info.group_id === info.group_id) {
    state.active.group = indexGroup(info);
    state.thread?.setGroup(info);
    scheduleRender();
  }
  queueRefresh();
}

let groupRefreshTimer = null;
function refreshActiveGroup() {
  clearTimeout(groupRefreshTimer);
  groupRefreshTimer = setTimeout(async () => {
    const active = state.active;
    if (!active || !active.group) return;
    try {
      const info = await groupInfo(active.group.info.group_id);
      groupSheets?.refresh(info);
      onGroupChanged(info);
    } catch (e) {
      if (state.active === active) closeThread();      // removed from it, or it was closed
      queueRefresh();
    }
  }, 400);
}

function setTab(tab) {
  state.tab = tab;
  for (const link of document.querySelectorAll("[data-chat-tab]")) {
    link.classList.toggle("active", link.dataset.chatTab === tab);
    if (link.dataset.chatTab === tab) link.setAttribute("aria-current", "page");
    else link.removeAttribute("aria-current");
  }
  const list = document.getElementById("chat-conversation-list");
  if (tab === "calls") {
    extras?.renderCalls(list);                     // not conversations: recent calls (chat_extras.js)
    return;
  }
  renderConversationList(
    list,
    state.conversations[tab],
    state.active ? state.active.conversation.key : null,
    (convo) => openConversation(convo),
  );
  if (tab === "channel") extras?.decorateChannels(list);
}

// The Calls tab draws itself when it is opened, and search results stay until the search is cleared: a list refresh
// (a message arriving) redraws neither
function redrawTab() {
  if (state.tab !== "calls" && !extras?.searching()) setTab(state.tab);
}

// The guild channels list is the dearest to work out (every channel of every guild you are in), so a message arriving
// in some room does not ask for it again within this long
let guildListAt = 0;
const GUILD_LIST_MS = 15000;

async function refreshLists() {
  const wantGuilds = !guildListAt || Date.now() - guildListAt > GUILD_LIST_MS;
  const [inbox, group, request, channel, guild] = await Promise.all([
    listConversations("inbox"),
    listConversations("group"),
    listConversations("request"),
    listConversations("channel"),
    wantGuilds ? listConversations("guild").catch(() => state.conversations.guild) : state.conversations.guild,
  ]);
  if (wantGuilds) guildListAt = Date.now();
  state.conversations.inbox = inbox;
  state.conversations.group = group;
  state.conversations.request = request;
  state.conversations.channel = channel;
  state.conversations.guild = guild;
  setTabBadge(document.getElementById("chat-channels-badge"), channel.filter((c) => c.unread_count > 0).length);
  setTabBadge(document.getElementById("chat-guilds-badge"), guild.filter((c) => c.unread_count > 0).length);

  setTabBadge(document.getElementById("chat-inbox-badge"), inbox.filter((c) => c.unread_count > 0).length);
  setTabBadge(document.getElementById("chat-groups-badge"), group.filter((c) => c.unread_count > 0).length);
  setTabBadge(document.getElementById("chat-requests-badge"), request.length);
  window.refreshChatBadge?.();

  redrawTab();
  sendWaiting().catch(() => {});
}

function showThreadNotice(text) {
  const pane = document.getElementById("chat-thread-pane");
  if (!pane) return;
  let note = pane.querySelector(".chat-thread-notice");
  if (!text) { if (note) note.remove(); return; }
  if (!note) {
    note = document.createElement("div");
    note.className = "chat-thread-notice alert alert-secondary text-small m-2";
    note.setAttribute("role", "status");
    pane.prepend(note);
  }
  note.textContent = text;
}

let refreshQueued = false;
function queueRefresh() {
  if (refreshQueued) return;
  refreshQueued = true;
  setTimeout(() => {
    refreshQueued = false;
    refreshLists().catch(() => {});
  }, 300);
}

function wireGlobalEvents(client) {
  client.on(RoomEvent.Timeline, (event, room) => {
    if (state.active && room && room.roomId === state.active.room.roomId) {
      scheduleRender();   // a message, or a reaction or edit of one
    }
    if (isShownMessage(event)) queueRefresh();
  });

  const inActiveRoom = (room) => state.active && room && room.roomId === state.active.room.roomId;
  client.on(RoomEvent.Receipt, (event, room) => { if (inActiveRoom(room)) scheduleRender(); });
  client.on(RoomEvent.Redaction, (event, room) => { if (inActiveRoom(room)) scheduleRender(); });
  client.on(RoomMemberEvent.Typing, (event, member) => {
    if (state.active && member && member.roomId === state.active.room.roomId) updateTyping();
  });

  client.on(RoomEvent.LocalEchoUpdated, (event, room) => {
    if (state.active && room && room.roomId === state.active.room.roomId) {
      scheduleRender();
    }
  });

  client.on(RoomEvent.MyMembership, (room, membership) => {
    // removed from the group that is open (or it was closed): leave the thread
    if (inActiveRoom(room) && (state.active.group || state.active.gchannel) && (membership === "leave" || membership === "ban")) closeThread();
    queueRefresh();
  });
  client.on(ClientEvent.Room, queueRefresh);
  client.on(RoomMemberEvent.Membership, (event, member) => {
    if (state.active && state.active.group && member && member.roomId === state.active.room.roomId) refreshActiveGroup();
    queueRefresh();
  });

  // Encrypted events arrive via Room.timeline before they're actually
  // decrypted - the initial render of a just-arrived event often has an
  // empty body with isDecryptionFailure() still false, since decryption
  // happens asynchronously afterward. Without this, a message can render
  // as permanently blank. Re-render once decryption (success or failure)
  // actually completes.
  client.on(MatrixEventEvent.Decrypted, (event) => {
    const room = state.client.getRoom(event.getRoomId());
    if (state.active && room && room.roomId === state.active.room.roomId) {
      scheduleRender();   // also picks up a decrypted reaction or edit
    }
  });
}

// --- sending on behalf of another page --------------------------------------------------
// The share sheet (assets/js/share_chat.js) keeps this page in a hidden frame and asks it to send a
// post's link into a chat. The message has to be encrypted here, where the keys are. Only this
// site's own parent page is listened to, and only into a chat the member can already write in.
let isReady = false;

function tellParent(message) {
  if (window.parent && window.parent !== window) window.parent.postMessage(message, window.location.origin);
}

async function sendShared({ room_id: roomId, text }) {
  if (!isReady) throw new Error("Chat is still starting.");
  if (typeof roomId !== "string" || typeof text !== "string" || !text.trim() || text.length > 2000) {
    throw new Error("That message can't be sent.");
  }
  const find = () => [...state.conversations.inbox, ...state.conversations.group].find((c) => c.room_id === roomId);
  let convo = find();
  if (!convo) {
    await refreshLists();          // a chat that was just started from the share sheet
    convo = find();
  }
  // the inbox holds every chat you can write in, including the ones you started yourself;
  // a request waiting for you to accept it is not in it
  if (!convo) throw new Error("You can't send to that chat.");
  if (state.locked) throw new Error("Open Chat once to unlock your messages, then try again.");
  if (convo.other_user && convo.other_user.ready === false) {
    waitFor(roomId, text);                // sent when they've opened chat (M-04)
    return;
  }
  await state.client.sendTextMessage(roomId, text);
}

window.addEventListener("message", async (event) => {
  if (window.parent === window || event.origin !== window.location.origin || event.source !== window.parent) return;
  const data = event.data;
  if (!data || typeof data.type !== "string") return;
  if (data.type === "ruqqus-chat-ping") {
    if (isReady) tellParent({ type: "ruqqus-chat-ready" });
    return;
  }
  if (data.type !== "ruqqus-chat-send") return;
  try {
    await sendShared(data);
    tellParent({ type: "ruqqus-chat-sent", id: data.id, ok: true });
  } catch (e) {
    tellParent({ type: "ruqqus-chat-sent", id: data.id, ok: false, error: e && e.message ? e.message : "Could not send." });
  }
});

// One chat client per browser: they would share one device and its crypto store, and two at once can lose keys.
// The newest tab (or side panel) takes over; the one it took over from stops and says so.
function holdChatLock() {
  if (!navigator.locks) return Promise.resolve();
  return new Promise((resolve) => {
    navigator.locks
      .request("uqqus-matrix", { steal: true }, () => {
        resolve();
        return new Promise(() => {});              // held for as long as this page lives
      })
      .catch(() => {
        // taken over by another tab
        if (state.client) state.client.stopClient();
        isReady = false;
        const pane = document.getElementById("chat-thread-pane");
        const list = document.getElementById("chat-conversation-list");
        const note = document.createElement("div");
        note.className = "text-center text-muted p-4";
        const text = document.createElement("p");
        text.textContent = "Chat is open in another tab.";
        const again = document.createElement("button");
        again.type = "button";
        again.className = "btn btn-primary btn-sm";
        again.textContent = "Use it here";
        again.addEventListener("click", () => window.location.reload());
        note.append(text, again);
        if (list) list.replaceChildren(note);
        if (pane) pane.replaceChildren();
      });
  });
}

async function main() {
  if (!boot) return;

  await holdChatLock();
  const session = await ensureSession(boot);

  const client = sdk.createClient({
    baseUrl: session.homeserver_url,
    accessToken: session.access_token,
    userId: session.user_id,
    deviceId: session.device_id,
    cryptoCallbacks,
  });
  state.client = client;

  // matrix-js-sdk's rust crypto backend loads this WASM binary via a
  // runtime fetch() relative to its own module URL, which doesn't survive
  // bundling into a single file - so we initialize it ourselves first,
  // pointing at the copy the build placed next to this bundle. Once
  // initialized, initRustCrypto()'s own (argument-less) init call below
  // just awaits this same promise instead of re-triggering it.
  await initCryptoWasm("/assets/js/matrix_sdk_crypto_wasm_bg.wasm");

  await client.initRustCrypto();
  // message keys only for devices their owner signed: a device made without the owner's key (which the site no
  // longer has) gets nothing, and nothing from one is trusted (crypto.js, audit M-03)
  client.getCrypto().setDeviceIsolationMode(new OnlySignedDevicesIsolationMode());

  await new Promise((resolve) => {
    const onSync = (newState) => {
      if (newState === "PREPARED" || newState === "SYNCING") {
        client.off(ClientEvent.Sync, onSync);
        resolve();
      }
    };
    client.on(ClientEvent.Sync, onSync);
    client.startClient({ initialSyncLimit: 20 }).catch(() => {});
  });

  wireGlobalEvents(client);

  extras = initExtras({
    boot,
    openRoom: (roomId) => openByKey(null, roomId),
    openKey: (key, roomId) => openByKey(key, roomId),
    startCall,
    refreshLists,
    showTab: () => setTab(state.tab),
  });

  groupSheets = initGroups({
    boot,
    onCreated: (made) => openByKey(`g${made.group_id}`, made.room_id),
    onChanged: onGroupChanged,
    onLeft: () => {
      closeThread();
      refreshLists().catch(() => {});
    },
  });

  for (const link of document.querySelectorAll("[data-chat-tab]")) {
    link.addEventListener("click", (e) => {
      e.preventDefault();
      setTab(link.dataset.chatTab);
      if (link.dataset.chatTab === "guild") {
        guildListAt = 0;                                  // opening the tab shows what is new
        refreshLists().catch(() => {});
      }
    });
  }

  await refreshLists();

  isReady = true;
  tellParent({ type: "ruqqus-chat-ready" });

  if (boot.initialRoom) {
    const match = state.conversations.inbox.find((c) => c.room_id === boot.initialRoom)
      || state.conversations.group.find((c) => c.room_id === boot.initialRoom)
      || state.conversations.request.find((c) => c.room_id === boot.initialRoom)
      || state.conversations.channel.find((c) => c.room_id === boot.initialRoom)
      || state.conversations.guild.find((c) => c.room_id === boot.initialRoom);
    if (match) {
      setTab(state.conversations.inbox.includes(match) ? "inbox" : state.conversations.group.includes(match) ? "group"
        : state.conversations.channel.includes(match) ? "channel"
        : state.conversations.guild.includes(match) ? "guild" : "request");
      openConversation(match);
    }
  }

  // A guild's Chat tab frames this page for one unencrypted channel (single): it has no keys to unlock, and the unlock
  // box (which takes the focus, and so scrolls the page around the frame) is for the chat page itself
  if (boot.single) return;

  // Non-blocking: the lists show at once; sending waits until this device has unlocked (crypto.js)
  const newCode = new URLSearchParams(window.location.search).get("recovery") === "new";
  ensureEncryptionReady(client, boot.formkey, { newCode })
    .then(async (ready) => {
      state.locked = !ready;
      if (!ready) return;
      await markReady(boot.formkey);
      const callSession = loadSession(CALL_STORAGE_KEY);
      await signOwnDevices(client, [callSession && callSession.device_id]);
      sendWaiting().catch(() => {});
    })
    .catch((e) => { console.warn("Chat: encryption setup failed", e); });

  // a call or Space window opened later makes a device of its own here: sign it as it appears
  window.addEventListener("storage", (event) => {
    if (event.key !== CALL_STORAGE_KEY || state.locked) return;
    const callSession = loadSession(CALL_STORAGE_KEY);
    signOwnDevices(client, [callSession && callSession.device_id]).catch(() => {});
  });
}

main();
