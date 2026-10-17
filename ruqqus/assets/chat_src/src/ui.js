// DOM rendering for the Chat page. Plain hand-written DOM manipulation,
// consistent with the rest of this site's vanilla-JS convention (no
// framework) - reuses the site's existing Bootstrap utility classes rather
// than introducing new visual language.
//
// Message text only ever goes in through textContent, never innerHTML.

import { composerNote } from "./guild_channels.js";

function escapeHtml(s) {
  const div = document.createElement("div");
  div.textContent = s == null ? "" : String(s);
  return div.innerHTML;
}

function timeLabel(ms) {
  if (!ms) return "";
  const d = new Date(ms);
  const now = new Date();
  if (d.toDateString() === now.toDateString()) {
    return d.toLocaleTimeString([], { hour: "numeric", minute: "2-digit" });
  }
  return d.toLocaleDateString([], { month: "short", day: "numeric" });
}

// A message that holds a link to one of this site's posts gets a small card under it (made for the
// reader by /api/chat/post_preview: their own visibility rules, and never the author). The links are
// recognised by the page's own host name (a copied link always says https), so a link to another site is
// just text.
const POST_LINK = new RegExp(
  "https?://" + window.location.host.replace(/[.*+?^${}()|[\]\\]/g, "\\$&") + "(?:/\\+\\w+)?/post/([0-9a-z]{1,10})(?:/[^\\s]*)?"
);
const previews = new Map();      // post id -> what the server said about it
const asking = new Set();        // post ids being asked about

function fillPostCard(card, data) {
  card.textContent = "";
  if (!data || !data.ok) {
    const notice = document.createElement("div");
    notice.className = "small text-muted p-2";
    notice.textContent = data && data.notice ? data.notice : "This post can't be shown.";
    card.appendChild(notice);
    return;
  }
  const link = document.createElement("a");
  link.className = "d-flex align-items-center text-decoration-none text-body";
  link.href = typeof data.url === "string" && data.url.startsWith("/") ? data.url : `/post/${card.dataset.postId}`;
  if (typeof data.thumb === "string" && data.thumb.startsWith("https://")) {
    const img = document.createElement("img");
    img.src = data.thumb;
    img.alt = "";
    img.width = img.height = 56;
    img.className = "chat-post-thumb";
    link.appendChild(img);
  }
  const text = document.createElement("div");
  text.className = "p-2 overflow-hidden";
  const title = document.createElement("div");
  title.className = "font-weight-bold chat-post-title";
  title.textContent = data.title || "(untitled post)";
  const meta = document.createElement("div");
  meta.className = "small text-muted text-truncate";
  const count = Number(data.comments) || 0;
  meta.textContent = `${data.guild ? "+" + data.guild + " · " : ""}${count} comment${count === 1 ? "" : "s"}`;
  text.appendChild(title);
  text.appendChild(meta);
  link.appendChild(text);
  card.appendChild(link);
}

function postCard(id) {
  const card = document.createElement("div");
  card.className = "chat-post-card mt-1";
  card.dataset.postId = id;
  if (previews.has(id)) {
    fillPostCard(card, previews.get(id));
    return card;
  }
  card.innerHTML = '<div class="small text-muted p-2">Loading post…</div>';
  if (!asking.has(id)) {
    asking.add(id);
    fetch(`/api/chat/post_preview/${id}`, { credentials: "same-origin" })
      .then((r) => (r.ok ? r.json() : { ok: false, notice: "This post can't be shown." }))
      .catch(() => ({ ok: false, notice: "This post can't be shown." }))
      .then((data) => {
        previews.set(id, data);
        asking.delete(id);
        document.querySelectorAll(`.chat-post-card[data-post-id="${id}"]`).forEach((el) => {
          // the card is taller than "Loading…": someone reading the newest message stays at the bottom
          const box = el.closest("#chat-messages");
          const atBottom = box && box.scrollHeight - box.scrollTop - box.clientHeight < 80;
          fillPostCard(el, data);
          if (atBottom) box.scrollTop = box.scrollHeight;
        });
      });
  }
  return card;
}

// the emoji offered under "React"
export const REACTION_CHOICES = ["👍", "❤️", "😂", "😮", "😢", "🙏"];

