// Pins, disappearing messages, search inside a chat and forwarding, in a 1:1 chat or a group (CLAUDE.md "Chat extras").
// Pins and the timer are room state the site's bot writes (routes/chat_extras_pages.py: only the bot sends room state);
// search reads what this browser can decrypt, since the site can't read a private chat. Everything is drawn with
// textContent.
import { Direction } from "matrix-js-sdk";

export const PINNED = "m.room.pinned_events";
export const DISAPPEAR = "org.uqqus.disappear";
export const TIMER_CHOICES = [0, 86400, 7 * 86400];

const SEARCH_PAGES = 40;            // pages of 50 read back at most: about 2,000 messages
const SEARCH_RESULTS = 50;

// --- the timer --------------------------------------------------------------------------------------------------------

export function timerLabel(seconds) {
  if (!seconds) return window.tr("Off");
  if (seconds >= 7 * 86400) return window.tr("1 week");
  return window.tr("1 day");
}

function stateTimer(state) {
  const event = state && state.getStateEvents(DISAPPEAR, "");
  return Number(event && event.getContent().seconds) || 0;
}

// the chat's timer now
export function timerNow(room) {
  return stateTimer(room.getLiveTimeline().getState(Direction.Forward));
}

// The timer a message was sent under: the last change to it before the message in what is loaded, else the timer as the
// loaded history starts. A message sent before a change keeps the timer it was sent under (the site noted it then).
export function timerAt(room, event) {
  const ts = event.getTs();
  let found = null;
  for (const e of room.getLiveTimeline().getEvents()) {
    if (e.getType() === DISAPPEAR && e.getStateKey() === "" && e.getTs() <= ts) found = Number(e.getContent().seconds) || 0;
  }
  return found !== null ? found : stateTimer(room.getLiveTimeline().getState(Direction.Backward));
}

// when a message goes (ms), or 0 when it doesn't
export function expiresAt(room, event) {
  const seconds = timerAt(room, event);
  return seconds ? event.getTs() + seconds * 1000 : 0;
}

// a change of the timer, drawn as a line in the chat: who, and what it is now
export function timerChangeText(event, nameOf) {
  const seconds = Number(event.getContent().seconds) || 0;
  const by = nameOf(event.getContent().by || event.getSender());
  if (!seconds) return by ? window.tr("%(name)s turned off disappearing messages.", { name: by }) : window.tr("Disappearing messages are off.");
  return by
    ? window.tr("%(name)s set messages to disappear after %(time)s.", { name: by, time: timerLabel(seconds) })
    : window.tr("Messages disappear after %(time)s.", { time: timerLabel(seconds) });
}

export function chooseTimer(panel, current, mayChange, onPick) {
  panel.body.textContent = "";
  const intro = document.createElement("p");
  intro.className = "small";
  intro.textContent = window.tr("New messages in this chat disappear for everyone after the time you choose. Messages sent before keep the time they were sent with.");
  panel.body.appendChild(intro);
  const note = document.createElement("p");
  note.className = "small text-muted";
  note.textContent = window.tr("Anyone in the chat can still copy a message or take a picture of the screen before it goes.");
  panel.body.appendChild(note);
  for (const seconds of TIMER_CHOICES) {
    const button = document.createElement("button");
    button.type = "button";
    button.className = `btn btn-sm mr-2 mb-2 ${seconds === current ? "btn-primary" : "btn-secondary"}`;
    button.textContent = timerLabel(seconds);
    button.setAttribute("aria-pressed", seconds === current ? "true" : "false");
    button.disabled = !mayChange;
    button.addEventListener("click", () => onPick(seconds));
    panel.body.appendChild(button);
  }
  if (!mayChange) {
    const why = document.createElement("p");
    why.className = "small text-muted mt-2";
    why.textContent = window.tr("Only a group's owner and admins can change this.");
    panel.body.appendChild(why);
  }
}

