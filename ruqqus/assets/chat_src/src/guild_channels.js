// A guild's channels in the chat client (routes/guild_channels.py, helpers/guild_channels.py). A channel is a Matrix room
// whose messages the site sends and signs; the people in it are many, so their names are fetched as they appear
// (only people who wrote in the channel are ever answered), and whom the viewer blocked or muted is marked for their
// own client alone.
import { guildChannelSenders } from "./api.js";

// The site user id in a Matrix id: @<prefix><user id>:<server> (helpers/matrix_client.py mxid_for)
export function userIdOf(mxid) {
  const match = /(\d+):[^:]*$/.exec(mxid || "");
  return match ? Number(match[1]) : null;
}

// Names by Matrix id for one open channel. `get` answers at once (a placeholder until the name has come);
// `onChange` runs when names have arrived, so the thread can be drawn again.
export function createSenders(channelId, onChange) {
  const known = new Map();       // user id -> { shown_name, username, blocked, muted }
  const asked = new Set();
  let timer = null;

  function ask() {
    timer = null;
    const ids = [...asked].filter((id) => !known.has(id)).slice(0, 50);
    if (!ids.length) return;
    guildChannelSenders(channelId, ids)
      .then((senders) => {
        for (const id of ids) {
          known.set(id, senders[String(id)] || { shown_name: "Someone who left", username: null, blocked: false, muted: false });
        }
        onChange();
      })
      .catch(() => { for (const id of ids) asked.delete(id); });      // tried again the next time it is drawn
  }

  return {
    get(mxid) {
      const id = userIdOf(mxid);
      if (id === null) return null;
      const found = known.get(id);
      if (found) return found;
      asked.add(id);
      if (!timer) timer = setTimeout(ask, 80);
      return null;
    },
  };
}

// the line under the composer: slow mode, or why it can't be used
export function composerNote(channel) {
  if (channel.encrypted && channel.may_write) return "End-to-end encrypted: only the people in this channel can read it.";
  if (!channel.may_write) {
    return channel.channel_kind === "announcement"
      ? "Only the guild's guildmasters write here. You can react to messages."
      : "You can read this channel but not write in it.";
  }
  const seconds = Number(channel.slow_mode_seconds) || 0;
  if (!seconds) return "";
  return seconds >= 120 ? `Slow mode: one message every ${Math.round(seconds / 60)} minutes.` : `Slow mode: one message every ${seconds} seconds.`;
}

// --- the panels over a channel's messages: pinned messages, search, notifications -------------------------------------------
// Everything is set with textContent.

function el(tag, className, text) {
  const node = document.createElement(tag);
  if (className) node.className = className;
  if (text != null) node.textContent = text;
  return node;
}

export function whenLabel(utc) {
  if (!utc) return "";
  const d = new Date(utc * 1000);
  return d.toDateString() === new Date().toDateString()
    ? d.toLocaleTimeString([], { hour: "numeric", minute: "2-digit" })
    : d.toLocaleDateString([], { month: "short", day: "numeric", year: "numeric" });
}

// A message as a row of a list (pinned, found): who wrote it, when, and the words
function messageRow(who, written, bodyText, note) {
  const row = el("div", "border-bottom py-2 chat-panel-row");
  const head = el("div", "small text-muted");
  head.textContent = `${who && who.shown_name ? who.shown_name : "Someone who left"} · ${whenLabel(written)}`;
  row.appendChild(head);
  const text = el("div", note ? "font-italic text-muted" : "chat-bubble", note || bodyText);
  row.appendChild(text);
  return row;
}

export function renderPins(body, pins, canModerate, onUnpin) {
  body.textContent = "";
  if (!pins.length) {
    body.appendChild(el("div", "text-muted text-center p-3", "Nothing is pinned in this channel."));
    return;
  }
  for (const pin of pins) {
    const note = pin.blocked ? "A message from someone you blocked" : pin.hidden ? "Hidden by your word filter" : "";
    const row = messageRow(pin.author, pin.written_utc, pin.body, note);
    if (canModerate) {
      const unpin = el("button", "btn btn-link btn-sm px-0", "Unpin");
      unpin.type = "button";
      unpin.addEventListener("click", () => onUnpin(pin));
      row.appendChild(unpin);
    }
    body.appendChild(row);
  }
}

export function renderSearch(body, foot, onSearch) {
  body.textContent = "";
  foot.textContent = "";
  const form = el("form", "d-flex");
  const input = el("input", "form-control mr-2");
  input.type = "search";
  input.placeholder = "Search this channel";
  input.setAttribute("aria-label", "Search this channel");
  input.maxLength = 100;
  const go = el("button", "btn btn-primary", "Search");
  go.type = "submit";
  form.append(input, go);
  form.addEventListener("submit", (e) => {
    e.preventDefault();
    if (input.value.trim()) onSearch(input.value.trim());
  });
  foot.appendChild(form);
  body.appendChild(el("div", "text-muted text-center p-3", "Type words to look for."));
  input.focus();
}

export function renderResults(body, data) {
  body.textContent = "";
  const results = data.results || [];
  if (!results.length) {
    body.appendChild(el("div", "text-muted text-center p-3", "Nothing found."));
  }
  for (const found of results) body.appendChild(messageRow(found.author, found.written_utc, found.body, ""));
  body.appendChild(el("div", "small text-muted text-center py-2", `Searches the last ${Number(data.days) || 90} days of messages.`));
}

