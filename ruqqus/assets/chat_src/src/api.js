// Thin wrapper around Ruqqus's own /api/chat/* routes. These hold ONLY
// integration metadata (who a conversation is with, which tab it's in,
// unread counts) - never message content, which lives solely in Matrix.
//
// Every POST takes the page's boot object (`site`), not a copy of its form key: a stale key (a credential event in another tab that
// spared this sign-in) is refreshed in place and the POST sent again (site.js sitePost).

import { sitePost } from "./site.js";

export async function listConversations(status) {
  const resp = await fetch(`/api/chat/conversations?status=${status}`, {
    credentials: "same-origin",
  });
  if (!resp.ok) throw new Error(window.tr("Could not load conversations"));
  const data = await resp.json();
  return data.conversations;
}

export async function acceptConversation(site, conversationId) {
  const resp = await sitePost(site, `/api/chat/conversations/${conversationId}/accept`);
  if (!resp.ok) throw new Error(window.tr("Could not accept conversation"));
}

export async function declineConversation(site, conversationId) {
  const resp = await sitePost(site, `/api/chat/conversations/${conversationId}/decline`);
  if (!resp.ok) throw new Error(window.tr("Could not decline conversation"));
}

export async function markRead(site, conversationId) {
  const resp = await sitePost(site, `/api/chat/conversations/${conversationId}/mark_read`);
  if (!resp.ok) throw new Error(window.tr("Could not mark conversation read"));
}

// --- groups (routes/chat_groups.py) ---------------------------------------------------------------
// A refused change answers {"error": "..."}: it is thrown as the message to show.
async function postJSON(url, site, extra) {
  const resp = await sitePost(site, url, extra || {});
  if (resp.status === 204) return null;
  let data = null;
  try {
    data = await resp.json();
  } catch (e) {
    data = null;
  }
  if (!resp.ok) throw new Error((data && data.error) || window.tr("That didn't work. Try again."));
  return data;
}

export async function groupInfo(groupId) {
  const resp = await fetch(`/api/chat/groups/${groupId}`, { credentials: "same-origin" });
  if (!resp.ok) throw new Error(window.tr("Could not load the group"));
  return resp.json();
}

export async function searchPeople(q) {
  const resp = await fetch(`/api/chat/people?q=${encodeURIComponent(q || "")}`, { credentials: "same-origin" });
  if (!resp.ok) throw new Error(window.tr("Could not search"));
  return (await resp.json()).people || [];
}

export const createGroup = (site, name, usernames) =>
  postJSON("/api/chat/groups", site, { name, usernames: usernames.join(",") });
export const addToGroup = (site, groupId, usernames) =>
  postJSON(`/api/chat/groups/${groupId}/add`, site, { usernames: usernames.join(",") });
export const removeFromGroup = (site, groupId, username) =>
  postJSON(`/api/chat/groups/${groupId}/remove`, site, { username });
export const setGroupRole = (site, groupId, username, role) =>
  postJSON(`/api/chat/groups/${groupId}/role`, site, { username, role });
export const renameGroup = (site, groupId, name) =>
  postJSON(`/api/chat/groups/${groupId}/rename`, site, { name });
export const leaveGroup = (site, groupId) => postJSON(`/api/chat/groups/${groupId}/leave`, site);
export const acceptGroup = (site, groupId) => postJSON(`/api/chat/groups/${groupId}/accept`, site);
export const declineGroup = (site, groupId) => postJSON(`/api/chat/groups/${groupId}/decline`, site);
export const markGroupRead = (site, groupId) => postJSON(`/api/chat/groups/${groupId}/mark_read`, site);

// --- broadcast channels (routes/broadcasts.py) ----------------------------------------------------------------------
export const postToChannel = (site, channelId, body, media = null) =>
  postJSON(`/api/broadcasts/${channelId}/messages`, site, media ? { body, media } : { body });
export const leaveChannel = (site, channelId) => postJSON(`/api/broadcasts/${channelId}/leave`, site);
export const markChannelRead = (site, channelId) => postJSON(`/api/broadcasts/${channelId}/mark_read`, site);
export const reportChannelMessage = (site, channelId, eventId, reason) =>
  postJSON(`/api/broadcasts/${channelId}/report`, site, { event_id: eventId, reason });