export function renderConversationList(container, conversations, activeConversationId, onSelect) {
  container.innerHTML = "";

  if (!conversations.length) {
    const empty = document.createElement("div");
    empty.className = "text-muted text-center p-4";
    empty.textContent = "Nothing here yet.";
    container.appendChild(empty);
    return;
  }

  for (const convo of conversations) {
    const row = document.createElement("a");
    row.href = "#";
    row.className = "d-flex align-items-center px-3 py-2 border-bottom text-decoration-none text-body chat-list-row";
    if (convo.key === activeConversationId) row.classList.add("bg-light");

    const unreadDot = convo.unread_count > 0
      ? '<span class="badge badge-danger rounded-circle align-top ml-1">&nbsp;</span>'
      : "";

    if (convo.kind === "guild_channel") {
      // a guild's channel (routes/guild_channels.py): its name, and the guild it is in
      const icon = { announcement: "fa-bullhorn", voice: "fa-volume-high", stage: "fa-podcast" }[convo.channel_kind] || "fa-hashtag";
      row.innerHTML = `
        <span class="chat-group-avatar rounded-circle mr-2 flex-shrink-0" style="width:40px;height:40px" aria-hidden="true"><i class="fas ${icon}"></i></span>
        <div class="flex-grow-1 overflow-hidden">
          <div class="font-weight-bold text-truncate">${escapeHtml(convo.label)}${unreadDot}</div>
          <div class="small text-muted text-truncate">${convo.encrypted ? '<i class="fas fa-lock" aria-hidden="true"></i> ' : ""}+${escapeHtml(convo.guild)}${convo.category ? " · " + escapeHtml(convo.category) : ""}${convo.channel_kind === "announcement" ? " · Announcements" : ""}</div>
        </div>
        ${convo.voice_people ? `<span class="badge badge-secondary badge-pill ml-1" aria-label="${Number(convo.voice_people)} in the call">${Number(convo.voice_people)}</span>` : ""}
        ${convo.mentions ? `<span class="badge badge-danger badge-pill ml-1" aria-label="${Number(convo.mentions)} unread mentions">${Number(convo.mentions)}</span>` : ""}
        ${convo.muted ? '<i class="fas fa-bell-slash text-muted ml-1" title="Muted" aria-hidden="true"></i>' : ""}
      `;
    } else if (convo.kind === "channel") {
      // a broadcast channel (routes/broadcasts.py): its name and how many are in it
      row.innerHTML = `
        <span class="chat-group-avatar rounded-circle mr-2 flex-shrink-0" style="width:40px;height:40px" aria-hidden="true"><i class="fas fa-tower-broadcast"></i></span>
        <div class="flex-grow-1 overflow-hidden">
          <div class="font-weight-bold text-truncate">${escapeHtml(convo.label)}${unreadDot}</div>
          <div class="small text-muted text-truncate">Channel · ${Number(convo.size) || 0} member${Number(convo.size) === 1 ? "" : "s"}</div>
        </div>
      `;
    } else if (convo.kind === "group") {
      // a group: its name (or the first members' names), how many people, and who invited you to it
      const invited = convo.invited_by ? `Invited by @${escapeHtml(convo.invited_by.username)}` : `${Number(convo.size) || 0} people`;
      row.innerHTML = `
        ${groupAvatar(40)}
        <div class="flex-grow-1 overflow-hidden">
          <div class="font-weight-bold text-truncate">${escapeHtml(convo.label)}${unreadDot}</div>
          <div class="small text-muted text-truncate">${invited}</div>
        </div>
        <div class="text-muted small ml-2">${timeLabel(convo.last_activity_utc * 1000)}</div>
      `;
    } else {
      row.innerHTML = `
        <img src="${escapeHtml(convo.other_user.profile_url)}" class="rounded-circle mr-2" width="40" height="40">
        <div class="flex-grow-1 overflow-hidden">
          <div class="font-weight-bold text-truncate">@${escapeHtml(convo.other_user.username)}${unreadDot}</div>
        </div>
        <div class="text-muted small ml-2">${timeLabel(convo.last_activity_utc * 1000)}</div>
      `;
    }
    row.addEventListener("click", (e) => {
      e.preventDefault();
      onSelect(convo);
    });
    container.appendChild(row);
  }
}

function groupAvatar(size) {
  return `<span class="chat-group-avatar rounded-circle mr-2 flex-shrink-0" style="width:${size}px;height:${size}px" aria-hidden="true"><i class="fas fa-users"></i></span>`;
}

