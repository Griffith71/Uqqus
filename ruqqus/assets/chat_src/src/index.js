import * as sdk from "matrix-js-sdk";
import { ClientEvent, Direction, EventType, RoomEvent, RoomMemberEvent, MatrixEventEvent, ReceiptType } from "matrix-js-sdk";
import { initAsync as initCryptoWasm } from "@matrix-org/matrix-sdk-crypto-wasm";

import { cryptoCallbacks, ensureEncryptionReady, keysPending, signOwnDevices, stopUnlocking } from "./crypto.js";
import { fetchKeyInfo, markCodeSaved, markReady } from "./keylock.js";
import { OnlySignedDevicesIsolationMode } from "matrix-js-sdk/lib/crypto-api/index.js";
// matrix-js-sdk has no `exports` map, so its backup manager can be reached by path: the same module the client's crypto uses
import { RustBackupManager } from "matrix-js-sdk/lib/rust-crypto/backup.js";
import { harvest, sweepEmpty } from "./harvest.js";
import { backupOnServer, findRescue, masterOnServer } from "./rescue.js";
import {
  ensureSession, forgetStoreKey, openCryptoStore, INDEX_KEY, currentEntry, markEntry, markUnlocked, readIndex, readWaiting, updateWaiting,
} from "./token.js";
import { LOCKED, READY, STOPPED, makeSendGate } from "./send_gate.js";
import { STOPPED as FRAME_STOPPED, frameRole, holdChatLock as holdTheChatLock, makeClient, startSession, uiForRole, unsentLine } from "./keeper.js";
import { watchSignedOut } from "./signed_out.js";
import { answerOwnVerifications } from "./verification.js";
import {
  listConversations, acceptConversation, declineConversation, markRead,
  groupInfo, acceptGroup, declineGroup, markGroupRead,
  postToChannel, leaveChannel, markChannelRead, reportChannelMessage, reportPrivateMessage, blockAccount,
  joinGuildChannel, postToGuildChannel, markGuildChannelRead, reportGuildChannelMessage, removeGuildChannelMessage,
  pinGuildChannelMessage, muteGuildChannel, acceptGuildChannelRules, guildChannelPins, searchGuildChannel,
  pinRoomMessage, setDisappearing, forwardTargets,
} from "./api.js";
import { renderConversationList, renderThreadShell, renderMessages, setTabBadge, noteUnreadCounts, personName } from "./ui.js";
import { askReport, contextOf } from "./report.js";
import { manageHelpers } from "./helpers.js";
import { attachmentOf, fileAgain, fileContent, recorder } from "./files.js";
import { DISAPPEAR, chooseTarget, chooseTimer, expiresAt, forwardedText, isForwarded, pinnedIds, renderPins as renderRoomPins,
  renderSearch as renderRoomSearch, timerAt, timerChangeText, timerNow } from "./room_extras.js";
import { createFresh } from "./fresh.js";
import { initGroups } from "./groups.js";
import { initExtras } from "./chat_extras.js";
import {
  createSenders, mentionPattern, renderMute, renderPins, renderResults, renderSearch, renderStageCard, renderVoiceCard, stopCards,
} from "./guild_channels.js";

const boot = window.RUQQUS_CHAT_BOOT;

// A room key this device receives is uploaded to the key backup only when the backup loop next runs, which the SDK starts after a random
// delay of up to 10 s: a key that arrives and whose device is then signed out within that time is in no backup. Up to 2 s here (the chat
// plan's 8). A private static of the installed SDK, pinned by tests/test_chat_harvest.py.
RustBackupManager.maxBackupLoopStartDelayMillis = 2000;

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
  fresh: createFresh(), // which message just arrived or was just sent: its bubble glints once (fresh.js, ui.js .is-new)
  locked: true,  // until this device has unlocked its encryption (crypto.js), nothing is sent from it
  keeper: null,  // the client's session keeper (keeper.js): renewals, other tabs, the store lock
};

// --- messages waiting for someone whose chat isn't set up yet (audit M-04) ---------------------------------------
// A message encrypted to someone with no device yet could never be read by them. So it waits here, in this browser,
// and goes as soon as they've opened chat (the conversation list says `ready`).

// Kept sealed under the account's waiting key (token.js readWaiting / updateWaiting): read when chat starts and held here to draw from,
// but every change is made on what is stored now (updateWaiting), so this page's copy never replaces what another tab added since. A
// page that stopped (another tab took chat over, a sign-out, a refused renewal) adds nothing and sends nothing: its client is stopped,
// and after a Log out the list is gone and must stay gone. It still takes off what it had already sent (`removing`): left in the list, the
// tab that took over (or this page reloaded) would send it again. That can't bring the list back after a Log out: the change is made on
// what is stored now, compare-and-set (token.js updateWaiting), and on an empty list it changes nothing, so nothing is written.
let waitingList = {};

function loadWaiting() {
  return waitingList;
}

function chatGone() {
  return chatLockLost || !!(state.keeper && state.keeper.stopped);
}

function changeWaiting(change, { removing = false } = {}) {
  if (!chatSession || (chatGone() && !removing)) return Promise.resolve(null);
  return updateWaiting(chatSession, change).then((list) => {
    waitingList = list;
    return list;
  }, () => null);                                   // not kept: what this page holds is still sent when possible
}

function waitFor(roomId, text) {
  if (chatGone()) return;
  const item = { text, at: Date.now() };
  (waitingList[roomId] = waitingList[roomId] || []).push(item);        // drawn at once (waitingNotice)
  changeWaiting((list) => { (list[roomId] = list[roomId] || []).push(item); });
}

let sendingWaiting = false;

// A room's messages go in order; only those sent leave the list, so a send that fails keeps the rest for next time.
async function sendWaiting() {
  if (state.locked || chatGone() || sendingWaiting) return;
  sendingWaiting = true;
  try {
    const waiting = (await changeWaiting(() => {})) || loadWaiting();      // what any tab added, as stored now
    const all = [...state.conversations.inbox, ...state.conversations.request];
    for (const roomId of Object.keys(waiting)) {
      const convo = all.find((c) => c.room_id === roomId);
      if (!convo || !convo.other_user || !convo.other_user.ready) continue;
      const sent = [];
      for (const item of waiting[roomId]) {
        if (chatGone()) break;
        try { await state.client.sendTextMessage(roomId, item.text); } catch (e) { break; }
        sent.push(item);
      }
      if (!sent.length) continue;
      await changeWaiting((list) => {
        const left = list[roomId] || [];
        for (const item of sent) {
          const at = left.findIndex((o) => o && o.at === item.at && o.text === item.text);
          if (at >= 0) left.splice(at, 1);
        }
        list[roomId] = left;
      }, { removing: true });
    }
  } finally {
    sendingWaiting = false;
  }
}

function waitingNotice(conversation) {
  const count = (loadWaiting()[conversation.room_id] || []).length;
  if (!count || !conversation.other_user) return "";
  return window.trn(
    "@%(name)s hasn't opened chat yet. Your message will be sent when they do.",
    "@%(name)s hasn't opened chat yet. Your %(num)s messages will be sent when they do.",
    count, { name: conversation.other_user.username });
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
  return (isMessageLikeEvent(event) && !isRelationEvent(event)) || (event.getType() === DISAPPEAR && event.getStateKey() === "");
}

// in a 1:1 chat or a group: whether this member may pin and set the timer (routes/chat_extras_pages.py says the same)
function mayChangeRoom(active) {
  if (!active || active.channel || active.gchannel || active.isRequest) return false;
  if (active.group) return ["owner", "admin"].includes(active.group.info.my_role);
  return state.tab !== "request";
}