// --- pins -------------------------------------------------------------------------------------------------------------

export function pinnedIds(room) {
  const event = room.getLiveTimeline().getState(Direction.Forward).getStateEvents(PINNED, "");
  const list = event && event.getContent().pinned;
  return Array.isArray(list) ? list.filter((id) => typeof id === "string") : [];
}

// a message by id, decrypted: from what is loaded, else asked of the server (null when it is gone or unreadable)
export async function eventById(client, room, eventId) {
  const loaded = room.findEventById(eventId);
  if (loaded) return loaded;
  try {
    const raw = await client.fetchRoomEvent(room.roomId, eventId);
    const event = client.getEventMapper()(raw);
    await client.decryptEventIfNeeded(event);
    return event;
  } catch (e) {
    return null;
  }
}

export async function renderPins(panel, { client, room, mayChange, textOf, onUnpin, onJump }) {
  const ids = pinnedIds(room);
  panel.body.textContent = "";
  if (!ids.length) {
    const none = document.createElement("p");
    none.className = "small text-muted";
    none.textContent = window.tr("No pinned messages yet.");
    panel.body.appendChild(none);
    return;
  }
  for (const id of ids) {
    const event = await eventById(client, room, id);
    const row = document.createElement("div");
    row.className = "border-bottom py-2";
    const text = document.createElement("div");
    text.className = "small text-break";
    text.textContent = !event || event.isRedacted() ? window.tr("This message was deleted.") : textOf(event);
    row.appendChild(text);
    if (event && !event.isRedacted()) {
      const jump = document.createElement("button");
      jump.type = "button";
      jump.className = "btn btn-link btn-sm p-0 mr-3";
      jump.textContent = window.tr("Show in the chat");
      jump.addEventListener("click", () => onJump(id));
      row.appendChild(jump);
    }
    if (mayChange) {
      const unpin = document.createElement("button");
      unpin.type = "button";
      unpin.className = "btn btn-link btn-sm p-0 text-muted";
      unpin.textContent = window.tr("Unpin");
      unpin.addEventListener("click", () => onUnpin(id));
      row.appendChild(unpin);
    }
    panel.body.appendChild(row);
  }
}

// --- search inside a chat ---------------------------------------------------------------------------------------------

