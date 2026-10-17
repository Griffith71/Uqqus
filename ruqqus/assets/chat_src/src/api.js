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

// --- groups (routes/chat_groups.py) ---------------------------------------------------------------
// A refused change answers {"error": "..."}: it is thrown as the message to show.
async function postJSON(url, formkey, extra) {
  const resp = await fetch(url, {
    method: "POST",
    credentials: "same-origin",
    headers: { "Content-Type": "application/x-www-form-urlencoded", Accept: "application/json" },
    body: formBody(formkey, extra),
  });
  if (resp.status === 204) return null;
  let data = null;
  try {
    data = await resp.json();
  } catch (e) {
    data = null;
  }
  if (!resp.ok) throw new Error((data && data.error) || "That didn't work. Try again.");
  return data;
}

export async function groupInfo(groupId) {
  const resp = await fetch(`/api/chat/groups/${groupId}`, { credentials: "same-origin" });
  if (!resp.ok) throw new Error("Could not load the group");
  return resp.json();
}

export async function searchPeople(q) {
  const resp = await fetch(`/api/chat/people?q=${encodeURIComponent(q || "")}`, { credentials: "same-origin" });
  if (!resp.ok) throw new Error("Could not search");
  return (await resp.json()).people || [];
}

export const createGroup = (formkey, name, usernames) =>
  postJSON("/api/chat/groups", formkey, { name, usernames: usernames.join(",") });
export const addToGroup = (formkey, groupId, usernames) =>
  postJSON(`/api/chat/groups/${groupId}/add`, formkey, { usernames: usernames.join(",") });
export const removeFromGroup = (formkey, groupId, username) =>
  postJSON(`/api/chat/groups/${groupId}/remove`, formkey, { username });
export const setGroupRole = (formkey, groupId, username, role) =>
  postJSON(`/api/chat/groups/${groupId}/role`, formkey, { username, role });
export const renameGroup = (formkey, groupId, name) =>
  postJSON(`/api/chat/groups/${groupId}/rename`, formkey, { name });
export const leaveGroup = (formkey, groupId) => postJSON(`/api/chat/groups/${groupId}/leave`, formkey);
export const acceptGroup = (formkey, groupId) => postJSON(`/api/chat/groups/${groupId}/accept`, formkey);
export const declineGroup = (formkey, groupId) => postJSON(`/api/chat/groups/${groupId}/decline`, formkey);
export const markGroupRead = (formkey, groupId) => postJSON(`/api/chat/groups/${groupId}/mark_read`, formkey);

// --- broadcast channels (routes/broadcasts.py) ----------------------------------------------------------------------
export const postToChannel = (formkey, channelId, body) =>
  postJSON(`/api/broadcasts/${channelId}/messages`, formkey, { body });
export const leaveChannel = (formkey, channelId) => postJSON(`/api/broadcasts/${channelId}/leave`, formkey);
export const markChannelRead = (formkey, channelId) => postJSON(`/api/broadcasts/${channelId}/mark_read`, formkey);
export const reportChannelMessage = (formkey, channelId, eventId, reason) =>
  postJSON(`/api/broadcasts/${channelId}/report`, formkey, { event_id: eventId, reason });

// --- guild channels (routes/guild_channels.py) -------------------------------------------------------------------------
export const joinGuildChannel = (formkey, channelId) => postJSON(`/api/guilds/channels/${channelId}/join`, formkey);
export const postToGuildChannel = (formkey, channelId, body, quote, thread) => {
  const extra = { body };
  if (quote) extra.quote = quote;
  if (thread) extra.thread = thread;
  return postJSON(`/api/guilds/channels/${channelId}/messages`, formkey, extra);
};
export const markGuildChannelRead = (formkey, channelId) => postJSON(`/api/guilds/channels/${channelId}/mark_read`, formkey);
export const reportGuildChannelMessage = (formkey, channelId, eventId, reason) =>
  postJSON(`/api/guilds/channels/${channelId}/report`, formkey, { event_id: eventId, reason });
export const removeGuildChannelMessage = (formkey, channelId, eventId) =>
  postJSON(`/api/guilds/channels/${channelId}/remove`, formkey, { event_id: eventId });

// names for the people who wrote in a channel: only people who did are ever answered
export async function guildChannelSenders(channelId, userIds) {
  const resp = await fetch(`/api/guilds/channels/${channelId}/senders?ids=${userIds.join(",")}`, { credentials: "same-origin" });
  if (!resp.ok) throw new Error("Could not load names");
  return (await resp.json()).senders || {};
}
export const pinGuildChannelMessage = (formkey, channelId, eventId, on) =>
  postJSON(`/api/guilds/channels/${channelId}/pin`, formkey, { event_id: eventId, on: on ? "1" : "0" });
export const muteGuildChannel = (formkey, channelId, hours) =>
  postJSON(`/api/guilds/channels/${channelId}/mute`, formkey, { hours: String(hours) });
export const acceptGuildChannelRules = (formkey, channelId) => postJSON(`/api/guilds/channels/${channelId}/accept_rules`, formkey);

export async function guildChannelPins(channelId) {
  const resp = await fetch(`/api/guilds/channels/${channelId}/pins`, { credentials: "same-origin" });
  if (!resp.ok) throw new Error("Could not load the pinned messages");
  return (await resp.json()).pins || [];
}

export async function searchGuildChannel(channelId, query) {
  const resp = await fetch(`/api/guilds/channels/${channelId}/search?q=${encodeURIComponent(query)}`, { credentials: "same-origin" });
  if (resp.status === 429) throw new Error("Searching too fast. Wait a moment.");
  if (!resp.ok) throw new Error("Could not search");
  return resp.json();
}