// --- guild channels (routes/guild_channels.py) -------------------------------------------------------------------------
export const joinGuildChannel = (site, channelId) => postJSON(`/api/guilds/channels/${channelId}/join`, site);
export const postToGuildChannel = (site, channelId, body, quote, thread) => {
  const extra = { body };
  if (quote) extra.quote = quote;
  if (thread) extra.thread = thread;
  return postJSON(`/api/guilds/channels/${channelId}/messages`, site, extra);
};
export const markGuildChannelRead = (site, channelId) => postJSON(`/api/guilds/channels/${channelId}/mark_read`, site);
// a message in a private chat: the page sends it and the few before it, as it shows them (report.js, routes/chat_reports_pages.py)
// a channel's owner adds or removes a helper (routes/broadcasts.py): answers the channel as it is now
export const setChannelHelper = (site, channelId, username, on) =>
  postJSON(`/api/broadcasts/${channelId}/helpers`, site, { username, on: on ? "1" : "0" });
// what this member is told of in a group (routes/push_pages.py): 0 every message, 1 calls only, 2 nothing
export const setGroupPush = (site, groupId, mode) => postJSON(`/api/chat/groups/${groupId}/push`, site, { mode });
export const reportPrivateMessage = (site, roomId, eventId, messages, reason) =>
  postJSON("/api/chat/report", site, { room: roomId, event_id: eventId, messages: JSON.stringify(messages), reason });
export const blockAccount = (site, username) => sitePost(site, "/settings/block", { username });
// pins and disappearing messages in a 1:1 chat or a group (routes/chat_extras_pages.py): room state the site's bot writes
export const pinRoomMessage = (site, roomId, eventId, pin) =>
  postJSON("/api/chat/pin", site, { room: roomId, event_id: eventId, pin: pin ? "1" : "0" });
export const setDisappearing = (site, roomId, seconds) => postJSON("/api/chat/disappear", site, { room: roomId, seconds: String(seconds) });

// the chats a message can be forwarded to: your Inbox chats and joined groups (routes/chat.py share_targets), never someone new
export async function forwardTargets(q) {
  const resp = await fetch(`/api/chat/share_targets?q=${encodeURIComponent(q || "")}`, { credentials: "same-origin" });
  if (!resp.ok) throw new Error(window.tr("Could not load your chats"));
  return ((await resp.json()).targets || []).filter((t) => t.room_id);
}
export const reportGuildChannelMessage = (site, channelId, eventId, reason) =>
  postJSON(`/api/guilds/channels/${channelId}/report`, site, { event_id: eventId, reason });
export const removeGuildChannelMessage = (site, channelId, eventId) =>
  postJSON(`/api/guilds/channels/${channelId}/remove`, site, { event_id: eventId });

// names for the people who wrote in a channel: only people who did are ever answered
export async function guildChannelSenders(channelId, userIds) {
  const resp = await fetch(`/api/guilds/channels/${channelId}/senders?ids=${userIds.join(",")}`, { credentials: "same-origin" });
  if (!resp.ok) throw new Error(window.tr("Could not load names"));
  return (await resp.json()).senders || {};
}
export const pinGuildChannelMessage = (site, channelId, eventId, on) =>
  postJSON(`/api/guilds/channels/${channelId}/pin`, site, { event_id: eventId, on: on ? "1" : "0" });
export const muteGuildChannel = (site, channelId, hours) =>
  postJSON(`/api/guilds/channels/${channelId}/mute`, site, { hours: String(hours) });
export const acceptGuildChannelRules = (site, channelId) => postJSON(`/api/guilds/channels/${channelId}/accept_rules`, site);

export async function guildChannelPins(channelId) {
  const resp = await fetch(`/api/guilds/channels/${channelId}/pins`, { credentials: "same-origin" });
  if (!resp.ok) throw new Error(window.tr("Could not load the pinned messages"));
  return (await resp.json()).pins || [];
}

export async function searchGuildChannel(channelId, query) {
  const resp = await fetch(`/api/guilds/channels/${channelId}/search?q=${encodeURIComponent(query)}`, { credentials: "same-origin" });
  if (resp.status === 429) throw new Error(window.tr("Searching too fast. Wait a moment."));
  if (!resp.ok) throw new Error(window.tr("Could not search"));
  return resp.json();
}