function textOf(event) {
  // first: on a failure the SDK puts its own error text in the body ("** Unable to decrypt: …")
  if (event.isDecryptionFailure()) return window.tr("This message can't be read on this device.");
  const body = event.getContent().body;   // an edited message reads as its latest text
  if (body) return body;
  if (event.isBeingDecrypted() || event.getType() === EventType.RoomMessageEncrypted) return window.tr("Decrypting…");
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
  // a disappearing message is gone from the page as soon as its time is up, before the bot deletes it on the server
  const nowMs = Date.now();
  let nextExpiry = 0;
  const expired = (event) => {
    if (channel || gchannel || event.getType() === DISAPPEAR) return false;
    const at = expiresAt(room, event);
    if (at && at > nowMs) nextExpiry = nextExpiry ? Math.min(nextExpiry, at) : at;
    return !!at && at <= nowMs;
  };
  const shown = gchannel
    ? events.filter((event) => !event.isRedacted() && !isThreadMessage(event) && !(infoOf(event) && infoOf(event).muted))
    : events.filter((event) => !expired(event));
  clearTimeout(state.active.expiryTimer);
  if (nextExpiry) state.active.expiryTimer = setTimeout(scheduleRender, Math.min(nextExpiry - nowMs + 50, 2 ** 31 - 1));
  const roomPins = !channel && !gchannel ? new Set(pinnedIds(room)) : null;
  const mayChange = mayChangeRoom(state.active);
  const nameOfMxid = (mxid) => mxid === myMxid ? window.tr("You")
    : group ? (group.byMxid.get(mxid)?.shown_name || "") : (mxid === otherMxidOf(conversation) ? personName(conversation.other_user) : "");
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
  // ("Seen" off in Settings > Chat: as on WhatsApp, someone who shows nobody what they read sees nobody's either)
  const lastMine = boot.receipts === false ? null
    : [...events].reverse().find((e) => e.getSender() === myMxid && !e.isRedacted() && !e.status);
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
    const age = state.fresh.age(event);
    return {
      eventId: event.getId(),
      // glinting for about a second after it arrived (ui.js): how far into it, so a redraw carries on
      isNew: age >= 0,
      newAge: age,
      mine,
      body: deleted ? "" : textOf(event),
      status: event.status,
      edited: !deleted && !!event.replacingEventId(),
      deleted,
      quote: quoted && !quoted.isRedacted() ? (quotedBlocked ? window.tr("A message from someone you blocked") : textOf(quoted)) : null,
      reactions: deleted ? [] : reactionsOf(room, event, myMxid),
      seen: event.getId() === seenId,
      seenBy: group ? seenBy : 0,
      canEdit: mine && !deleted && (!gchannel || gchannel.encrypted) && event.getContent().msgtype === "m.text",
      // a line the call window writes when a call ends ("Voice call · 12 min"): drawn as a note, not a bubble
      notice: event.getType() === DISAPPEAR || (!deleted && event.getContent().msgtype === "m.notice"),
      timerNotice: event.getType() === DISAPPEAR ? timerChangeText(event, nameOfMxid) : null,
      // pinned in a 1:1 chat or a group (Matrix's own pinned list, written by the bot: routes/chat_extras_pages.py)
      roomPinned: !!(roomPins && roomPins.has(event.getId())),
      roomPinnable: !!(roomPins && mayChange && !deleted && !event.status && event.getType() !== DISAPPEAR && !event.isDecryptionFailure()),
      // forwarded on: never a message that disappears, nor one still sending or unreadable here
      forwardable: !channel && !gchannel && !isRequest && !deleted && !event.status && event.getType() !== DISAPPEAR
        && !event.isDecryptionFailure() && !timerAt(room, event) && event.getContent().msgtype !== "m.notice",
      forwarded: !deleted && isForwarded(event),
      // ...and what it says as data (call.js), so it is drawn in the reader's own language (ui.js)
      callSummary: !deleted && event.getContent().msgtype === "m.notice" ? event.getContent()["org.uqqus.call_summary"] || null : null,
      // in a group, who wrote it (someone who has left has no row any more); in a channel, which writer
      // Translators: in place of the name of someone who wrote in a group chat and has left it
      sender: group && !mine ? (author ? author.shown_name : window.tr("Someone who left"))
        // Translators: in place of the name of someone who used to write in a channel and no longer does
        : channel && !mine ? (writerOf(event) ? writerOf(event).shown_name : window.tr("A former helper"))
        : gchannel && !mine ? (info ? info.shown_name : "…") : null,
      // a file sent in the chat (files.js): its encrypted address and key, from the decrypted message
      attachment: deleted ? null : attachmentOf(event.getContent()),
      // a picture in a public channel, on this site and signed with the message (routes/broadcasts.py): shown as it is
      picture: !deleted && channel && channel.public ? channelPictureOf(event.getContent()) : null,
      // their picture beside the name (ui.js senderAvatar keeps only this site's addresses and https ones)
      senderAvatar: group && !mine ? (author ? author.profile_url : null)
        : channel && !mine ? (writerOf(event) ? writerOf(event).profile_url : null)
        : gchannel && !mine ? (info ? info.profile_url : null) : null,
      blocked: !!(author && author.blocked) || !!(info && info.blocked),
      filtered: !!(channel && channel.public && !mine && !deleted && filteredOut(event)) || !!(gchannel && !mine && !deleted && filteredOut(event)),
      // a 1:1 chat's or a group's message is reported with what this page shows of it (report.js); a public channel's and an
      // unencrypted guild channel's by the site, which keeps them
      reportable: !!(channel && channel.public && !mine && !deleted) || !!(gchannel && !gchannel.encrypted && !mine && !deleted)
        || (!channel && !gchannel && !mine && !deleted && !event.status),
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
    // not to a whole guild's channel; "Seen" off: a private receipt, which the server keeps for this account alone
    if (!gchannel) state.client.sendReadReceipt(last, boot.receipts === false ? ReceiptType.ReadPrivate : ReceiptType.Read).catch(() => {});
    const marked = gchannel ? markGuildChannelRead(boot, conversation.channel_id)
      : channel ? markChannelRead(boot, conversation.channel_id)
      : group ? markGroupRead(boot, conversation.group_id) : markRead(boot, conversation.conversation_id);
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
    window.alert(window.tr("Couldn't load earlier messages. Try again in a moment."));
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
  // Translators: the title of the panel that shows a message's thread in a guild channel
  const panel = state.thread.openPanel(window.tr("Thread"), () => { active.threadPanel = null; });
  const form = document.createElement("form");
  form.className = "d-flex";
  const input = document.createElement("textarea");
  input.className = "form-control mr-2 chat-composer-input";
  input.rows = 1;
  input.placeholder = window.tr("Add to the thread...");
  input.setAttribute("aria-label", window.tr("Add to the thread"));
  const send = document.createElement("button");
  send.type = "submit";
  send.className = "btn btn-primary align-self-end";
  // Translators: a button that sends a message
  send.textContent = window.tr("Send");
  form.append(input, send);
  if (active.gchannel.may_write) {
    panel.foot.appendChild(form);
    panel.showFoot();
  }
  const submit = () => {
    const text = input.value.trim();
    if (!text) return;
    input.value = "";
    postToGuildChannel(boot, active.conversation.channel_id, text, null, rootId).catch((e) => {
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
  const panel = state.thread.openPanel(window.tr("Pinned messages"));
  try {
    const pins = await guildChannelPins(active.conversation.channel_id);
    active.pinned = new Set(pins.map((p) => p.event_id));
    const draw = (list) => renderPins(panel.body, list, active.gchannel.can_pin, async (pin) => {
      try {
        await pinGuildChannelMessage(boot, active.conversation.channel_id, pin.event_id, false);
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
  // Translators: the title of the panel that searches a channel's messages
  const panel = state.thread.openPanel(window.tr("Search"));
  panel.showFoot();
  renderSearch(panel.body, panel.foot, async (query) => {
    try {
      renderResults(panel.body, await searchGuildChannel(active.conversation.channel_id, query), (eventId) => {
        panel.close();
        jumpTo(eventId);
      });
    } catch (e) {
      panel.body.textContent = e.message;
    }
  });
}

// A found message, shown where it is in the thread: older messages are loaded until it is there (at most ten pages of 50, or
// the start of the room), then it is scrolled into view and marked for a moment. Searching reads the last 90 days only.
async function jumpTo(eventId) {
  const active = state.active;
  if (!active || !active.room) return;
  const loaded = () => active.room.getLiveTimeline().getEvents().some((e) => e.getId() === eventId);
  for (let page = 0; page < 10 && !loaded() && state.active === active; page += 1) {
    const before = active.room.getLiveTimeline().getEvents().length;
    try {
      await state.client.scrollback(active.room, 50);
    } catch (e) {
      break;
    }
    if (active.room.getLiveTimeline().getEvents().length === before) break;      // the start of the room
  }
  if (state.active !== active) return;
  renderActiveThread();
  await new Promise((resolve) => requestAnimationFrame(resolve));
  const row = [...document.querySelectorAll("#chat-messages .chat-msg")].find((el) => el.dataset.eventId === eventId);
  if (!row) {
    showThreadNotice(active.gchannel ? window.tr("That message couldn't be found in the channel any more.")
      : window.tr("That message couldn't be found in this chat any more."));
    return;
  }
  row.scrollIntoView({ block: "center" });
  row.classList.add("chat-msg-found");
  setTimeout(() => row.classList.remove("chat-msg-found"), 2500);
}

// --- pins, the timer, search inside a chat and forwarding (room_extras.js) ---------------------------------------------
function openRoomPins() {
  const active = state.active;
  if (!active || !state.thread) return;
  const panel = state.thread.openPanel(window.tr("Pinned messages"));
  const draw = () => renderRoomPins(panel, {
    client: state.client, room: active.room, mayChange: mayChangeRoom(active), textOf,
    onJump: (id) => { panel.close(); jumpTo(id); },
    onUnpin: async (id) => {
      try {
        await pinRoomMessage(boot, active.room.roomId, id, false);
        setTimeout(draw, 800);              // once the bot's new list has come back
      } catch (e) {
        showThreadNotice(e.message);
      }
    },
  });
  draw();
}

function openTimer() {
  const active = state.active;
  if (!active || !state.thread) return;
  const panel = state.thread.openPanel(window.tr("Disappearing messages"));
  const draw = () => chooseTimer(panel, timerNow(active.room), mayChangeRoom(active), async (seconds) => {
    try {
      await setDisappearing(boot, active.room.roomId, seconds);
      panel.close();
    } catch (e) {
      showThreadNotice(e.message);
    }
  });
  draw();
}

function openRoomSearch() {
  const active = state.active;
  if (!active || !state.thread) return;
  // Translators: the title of the panel that searches a chat's messages
  const panel = state.thread.openPanel(window.tr("Search"));
  renderRoomSearch(panel, {
    client: state.client, room: active.room, textOf,
    isShown: (event) => isMessageLikeEvent(event) && !isRelationEvent(event) && !event.isDecryptionFailure(),
    onJump: (id) => { panel.close(); jumpTo(id); },
  });
}

// A message forwarded to another of your chats: its words marked "Forwarded" (never who wrote it), or its file encrypted
// afresh into your own storage for that chat. Sent by this page's own client (the chats are end-to-end encrypted).
function openForward(item) {
  const active = state.active;
  if (!active || !state.thread) return;
  const event = active.room.findEventById(item.eventId);
  if (!event || !item.forwardable) return;
  const panel = state.thread.openPanel(window.tr("Forward to…"));
  chooseTarget(panel, forwardTargets, active.room.roomId, async (target) => {
    panel.close();
    const room = await waitForRoom(target.room_id, 8000);
    if (!room) {
      showThreadNotice(window.tr("That chat isn't ready on this device yet. Open it once, then try again."));
      return;
    }
    const attachment = attachmentOf(event.getContent());
    try {
      if (attachment) {
        const refusal = fileRefusal();
        if (refusal) throw new Error(refusal);
        uploadLine(window.tr("Sending %(name)s…", { name: attachment.name || window.tr("File") }));
        const file = await fileAgain(attachment);
        const content = await fileContent(file, target.room_id, { voice: attachment.voice ? {} : null, name: attachment.name });
        content["org.uqqus.forwarded"] = true;
        await state.client.sendMessage(target.room_id, content);
        uploadLine("");
      } else {
        await state.client.sendMessage(target.room_id, forwardedText(event.getContent()));
      }
      showThreadNotice(window.tr("Forwarded."));
    } catch (e) {
      uploadLine("");
      showThreadNotice(e && e.message ? e.message : window.tr("That didn't work. Try again."));
    }
  });
}

function openMute() {
  const active = state.active;
  if (!active || !active.gchannel || !state.thread) return;
  const panel = state.thread.openPanel(window.tr("Notifications"));
  const draw = () => renderMute(panel.body, !!active.gchannel.muted, async (hours) => {
    try {
      const answer = await muteGuildChannel(boot, active.conversation.channel_id, hours);
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
    await acceptGuildChannelRules(boot, active.conversation.channel_id);
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

// A message in a 1:1 chat or a group: the box (why, and blocking the writer in the same place), then the message and up to
// four before it, as this page reads them, to the site (helpers/chat_reports.py). The writer is found by the chat's own
// people, never typed.
async function reportPrivately(item, { conversation, group, room }) {
  const event = room.findEventById(item.eventId);
  if (!event) return;
  const author = group ? group.byMxid.get(event.getSender()) : conversation.other_user;
  const name = author && author.username ? author.username : null;
  const answer = await askReport({ name });
  if (!answer) return;
  const shown = room.getLiveTimeline().getEvents().filter(isShownMessage);
  try {
    await reportPrivateMessage(boot, room.roomId, item.eventId, contextOf(shown, item.eventId, textOf), answer.reason);
    if (!answer.block || !name) {
      showThreadNotice(window.tr("Thanks: the site's admins will take a look."));
      return;
    }
    // the site's own block (Settings > Block & Mute's rules: a block that would end a paid subscription asks for the password there)
    const blocked = await blockAccount(boot, name).then((r) => r.ok).catch(() => false);
    showThreadNotice(blocked ? window.tr("Thanks: the site's admins will take a look, and you've blocked @%(name)s.", { name })
      : window.tr("Thanks: the site's admins will take a look. @%(name)s couldn't be blocked from here: block them from their profile.", { name }));
  } catch (e) {
    showThreadNotice(e.message);
  }
}

function updateTyping() {
  if (!state.active || !state.thread) return;
  const { room, conversation, group, channel, gchannel } = state.active;
  if (channel || gchannel) return;
  if (boot.receipts === false) { state.thread.setTyping(null); return; }      // "Seen" off: nobody's typing either
  if (!group) {
    const other = room.getMember(otherMxidOf(conversation));
    state.thread.setTyping(other && other.typing ? window.tr("%(name)s is typing…", { name: personName(conversation.other_user) }) : null);
    return;
  }
  const me = state.client.getUserId();
  const typing = group.info.members
    .filter((m) => m.mxid !== me && !m.blocked && room.getMember(m.mxid)?.typing)
    .map((m) => m.shown_name);
  let text = null;
  if (typing.length === 1) text = window.tr("%(name)s is typing…", { name: typing[0] });
  else if (typing.length === 2) text = window.tr("%(first)s and %(second)s are typing…", { first: typing[0], second: typing[1] });
  else if (typing.length > 2) text = window.tr("Several people are typing…");
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
  if (boot.receipts === false) return;                                                 // "Seen" off: never told
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
  else if (context.type === "edit") state.thread.setContext(window.tr("Editing message"));
  // Translators: above the message box while quoting a message; the quoted message's text follows
  else state.thread.setContext(window.tr("Quoting: %(text)s", { text: textOf(context.event) }));
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
    const { conversation, gchannel, channel, group, room } = state.active;
    if (!channel && !gchannel) return reportPrivately(item, { conversation, group, room });
    const reason = window.prompt(window.tr("What's wrong with this message? (optional)"));
    if (reason === null) return;
    try {
      if (gchannel) {
        await reportGuildChannelMessage(boot, conversation.channel_id, item.eventId, reason);
        window.alert(window.tr("Thanks: the guild's guildmasters will take a look."));
      } else {
        await reportChannelMessage(boot, conversation.channel_id, item.eventId, reason);
        window.alert(window.tr("Thanks: the site's admins will take a look."));
      }
    } catch (e) {
      window.alert(e.message);
    }
  },
  onThread(item) {
    openThread(item.eventId);
  },
  async onPin(item) {
    const { conversation, gchannel, room } = state.active;
    if (!gchannel) {
      try {
        await pinRoomMessage(boot, room.roomId, item.eventId, !item.roomPinned);
      } catch (e) {
        showThreadNotice(e.message);
      }
      return;                     // the bot's new pinned list arrives as room state and redraws the thread
    }
    try {
      const answer = await pinGuildChannelMessage(boot, conversation.channel_id, item.eventId, !item.pinned);
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
    if (!window.confirm(window.tr("Remove this message for everyone? The guild's log will say you did."))) return;
    try {
      await removeGuildChannelMessage(boot, conversation.channel_id, item.eventId);
    } catch (e) {
      window.alert(e.message);
    }
  },
  onForward(item) {
    openForward(item);
  },
  onDelete(item) {
    if (!window.confirm(window.tr("Delete this message for everyone?"))) return;
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
      await joinGuildChannel(boot, conversation.channel_id);
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
  state.fresh = createFresh();
  state.active = { conversation, room, isRequest, group, channel, gchannel };
  queueMicrotask(() => drawThreadNotice());

  const pane = document.getElementById("chat-thread-pane");
  state.thread = renderThreadShell(pane, {
    otherUser: conversation.other_user,
    group: group ? { label: group.info.label, size: group.info.size } : null,
    requestText: !isGroup ? null : conversation.invited_by
      ? window.tr("@%(name)s added you to this group. Accept to read it and write in it.", { name: conversation.invited_by.username })
      : window.tr("Someone added you to this group. Accept to read it and write in it."),
    onInfo: group ? () => groupSheets.openInfo(state.active.group.info) : null,
    onCall: isChannel || isGuildChannel ? null : (kind) => startCall(conversation.room_id, kind),
    // files and voice notes: a 1:1 chat and a group only (a channel's are not built)
    onAttach: isGuildChannel ? null : isChannel ? (channel && channel.public && channel.may_write ? pickChannelPicture : null) : pickFiles,
    onVoice: isChannel || isGuildChannel || !mayRecord() ? null : toggleVoiceNote,
    channel,
    guildChannel: gchannel,
    onSearch: gchannel ? openSearch : (!isChannel && !isRequest ? openRoomSearch : null),
    onPins: gchannel ? openPins : (!isChannel && !isRequest ? openRoomPins : null),
    onTimer: !isChannel && !isGuildChannel && !isRequest ? openTimer : null,
    onMute: gchannel ? openMute : null,
    onAcceptRules: gchannel ? onAcceptRules : null,
    onLeave: isChannel ? () => onLeaveChannel(conversation) : null,
    // a channel's owner chooses up to three helpers who write in it too (helpers.js, POST /api/broadcasts/<id>/helpers)
    onHelpers: isChannel ? () => manageHelpers({ boot, channel: state.active.channel, onChanged: (updated) => {
      if (state.active && state.active.channel && state.active.channel.channel_id === updated.channel_id) {
        state.active.channel = { ...updated, writers: new Map((updated.writers || []).map((w) => [w.mxid, w])) };
        scheduleRender();
      }
    } }) : null,
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
  if (!window.confirm(window.tr("Leave this channel?"))) return;
  try {
    await leaveChannel(boot, conversation.channel_id);
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
    postToGuildChannel(boot, gchannel.channel_id, text, quote).catch((e) => {
      window.alert(e.message);
      if (state.thread && !state.thread.inputEl.value) {     // slow mode and the like: what you wrote is not lost
        state.thread.inputEl.value = text;
        state.thread.fit?.();
      }
    });
    return;
  }
  if (state.active && state.active.channel && state.active.channel.public) {
    // a public channel is written through the site (word filter, daily count, bans), which sends it as you: nothing to unlock
    setContext(null);
    postToChannel(boot, state.active.channel.channel_id, text).catch((e) => window.alert(e.message));
    return;
  }
  const pair = state.active && state.active.conversation && state.active.conversation.other_user;
  if (pair && state.active.conversation.other_user.ready === false && !state.context) {
    // kept in its own sealed list and sent once they've opened chat and this device has unlocked (sendWaiting)
    waitFor(room.roomId, text);
    showThreadNotice(waitingNotice(state.active.conversation));
    return;
  }
  if (sendGate.state === STOPPED) {
    // chat stopped in this tab (chatUi.stop): nothing is queued or promised, and what was typed stays in the box
    if (state.thread && !state.thread.inputEl.value) {
      state.thread.inputEl.value = text;
      state.thread.fit?.();
    }
    drawThreadNotice();
    return;
  }
  const context = state.context;
  setContext(null);
  stopTyping();
  if (context && context.type === "edit" && text === context.event.getContent().body) return;
  // this device encrypts it: sent now when it has unlocked, otherwise it waits in order and goes once it has (send_gate.js). It is
  // never refused while chat is still starting (that used to lose the message with an alert to reload the page)
  sendGate.submit({ roomId: room.roomId, text, context });
}

// --- files and voice notes (files.js) ---------------------------------------------------------------------------

function uploadLine(text, isError = false) {
  const line = state.thread && state.thread.uploadEl;
  if (!line) return null;
  line.textContent = text || "";
  line.classList.toggle("text-danger", isError);
  line.classList.toggle("text-muted", !isError);
  return line;
}

// Why files can't be sent in the open chat now, or null. A file is encrypted for the devices in the chat now, so it waits
// for nothing: it goes only once this device has unlocked, and never to someone who hasn't opened chat yet (nobody could
// open it).
function fileRefusal() {
  const active = state.active;
  if (!active || !active.room || active.channel || active.gchannel) return window.tr("Files can't be sent here.");
  if (sendGate.state !== READY) return window.tr("Files can be sent once chat is unlocked on this device.");
  const other = active.conversation && active.conversation.other_user;
  if (other && other.ready === false) return window.tr("Files can be sent once @%(name)s has opened chat.", { name: other.username });
  if (!window.MediaUpload) return window.tr("Files can't be sent here.");
  return null;
}

async function sendFiles(files, { voice = null } = {}) {
  const refusal = fileRefusal();
  if (refusal) {
    uploadLine(refusal, true);
    return;
  }
  const roomId = state.active.room.roomId;
  // the account the member's pictures go to takes chat files: linked here first when there is none (media_upload.js)
  const ready = await window.MediaUpload.ensure("image", state.thread.uploadEl, window.tr("Where should the files you send in chats go?"),
    null, window.tr("They stay in your own account there, encrypted, using its free space. Only the people in the chat can open them."));
  if (!ready) return;
  for (const file of files) {
    const name = voice ? window.tr("Voice note") : file.name;
    uploadLine(window.tr("Sending %(name)s…", { name }));
    try {
      const content = await fileContent(file, roomId, {
        voice,
        name: voice ? window.tr("Voice note") : null,
        onProgress: (part) => uploadLine(window.tr("Sending %(name)s… %(percent)s%%", { name, percent: Math.round(part * 100) })),
      });
      if (!state.active || !state.active.room || state.active.room.roomId !== roomId || sendGate.state === STOPPED) return;
      await state.client.sendMessage(roomId, content);
    } catch (e) {
      uploadLine(e && e.message ? e.message : window.tr("The file couldn't be sent. Try again."), true);
      return;
    }
  }
  uploadLine("");
  renderActiveThread();
}

function pickFiles() {
  const refusal = fileRefusal();
  if (refusal) {
    uploadLine(refusal, true);
    return;
  }
  const input = document.createElement("input");
  input.type = "file";
  input.multiple = true;
  input.addEventListener("change", () => {
    const files = Array.from(input.files || []).slice(0, 10);
    if (files.length) sendFiles(files);
  });
  input.click();
}

// A public channel's writer sends a picture: uploaded as a post's picture is (checked and scanned, routes/media.py), then
// sent by the site with the words in the box (routes/broadcasts.py broadcast_message)
function pickChannelPicture() {
  const active = state.active;
  if (!active || !active.channel || !window.MediaUpload) return;
  const input = document.createElement("input");
  input.type = "file";
  input.accept = "image/jpeg,image/png,image/gif,image/webp";
  input.addEventListener("change", async () => {
    const file = input.files && input.files[0];
    if (!file) return;
    const ready = await window.MediaUpload.ensure("image", state.thread.uploadEl);
    if (!ready) return;
    uploadLine(window.tr("Sending %(name)s…", { name: file.name }));
    try {
      const asset = await window.MediaUpload.upload(file, "image");
      const words = state.thread ? state.thread.inputEl.value.trim() : "";
      await postToChannel(boot, active.channel.channel_id, words, asset.id);
      if (state.thread && words) { state.thread.inputEl.value = ""; state.thread.fit?.(); }
      uploadLine("");
    } catch (e) {
      uploadLine(e && e.message ? e.message : window.tr("The file couldn't be sent. Try again."), true);
    }
  });
  input.click();
}

function channelPictureOf(content) {
  const path = content && content["org.uqqus.picture"];
  if (typeof path !== "string" || !/^\/media\/[0-9a-z]{1,13}\/[A-Za-z0-9_-]{16,64}\.(jpg|png|gif|webp)$/.test(path)) return null;
  const info = content.info || {};
  return { path, w: Number(info.w) || 0, h: Number(info.h) || 0, only: content["org.uqqus.picture_only"] === true };
}

let voiceRecording = null;

// the microphone is the chat page's own (helpers/device_access.py): never inside the side panel or another page's frame
function mayRecord() {
  if (!navigator.mediaDevices || !window.MediaRecorder || frameRole() !== "top") return false;
  const policy = document.permissionsPolicy || document.featurePolicy;
  return !(policy && typeof policy.allowsFeature === "function") || policy.allowsFeature("microphone");
}

// The microphone button: the first press records, the second sends it (a cancel on the line drops it). A second press while the
// microphone is still starting (the browser asking, a slow first start) stops that same recording once it has started; it
// never starts another.
async function toggleVoiceNote(button) {
  const setPressed = (on) => {
    button.setAttribute("aria-pressed", on ? "true" : "false");
    button.classList.toggle("text-danger", on);
  };
  if (voiceRecording) {
    const { rec, started } = voiceRecording;
    voiceRecording = null;
    setPressed(false);
    const ok = await started;
    const made = ok ? await rec.stop() : null;
    if (made && made.duration >= 500) sendFiles([made.file], { voice: { duration: made.duration } });
    else uploadLine("");
    return;
  }
  const refusal = fileRefusal();
  if (refusal) {
    uploadLine(refusal, true);
    return;
  }
  const rec = recorder();
  const started = rec.start().then(() => true, () => false);
  const mine = { rec, started };
  voiceRecording = mine;
  setPressed(true);
  if (!(await started)) {
    if (voiceRecording === mine) voiceRecording = null;
    setPressed(false);
    uploadLine(window.tr("The microphone couldn't be used. Check that this site may use it."), true);
    return;
  }
  if (voiceRecording !== mine) return;           // pressed again while it started: that press stops and sends it
  const line = uploadLine(window.tr("Recording… press the microphone again to send it."));
  if (line) {
    const cancel = document.createElement("button");
    cancel.type = "button";
    cancel.className = "btn btn-link btn-sm p-0 ml-2";
    cancel.textContent = window.tr("Cancel");
    cancel.addEventListener("click", () => {
      if (voiceRecording !== mine) return;
      voiceRecording = null;
      rec.cancel();
      setPressed(false);
      uploadLine("");
    });
    line.append(cancel);
  }
}

// One message this device encrypts and sends (send_gate.js hands them over in order).
function deliver({ roomId, text, context }) {
  let sent;
  if (context && context.type === "edit") {
    // an edit is a new event that replaces the text of the original
    sent = state.client.sendMessage(roomId, {
      msgtype: "m.text",
      body: `* ${text}`,
      "m.new_content": { msgtype: "m.text", body: text },
      "m.relates_to": { rel_type: "m.replace", event_id: context.event.getId() },
    });
  } else if (context && context.type === "quote") {
    sent = state.client.sendMessage(roomId, {
      msgtype: "m.text",
      body: text,
      "m.relates_to": { "m.in_reply_to": { event_id: context.event.getId() } },
    });
  } else {
    sent = state.client.sendTextMessage(roomId, text);
  }
  renderActiveThread();
  return sent.catch((e) => {
    renderActiveThread();
    throw e;
  });
}

const sendGate = makeSendGate({ deliver, onChange: () => drawThreadNotice() });

// The line above the thread: what waits to be sent from this device first, otherwise the first messages waiting for someone whose
// chat isn't set up yet.
function drawThreadNotice() {
  const active = state.active;
  if (!active || !active.room) return;
  if (sendGate.state === STOPPED) {
    showThreadNotice(window.tr("Chat stopped in this tab."));
    return;
  }
  const count = sendGate.pending(active.room.roomId);
  if (count && sendGate.state === LOCKED) {
    showThreadNotice(window.trn(
      "Your message will be sent once chat is unlocked on this device.",
      "Your %(num)s messages will be sent once chat is unlocked on this device.", count),
    boot.single ? {} : { action: window.tr("Unlock"), onAction: () => unlockChat() });   // a guild's frame has no keys: Chat unlocks
    return;
  }
  if (count) {
    showThreadNotice(window.trn("Sending once chat is ready…", "Sending %(num)s messages once chat is ready…", count));
    return;
  }
  const pair = !["group", "channel", "guild_channel"].includes(active.conversation.kind);
  showThreadNotice(pair ? waitingNotice(active.conversation) : "");
}

// Unlocking this device: asked once when chat starts, and again from the thread's Unlock when the member left it locked with messages
// waiting. One at a time.
let unlocking = null;
let unlockChat = () => Promise.resolve(false);

async function onAccept(conversation, room) {
  if (conversation.kind === "group") {
    await acceptGroup(boot, conversation.group_id);     // the server joins the room for you
  } else {
    await state.client.joinRoom(room.roomId);
    await acceptConversation(boot, conversation.conversation_id);
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
  const empty = document.createElement("div");
  empty.className = "d-flex align-items-center justify-content-center h-100 text-muted";
  empty.textContent = window.tr("Select a conversation");
  document.getElementById("chat-thread-pane").replaceChildren(empty);
  document.getElementById("chat-app")?.classList.remove("chat-has-room");
}

async function onDecline(conversation, room) {
  if (conversation.kind === "group") {
    await declineGroup(boot, conversation.group_id);    // the server leaves the room for you
  } else {
    await state.client.leave(room.roomId);
    await declineConversation(boot, conversation.conversation_id);
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
  // a page that stopped (another tab took chat over, its keeper stopped) keeps the line that says so where the list was: a tab pressed,
  // or a list answer that arrives late, never draws over it
  if (chatGone()) return;
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
  if (chatGone()) return;                              // stopped while the site answered: nothing is drawn over the stop line
  if (wantGuilds) guildListAt = Date.now();
  state.conversations.inbox = inbox;
  state.conversations.group = group;
  state.conversations.request = request;
  state.conversations.channel = channel;
  state.conversations.guild = guild;
  noteUnreadCounts([...inbox, ...group, ...request, ...channel, ...guild], state.active ? state.active.conversation.key : null);
  setTabBadge(document.getElementById("chat-channels-badge"), channel.filter((c) => c.unread_count > 0).length);
  setTabBadge(document.getElementById("chat-guilds-badge"), guild.filter((c) => c.unread_count > 0).length);

  setTabBadge(document.getElementById("chat-inbox-badge"), inbox.filter((c) => c.unread_count > 0).length);
  setTabBadge(document.getElementById("chat-groups-badge"), group.filter((c) => c.unread_count > 0).length);
  setTabBadge(document.getElementById("chat-requests-badge"), request.length);
  window.refreshChatBadge?.();

  redrawTab();
  sendWaiting().catch(() => {});
}

// The lists when the page starts, asked until they come: one refused (the site's 100 requests a minute for a member, an app restarting)
// used to leave "Loading…" for good. Asked again after 15 s, then 30, then each minute, never sooner: repeated 429s count toward the
// address's time out. A page that stopped meanwhile asks nothing more.
async function listsAtStart() {
  for (let wait = 15_000; ; wait = Math.min(wait * 2, 60_000)) {
    try {
      await refreshLists();
      return;
    } catch (e) { /* said in the list below, and asked again */ }
    if (chatGone()) return;
    const empty = document.getElementById("chat-list-empty");
    if (empty) empty.textContent = window.tr("Couldn't load your chats. Trying again…");
    await new Promise((resolve) => { setTimeout(resolve, wait); });
    if (chatGone()) return;
  }
}

function showThreadNotice(text, { action = null, onAction = null } = {}) {
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
  const line = document.createElement("span");
  line.textContent = text;
  note.replaceChildren(line);
  if (action && onAction) {
    const button = document.createElement("button");
    button.type = "button";
    button.className = "btn btn-link btn-sm p-0 ml-2 align-baseline";
    button.textContent = action;
    button.addEventListener("click", () => {
      button.disabled = true;
      onAction();
    });
    note.append(button);
  }
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
  client.on(RoomEvent.Timeline, (event, room, toStartOfTimeline, removed, data) => {
    if (state.active && room && room.roomId === state.active.room.roomId) {
      state.fresh.note(event, toStartOfTimeline, data, isShownMessage(event));
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
const SHARE_WAIT_MS = 10 * 1000;                   // under share_chat.js's SEND_WAIT (15 s), so the sheet hears why

function tellParent(message) {
  if (window.parent && window.parent !== window) window.parent.postMessage(message, window.location.origin);
}

async function sendShared({ room_id: roomId, text }) {
  if (!isReady) throw new Error(window.tr("Chat is still starting."));
  if (typeof roomId !== "string" || typeof text !== "string" || !text.trim() || text.length > 2000) {
    throw new Error(window.tr("That message can't be sent."));
  }
  const find = () => [...state.conversations.inbox, ...state.conversations.group].find((c) => c.room_id === roomId);
  let convo = find();
  if (!convo) {
    await refreshLists();          // a chat that was just started from the share sheet
    convo = find();
  }
  // the inbox holds every chat you can write in, including the ones you started yourself;
  // a request waiting for you to accept it is not in it
  if (!convo) throw new Error(window.tr("You can't send to that chat."));
  if (convo.other_user && convo.other_user.ready === false) {
    waitFor(roomId, text);                // sent when they've opened chat and this device has unlocked (M-04)
    return;
  }
  // still unlocking: waited for a little, as the sheet waits for an answer; left locked, the member is told to open Chat
  const unlocked = await Promise.race([sendGate.settled(), new Promise((resolve) => setTimeout(() => resolve(null), SHARE_WAIT_MS))]);
  if (unlocked !== READY) throw new Error(window.tr("Open Chat once to unlock your messages, then try again."));
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
    tellParent({ type: "ruqqus-chat-sent", id: data.id, ok: false, error: e && e.message ? e.message : window.tr("Could not send.") });
  }
});

// One chat client per browser: they would share one device and its crypto store, and two at once can lose keys.
// The newest tab (or side panel) takes over; the one it took over from stops and says so. That can happen while this page is
// still getting its session or opening its store, when there is nothing yet to stop, so main() asks chatLockLost at each step.
let chatLockLost = false;
let chatSession = null;

function holdChatLock() {
  // taken over by another tab: told here, then the keeper stops in the safe order (keeper.js holdChatLock: the store this page may
  // still be opening settles, the client stops, the store lock goes), so the tab that took over never opens the store beside it
  return holdTheChatLock({
    keeper: () => state.keeper,
    lost: () => {
      chatLockLost = true;
      isReady = false;
      keysWaitLine("");                          // a page that stopped waits for nothing more: the note below is what it says
      stopUnlocking();                           // an unlock box left open could still reset the account's keys (crypto.js)
      // messages this page held to send once it unlocked go with it: said, never dropped without a word (send_gate.js)
      const unsent = sendGate.pending();
      sendGate.stop();                           // nothing more is queued or promised here: what was written is said in the note below
      const role = frameRole();
      if (role === "panel" || role === "share") {
        // a frame shows no note of its own (nobody sees the hidden Send-in-chat frame): its parent drops it and makes a new one when it
        // is next needed (share_chat.js), or shows the line with a Reload (side_panels.js)
        uiForRole(role, chatUi).stop(unsent ? unsentLine(unsent) : window.tr("Chat is open in another tab."), { why: "taken_over" });
        return;
      }
      const pane = document.getElementById("chat-thread-pane");
      const list = document.getElementById("chat-conversation-list");
      const note = document.createElement("div");
      note.className = "text-center text-muted p-4";
      const text = document.createElement("p");
      text.textContent = window.tr("Chat is open in another tab.");
      const again = document.createElement("button");
      again.type = "button";
      again.className = "btn btn-primary btn-sm";
      again.textContent = window.tr("Use it here");
      again.addEventListener("click", () => window.location.reload());
      note.append(text);
      if (unsent) {
        const lostLine = document.createElement("p");
        lostLine.textContent = unsentLine(unsent);
        note.append(lostLine);
      }
      note.append(again);
      if (role === "single") {
        // the guild Chat tab's frame: its list is hidden, so the note goes where the thread was (noteArea), and its page is told, so
        // the frame is made again when the member comes back to that tab (guild/chat.html); never a blank frame
        const area = noteArea();
        if (area) area.replaceChildren(note);
        tellParent({ type: FRAME_STOPPED, why: "taken_over", text: "" });
        return;
      }
      if (list) list.replaceChildren(note);
      if (pane) pane.replaceChildren();
    },
  });
}

// This account's call device in this browser (the call window's and a Space's), from the index.
let signedCallDevice = null;

function callDevice() {
  const entry = currentEntry(readIndex(), boot.mxid, "call");
  return entry ? entry.d : null;
}

// --- what the page shows when chat can't start or stops (keeper.js, the chat plan's 6.6) ------------------------------------------
// In place of the conversation list, or of the thread in the guild Chat tab's frame (`single`), whose list is hidden. A frame in the
// side panel or the Send-in-chat sheet shows none of this: keeper.js hands it to the parent page.

function noteArea() {
  return document.getElementById(boot.single ? "chat-thread-pane" : "chat-conversation-list");
}

function bannerHome() {
  return document.getElementById(boot.single ? "chat-thread-pane" : "chat-list-pane");
}

function drawLine(text, { action = null, onAction = null } = {}) {
  const note = document.createElement("div");
  note.className = "text-center text-muted p-4";
  const line = document.createElement("p");
  line.textContent = text;
  note.append(line);
  if (action && onAction) {
    const button = document.createElement("button");
    button.type = "button";
    button.className = "btn btn-primary btn-sm";
    button.textContent = action;
    button.addEventListener("click", () => {
      button.disabled = true;
      onAction();
    });
    note.append(button);
  }
  return note;
}

function setBanner(text, { action = null, onAction = null, closable = false } = {}) {
  const old = document.getElementById("chat-banner");
  if (old) old.remove();
  const home = bannerHome();
  if (!text || !home) return;
  const banner = document.createElement("div");
  banner.id = "chat-banner";
  banner.className = "alert alert-warning small m-2 mb-0 d-flex align-items-center";
  banner.setAttribute("role", "status");
  const line = document.createElement("span");
  line.className = "flex-grow-1";
  line.textContent = text;
  banner.append(line);
  if (action && onAction) {
    const button = document.createElement("button");
    button.type = "button";
    button.className = "btn btn-link btn-sm p-0 ml-2";
    button.textContent = action;
    button.addEventListener("click", onAction);
    banner.append(button);
  }
  if (closable) {
    const close = document.createElement("button");
    close.type = "button";
    close.className = "close ml-2";
    close.setAttribute("aria-label", window.tr("Close"));
    close.textContent = "\u00d7";
    close.addEventListener("click", () => banner.remove());
    banner.append(close);
  }
  home.prepend(banner);
}

// a line of its own above the list while another tab finishes this account's chat set-up (crypto.js otherTabFinishing); "" takes it away.
// Never in the note area: that holds the conversation list, and the note a page that lost chat leaves ("Chat is open in another tab",
// Use it here), which taking the line away would have erased. Not drawn once the page has stopped.
// A reminder above the list while the member hasn't said they kept their recovery code (accounts set up before it was asked, or a code box
// closed some other way): "Make a new code" or "I have it"; "Later" hides it for this tab only (sessionStorage CODE_LATER).
export const CODE_LATER = "uqqus_chat_code_later";
async function codeReminder() {
  try {
    if (chatGone() || !["top", "panel"].includes(frameRole())) return;
    try { if (sessionStorage.getItem(CODE_LATER)) return; } catch (e) { /* no storage: shown */ }
    const info = await fetchKeyInfo();
    if (!info || !info.recovery_blob || info.code_saved) return;
    const home = bannerHome();
    if (!home || chatGone() || document.getElementById("chat-code-reminder")) return;
    const line = document.createElement("div");
    line.id = "chat-code-reminder";
    line.className = "alert alert-warning small m-2 mb-0";
    line.setAttribute("role", "status");
    const words = document.createElement("span");
    words.textContent = window.tr("Have you kept your chat recovery code? Without it, a forgotten password can lose your old messages.") + " ";
    const make = document.createElement("a");
    make.href = "/chat?recovery=new";
    make.className = "mr-2";
    make.textContent = window.tr("Make a new code");
    const have = document.createElement("button");
    have.type = "button";
    have.className = "btn btn-link btn-sm p-0 mr-2 align-baseline";
    have.textContent = window.tr("I have it");
    have.addEventListener("click", async () => { await markCodeSaved(boot); line.remove(); });
    const later = document.createElement("button");
    later.type = "button";
    later.className = "btn btn-link btn-sm p-0 align-baseline text-muted";
    later.textContent = window.tr("Later");
    later.addEventListener("click", () => {
      try { sessionStorage.setItem(CODE_LATER, "1"); } catch (e) { /* hidden for this page only */ }
      line.remove();
    });
    line.append(words, make, have, later);
    home.prepend(line);
  } catch (e) { /* a reminder is never worth an error */ }
}

function keysWaitLine(text) {
  const old = document.getElementById("chat-keys-wait");
  if (old) old.remove();
  const home = bannerHome();
  if (!text || !home || chatGone()) return;
  const line = document.createElement("div");
  line.id = "chat-keys-wait";
  line.className = "alert alert-info small m-2 mb-0";
  line.setAttribute("role", "status");
  line.textContent = text;
  home.prepend(line);
}

// Whether the member can see this page: a frame in the side panel is hidden while the panel is closed (side_panels.js), and the
// Send-in-chat frame always is (share_chat.js). A page they can't see begins no keys and asks nothing (crypto.js NotSeen).
function inSight() {
  const role = frameRole();
  if (role === "share") return false;
  if (role !== "panel") return true;
  try {
    const frame = window.frameElement;
    return !!frame && frame.getClientRects().length > 0;
  } catch (e) {
    return false;
  }
}

const chatUi = {
  status(text) {
    const area = noteArea();
    if (area && text) area.replaceChildren(drawLine(text));
  },
  stop(text, options = {}) {
    isReady = false;
    keysWaitLine("");
    if (state.keeper && state.keeper.stopped) {
      // stopped without a reload (keeper.js: a refused renewal, or messages waiting when it ended): the client is stopped and, after
      // a Log out elsewhere, the sign-in is over. The open conversation goes (its messages and its box), and nothing more is queued or
      // promised (send_gate.js STOPPED); the line, with its Reload, is what is left. Not for a start that failed: there is no client yet.
      sendGate.stop();
      closeThread();
    }
    const area = noteArea();
    if (area) area.replaceChildren(drawLine(text, options));
  },
  banner: (text, options = {}) => setBanner(text, options),
  notice: (text) => setBanner(text, { closable: true }),
};

async function main() {
  if (!boot) return;

  await holdChatLock();
  // a page open from before a QR sign-in reloads into the password form; a refusal, no storage or a site that can't answer is said
  // in place (keeper.js startSession)
  // a page that lost chat to another tab while the site answered drops that answer (TakenOver), so it never marks the device the other
  // tab holds `removed` (token.js settle)
  const session = await startSession(() => ensureSession(boot, "chat", { stillMine: () => !chatLockLost }), chatUi,
    { stillMine: () => !chatLockLost });
  if (!session || chatLockLost) return forgetStoreKey(session);         // never opened: the raw store key goes now (1.2)

  chatSession = session;
  waitingList = await readWaiting(session);
  const { client, keeper } = makeClient(sdk, session, {
    cryptoCallbacks,
    // emoji only: a new app is verified by comparing them on this page (verification.js); this page can't scan a code
    verificationMethods: ["m.sas.v1"],
  }, { ui: chatUi });
  state.client = client;
  state.keeper = keeper;
  keeper.unsent = () => sendGate.pending();      // a page that stops with messages still waiting says so instead of reloading them away
  keeper.onStopped = stopUnlocking;              // a page whose client stopped asks nothing more (crypto.js)
  keeper.keysPending = keysPending;              // a page ending while new keys' copies are saved finishes them before it reloads
  watchSignedOut(client, keeper);
  // taken over while this page was still getting its session: there was no keeper then for the lost lock to stop
  if (chatLockLost || keeper.stopped) return keeper.shutdown();

  // the store's own lock, waited for (another tab that had it lets go only once its client has stopped), then the wasm, then the
  // store, asking between each whether another tab took over meanwhile (keeper.js openStore, 6.4).
  // matrix-js-sdk's rust crypto backend loads the WASM binary via a runtime fetch() relative to its own module URL, which doesn't
  // survive bundling into a single file - so it is initialized here first, pointing at the copy the build placed next to this bundle;
  // initRustCrypto()'s own (argument-less) init call then just awaits this same promise.
  let opened = false;
  try {
    opened = await keeper.openStore(() => openCryptoStore(client, session), {
      prepare: () => initCryptoWasm("/assets/js/matrix_sdk_crypto_wasm_bg.wasm"),
    });
  } catch (e) {
    // the store lock never came, a store that won't open twice in this tab, or anything else: said, never an endless "Loading…".
    // A side-panel or Send-in-chat frame tells its parent instead (keeper.ui, uqqus-chat:stopped). A page that lost chat to another tab
    // meanwhile has said so already ("Chat is open in another tab."): that line, and its "Use it here", stay
    keeper.shutdown();
    if (chatLockLost) return undefined;
    return keeper.ui.stop(window.tr("Chat couldn't start in this browser."), {
      why: "unavailable", action: window.tr("Try again"), onAction: () => window.location.reload(),
    });
  }
  if (!opened || chatLockLost || keeper.stopped) return;
  // the stores this browser kept of the account's older devices: one found empty goes, in the background (harvest.js, 2.4 case 3)
  const swept = sweepEmpty(session, { stopped: chatGone }).catch(() => []);
  // the room keys this browser's older devices of the account held move to this one, whose backup uploads them, and only then do their
  // stores go (harvest.js, 2.4 case 2, 8): in the background, after the sweep, once this device is unlocked. `hold`: the import into this
  // device's store is finished before the keeper stops the client and lets the store lock go (keeper.js holdFor)
  const harvestKept = () => swept.then(() => harvest(client, session, { stopped: chatGone, hold: (work) => keeper.holdFor(work) }))
    .catch(() => {});
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
  answerOwnVerifications(client);

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

  await listsAtStart();
  // taken over (or its keeper stopped) while the lists were on their way: the page has said why, and nothing here starts again over it
  // (no "ready" to a parent, no thread opened, no unlock that would leave what is typed waiting for good)
  if (chatGone()) return;

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
  if (boot.single) {
    sendGate.locked();
    // it takes the older stores' keys only when this device was unlocked here before (`x`, token.js markUnlocked): its store then holds
    // the account's keys and its backup (switched on as the client starts), as on the chat page after an unlock; a device never unlocked
    // here leaves them to the chat page or the side panel
    if (session.entry && session.entry.x) harvestKept();
    return;
  }

  // Non-blocking: the lists show at once; sending waits until this device has unlocked (crypto.js)
  const newCode = new URLSearchParams(window.location.search).get("recovery") === "new";
  if (newCode) {
    // read once: a reload, a restored tab or a history entry never asks again (crypto.js asks before it replaces the code)
    try {
      const here = new URL(window.location.href);
      here.searchParams.delete("recovery");
      window.history.replaceState(window.history.state, "", here.pathname + here.search + here.hash);
    } catch (e) { /* the address keeps it: the question still comes first */ }
  }
  // why this device is new (6.5): one line above the unlock box, or a notice when nothing was asked; then forgotten
  const why = session.why;
  if (why) markEntry(session.entry, { why: null }).catch(() => {});
  // keeper.ui: a side-panel or Send-in-chat frame posts the line to its parent (uqqus-chat:notice), which shows it
  let first = { newCode, why, notify: keeper.ui.notice };
  unlockChat = () => {
    if (!unlocking) {
      const asked = first || {};
      first = null;
      sendGate.starting();
      // `stopped`: once this page has lost chat or its keeper stopped, nothing more is asked, reset or saved (crypto.js)
      // `hold`: keys being made or reset on the server are finished before the keeper stops the client (keeper.js holdFor)
      // `wait`: another tab of this account is finishing its new keys' copies: this page waits for it, and says so
      // `inSight`: a hidden frame begins no keys and asks nothing (the chat page, or the panel once open, does)
      // `keyReset`: a Start over's key upload goes through the site with the grant the member's password bought (keeper.js)
      // `notify` on every call (a later Unlock's Start over that fails says so too); `newCode` and `why` only the first time
      unlocking = ensureEncryptionReady(client, boot, { notify: keeper.ui.notice, ...asked, stopped: chatGone, hold: (work) => keeper.holdFor(work),
        wait: keysWaitLine, inSight,
        keyReset: { arm: (grant) => keeper.armKeyReset(grant), disarm: () => keeper.disarmKeyReset() },
        // a store this browser kept of an earlier device may hold the keys a reset left locked (rescue.js): the chat page and its panel only
        // (after the sweep: it opens the same stores under the same locks, and a store it held would be skipped)
        rescue: ["top", "panel"].includes(frameRole()) ? {
          find: () => swept.then(() => findRescue(session, { serverMaster: () => masterOnServer(client), stopped: chatGone })),
          master: () => masterOnServer(client),
          backup: () => backupOnServer(client),
        } : null })
        .catch((e) => { console.warn("Chat: encryption setup failed", e); return false; })
        .then(async (ready) => {
          if (chatGone()) return false;            // the page has said why it stopped: no Unlock is offered over it
          if (!ready) {
            sendGate.locked();                     // what waits stays; the thread offers Unlock again
            return false;
          }
          state.locked = false;
          sendGate.ready();                        // what was written while unlocking goes now, in order
          // the store now holds this account's keys (2.2 `x`); then the call device this browser made, from its own index (3.4)
          markUnlocked(session).catch(() => {});
          await markReady(boot).catch(() => {});
          signedCallDevice = callDevice();
          await signOwnDevices(client, [signedCallDevice]).catch(() => {});
          sendWaiting().catch(() => {});
          codeReminder();
          // the older stores' keys (harvest.js): never in the hidden Send-in-chat frame
          if (frameRole() !== "share") harvestKept();
          return true;
        })
        .finally(() => { unlocking = null; });
    }
    return unlocking;
  };
  unlockChat();

  // a call or Space window opened later makes a device of its own here: sign it as it appears. Only this account's call device, read
  // from this browser's own index (a device the server merely lists is never signed), and only when it changes
  window.addEventListener("storage", (event) => {
    if ((event.key !== INDEX_KEY && event.key !== null) || state.locked) return;
    const device = callDevice();
    if (!device || device === signedCallDevice) return;
    signedCallDevice = device;
    signOwnDevices(client, [device]).catch(() => {});
  });
}

main();
