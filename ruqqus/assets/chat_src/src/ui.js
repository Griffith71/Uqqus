// DOM rendering for the Chat page. Plain hand-written DOM manipulation,
// consistent with the rest of this site's vanilla-JS convention (no
// framework) - reuses the site's existing Bootstrap utility classes rather
// than introducing new visual language.

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
    if (convo.conversation_id === activeConversationId) row.classList.add("bg-light");

    const unreadDot = convo.unread_count > 0
      ? '<span class="badge badge-danger rounded-circle align-top ml-1">&nbsp;</span>'
      : "";

    row.innerHTML = `
      <img src="${escapeHtml(convo.other_user.profile_url)}" class="rounded-circle mr-2" width="40" height="40">
      <div class="flex-grow-1 overflow-hidden">
        <div class="font-weight-bold text-truncate">@${escapeHtml(convo.other_user.username)}${unreadDot}</div>
      </div>
      <div class="text-muted small ml-2">${timeLabel(convo.last_activity_utc * 1000)}</div>
    `;
    row.addEventListener("click", (e) => {
      e.preventDefault();
      onSelect(convo);
    });
    container.appendChild(row);
  }
}

export function renderThreadShell(pane, { otherUser, isRequest, onAccept, onDecline, onSend }) {
  pane.innerHTML = `
    <div class="d-flex align-items-center border-bottom px-3 py-2">
      <img src="${escapeHtml(otherUser.profile_url)}" class="rounded-circle mr-2" width="36" height="36">
      <div class="font-weight-bold">@${escapeHtml(otherUser.username)}</div>
    </div>
    ${isRequest ? `
    <div class="d-flex align-items-center justify-content-between px-3 py-2 bg-light border-bottom">
      <div class="text-muted small">Message request. They won't know you've seen this until you accept.</div>
      <div>
        <button type="button" class="btn btn-sm btn-secondary mr-2" id="chat-decline-btn">Delete</button>
        <button type="button" class="btn btn-sm btn-primary" id="chat-accept-btn">Accept</button>
      </div>
    </div>` : ""}
    <div class="flex-grow-1 overflow-auto px-3 py-2" id="chat-messages"></div>
    <div class="border-top p-2">
      <form id="chat-composer" class="d-flex">
        <input type="text" class="form-control mr-2" id="chat-composer-input" placeholder="Start a message..." autocomplete="off">
        <button type="submit" class="btn btn-primary">Send</button>
      </form>
    </div>
  `;

  if (isRequest) {
    pane.querySelector("#chat-accept-btn").addEventListener("click", onAccept);
    pane.querySelector("#chat-decline-btn").addEventListener("click", onDecline);
  }

  const form = pane.querySelector("#chat-composer");
  const input = pane.querySelector("#chat-composer-input");
  form.addEventListener("submit", (e) => {
    e.preventDefault();
    const text = input.value.trim();
    if (!text) return;
    input.value = "";
    onSend(text);
  });

  return { messagesEl: pane.querySelector("#chat-messages"), inputEl: input };
}

export function renderMessages(messagesEl, items, myUserId) {
  messagesEl.innerHTML = "";
  for (const item of items) {
    appendMessage(messagesEl, item, myUserId, false);
  }
  messagesEl.scrollTop = messagesEl.scrollHeight;
}

function appendMessage(messagesEl, item, myUserId, scroll = true) {
  const isMine = item.senderId === myUserId;
  const row = document.createElement("div");
  row.className = `d-flex mb-2 ${isMine ? "justify-content-end" : "justify-content-start"}`;
  row.dataset.eventId = item.eventId;

  const pending = item.status ? ' style="opacity:0.6;"' : "";
  row.innerHTML = `
    <div class="px-3 py-2 rounded ${isMine ? "bg-primary text-white" : "bg-light"}" ${pending}>
      ${escapeHtml(item.body)}
    </div>
  `;
  messagesEl.appendChild(row);
  if (scroll) messagesEl.scrollTop = messagesEl.scrollHeight;
}

export function setTabBadge(el, count) {
  if (count > 0) {
    el.textContent = String(count);
    el.classList.remove("d-none");
  } else {
    el.classList.add("d-none");
  }
}