function peopleLabel(n) {
  const count = Number(n) || 0;
  return `${count} ${count === 1 ? "person" : "people"}`;
}

// callbacks: onAccept, onDecline, onSend(text), onTyping(isTyping), onCancelContext(), onInfo() (a group's sheet),
// onCall(kind) (a voice or video call: routes/calls.py). A 1:1 chat passes otherUser; a group passes group
// ({ label, size }) and requestText for an invitation.
export function renderThreadShell(pane, { otherUser, group, channel, guildChannel, onSearch, onPins, onMute, onAcceptRules, requestText, isRequest, onAccept, onDecline, onSend, onTyping, onCancelContext, onInfo, onCall, onLeave }) {
  const callButtons = !isRequest && onCall ? `
      <button type="button" class="btn btn-link btn-sm text-muted" data-call="audio" title="Voice call" aria-label="Voice call"><i class="fas fa-phone"></i></button>
      <button type="button" class="btn btn-link btn-sm text-muted" data-call="video" title="Video call" aria-label="Video call"><i class="fas fa-video"></i></button>` : "";
  const header = guildChannel ? `
      <span class="chat-gc-id chat-group-avatar rounded-circle mr-2 flex-shrink-0" style="width:36px;height:36px" aria-hidden="true"><i class="fas ${guildChannel.channel_kind === "announcement" ? "fa-bullhorn" : "fa-hashtag"}"></i></span>
      <div class="chat-gc-id flex-grow-1 overflow-hidden">
        <div class="font-weight-bold text-truncate" id="chat-thread-title">${escapeHtml(guildChannel.label)}</div>
        <div class="small text-muted text-truncate">+${escapeHtml(guildChannel.guild)}${guildChannel.topic ? " · " + escapeHtml(guildChannel.topic) : ""}</div>
      </div>
      ${guildChannel.encrypted ? '<span class="text-muted px-1" title="End-to-end encrypted" aria-label="End-to-end encrypted"><i class="fas fa-lock" aria-hidden="true"></i></span>' : `<button type="button" class="btn btn-link btn-sm text-muted" id="chat-gc-search" title="Search this channel" aria-label="Search this channel"><i class="fas fa-magnifying-glass" aria-hidden="true"></i></button>
      <button type="button" class="btn btn-link btn-sm text-muted" id="chat-gc-pins" title="Pinned messages" aria-label="Pinned messages"><i class="fas fa-thumbtack" aria-hidden="true"></i></button>`}
      <button type="button" class="btn btn-link btn-sm text-muted" id="chat-gc-mute" title="Notifications" aria-label="Notifications"><i class="fas ${guildChannel.muted ? "fa-bell-slash" : "fa-bell"}" aria-hidden="true"></i></button>` : channel ? `
      <span class="chat-group-avatar rounded-circle mr-2 flex-shrink-0" style="width:36px;height:36px" aria-hidden="true"><i class="fas fa-tower-broadcast"></i></span>
      <div class="flex-grow-1 overflow-hidden">
        <div class="font-weight-bold text-truncate" id="chat-thread-title">${escapeHtml(channel.label)}</div>
        <div class="small text-muted">${channel.public ? "Public channel" : "Channel for circles · end-to-end encrypted"} · ${Number(channel.size) || 0} member${Number(channel.size) === 1 ? "" : "s"}</div>
      </div>
      ${channel.role === "owner" ? "" : `<button type="button" class="btn btn-link btn-sm text-muted" id="chat-channel-leave">Leave</button>`}` : group ? `
      ${groupAvatar(36)}
      <div class="flex-grow-1 overflow-hidden">
        <div class="font-weight-bold text-truncate" id="chat-thread-title">${escapeHtml(group.label)}</div>
        <div class="small text-muted" id="chat-thread-subtitle">${peopleLabel(group.size)}</div>
      </div>${callButtons}
      <button type="button" class="btn btn-link btn-sm text-muted" id="chat-group-info-btn" title="Group info" aria-label="Group info"><i class="fas fa-users"></i></button>` : `
      <img src="${escapeHtml(otherUser.profile_url)}" class="rounded-circle mr-2" width="36" height="36">
      <div class="font-weight-bold flex-grow-1">@${escapeHtml(otherUser.username)}</div>${callButtons}`;
  const notice = requestText || "Message request. They won't know you've seen this until you accept.";
  pane.innerHTML = `
    <div class="d-flex align-items-center border-bottom px-3 py-2" id="chat-thread-header">${header}
    </div>
    <div class="chat-call-bar d-none" id="chat-call-bar" role="status">
      <i class="fas fa-phone text-primary" aria-hidden="true"></i>
      <span class="flex-grow-1" id="chat-call-bar-text"></span>
      <button type="button" class="btn btn-primary btn-sm" id="chat-call-join">Join</button>
    </div>
    ${isRequest ? `
    <div class="d-flex align-items-center justify-content-between px-3 py-2 bg-light border-bottom">
      <div class="text-muted small" id="chat-request-text"></div>
      <div>
        <button type="button" class="btn btn-sm btn-secondary mr-2" id="chat-decline-btn">Delete</button>
        <button type="button" class="btn btn-sm btn-primary" id="chat-accept-btn">Accept</button>
      </div>
    </div>` : ""}
    <div class="flex-grow-1 overflow-auto px-3 py-2" id="chat-messages"></div>
    <div class="chat-panel d-none" id="chat-panel" role="dialog" aria-modal="false">
      <div class="d-flex align-items-center border-bottom px-3 py-2">
        <div class="font-weight-bold flex-grow-1 text-truncate" id="chat-panel-title"></div>
        <button type="button" class="btn btn-link btn-sm text-muted" id="chat-panel-close" aria-label="Close">&times;</button>
      </div>
      <div class="flex-grow-1 overflow-auto px-3 py-2" id="chat-panel-body"></div>
      <div class="border-top p-2 d-none" id="chat-panel-foot"></div>
    </div>
    <div id="chat-typing" class="px-3 small text-muted chat-typing" aria-live="polite"></div>
    <div id="chat-context" class="d-none align-items-center border-top px-3 py-1 small bg-light">
      <div class="flex-grow-1 text-truncate" id="chat-context-text"></div>
      <button type="button" class="btn btn-link btn-sm text-muted" id="chat-context-cancel" aria-label="Cancel">&times;</button>
    </div>
    <div class="border-top p-2">
      <form id="chat-composer" class="d-flex">
        <textarea class="form-control mr-2 chat-composer-input" id="chat-composer-input" rows="1" placeholder="Start a message..." aria-label="Message" autocomplete="off"></textarea>
        <button type="submit" class="btn btn-primary align-self-end">Send</button>
      </form>
    </div>
  `;

  if (isRequest) {
    pane.querySelector("#chat-request-text").textContent = notice;
    pane.querySelector("#chat-accept-btn").addEventListener("click", onAccept);
    pane.querySelector("#chat-decline-btn").addEventListener("click", onDecline);
  }
  if (group && onInfo) pane.querySelector("#chat-group-info-btn").addEventListener("click", onInfo);
  const leave = pane.querySelector("#chat-channel-leave");
  if (leave && onLeave) leave.addEventListener("click", onLeave);
  for (const b of pane.querySelectorAll("[data-call]")) b.addEventListener("click", () => onCall(b.dataset.call));
  const callBar = pane.querySelector("#chat-call-bar");
  let callKind = "audio";
  pane.querySelector("#chat-call-join").addEventListener("click", () => onCall && onCall(callKind));

  const form = pane.querySelector("#chat-composer");
  const input = pane.querySelector("#chat-composer-input");
  const typingEl = pane.querySelector("#chat-typing");
  const contextEl = pane.querySelector("#chat-context");
  const contextText = pane.querySelector("#chat-context-text");

  // the box grows with its text up to a few lines (its max-height), then scrolls
  const fit = () => {
    input.style.height = "auto";
    input.style.height = `${input.scrollHeight + 2}px`;
  };

  form.addEventListener("submit", (e) => {
    e.preventDefault();
    const text = input.value.trim();
    if (!text) return;
    input.value = "";
    fit();
    onSend(text);
  });

  // Enter sends, Shift+Enter starts a new line (never while an input method is still composing a character)
  input.addEventListener("keydown", (e) => {
    if (e.key === "Enter" && !e.shiftKey && !e.isComposing) {
      e.preventDefault();
      form.requestSubmit();
    }
  });

  // typing: tell the other side while there is text, and stop when it is gone or sent
  input.addEventListener("input", () => {
    fit();
    onTyping?.(input.value.length > 0);
  });
  input.addEventListener("blur", () => onTyping?.(false));

  pane.querySelector("#chat-context-cancel").addEventListener("click", () => {
    onCancelContext?.();
    input.focus();
  });
  input.addEventListener("keydown", (e) => {
    if (e.key === "Escape" && !contextEl.classList.contains("d-none")) onCancelContext?.();
  });

  if (guildChannel) {
    for (const [id, handler] of [["chat-gc-search", onSearch], ["chat-gc-pins", onPins], ["chat-gc-mute", onMute]]) {
      const button = pane.querySelector("#" + id);
      if (button && handler) button.addEventListener("click", handler);
    }
    // a channel that asks its rules to be accepted: the rules, and one button, in place of the box
    if (guildChannel.needs_rules) {
      form.classList.replace("d-flex", "d-none");
      const box = document.createElement("div");
      box.className = "px-2 py-2";
      const title = document.createElement("div");
      title.className = "font-weight-bold small mb-1";
      title.textContent = "Rules of this channel";
      const text = document.createElement("div");
      text.className = "small chat-rules-text mb-2";
      text.textContent = guildChannel.rules;
      const agree = document.createElement("button");
      agree.type = "button";
      agree.className = "btn btn-primary btn-sm";
      agree.id = "chat-accept-rules";
      agree.textContent = "I agree to the rules";
      agree.addEventListener("click", () => onAcceptRules && onAcceptRules());
      box.append(title, text, agree);
      form.parentElement.appendChild(box);
    }
    // who may write, and slow mode, are the site's rules (helpers/guild_channels.py): this line only says them
    const note = guildChannel.needs_rules ? "" : composerNote(guildChannel);
    if (!guildChannel.may_write && !guildChannel.needs_rules) form.classList.replace("d-flex", "d-none");      // d-flex is !important: hidden would lose
    if (note) {
      const line = document.createElement("div");
      line.className = "small text-muted text-center py-1";
      line.id = "chat-channel-note";
      line.textContent = note;
      form.parentElement.appendChild(line);
    }
  }
  if (channel && !channel.may_write) {
    // members read and react; only the owner and helpers write (the room's power levels say the same)
    form.classList.replace("d-flex", "d-none");      // d-flex is !important: the hidden attribute would lose
    const note = document.createElement("div");
    note.className = "small text-muted text-center py-2";
    note.textContent = "Only the channel's owner and helpers write here. You can react to their messages.";
    form.parentElement.appendChild(note);
  }

  return {
    messagesEl: pane.querySelector("#chat-messages"),
    inputEl: input,
    fit,
    // a line above the box: "Quoting ..." / "Editing message", or none
    setContext(label) {
      contextEl.classList.toggle("d-none", !label);
      contextEl.classList.toggle("d-flex", !!label);
      contextText.textContent = label || "";
    },
    setTyping(text) {
      typingEl.textContent = text || "";
    },
    // a call going on in this chat (GET /api/calls/status): "Voice call · 3 people" and Join
    setCall(status) {
      const show = !!(status && status.active && !status.in_call && onCall);
      callBar.classList.toggle("d-none", !show);
      callBar.classList.toggle("d-flex", show);
      if (!show) return;
      callKind = status.kind === "video" ? "video" : "audio";
      const people = Number(status.people) || 0;
      pane.querySelector("#chat-call-bar-text").textContent =
        `${callKind === "video" ? "Video" : "Voice"} call going on · ${people} ${people === 1 ? "person" : "people"}`;
    },
    // a panel over the messages (a thread, the pinned messages, search, notifications): { body, foot, close }
    openPanel(title, onClose) {
      const panel = pane.querySelector("#chat-panel");
      const body = pane.querySelector("#chat-panel-body");
      const foot = pane.querySelector("#chat-panel-foot");
      pane.querySelector("#chat-panel-title").textContent = title;
      body.textContent = "";
      foot.textContent = "";
      foot.classList.add("d-none");
      panel.classList.remove("d-none");
      panel.classList.add("d-flex");
      const close = () => {
        panel.classList.add("d-none");
        panel.classList.remove("d-flex");
        if (onClose) onClose();
      };
      pane.querySelector("#chat-panel-close").onclick = close;
      return { body, foot, close, showFoot() { foot.classList.remove("d-none"); } };
    },
    // a group's name and size after a change in its sheet
    setGroup(next) {
      const title = pane.querySelector("#chat-thread-title");
      const subtitle = pane.querySelector("#chat-thread-subtitle");
      if (title) title.textContent = next.label;
      if (subtitle) subtitle.textContent = peopleLabel(next.size);
    },
  };
}