// Every word, in any case, in the messages this browser can read: what is loaded first, then older pages, decrypted as
// they come (slow on a very long chat, so it says how far it has read, and stops when the panel closes).
export function renderSearch(panel, { client, room, textOf, isShown, onJump }) {
  const form = document.createElement("form");
  form.className = "d-flex";
  const input = document.createElement("input");
  input.type = "search";
  input.className = "form-control form-control-sm mr-2";
  input.placeholder = window.tr("Search this chat");
  input.setAttribute("aria-label", window.tr("Search this chat"));
  const go = document.createElement("button");
  go.type = "submit";
  go.className = "btn btn-primary btn-sm";
  go.textContent = window.tr("Search");
  form.appendChild(input);
  form.appendChild(go);
  panel.foot.appendChild(form);
  panel.showFoot();
  let run = 0;
  form.addEventListener("submit", async (e) => {
    e.preventDefault();
    const words = input.value.trim().toLowerCase().split(/\s+/).filter(Boolean);
    if (!words.length) return;
    const mine = ++run;
    const status = document.createElement("p");
    status.className = "small text-muted";
    status.setAttribute("role", "status");
    const list = document.createElement("div");
    panel.body.textContent = "";
    panel.body.appendChild(status);
    panel.body.appendChild(list);
    const seen = new Set();
    let found = 0;
    const scan = () => {
      for (const event of [...room.getLiveTimeline().getEvents()].reverse()) {
        if (seen.has(event.getId()) || !isShown(event) || event.isRedacted()) continue;
        seen.add(event.getId());
        const text = textOf(event);
        const low = text.toLowerCase();
        if (!words.every((w) => low.includes(w)) || found >= SEARCH_RESULTS) continue;
        found += 1;
        const row = document.createElement("div");
        row.className = "border-bottom py-2";
        const when = document.createElement("div");
        when.className = "small text-muted";
        when.textContent = new Date(event.getTs()).toLocaleString(window.I18N.intl, { day: "numeric", month: "short", year: "numeric", hour: "numeric", minute: "2-digit" });
        const body = document.createElement("div");
        body.className = "small text-break";
        body.textContent = text;
        const jump = document.createElement("button");
        jump.type = "button";
        jump.className = "btn btn-link btn-sm p-0";
        jump.textContent = window.tr("Show in the chat");
        jump.addEventListener("click", () => onJump(event.getId()));
        row.appendChild(when);
        row.appendChild(body);
        row.appendChild(jump);
        list.appendChild(row);
      }
    };
    for (let page = 0; page <= SEARCH_PAGES; page += 1) {
      if (mine !== run || !panel.body.isConnected || found >= SEARCH_RESULTS) break;
      const events = room.getLiveTimeline().getEvents();
      await Promise.all(events.filter((ev) => ev.isBeingDecrypted() || ev.shouldAttemptDecryption?.())
        .map((ev) => client.decryptEventIfNeeded(ev).catch(() => {})));
      scan();
      status.textContent = window.trn("%(num)s message read…", "%(num)s messages read…", seen.size);
      if (!room.getLiveTimeline().getPaginationToken(Direction.Backward)) break;      // the start of the chat
      try {
        await client.scrollback(room, 50);
      } catch (err) {
        break;
      }
    }
    if (mine !== run) return;
    status.textContent = found
      ? window.trn("%(num)s message found.", "%(num)s messages found.", found)
      : window.tr("Nothing found in the messages this browser can read.");
  });
  input.focus();
}

// --- forwarding -------------------------------------------------------------------------------------------------------

// The chats a message can go to (your Inbox chats and joined groups), filtered as you type; `onPick(target)`.
export function chooseTarget(panel, loadTargets, currentRoomId, onPick) {
  const input = document.createElement("input");
  input.type = "search";
  input.className = "form-control form-control-sm mb-2";
  input.placeholder = window.tr("Search your chats");
  input.setAttribute("aria-label", window.tr("Search your chats"));
  const list = document.createElement("div");
  panel.body.textContent = "";
  panel.body.appendChild(input);
  panel.body.appendChild(list);
  let asked = 0;
  const draw = async () => {
    const mine = ++asked;
    let targets = [];
    try {
      targets = await loadTargets(input.value.trim());
    } catch (e) {
      list.textContent = e.message;
      return;
    }
    if (mine !== asked) return;
    list.textContent = "";
    const shown = targets.filter((t) => t.room_id !== currentRoomId);
    if (!shown.length) {
      const none = document.createElement("p");
      none.className = "small text-muted";
      none.textContent = window.tr("No chats found.");
      list.appendChild(none);
    }
    for (const target of shown) {
      const button = document.createElement("button");
      button.type = "button";
      button.className = "btn btn-link btn-block text-left text-reset py-2 border-bottom";
      button.textContent = target.kind === "group" ? target.label : (target.display_name ? `${target.display_name} @${target.username}` : `@${target.username}`);
      button.addEventListener("click", () => onPick(target));
      list.appendChild(button);
    }
  };
  let timer = null;
  input.addEventListener("input", () => {
    clearTimeout(timer);
    timer = setTimeout(draw, 250);
  });
  draw();
  input.focus();
}

// What a forwarded message says: its words only, marked "Forwarded" and never who wrote it (a quote, an edit's history
// and the original's sender are left behind).
export function forwardedText(content) {
  return { msgtype: "m.text", body: String(content.body || ""), "org.uqqus.forwarded": true };
}

export function isForwarded(event) {
  return !!event.getContent()["org.uqqus.forwarded"];
}
