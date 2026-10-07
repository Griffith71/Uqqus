// DOM rendering for the Chat page. Plain hand-written DOM manipulation,
// consistent with the rest of this site's vanilla-JS convention (no
// framework) - reuses the site's existing Bootstrap utility classes rather
// than introducing new visual language.
//
// Message text only ever goes in through textContent, never innerHTML.

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

// callbacks: onAccept, onDecline, onSend(text), onTyping(isTyping), onCancelContext()
export function renderThreadShell(pane, { otherUser, isRequest, onAccept, onDecline, onSend, onTyping, onCancelContext }) {
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
    <div id="chat-typing" class="px-3 small text-muted chat-typing" aria-live="polite"></div>
    <div id="chat-context" class="d-none align-items-center border-top px-3 py-1 small bg-light">
      <div class="flex-grow-1 text-truncate" id="chat-context-text"></div>
      <button type="button" class="btn btn-link btn-sm text-muted" id="chat-context-cancel" aria-label="Cancel">&times;</button>
    </div>
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
  const typingEl = pane.querySelector("#chat-typing");
  const contextEl = pane.querySelector("#chat-context");
  const contextText = pane.querySelector("#chat-context-text");

  form.addEventListener("submit", (e) => {
    e.preventDefault();
    const text = input.value.trim();
    if (!text) return;
    input.value = "";
    onSend(text);
  });

  // typing: tell the other side while there is text, and stop when it is gone or sent
  input.addEventListener("input", () => onTyping?.(input.value.length > 0));
  input.addEventListener("blur", () => onTyping?.(false));

  pane.querySelector("#chat-context-cancel").addEventListener("click", () => {
    onCancelContext?.();
    input.focus();
  });
  input.addEventListener("keydown", (e) => {
    if (e.key === "Escape" && !contextEl.classList.contains("d-none")) onCancelContext?.();
  });

  return {
    messagesEl: pane.querySelector("#chat-messages"),
    inputEl: input,
    // a line above the box: "Quoting ..." / "Editing message", or none
    setContext(label) {
      contextEl.classList.toggle("d-none", !label);
      contextEl.classList.toggle("d-flex", !!label);
      contextText.textContent = label || "";
    },
    setTyping(name) {
      typingEl.textContent = name ? `@${name} is typing…` : "";
    },
  };
}

// items: [{ eventId, mine, body, status, edited, deleted, quote, reactions, seen, canEdit }]
// handlers: { onQuote(item), onReact(item, key), onEdit(item), onDelete(item) }
export function renderMessages(messagesEl, items, handlers) {
  // keep the reader's place unless they were at the bottom
  const nearBottom = messagesEl.scrollHeight - messagesEl.scrollTop - messagesEl.clientHeight < 80;
  const before = messagesEl.scrollTop;
  messagesEl.innerHTML = "";
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

  if (item.quote) {
    const quote = document.createElement("div");
    quote.className = "chat-quote small text-muted text-truncate";
    quote.textContent = item.quote;
    column.appendChild(quote);
  }

  const line = document.createElement("div");
  line.className = "d-flex align-items-center";

  const bubble = document.createElement("div");
  bubble.className = `px-3 py-2 rounded chat-bubble ${item.mine ? "bg-primary text-white" : "bg-light"}`;
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
    actions.appendChild(iconButton("Quote this message", "fas fa-quote-left", () => handlers.onQuote(item)));

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
    seen.textContent = "Seen";
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
