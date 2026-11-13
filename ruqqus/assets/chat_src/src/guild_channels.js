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
          // Translators: in place of the name of someone who wrote in a channel and has left it
          known.set(id, senders[String(id)] || { shown_name: window.tr("Someone who left"), username: null, blocked: false, muted: false });
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
  if (channel.encrypted && channel.may_write) return window.tr("End-to-end encrypted: only the people in this channel can read it.");
  if (!channel.may_write) {
    return channel.channel_kind === "announcement"
      ? window.tr("Only the guild's guildmasters write here. You can react to messages.")
      : window.tr("You can read this channel but not write in it.");
  }
  const seconds = Number(channel.slow_mode_seconds) || 0;
  if (!seconds) return "";
  return seconds >= 120
    ? window.trn("Slow mode: one message every %(num)s minute.", "Slow mode: one message every %(num)s minutes.", Math.round(seconds / 60))
    : window.trn("Slow mode: one message every %(num)s second.", "Slow mode: one message every %(num)s seconds.", seconds);
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
    ? d.toLocaleTimeString(window.I18N.intl, { hour: "numeric", minute: "2-digit" })
    : d.toLocaleDateString(window.I18N.intl, { month: "short", day: "numeric", year: "numeric" });
}

// A message as a row of a list (pinned, found): who wrote it, when, and the words
function messageRow(who, written, bodyText, note) {
  const row = el("div", "border-bottom py-2 chat-panel-row");
  const head = el("div", "small text-muted");
  const name = who && who.shown_name ? who.shown_name : window.tr("Someone who left");
  // Translators: above a pinned or found message: who wrote it and when ("Ann · 14:05")
  head.textContent = window.tr("%(name)s · %(when)s", { name, when: whenLabel(written) });
  row.appendChild(head);
  const text = el("div", note ? "font-italic text-muted" : "chat-bubble", note || bodyText);
  row.appendChild(text);
  return row;
}

