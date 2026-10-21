"""Finding chats (the owner, 2026-10-21): the chat page's search, and the chats an account or a guild made in their
About boxes. The promises: groups are private, so only your own are ever found; never a chat request, never anyone in
a block either way, never a name your word filter hides; every list follows the rules its own tab or page uses. And
the site's internal boards are the site's own colour, never Ruqqus's old purple."""
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def read(*parts):
    return ROOT.joinpath(*parts).read_text(encoding="utf-8")


def func(src, name):
    return src.split(f"def {name}(", 1)[1].split("\n@app.route", 1)[0].split("\n\n\ndef ", 1)[0]


CHAT = read("ruqqus", "routes", "chat.py")
EXTRAS = read("ruqqus", "assets", "chat_src", "src", "chat_extras.js")


def test_the_search_route_needs_an_account_and_is_limited():
    head = CHAT.split("def chat_search(", 1)[0].rsplit("@app.route", 1)[1]
    assert '"/api/chat/search"' in head and "@auth_required" in head
    assert '@limiter.limit("30/minute", key_func=member_limit_key)' in head


def test_the_search_follows_each_lists_own_rules():
    search = func(CHAT, "chat_search")
    # your 1:1 chats: never a request, a block either way, a hidden name
    assert 'if convo.tab_for(v.id) != "inbox":' in search
    assert "is_blocked(v, other) or user_hidden(other, v)" in search
    # groups: only the ones you joined (they are private and invite-only)
    assert 'chat_group_store.groups_for(g.db, v.id, "joined", limit=200)' in search
    assert '"invited"' not in search
    # public channels: never an owner in a block, never a name the filter hides
    assert "broadcast_store.search_public(g.db, q, v.id" in search and "text_hidden(row[3], v) or _blocked(row, v)" in search
    # your guilds' chats: the Guilds tab's own list and read rules; other guilds: who may see them, and their Chat tab
    assert "conversations_for(v)" in search
    assert "listable_boards(" in search and "not board.can_view(v) or board.is_banned" in search
    store = read("ruqqus", "helpers", "broadcast_store.py").split("def search_public(", 1)[1].split("\ndef ", 1)[0]
    assert "audience = 0 AND deleted_utc = 0" in store and "LIKE LOWER(:q) ESCAPE" in store
    assert "like_pattern(q)" in store


def test_the_about_boxes_use_the_channels_page_and_the_chat_tab_rules():
    channels = func(read("ruqqus", "routes", "broadcasts.py"), "channels_for")
    assert "v.any_block_exists(owner)" in channels and "_may_see(row, v)" in channels and "text_hidden(row[3], v)" in channels
    assert "{% set about_chats = channels_for(u, v) %}" in read("ruqqus", "templates", "partials", "profile_about.html")
    guild = func(read("ruqqus", "routes", "guild_channels.py"), "guild_channels_for")
    assert "has_chat(board, v)" in guild and "_read_refusal(row, v, _hidden(board, v, row))" in guild
    assert "{% set about_channels = guild_channels_for(b, v) %}" in read("ruqqus", "templates", "board.html")


def test_the_search_box_draws_text_only():
    assert 'id="chat-search"' in read("ruqqus", "templates", "chat.html")
    assert "fetch(`/api/chat/search?q=${encodeURIComponent(q)}`" in EXTRAS
    assert "innerHTML" not in EXTRAS
    # results stay while a message arrives: the list refresh leaves a search alone
    assert 'if (state.tab !== "calls" && !extras?.searching()) setTab(state.tab);' in read(
        "ruqqus", "assets", "chat_src", "src", "index.js")


def test_profile_posts_and_the_internal_boards_are_the_sites_own_colour():
    page = read("ruqqus", "templates", "submission.html")
    assert '{% if p.is_profile_post %}\n{{ theme_links(asset_url("style/main.css"), asset_url("style/main_dark.css")) }}' in page
    boards = read("ruqqus", "routes", "boards.py")
    assert "INTERNAL_BOARDS = (1, 2)" in boards
    assert 'colour = app.config["SITE_COLOR"] if board.id in INTERNAL_BOARDS else board.color' in boards
    migration = read("scripts", "migrations", "2026-11-14_site_board_colour.sql")
    assert "WHERE id IN (1, 2) AND color = '805ad5'" in migration and "2026-11-14_site_board_colour.sql" in read("schema.sql")