// items: [{ eventId, mine, body, status, edited, deleted, quote, reactions, seen, seenBy, canEdit, sender, blocked }]
// sender: who wrote it, in a group (null for your own and in a 1:1 chat); blocked: someone you blocked wrote it
// handlers: { onQuote(item), onReact(item, key), onEdit(item), onDelete(item), onReport(item), onRemove(item) }
// removable: a guildmaster may remove this message from a guild channel
export function renderMessages(messagesEl, items, handlers, more) {
  // keep the reader's place unless they were at the bottom
  const nearBottom = messagesEl.scrollHeight - messagesEl.scrollTop - messagesEl.clientHeight < 80;
  const before = messagesEl.scrollTop;
  messagesEl.innerHTML = "";
  if (more && more.onLoad) {
    // older messages exist (a guild channel): one button at the top
    const older = document.createElement("div");
    older.className = "text-center mb-2";
    const button = document.createElement("button");
    button.type = "button";
    button.className = "btn btn-sm btn-link";
    button.id = "chat-load-earlier";
    button.textContent = "Load earlier messages";
    button.addEventListener("click", () => more.onLoad());
    older.appendChild(button);
    messagesEl.appendChild(older);
  }
  for (const item of items) {
    messagesEl.appendChild(buildMessage(item, handlers));
  }
  messagesEl.scrollTop = nearBottom ? messagesEl.scrollHeight : before;
}