export function renderPins(body, pins, canModerate, onUnpin) {
  body.textContent = "";
  if (!pins.length) {
    body.appendChild(el("div", "text-muted text-center p-3", window.tr("Nothing is pinned in this channel.")));
    return;
  }
  for (const pin of pins) {
    const note = pin.blocked ? window.tr("A message from someone you blocked") : pin.hidden ? window.tr("Hidden by your word filter") : "";
    const row = messageRow(pin.author, pin.written_utc, pin.body, note);
    if (canModerate) {
      const unpin = el("button", "btn btn-link btn-sm px-0", window.tr("Unpin"));
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
  input.placeholder = window.tr("Search this channel");
  input.setAttribute("aria-label", window.tr("Search this channel"));
  input.maxLength = 100;
  // Translators: a button that searches a channel's messages
  const go = el("button", "btn btn-primary", window.tr("Search"));
  go.type = "submit";
  form.append(input, go);
  form.addEventListener("submit", (e) => {
    e.preventDefault();
    if (input.value.trim()) onSearch(input.value.trim());
  });
  foot.appendChild(form);
  body.appendChild(el("div", "text-muted text-center p-3", window.tr("Type words to look for.")));
  input.focus();
}

export function renderResults(body, data, onJump) {
  body.textContent = "";
  const results = data.results || [];
  if (!results.length) {
    body.appendChild(el("div", "text-muted text-center p-3", window.tr("Nothing found.")));
  }
  for (const found of results) {
    const row = messageRow(found.author, found.written_utc, found.body, "");
    if (onJump && typeof found.event_id === "string") {
      // Translators: a button under a found message: shows it where it is in the channel
      const jump = el("button", "btn btn-link btn-sm p-0 mt-1", window.tr("Show in the channel"));
      jump.type = "button";
      jump.addEventListener("click", () => onJump(found.event_id));
      row.appendChild(jump);
    }
    body.appendChild(row);
  }
  body.appendChild(el("div", "small text-muted text-center py-2", window.trn(
    "Searches the last %(num)s day of messages.", "Searches the last %(num)s days of messages.", Number(data.days) || 90)));
}

// how long to mute a channel for (hours; -1 until turned back on), labelled in the page's language when drawn
function muteChoices() {
  return [
    ...[1, 8, 24].map((hours) => [hours, window.trn("For %(num)s hour", "For %(num)s hours", hours)]),
    [-1, window.tr("Until I turn it back on")],
  ];
}

export function renderMute(body, muted, onChoose) {
  body.textContent = "";
  body.appendChild(el("p", "small text-muted", muted
    ? window.tr("This channel is muted for you: no unread dot and no mention notifications.")
    : window.tr("Muting a channel hides its unread dot and stops its mention notifications. Nobody else is told.")));
  for (const [hours, label] of muteChoices()) {
    const button = el("button", "btn btn-outline-secondary btn-block text-left", label);
    button.type = "button";
    button.addEventListener("click", () => onChoose(hours));
    body.appendChild(button);
  }
  if (muted) {
    const on = el("button", "btn btn-primary btn-block", window.tr("Turn notifications back on"));
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

function cardShell(pane, conv, icon, kindLine) {
  pane.textContent = "";
  const box = el("div", "p-4");
  const head = el("div", "d-flex align-items-center mb-2");
  const mark = el("span", "chat-group-avatar rounded-circle mr-2 flex-shrink-0");
  mark.style.cssText = "width:40px;height:40px";
  const i = el("i", `fas ${icon}`);
  i.setAttribute("aria-hidden", "true");
  mark.appendChild(i);
  const title = el("div", "flex-grow-1 overflow-hidden");
  title.append(el("div", "font-weight-bold text-truncate", conv.label), el("div", "small text-muted", kindLine));
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
  row.appendChild(el("span", "", person.shown_name || window.tr("Someone")));
  return row;
}

export function renderVoiceCard(pane, conv, join) {
  stopCards();
  // Translators: under a voice channel's name: its kind and its guild ("Voice channel · +pets")
  const box = cardShell(pane, conv, "fa-volume-high", window.tr("Voice channel · +%(guild)s", { guild: conv.guild }));
  const status = el("div", "text-muted mb-2", window.tr("Loading…"));
  const people = el("div", "mb-3");
  const button = el("button", "btn btn-primary", window.tr("Join voice"));
  button.type = "button";
  const note = el("div", "small text-muted mt-2", window.trn(
    "Voice goes browser to browser through the site's relay, so nobody sees anyone's address. Up to %(num)s person.",
    "Voice goes browser to browser through the site's relay, so nobody sees anyone's address. Up to %(num)s people.", 8));
  box.append(status, people, button, note);
  let full = false;

  button.addEventListener("click", () => join());
  if (!conv.may_write) {
    button.disabled = true;
    note.textContent = window.tr("You can see who is here, but you can't speak in this channel.");
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
      status.textContent = n ? window.trn("%(num)s person in the call", "%(num)s people in the call", n) : window.tr("Nobody is here yet.");
      if (conv.may_write) button.disabled = full;
      if (full) note.textContent = window.trn("This voice channel is full (%(num)s person).", "This voice channel is full (%(num)s people).", 8);
    } catch (e) {
      /* the next try */
    }
  };
  refresh();
  voiceTimer = setInterval(refresh, 15000);
}

export function renderStageCard(pane, conv) {
  stopCards();
  // Translators: under a stage channel's name: its kind and its guild ("Stage · +pets")
  const box = cardShell(pane, conv, "fa-podcast", window.tr("Stage · +%(guild)s", { guild: conv.guild }));
  const space = conv.space || { status: "none" };
  if (space.status === "none") {
    box.appendChild(el("p", "text-muted", window.tr("No Space is shown on this stage right now.")));
    return;
  }
  // Translators: whether the Space on a stage channel is live, scheduled or over
  const labels = { live: window.tr("Live now"), scheduled: window.tr("Coming up"), ended: window.tr("Ended") };
  const label = labels[space.status] || space.status;
  box.appendChild(el("div", "font-weight-bold", space.title || window.tr("A Space")));
  box.appendChild(el("div", "text-muted mb-3", label));
  if (space.status !== "ended") {
    const link = el("a", "btn btn-primary", space.status === "live" ? window.tr("Listen") : window.tr("Open the Space"));
    if (typeof space.url === "string" && space.url.startsWith("/space/")) link.href = space.url;
    link.target = "_top";
    box.appendChild(link);
  }
}