const MUTE_CHOICES = [[1, "For 1 hour"], [8, "For 8 hours"], [24, "For 24 hours"], [-1, "Until I turn it back on"]];

export function renderMute(body, muted, onChoose) {
  body.textContent = "";
  body.appendChild(el("p", "small text-muted", muted
    ? "This channel is muted for you: no unread dot and no mention notifications."
    : "Muting a channel hides its unread dot and stops its mention notifications. Nobody else is told."));
  for (const [hours, label] of MUTE_CHOICES) {
    const button = el("button", "btn btn-outline-secondary btn-block text-left", label);
    button.type = "button";
    button.addEventListener("click", () => onChoose(hours));
    body.appendChild(button);
  }
  if (muted) {
    const on = el("button", "btn btn-primary btn-block", "Turn notifications back on");
    on.type = "button";
    on.addEventListener("click", () => onChoose(0));
    body.appendChild(on);
  }
}

// @name in a message: whether it is the viewer's
export function mentionPattern(username) {
  const safe = String(username || "").replace(/[.*+?^${}()|[\]\\]/g, "\\$&");
  return new RegExp("(^|[^\\w@])@" + safe + "(?![\\w@])", "i");
}

// --- voice and stage channels: a card in place of a thread of messages ------------------------------------------------------
// A voice channel is a call room that is always there: the card says who is in it and opens the call window (the same
// one calls use). A stage channel shows the Space its guildmasters chose.

let voiceTimer = null;

export function stopCards() {
  clearInterval(voiceTimer);
  voiceTimer = null;
}

function cardShell(pane, conv, icon, kindLabel) {
  pane.textContent = "";
  const box = el("div", "p-4");
  const head = el("div", "d-flex align-items-center mb-2");
  const mark = el("span", "chat-group-avatar rounded-circle mr-2 flex-shrink-0");
  mark.style.cssText = "width:40px;height:40px";
  const i = el("i", `fas ${icon}`);
  i.setAttribute("aria-hidden", "true");
  mark.appendChild(i);
  const title = el("div", "flex-grow-1 overflow-hidden");
  title.append(el("div", "font-weight-bold text-truncate", conv.label), el("div", "small text-muted", `${kindLabel} · +${conv.guild}`));
  head.append(mark, title);
  box.appendChild(head);
  if (conv.topic) box.appendChild(el("p", "text-muted", conv.topic));
  pane.appendChild(box);
  return box;
}

function personRow(person) {
  const row = el("div", "d-flex align-items-center mb-1");
  if (typeof person.profile_url === "string" && /^(https?:\/\/|\/)/.test(person.profile_url)) {
    const img = el("img", "rounded-circle mr-2");
    img.width = img.height = 28;
    img.alt = "";
    img.src = person.profile_url;
    row.appendChild(img);
  }
  row.appendChild(el("span", "", person.shown_name || "Someone"));
  return row;
}

export function renderVoiceCard(pane, conv, join) {
  stopCards();
  const box = cardShell(pane, conv, "fa-volume-high", "Voice channel");
  const status = el("div", "text-muted mb-2", "Loading…");
  const people = el("div", "mb-3");
  const button = el("button", "btn btn-primary", "Join voice");
  button.type = "button";
  const note = el("div", "small text-muted mt-2", "Voice goes browser to browser through the site's relay, so nobody sees anyone's address. Up to 8 people.");
  box.append(status, people, button, note);
  let full = false;

  button.addEventListener("click", () => join());
  if (!conv.may_write) {
    button.disabled = true;
    note.textContent = "You can see who is here, but you can't speak in this channel.";
  }

  const refresh = async () => {
    try {
      const resp = await fetch(`/api/calls/status?room=${encodeURIComponent(conv.room_id)}`, { credentials: "same-origin" });
      if (!resp.ok) return;
      const data = await resp.json();
      people.textContent = "";
      for (const person of data.who || []) people.appendChild(personRow(person));
      const n = Number(data.people) || 0;
      full = n >= 8 && !data.in_call;
      status.textContent = n ? `${n} ${n === 1 ? "person" : "people"} in the call` : "Nobody is here yet.";
      if (conv.may_write) button.disabled = full;
      if (full) note.textContent = "This voice channel is full (8 people).";
    } catch (e) {
      /* the next try */
    }
  };
  refresh();
  voiceTimer = setInterval(refresh, 15000);
}

export function renderStageCard(pane, conv) {
  stopCards();
  const box = cardShell(pane, conv, "fa-podcast", "Stage");
  const space = conv.space || { status: "none" };
  if (space.status === "none") {
    box.appendChild(el("p", "text-muted", "No Space is shown on this stage right now."));
    return;
  }
  const label = { live: "Live now", scheduled: "Coming up", ended: "Ended" }[space.status] || space.status;
  box.appendChild(el("div", "font-weight-bold", space.title || "A Space"));
  box.appendChild(el("div", "text-muted mb-3", label));
  if (space.status !== "ended") {
    const link = el("a", "btn btn-primary", space.status === "live" ? "Listen" : "Open the Space");
    if (typeof space.url === "string" && space.url.startsWith("/space/")) link.href = space.url;
    link.target = "_top";
    box.appendChild(link);
  }
}
