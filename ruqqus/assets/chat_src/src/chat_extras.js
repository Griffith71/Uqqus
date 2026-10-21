// The chat page's newer parts (templates/chat.html, partials/chat_group_sheets.html; the owner, 2026-10-20):
//   - New chat: a sheet that searches people you follow and chat with (GET /api/chat/people) and starts a 1:1 chat
//     (POST /api/chat/start); a handle typed in full can be written to as well. The server's inbox-or-request rule and
//     block check decide where it lands, as from a profile.
//   - The Calls tab: your recent calls in chats you are in now (GET /api/calls/history), with Call back.
//   - The Channels tab: New channel (POST /api/broadcasts, the rules of routes/broadcasts.py) and "From people you
//     follow", their public channels with Join (GET /api/broadcasts/following).
//   - Search (GET /api/chat/search): your own chats, groups and channels, public channels you are not in, your guilds'
//     chats and guilds with chat. Groups are private: only your own are ever found. Clearing it shows the tab again.
// Every name goes in as text.

import { searchPeople } from "./api.js";

const byId = (id) => document.getElementById(id);
const HANDLE = /^@?[A-Za-z0-9_]{3,25}$/;

function sheet(id, action) {
  if (window.jQuery) window.jQuery(`#${id}`).modal(action);
}

function say(el, text, bad) {
  if (!el) return;
  el.textContent = text || "";
  el.classList.toggle("text-danger", !!bad);
}

function debounce(fn, ms) {
  let timer = null;
  return (...args) => {
    clearTimeout(timer);
    timer = setTimeout(() => fn(...args), ms);
  };
}

async function post(url, formkey, fields = {}) {
  const body = new URLSearchParams({ formkey, ...fields });
  const resp = await fetch(url, { method: "POST", credentials: "same-origin", headers: { Accept: "application/json" }, body });
  let data = null;
  try { data = await resp.json(); } catch (e) { data = null; }
  if (!resp.ok) throw new Error((data && data.error) || "That didn't work. Try again.");
  return data;
}

function avatar(url, size = 36) {
  const img = document.createElement("img");
  img.alt = "";
  img.width = size;
  img.height = size;
  img.className = "rounded-circle flex-shrink-0";
  img.loading = "lazy";
  // a picture on this site or over https (an address the server gave: a profile picture)
  let ok = false;
  try {
    const parsed = new URL(String(url || ""), window.location.origin);
    ok = !!url && (parsed.origin === window.location.origin || parsed.protocol === "https:");
  } catch (e) {
    ok = false;
  }
  if (ok) img.src = url;
  return img;
}

function row({ picture, icon, title, sub, action }) {
  const el = document.createElement("div");
  el.className = "chat-extra-row d-flex align-items-center px-3 py-2 border-bottom";
  if (icon) {
    const box = document.createElement("span");
    box.className = "chat-extra-icon rounded-circle flex-shrink-0 d-inline-flex align-items-center justify-content-center";
    box.setAttribute("aria-hidden", "true");
    const i = document.createElement("i");
    i.className = `fas ${icon}`;
    box.appendChild(i);
    el.appendChild(box);
  } else {
    el.appendChild(avatar(picture));
  }
  const text = document.createElement("div");
  text.className = "flex-grow-1 min-w-0 ml-2";
  const strong = document.createElement("div");
  strong.className = "font-weight-bold text-truncate";
  strong.textContent = title;
  text.appendChild(strong);
  if (sub) {
    const small = document.createElement("div");
    small.className = "text-small text-muted text-truncate";
    small.textContent = sub;
    text.appendChild(small);
  }
  el.appendChild(text);
  if (action) el.appendChild(action);
  return el;
}

function button(label, className, onClick, ariaLabel) {
  const b = document.createElement("button");
  b.type = "button";
  b.className = `btn btn-sm ${className} ml-2 flex-shrink-0`;
  b.textContent = label;
  if (ariaLabel) b.setAttribute("aria-label", ariaLabel);
  b.addEventListener("click", onClick);
  return b;
}

function when(utc) {
  const d = new Date(utc * 1000);
  const today = new Date();
  if (d.toDateString() === today.toDateString()) return d.toLocaleTimeString([], { hour: "numeric", minute: "2-digit" });
  return d.toLocaleDateString([], { day: "numeric", month: "short" });
}

function length(seconds) {
  if (seconds == null) return "no answer or still going";
  if (seconds < 60) return `${seconds} s`;
  const minutes = Math.round(seconds / 60);
  return minutes < 60 ? `${minutes} min` : `${Math.floor(minutes / 60)} h ${minutes % 60} min`;
}

function emptyNote(container, text) {
  const note = document.createElement("div");
  note.className = "text-muted text-center p-4";
  note.textContent = text;
  container.appendChild(note);
}

