// Reporting a message in a private chat (a 1:1 chat or a group; routes/chat_reports_pages.py, helpers/chat_reports.py).
// The chat is end-to-end encrypted, so this page sends the reported message and up to four before it as it shows them;
// the box says so before anything is sent, and offers to block the writer in the same place. Everything is set as text.

export const CONTEXT = 4;            // messages before the reported one (helpers/chat_reports.py CONTEXT_MAX less one)

// The reported message and the few before it that this page could read, oldest first: [{event_id, body}]. `events` are
// the thread's shown messages in order; `textOf` the page's own reading of one (null for one it couldn't read).
export function contextOf(events, eventId, textOf) {
  const at = events.findIndex((e) => e.getId() === eventId);
  if (at < 0) return [];
  const out = [];
  for (let i = at - 1; i >= 0 && out.length < CONTEXT; i -= 1) {
    const event = events[i];
    if (event.isRedacted() || event.status) continue;
    const body = textOf(event);
    if (typeof body === "string" && body) out.unshift({ event_id: event.getId(), body });
  }
  const reported = events[at];
  out.push({ event_id: reported.getId(), body: textOf(reported) || "" });
  return out;
}

// The box: why (optional), and "Also block @name" when the writer has a name here. Answers {reason, block} or null.
export function askReport({ name }) {
  return new Promise((done) => {
    const shade = document.createElement("div");
    shade.className = "chat-key-dialog";
    shade.setAttribute("role", "dialog");
    shade.setAttribute("aria-modal", "true");
    const box = document.createElement("div");
    box.className = "chat-key-box";
    const title = document.createElement("h2");
    title.className = "h5";
    title.id = `chat-report-title-${Date.now()}`;
    title.textContent = window.tr("Report this message");
    shade.setAttribute("aria-labelledby", title.id);
    box.append(title);
    for (const line of [
      window.tr("This chat is end-to-end encrypted, so the site can't read it. Reporting sends this message and up to four before it, as you see them here, to the site's admins."),
      window.tr("They are never told who reported them."),
    ]) {
      const p = document.createElement("p");
      p.className = "text-small";
      p.textContent = line;
      box.append(p);
    }
    const label = document.createElement("label");
    label.className = "text-small d-block";
    label.textContent = window.tr("What's wrong with this message? (optional)");
    const reason = document.createElement("input");
    reason.type = "text";
    reason.maxLength = 200;
    reason.className = "form-control mt-1";
    label.append(reason);
    box.append(label);
    let block = null;
    if (name) {
      const row = document.createElement("label");
      row.className = "d-flex align-items-center text-small mt-2";
      block = document.createElement("input");
      block.type = "checkbox";
      block.className = "mr-2";
      row.append(block, document.createTextNode(window.tr("Also block @%(name)s", { name })));
      box.append(row);
    }
    const buttons = document.createElement("div");
    buttons.className = "d-flex flex-wrap mt-3";
    buttons.style.gap = "0.5rem";
    const send = document.createElement("button");
    send.type = "button";
    send.className = "btn btn-danger";
    send.textContent = window.tr("Report");
    const cancel = document.createElement("button");
    cancel.type = "button";
    cancel.className = "btn btn-link text-muted";
    cancel.textContent = window.tr("Cancel");
    buttons.append(send, cancel);
    box.append(buttons);
    shade.append(box);
    const finish = (answer) => {
      shade.remove();
      document.removeEventListener("keydown", onKey);
      done(answer);
    };
    const onKey = (e) => { if (e.key === "Escape") finish(null); };
    send.addEventListener("click", () => finish({ reason: reason.value, block: !!(block && block.checked) }));
    cancel.addEventListener("click", () => finish(null));
    document.addEventListener("keydown", onKey);
    document.body.append(shade);
    reason.focus();
  });
}
