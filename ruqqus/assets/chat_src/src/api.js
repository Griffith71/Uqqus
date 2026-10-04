// Thin wrapper around Ruqqus's own /api/chat/* routes. These hold ONLY
// integration metadata (who a conversation is with, which tab it's in,
// unread counts) - never message content, which lives solely in Matrix.

function formBody(formkey, extra) {
  const body = new URLSearchParams();
  body.set("formkey", formkey);
  if (extra) {
    for (const k in extra) body.set(k, extra[k]);
  }
  return body;
}

export async function listConversations(status) {
  const resp = await fetch(`/api/chat/conversations?status=${status}`, {
    credentials: "same-origin",
  });
  if (!resp.ok) throw new Error("Could not load conversations");
  const data = await resp.json();
  return data.conversations;
}

export async function acceptConversation(formkey, conversationId) {
  const resp = await fetch(`/api/chat/conversations/${conversationId}/accept`, {
    method: "POST",
    credentials: "same-origin",
    headers: { "Content-Type": "application/x-www-form-urlencoded" },
    body: formBody(formkey),
  });
  if (!resp.ok) throw new Error("Could not accept conversation");
}

export async function declineConversation(formkey, conversationId) {
  const resp = await fetch(`/api/chat/conversations/${conversationId}/decline`, {
    method: "POST",
    credentials: "same-origin",
    headers: { "Content-Type": "application/x-www-form-urlencoded" },
    body: formBody(formkey),
  });
  if (!resp.ok) throw new Error("Could not decline conversation");
}

export async function markRead(formkey, conversationId) {
  const resp = await fetch(`/api/chat/conversations/${conversationId}/mark_read`, {
    method: "POST",
    credentials: "same-origin",
    headers: { "Content-Type": "application/x-www-form-urlencoded" },
    body: formBody(formkey),
  });
  if (!resp.ok) throw new Error("Could not mark conversation read");
}