export function initExtras({ boot, openRoom, openKey, startCall, refreshLists, showTab }) {
  // --- New chat -------------------------------------------------------------------------------------------------
  const search = byId("chat-new-search");
  const results = byId("chat-new-results");
  const status = byId("chat-new-status");
  let token = 0;

  async function begin(username) {
    say(status, "Starting the chat…");
    try {
      const resp = await fetch("/api/chat/start?json=1", {
        method: "POST", credentials: "same-origin", headers: { Accept: "application/json" },
        body: new URLSearchParams({ formkey: boot.formkey, username }),
      });
      const data = await resp.json().catch(() => ({}));
      if (!resp.ok || !data.room_id) throw new Error(data.error || "You can't start a chat with them.");
      sheet("chatNew", "hide");
      await openRoom(data.room_id);
    } catch (e) {
      say(status, e.message, true);
    }
  }

  function draw(people, typed) {
    results.textContent = "";
    for (const person of people) {
      results.appendChild(row({
        picture: person.profile_url,
        title: person.shown_name || person.display_name || `@${person.username}`,
        sub: `@${person.username}`,
        action: button("Chat", "btn-primary", () => begin(person.username), `Chat with @${person.username}`),
      }));
    }
    const handle = typed.replace(/^@/, "");
    if (HANDLE.test(typed) && !people.some((p) => p.username.toLowerCase() === handle.toLowerCase())) {
      results.appendChild(row({
        icon: "fa-at", title: `@${handle}`, sub: "Someone you don't follow gets a message request",
        action: button("Chat", "btn-outline-primary", () => begin(handle), `Chat with @${handle}`),
      }));
    }
    if (!results.children.length) emptyNote(results, typed ? "Nobody matches. Type their whole @handle." : "Follow someone, or type their @handle.");
  }

  function load() {
    const mine = ++token;
    const typed = search.value.trim();
    searchPeople(typed)
      .then((people) => { if (mine === token) draw(people, typed); })
      .catch(() => { if (mine === token) say(status, "Could not search. Try again.", true); });
  }

  if (search) {
    search.addEventListener("input", debounce(load, 250));
    byId("chat-new-chat").addEventListener("click", () => {
      search.value = "";
      say(status, "");
      load();
      sheet("chatNew", "show");
    });
  }

  // --- the Calls tab ----------------------------------------------------------------------------------------------
  async function renderCalls(container) {
    container.textContent = "";
    emptyNote(container, "Loading…");
    let calls = [];
    try {
      const resp = await fetch("/api/calls/history", { credentials: "same-origin" });
      calls = resp.ok ? (await resp.json()).calls || [] : [];
    } catch (e) {
      calls = [];
    }
    container.textContent = "";
    if (!calls.length) return emptyNote(container, "No calls yet. Start one from a chat's Voice call or Video call.");
    for (const call of calls) {
      const video = call.call_kind === "video";
      const sub = `${call.outgoing ? "You called" : "Called you"} · ${video ? "video" : "voice"} · ${when(call.started_utc)} · ${length(call.seconds)}`;
      container.appendChild(row({
        picture: call.avatar, icon: call.kind === "group" ? "fa-user-group" : null,
        title: call.label, sub,
        action: button(video ? "Video call" : "Call", "btn-outline-primary", () => startCall(call.room_id, call.call_kind),
          `${video ? "Video call" : "Call"} ${call.label}`),
      }));
    }
  }

  // --- the Channels tab --------------------------------------------------------------------------------------------
  const channelName = byId("chat-channel-name");
  const channelStatus = byId("chat-channel-status");

  async function joinChannel(channel, joinButton) {
    joinButton.disabled = true;
    try {
      await post(`/api/broadcasts/${channel.channel_id}/join`, boot.formkey);
      await refreshLists();
      await openRoom(channel.room_id);
    } catch (e) {
      joinButton.disabled = false;
      joinButton.textContent = "Try again";
      joinButton.title = e.message;
    }
  }

  async function decorateChannels(container) {
    const bar = document.createElement("div");
    bar.className = "d-flex align-items-center justify-content-between px-3 py-2 border-bottom";
    const label = document.createElement("span");
    label.className = "text-small text-muted";
    label.textContent = "Channels you are in";
    bar.appendChild(label);
    bar.appendChild(button("New channel", "btn-primary", () => {
      channelName.value = "";
      say(channelStatus, "");
      sheet("chatChannelNew", "show");
    }));
    container.prepend(bar);
    let channels = [];
    try {
      const resp = await fetch("/api/broadcasts/following", { credentials: "same-origin" });
      channels = resp.ok ? (await resp.json()).channels || [] : [];
    } catch (e) {
      channels = [];
    }
    if (!channels.length || !container.isConnected) return;
    const heading = document.createElement("div");
    heading.className = "text-small text-muted font-weight-bold px-3 pt-3 pb-1";
    heading.textContent = "From people you follow";
    container.appendChild(heading);
    for (const channel of channels) {
      const owner = channel.owner || {};
      const join = button("Join", "btn-outline-primary", () => joinChannel(channel, join), `Join ${channel.label}`);
      container.appendChild(row({
        picture: owner.profile_url, title: channel.label,
        sub: `${owner.shown_name || owner.username || ""} · ${channel.size} ${channel.size === 1 ? "member" : "members"}`,
        action: join,
      }));
    }
  }

  const create = byId("chat-channel-create");
  if (create) {
    create.addEventListener("click", async () => {
      create.disabled = true;
      say(channelStatus, "Making the channel…");
      try {
        // public, or for the Friends circle (everyone you follow who follows you back); other circles are picked on
        // your channels page (/@you/channels)
        const audience = document.querySelector('input[name="chat-channel-audience"]:checked');
        const friends = audience && audience.value === "friends";
        const fields = { name: channelName.value.trim(), audience: friends ? "4" : "0" };
        if (friends) fields.circles = "friends";
        const made = await post("/api/broadcasts", boot.formkey, fields);
        sheet("chatChannelNew", "hide");
        await refreshLists();
        await openRoom(made.room_id);
      } catch (e) {
        say(channelStatus, e.message, true);
      } finally {
        create.disabled = false;
      }
    });
  }

  // --- search ---------------------------------------------------------------------------------------------------
  const searchBox = byId("chat-search");
  const list = byId("chat-conversation-list");
  let searchToken = 0;

  function section(title) {
    const heading = document.createElement("div");
    heading.className = "text-small text-muted font-weight-bold px-3 pt-3 pb-1";
    heading.textContent = title;
    list.appendChild(heading);
  }

  function drawFound(found) {
    list.textContent = "";
    const empty = !found.chats.length && !found.channels.length && !found.guild_chats.length && !found.guilds.length;
    if (empty) return emptyNote(list, "Nothing matches. Groups are private: only the ones you are in are found.");
    if (found.chats.length) {
      section("Your chats");
      for (const chat of found.chats) {
        list.appendChild(row({
          picture: chat.avatar, icon: chat.kind === "group" ? "fa-user-group" : chat.kind === "channel" ? "fa-tower-broadcast" : null,
          title: chat.label, sub: chat.sub,
          action: button("Open", "btn-outline-primary", () => { clear(); openKey(chat.key, chat.room_id); }, `Open ${chat.label}`),
        }));
      }
    }
    if (found.channels.length) {
      section("Channels");
      for (const channel of found.channels) {
        const owner = channel.owner || {};
        const join = button("Join", "btn-outline-primary", async () => { clear(); await joinChannel(channel, join); }, `Join ${channel.label}`);
        list.appendChild(row({
          picture: owner.profile_url, title: channel.label,
          sub: `${owner.shown_name || owner.username || ""} · ${channel.size} ${channel.size === 1 ? "member" : "members"}`,
          action: join,
        }));
      }
    }
    if (found.guild_chats.length) {
      section("Your guilds' chats");
      for (const chat of found.guild_chats) {
        list.appendChild(row({
          icon: chat.channel_kind === "voice" ? "fa-volume-high" : "fa-hashtag", title: chat.label, sub: `+${chat.guild}`,
          action: button("Open", "btn-outline-primary", () => { clear(); openKey(chat.key, chat.room_id); }, `Open ${chat.label}`),
        }));
      }
    }
    if (found.guilds.length) {
      section("Guilds with chat");
      for (const guild of found.guilds) {
        const link = document.createElement("a");
        link.className = "btn btn-sm btn-outline-primary ml-2 flex-shrink-0";
        link.href = guild.url.startsWith("/+") ? guild.url : "/";
        link.target = "_top";
        link.textContent = "Open";
        link.setAttribute("aria-label", `Open the chat of +${guild.name}`);
        list.appendChild(row({
          picture: guild.avatar, title: `+${guild.name}`,
          sub: `${guild.channels} ${guild.channels === 1 ? "channel" : "channels"}`, action: link,
        }));
      }
    }
  }

  function clear() {
    if (!searchBox) return;
    searchBox.value = "";
    searchToken++;
    showTab();
  }

  async function runSearch() {
    const q = searchBox.value.trim();
    const mine = ++searchToken;
    if (q.replace(/^[@+]/, "").length < 2) {
      if (!q) showTab();
      return;
    }
    try {
      const resp = await fetch(`/api/chat/search?q=${encodeURIComponent(q)}`, { credentials: "same-origin" });
      const found = resp.ok ? await resp.json() : null;
      if (mine !== searchToken || !found) return;
      drawFound(found);
    } catch (e) {
      if (mine === searchToken) emptyNote(list, "Could not search. Try again.");
    }
  }

  if (searchBox) {
    searchBox.addEventListener("input", debounce(runSearch, 300));
    searchBox.addEventListener("keydown", (event) => { if (event.key === "Escape") clear(); });
  }

  // a tab chosen while searching shows that tab
  for (const link of document.querySelectorAll("[data-chat-tab]")) {
    link.addEventListener("click", () => { if (searchBox && searchBox.value) { searchBox.value = ""; searchToken++; } });
  }

  function searching() {
    return !!(searchBox && searchBox.value.trim().replace(/^[@+]/, "").length >= 2);
  }

  return { renderCalls, decorateChannels, searching };
}
