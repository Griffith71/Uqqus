import * as sdk from "matrix-js-sdk";
import { ClientEvent, EventType, RoomEvent, RoomMemberEvent, MatrixEventEvent, ReceiptType } from "matrix-js-sdk";
import { initAsync as initCryptoWasm } from "@matrix-org/matrix-sdk-crypto-wasm";

import { ensureSession } from "./token.js";
import { cryptoCallbacks, ensureEncryptionReady } from "./crypto.js";
import { fetchStoredRecoveryKey } from "./recovery.js";
import { listConversations, acceptConversation, declineConversation, markRead } from "./api.js";
import { renderConversationList, renderThreadShell, renderMessages, setTabBadge } from "./ui.js";

const boot = window.RUQQUS_CHAT_BOOT;

const state = {
  client: null,
  tab: "inbox",
  conversations: { inbox: [], request: [] },
  active: null, // { conversation, room }
  thread: null, // { messagesEl, inputEl, setContext, setTyping }
  context: null, // { type: "quote" | "edit", event } - what the box is answering or replacing
  typingSent: 0, // when we last told the room we are typing (0: not typing)
  typingTimer: null,
  renderQueued: false,
};

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

// a message the thread shows as a bubble
function isShownMessage(event) {
  return isMessageLikeEvent(event) && !isRelationEvent(event);
}

function textOf(event) {
  const body = event.getContent().body;   // an edited message reads as its latest text
  if (body) return body;
  if (event.isDecryptionFailure()) return "[Unable to decrypt message]";
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

function renderActiveThread() {
  const { room, conversation, isRequest } = state.active;
  const myMxid = state.client.getUserId();

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

  // the last message of mine, and whether the other person has read it
  const otherMxid = otherMxidOf(conversation);
  const lastMine = [...events].reverse().find((e) => e.getSender() === myMxid && !e.isRedacted() && !e.status);
  const seenId = lastMine && room.hasUserReadEvent(otherMxid, lastMine.getId()) ? lastMine.getId() : null;

  const items = events.map((event) => {
    const deleted = event.isRedacted();
    const quoted = byId.get(event.getOriginalContent()["m.relates_to"]?.["m.in_reply_to"]?.event_id);
    const mine = event.getSender() === myMxid;
    return {
      eventId: event.getId(),
      mine,
      body: deleted ? "" : textOf(event),
      status: event.status,
      edited: !deleted && !!event.replacingEventId(),
      deleted,
      quote: quoted && !quoted.isRedacted() ? textOf(quoted) : null,
      reactions: deleted ? [] : reactionsOf(room, event, myMxid),
      seen: event.getId() === seenId,
      canEdit: mine && !deleted && event.getContent().msgtype === "m.text",
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
    markRead(boot.formkey, conversation.conversation_id)
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
  const other = state.active.room.getMember(otherMxidOf(state.active.conversation));
  state.thread.setTyping(other && other.typing ? state.active.conversation.other_user.username : null);
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
  onDelete(item) {
    if (!window.confirm("Delete this message for everyone?")) return;
    state.client.redactEvent(state.active.room.roomId, item.eventId).catch(() => {});
  },
};

async function openConversation(conversation) {
  const isRequest = state.tab === "request";
  const room = state.client.getRoom(conversation.room_id);
  if (!room) return;

  stopTyping();
  state.context = null;
  state.active = { conversation, room, isRequest };

  const pane = document.getElementById("chat-thread-pane");
  state.thread = renderThreadShell(pane, {
    otherUser: conversation.other_user,
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
}

function onSend(room, text) {
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
  await state.client.joinRoom(room.roomId);
  await acceptConversation(boot.formkey, conversation.conversation_id);
  await refreshLists();
  setTab("inbox");
  const refreshed = state.conversations.inbox.find((c) => c.conversation_id === conversation.conversation_id);
  if (refreshed) openConversation(refreshed);
}

async function onDecline(conversation, room) {
  await state.client.leave(room.roomId);
  await declineConversation(boot.formkey, conversation.conversation_id);
  state.active = null;
  document.getElementById("chat-thread-pane").innerHTML =
    '<div class="d-flex align-items-center justify-content-center h-100 text-muted">Select a conversation</div>';
  await refreshLists();
}

function setTab(tab) {
  state.tab = tab;
  for (const link of document.querySelectorAll("[data-chat-tab]")) {
    link.classList.toggle("active", link.dataset.chatTab === tab);
  }
  renderConversationList(
    document.getElementById("chat-conversation-list"),
    state.conversations[tab],
    state.active ? state.active.conversation.conversation_id : null,
    (convo) => openConversation(convo),
  );
}

async function refreshLists() {
  const [inbox, request] = await Promise.all([
    listConversations("inbox"),
    listConversations("request"),
  ]);
  state.conversations.inbox = inbox;
  state.conversations.request = request;

  setTabBadge(document.getElementById("chat-inbox-badge"), inbox.filter((c) => c.unread_count > 0).length);
  setTabBadge(document.getElementById("chat-requests-badge"), request.length);
  window.refreshChatBadge?.();

  setTab(state.tab);
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

  client.on(RoomEvent.MyMembership, queueRefresh);
  client.on(ClientEvent.Room, queueRefresh);
  client.on(RoomMemberEvent.Membership, queueRefresh);

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

async function main() {
  if (!boot) return;

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

  for (const link of document.querySelectorAll("[data-chat-tab]")) {
    link.addEventListener("click", (e) => {
      e.preventDefault();
      setTab(link.dataset.chatTab);
    });
  }

  await refreshLists();

  if (boot.initialRoom) {
    const match = state.conversations.inbox.find((c) => c.room_id === boot.initialRoom)
      || state.conversations.request.find((c) => c.room_id === boot.initialRoom);
    if (match) {
      setTab(state.conversations.inbox.includes(match) ? "inbox" : "request");
      openConversation(match);
    }
  }

  // Non-blocking: lets the list/thread UI render immediately rather than
  // waiting on cross-signing/secret-storage setup.
  fetchStoredRecoveryKey()
    .catch(() => null)
    .then((storedRecoveryKey) => ensureEncryptionReady(client, storedRecoveryKey, boot.formkey))
    .catch(() => {});
}

main();