function iconButton(label, iconClass, onClick) {
  const b = document.createElement("button");
  b.type = "button";
  b.className = "btn btn-link btn-sm text-muted px-1 chat-action";
  b.setAttribute("aria-label", label);
  b.title = label;
  b.innerHTML = `<i class="${iconClass}"></i>`;
  b.addEventListener("click", onClick);
  return b;
}

function buildMessage(item, handlers) {
  const row = document.createElement("div");
  row.className = `d-flex mb-2 chat-msg ${item.mine ? "justify-content-end" : "justify-content-start"}`;
  row.dataset.eventId = item.eventId;

  const column = document.createElement("div");
  column.className = `d-flex flex-column chat-msg-column ${item.mine ? "align-items-end" : "align-items-start"}`;

  if (item.notice) {
    row.className = "d-flex justify-content-center mb-2 chat-msg chat-notice";
    const note = document.createElement("div");
    note.className = "small text-muted chat-notice-text";
    const icon = document.createElement("i");
    icon.className = "fas fa-phone mr-1";
    icon.setAttribute("aria-hidden", "true");
    note.appendChild(icon);
    note.appendChild(document.createTextNode(item.body));
    row.appendChild(note);
    return row;
  }

  if (item.sender) {
    const who = document.createElement("div");
    who.className = "small text-muted chat-sender";
    who.textContent = item.sender;
    // a guild role, as a chip in the flair palette (the server only sends a palette colour; the pattern is a second lock)
    if (item.senderRole && /^[a-z]{1,12}$/.test(item.senderRole.colour || "")) {
      const chip = document.createElement("span");
      chip.className = `flair-chip flair-${item.senderRole.colour} ml-1`;
      chip.textContent = item.senderRole.name;
      who.appendChild(chip);
    }
    column.appendChild(who);
  }

  if (item.filtered) {
    const hidden = document.createElement("div");
    hidden.className = "px-3 py-2 rounded chat-bubble bg-light font-italic text-muted";
    hidden.textContent = "Hidden by your word filter";
    column.appendChild(hidden);
    row.appendChild(column);
    return row;
  }

  if (item.blocked) {
    // the blocker's own view only: the other person is never told (helpers/chat_groups.py)
    const hidden = document.createElement("div");
    hidden.className = "px-3 py-2 rounded chat-bubble bg-light font-italic text-muted";
    hidden.textContent = "A message from someone you blocked";
    column.appendChild(hidden);
    row.appendChild(column);
    return row;
  }

  if (item.quote) {
    const quote = document.createElement("div");
    quote.className = "chat-quote small text-muted text-truncate";
    quote.textContent = item.quote;
    column.appendChild(quote);
  }

  const line = document.createElement("div");
  line.className = "d-flex align-items-center";

  const bubble = document.createElement("div");
  bubble.className = `px-3 py-2 rounded chat-bubble ${item.mine ? "bg-primary text-white" : "bg-light"}${item.mentionsMe ? " chat-mention" : ""}`;
  if (item.status) bubble.style.opacity = "0.6";
  const shared = !item.deleted && item.body ? POST_LINK.exec(item.body) : null;
  if (item.deleted) {
    bubble.classList.add("font-italic");
    bubble.textContent = "Message deleted";
  } else {
    // a shared post: the words around the link (if any), and the card under the message
    bubble.textContent = shared ? item.body.replace(shared[0], "").trim() || "Shared a post" : item.body;
    if (item.edited) {
      const mark = document.createElement("span");
      mark.className = "small ml-2 chat-edited";
      mark.textContent = "(edited)";
      bubble.appendChild(mark);
    }
  }

  if (item.deleted || item.status) {
    line.appendChild(bubble);        // nothing to do to a deleted or still-sending message
  } else {
    const actions = document.createElement("div");
    actions.className = "chat-actions d-flex align-items-center";
    if (handlers.onQuote) actions.appendChild(iconButton("Quote this message", "fas fa-quote-left", () => handlers.onQuote(item)));
    if (item.threadable && handlers.onThread) actions.appendChild(iconButton("Start or open a thread", "far fa-comments", () => handlers.onThread(item)));

    const picker = document.createElement("div");
    picker.className = "chat-picker d-none";
    for (const key of REACTION_CHOICES) {
      const choice = document.createElement("button");
      choice.type = "button";
      choice.className = "btn btn-link btn-sm px-1";
      choice.textContent = key;
      choice.setAttribute("aria-label", `React with ${key}`);
      choice.addEventListener("click", () => handlers.onReact(item, key));
      picker.appendChild(choice);
    }
    actions.appendChild(iconButton("React", "far fa-smile", () => picker.classList.toggle("d-none")));
    actions.appendChild(picker);

    if (item.mine && item.canEdit) {
      actions.appendChild(iconButton("Edit this message", "fas fa-pen", () => handlers.onEdit(item)));
    }
    if (item.reportable && handlers.onReport) {
      actions.appendChild(iconButton("Report this message", "fas fa-flag", () => handlers.onReport(item)));
    }
    if (item.pinnable && handlers.onPin) {
      actions.appendChild(iconButton(item.pinned ? "Unpin this message" : "Pin this message", "fas fa-thumbtack", () => handlers.onPin(item)));
    }
    if (item.removable && handlers.onRemove) {
      actions.appendChild(iconButton("Remove this message (guildmaster)", "fas fa-comment-slash", () => handlers.onRemove(item)));
    }
    if (item.mine) {
      actions.appendChild(iconButton("Delete this message", "fas fa-trash", () => handlers.onDelete(item)));
    }

    if (item.mine) {
      line.appendChild(actions);
      line.appendChild(bubble);
    } else {
      line.appendChild(bubble);
      line.appendChild(actions);
    }
  }
  column.appendChild(line);

  if (shared) column.appendChild(postCard(shared[1]));

  if (item.threadCount && handlers.onThread) {
    const link = document.createElement("button");
    link.type = "button";
    link.className = "btn btn-link btn-sm px-0 chat-thread-link";
    link.textContent = `${item.threadCount} message${item.threadCount === 1 ? "" : "s"} in thread`;
    link.addEventListener("click", () => handlers.onThread(item));
    column.appendChild(link);
  }

  if (item.reactions && item.reactions.length) {
    const chips = document.createElement("div");
    chips.className = "chat-reactions";
    for (const reaction of item.reactions) {
      const chip = document.createElement("button");
      chip.type = "button";
      chip.className = `btn btn-sm chat-reaction ${reaction.mine ? "btn-primary" : "btn-outline-secondary"}`;
      chip.setAttribute("aria-pressed", String(reaction.mine));
      chip.textContent = `${reaction.key} ${reaction.count}`;
      chip.addEventListener("click", () => handlers.onReact(item, reaction.key));
      chips.appendChild(chip);
    }
    column.appendChild(chips);
  }

  if (item.seen) {
    const seen = document.createElement("div");
    seen.className = "small text-muted chat-seen";
    seen.textContent = item.seenBy ? `Seen by ${item.seenBy}` : "Seen";
    column.appendChild(seen);
  }

  row.appendChild(column);
  return row;
}

export function setTabBadge(el, count) {
  if (count > 0) {
    el.textContent = String(count);
    el.classList.remove("d-none");
  } else {
    el.classList.add("d-none");
  }
}
