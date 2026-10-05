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
  thread: null, // { messagesEl, inputEl }
};

function isMessageLikeEvent(event) {
  return event.getType() === EventType.RoomMessage || event.getType() === EventType.RoomMessageEncrypted;
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
    .filter(isMessageLikeEvent);

  const items = events.map((event) => {
    const body = event.getContent().body;
    let text;
    if (body) {
      text = body;
    } else if (event.isDecryptionFailure()) {
      text = "[Unable to decrypt message]";
    } else if (event.isBeingDecrypted() || event.getType() === EventType.RoomMessageEncrypted) {
      text = "Decrypting…";
    } else {
      text = "";
    }
    return {
      eventId: event.getId(),
      senderId: event.getSender() === myMxid ? boot.userId : -1,
      body: text,
      status: event.status,
    };
  });

  renderMessages(state.thread.messagesEl, items, boot.userId);

  if (!isRequest) {
    const last = events[events.length - 1];
    if (last) {
      state.client.sendReadReceipt(last, ReceiptType.Read).catch(() => {});
    }
    markRead(boot.formkey, conversation.conversation_id)
      .then(() => window.refreshChatBadge?.()) // navbar chat badge (assets/js/chat_badge.js)
      .catch(() => {});
  }
}

async function openConversation(conversation) {
  const isRequest = state.tab === "request";
  const room = state.client.getRoom(conversation.room_id);
  if (!room) return;

  state.active = { conversation, room, isRequest };

  const pane = document.getElementById("chat-thread-pane");
  state.thread = renderThreadShell(pane, {
    otherUser: conversation.other_user,
    isRequest,
    onAccept: () => onAccept(conversation, room),
    onDecline: () => onDecline(conversation, room),
    onSend: (text) => onSend(room, text),
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
  state.client.sendTextMessage(room.roomId, text).catch(() => {
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
    if (!isMessageLikeEvent(event)) return;
    if (state.active && room && room.roomId === state.active.room.roomId) {
      renderActiveThread();
    }
    queueRefresh();
  });

  client.on(RoomEvent.LocalEchoUpdated, (event, room) => {
    if (state.active && room && room.roomId === state.active.room.roomId) {
      renderActiveThread();
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
      renderActiveThread();
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
