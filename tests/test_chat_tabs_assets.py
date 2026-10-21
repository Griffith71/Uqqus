"""The chat page's tabs and sheets (the owner, 2026-10-20): Inbox for 1:1 chats, Groups, Requests, Channels, Guilds,
Calls; New chat and New channel. The promises: groups leave the Inbox for their own tab; the Calls tab only shows calls
in chats the member is in now, never with someone in a block either way; channels "from people you follow" are their
public channels only, never across a block; every name the scripts draw goes in as text."""
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def read(*parts):
    return ROOT.joinpath(*parts).read_text(encoding="utf-8")


def func(src, name):
    return src.split(f"def {name}(", 1)[1].split("\n@app.route", 1)[0]


PAGE = read("ruqqus", "templates", "chat.html")
SHEETS = read("ruqqus", "templates", "partials", "chat_group_sheets.html")
INDEX = read("ruqqus", "assets", "chat_src", "src", "index.js")
EXTRAS = read("ruqqus", "assets", "chat_src", "src", "chat_extras.js")


def test_the_tabs_in_their_order():
    tabs = re.findall(r'data-chat-tab="(\w+)"', PAGE)
    assert tabs == ["inbox", "group", "request", "channel", "guild", "calls"]
    assert 'id="chat-new-chat"' in PAGE and 'id="chat-new-group"' in PAGE
    assert 'id="chatNew"' in SHEETS and 'id="chatChannelNew"' in SHEETS


def test_groups_leave_the_inbox_for_their_own_tab():
    listing = func(read("ruqqus", "routes", "chat.py"), "chat_conversations")
    assert 'group_tab = {"group": "joined", "request": "invited"}.get(status)' in listing
    assert 'listConversations("group")' in INDEX and "conversations: { inbox: [], group: [], request: [], channel: [], guild: [] }," in INDEX
    # a chat that can be written in is either: sharing a post into a chat finds groups too
    assert "[...state.conversations.inbox, ...state.conversations.group].find((c) => c.room_id === roomId)" in INDEX


def test_the_calls_tab_only_shows_chats_the_member_is_in_and_never_across_a_block():
    history = func(read("ruqqus", "routes", "calls.py"), "calls_history")
    assert "@auth_required" in read("ruqqus", "routes", "calls.py").split("def calls_history(", 1)[0].rsplit("@app.route", 1)[1]
    assert "ChatConversation.user_a_id == v.id, ChatConversation.user_b_id == v.id" in history
    assert "is_blocked(v, other) or user_hidden(other, v)" in history
    assert 'group_store.groups_for(g.db, v.id, "joined", limit=200)' in history
    assert "call_store.history(g.db, wanted)" in history
    store = read("ruqqus", "helpers", "call_store.py").split("def history(", 1)[1].split("\ndef ", 1)[0]
    assert "WHERE room_id IN :rooms" in store and "started_by_id" in store
    assert "extras?.renderCalls(list);" in INDEX


def test_channels_from_people_you_follow_are_public_and_never_across_a_block():
    route = func(read("ruqqus", "routes", "broadcasts.py"), "broadcasts_following")
    assert "Follow.user_id == v.id" in route and "not v.any_block_exists(u)" in route and "not user_hidden(u, v)" in route
    store = read("ruqqus", "helpers", "broadcast_store.py").split("def public_of_owners(", 1)[1].split("\ndef ", 1)[0]
    assert "audience = 0 AND deleted_utc = 0" in store and "NOT IN (SELECT broadcast_id FROM broadcast_members" in store
    # a new channel from /chat goes through the one creating route and its rules
    assert 'post("/api/broadcasts", boot.formkey, fields)' in EXTRAS


def test_new_chat_uses_the_routes_that_decide_where_it_lands():
    assert 'fetch("/api/chat/start?json=1"' in EXTRAS and "searchPeople(typed)" in EXTRAS


def test_the_scripts_write_text_only():
    assert "innerHTML" not in EXTRAS
    assert EXTRAS.count("textContent") >= 8
