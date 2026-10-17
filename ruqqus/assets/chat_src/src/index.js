import * as sdk from "matrix-js-sdk";
import { ClientEvent, EventType, RoomEvent, RoomMemberEvent, MatrixEventEvent, ReceiptType } from "matrix-js-sdk";
import { initAsync as initCryptoWasm } from "@matrix-org/matrix-sdk-crypto-wasm";

import { cryptoCallbacks, ensureEncryptionReady, signOwnDevices } from "./crypto.js";
import { markReady } from "./keylock.js";
import { OnlySignedDevicesIsolationMode } from "matrix-js-sdk/lib/crypto-api/index.js";
import { ensureSession, loadSession, CALL_STORAGE_KEY } from "./token.js";
import {
  listConversations, acceptConversation, declineConversation, markRead,
  groupInfo, acceptGroup, declineGroup, markGroupRead,
  postToChannel, leaveChannel, markChannelRead, reportChannelMessage,
} from "./api.js";
import { renderConversationList, renderThreadShell, renderMessages, setTabBadge } from "./ui.js";
import { initGroups } from "./groups.js";

const boot = window.RUQQUS_CHAT_BOOT;

const state = {
  client: null,
  tab: "inbox",
  conversations: { inbox: [], request: [], channel: [] },
  active: null, // { conversation, room, isRequest, group } - group: a group's members, for its threads
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

function renderActiveThread() {
  const { room, conversation, isRequest, group, channel } = state.active;
  const myMxid = state.client.getUserId();
  const memberOf = (event) => (group ? group.byMxid.get(event.getSender()) : null);
  const writerOf = (event) => (channel ? channel.writers.get(event.getSender()) : null);

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

  // the last message of mine, and whether the other person (in a group: how many of the others) has read it
  const lastMine = [...events].reverse().find((e) => e.getSender() === myMxid && !e.isRedacted() && !e.status);
  let seenId = null;
  let seenBy = 0;
  if (lastMine && group) {
    seenBy = group.info.members.filter((m) => m.joined && m.mxid !== myMxid && room.hasUserReadEvent(m.mxid, lastMine.getId())).length;
    if (seenBy) seenId = lastMine.getId();
  } else if (lastMine && !channel && room.hasUserReadEvent(otherMxidOf(conversation), lastMine.getId())) {
    seenId = lastMine.getId();
  }

  const items = events.map((event) => {
    const deleted = event.isRedacted();
    const quoted = byId.get(event.getOriginalContent()["m.relates_to"]?.["m.in_reply_to"]?.event_id);
    const mine = event.getSender() === myMxid;
    const author = !mine ? memberOf(event) : null;
    const quotedBlocked = quoted && memberOf(quoted)?.blocked;
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
      canEdit: mine && !deleted && event.getContent().msgtype === "m.text",
      // a line the call window writes when a call ends ("Voice call · 12 min"): drawn as a note, not a bubble
      notice: !deleted && event.getContent().msgtype === "m.notice",
      // in a group, who wrote it (someone who has left has no row any more); in a channel, which writer
      sender: group && !mine ? (author ? author.shown_name : "Someone who left")
        : channel && !mine ? (writerOf(event) ? writerOf(event).shown_name : "A former helper") : null,
      blocked: !!(author && author.blocked),
      filtered: !!(channel && channel.public && !mine && !deleted && filteredOut(event)),
      reportable: !!(channel && channel.public && !mine && !deleted),
    };
  });

  renderMessages(state.thread.messagesEl, items, messageHandlers);
  updateTyping();

  // Tell the server and the other person once per newest message, not once per draw:
  // our own receipt comes back as a Receipt event that redraws the thread, which
  // would send another receipt, and so on.
  const last = events[events.length - 1];
  if (!isRequest && last && !last.status && state.active.readUpTo !== last.getId()) {
    state.active.readUpTo = last.getId();
    state.client.sendReadReceipt(last, ReceiptType.Read).catch(() => {});
    const marked = channel ? markChannelRead(boot.formkey, conversation.channel_id)
      : group ? markGroupRead(boot.formkey, conversation.group_id) : markRead(boot.formkey, conversation.conversation_id);
    marked
      .then(() => window.refreshChatBadge?.()) // navbar chat badge (assets/js/chat_badge.js)
      .catch(() => {});
  }
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
  const { room, conversation, group, channel } = state.active;
  if (channel) return;
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
  if (!state.active || state.active.isRequest) return;
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
    const { conversation } = state.active;
    const reason = window.prompt("What's wrong with this message? (optional)");
    if (reason === null) return;
    try {
      await reportChannelMessage(boot.formkey, conversation.channel_id, item.eventId, reason);
      window.alert("Thanks: the site's admins will take a look.");
    } catch (e) {
      window.alert(e.message);
    }
  },
  onDelete(item) {
    if (!window.confirm("Delete this message for everyone?")) return;
    state.client.redactEvent(state.active.room.roomId, item.eventId).catch(() => {});
  },
};

