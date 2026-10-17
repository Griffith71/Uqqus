"""Guild flair, wiki and events wired into the guild page (board.html), the guild JSON, the mod log and the docs."""
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def read(*parts):
    return ROOT.joinpath(*parts).read_text(encoding="utf-8")


def test_the_guild_page_draws_tabs_resources_pinned_and_next_event():
    board = read("ruqqus", "templates", "board.html")
    feedtop = board.split("{% block feedtop %}", 1)[1].split("{% endblock %}", 1)[0]
    assert feedtop.index("guild_tabs(b, v, \"posts\")") < feedtop.index("guild_resources(b, v)") < \
        feedtop.index("pinned_row(pinned_posts)") < feedtop.index("flair_filter(")
    assert '{% include "partials/guild_next_event.html" %}' in board
    for path in ("/mod/resources", "/wiki", "/events"):
        assert f'href="/+{{{{ b.name }}}}{path}"' in board
    route = read("ruqqus", "routes", "boards.py")
    assert "listing=feed_posts," in route and "pinned_posts=pinned_posts," in route
    assert '"data": [x.json for x in posts]' in route                     # the API keeps pinned posts first


def test_the_guild_json_and_the_mod_log():
    boards = read("ruqqus", "classes", "boards.py")
    for key in ("flairs", "require_flair", "wiki_enabled", "next_event"):
        assert f"data['{key}']" in boards
    assert "'going', 'interested')" in boards                              # counts, never who
    logs = read("ruqqus", "classes", "mod_logs.py")
    for kind in ("wiki_edit", "wiki_restore", "wiki_settings", "wiki_delete", "wiki_toggle", "resources_edit",
                 "event_create", "event_edit", "event_cancel"):
        assert f'"{kind}":{{' in logs, kind


def test_the_routes_are_registered_and_documented():
    init = read("ruqqus", "routes", "__init__.py")
    for name in ("guild_flair", "guild_wiki", "guild_events"):
        assert f"from .{name} import *" in init
    docs = read("CLAUDE.md")
    assert "## Guild flair, wiki and events (legacy app)" in docs
    for needle in ("2026-10-29_guild_flair.sql", "2026-10-30_guild_wiki.sql", "2026-10-31_guild_events.sql",
                   "Counts only, never who", "public guilds only"):
        assert needle in docs, needle