async function openConversation(conversation) {
  const isRequest = state.tab === "request";
  const isGroup = conversation.kind === "group";
  const isChannel = conversation.kind === "channel";
  const room = isGroup || isChannel ? await waitForRoom(conversation.room_id, 8000) : state.client.getRoom(conversation.room_id);
  if (!room) return;
  const channel = isChannel
    ? { ...conversation, writers: new Map((conversation.writers || []).map((w) => [w.mxid, w])) }
    : null;

  let group = null;
  if (isGroup) {
    try {
      group = indexGroup(await groupInfo(conversation.group_id));
    } catch (e) {
      return;                       // no longer in it: the list is refreshed by the membership change
    }
  }

  stopTyping();
  state.context = null;
  state.active = { conversation, room, isRequest, group, channel };
  queueMicrotask(() => showThreadNotice(isGroup || isChannel ? "" : waitingNotice(conversation)));

  const pane = document.getElementById("chat-thread-pane");
  const inviter = isGroup && conversation.invited_by ? `@${conversation.invited_by.username}` : "Someone";
  state.thread = renderThreadShell(pane, {
    otherUser: conversation.other_user,
    group: group ? { label: group.info.label, size: group.info.size } : null,
    requestText: isGroup ? `${inviter} added you to this group. Accept to read it and write in it.` : null,
    onInfo: group ? () => groupSheets.openInfo(state.active.group.info) : null,
    onCall: isChannel ? null : (kind) => startCall(conversation.room_id, kind),
    channel,
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

  if (isRequest) {
    try {
      await state.client.scrollback(room, 30);
    } catch (e) {
      // best-effort preview - an empty/partial history is an acceptable
      // degraded state here, never a blocking error for the viewer
    }
  }

  renderActiveThread();
  watchCall();
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
  setTab("inbox");
  const refreshed = state.conversations.inbox.find((c) => c.key === conversation.key);
  if (refreshed) openConversation(refreshed);
}

function closeThread() {
  stopTyping();
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
  if (!active || active.isRequest) return;
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

async function openByKey(key, roomId) {
  await refreshLists();
  const found = state.conversations.inbox.find((c) => c.key === key || c.room_id === roomId);
  if (!found) return;
  setTab("inbox");
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
  }
  renderConversationList(
    document.getElementById("chat-conversation-list"),
    state.conversations[tab],
    state.active ? state.active.conversation.key : null,
    (convo) => openConversation(convo),
  );
}

async function refreshLists() {
  const [inbox, request, channel] = await Promise.all([
    listConversations("inbox"),
    listConversations("request"),
    listConversations("channel"),
  ]);
  state.conversations.inbox = inbox;
  state.conversations.request = request;
  state.conversations.channel = channel;
  setTabBadge(document.getElementById("chat-channels-badge"), channel.filter((c) => c.unread_count > 0).length);

  setTabBadge(document.getElementById("chat-inbox-badge"), inbox.filter((c) => c.unread_count > 0).length);
  setTabBadge(document.getElementById("chat-requests-badge"), request.length);
  window.refreshChatBadge?.();

  setTab(state.tab);
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
    if (inActiveRoom(room) && state.active.group && (membership === "leave" || membership === "ban")) closeThread();
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
  const find = () => state.conversations.inbox.find((c) => c.room_id === roomId);
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
    });
  }

  await refreshLists();

  isReady = true;
  tellParent({ type: "ruqqus-chat-ready" });

  if (boot.initialRoom) {
    const match = state.conversations.inbox.find((c) => c.room_id === boot.initialRoom)
      || state.conversations.request.find((c) => c.room_id === boot.initialRoom)
      || state.conversations.channel.find((c) => c.room_id === boot.initialRoom);
    if (match) {
      setTab(state.conversations.inbox.includes(match) ? "inbox" : state.conversations.channel.includes(match) ? "channel" : "request");
      openConversation(match);
    }
  }

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
