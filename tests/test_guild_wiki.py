"""Guild wikis and pinned resources (helpers/guild_wiki.py, helpers/guild_wiki_store.py, routes/guild_wiki.py). The
promises: who edits a page follows its level and never a suspended account, a bot, an exile or anyone in a block with
the founder (one message, the block never named); a locked page is guildmasters' only; every save is a revision and a
restore adds one (nothing lost); one guild never reaches another's page or resource; every read goes through the
guild's visibility; resource links are checked and leave with nofollow; a diff marks lines with a sign, not only a
colour."""
import re
from pathlib import Path

import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.orm import Session

from ruqqus.helpers import guild_wiki as rules
from ruqqus.helpers import guild_wiki_store as store

ROOT = Path(__file__).resolve().parent.parent
NOW = 1_800_000_000


def read(*parts):
    return ROOT.joinpath(*parts).read_text(encoding="utf-8")


# --- the rules -------------------------------------------------------------------------------------------------------

def test_addresses_titles_and_text():
    assert rules.clean_slug("  Getting Started ") == "getting-started" and rules.clean_slug("faq_2") == "faq-2"
    for bad in ("", "---", "x" * (rules.SLUG_MAX + 1), "../etc", "ünïcode", "a/b"):
        with pytest.raises(rules.Refused):
            rules.clean_slug(bad)
    assert rules.clean_title("", "getting-started") == "Getting started"
    for bad in ("<b>x</b>", "x" * (rules.TITLE_MAX + 1), "bell\x07"):
        with pytest.raises(rules.Refused):
            rules.clean_title(bad, "x")
    with pytest.raises(rules.Refused):
        rules.clean_body("x" * (rules.WIKI_MAX + 1))
    assert rules.clean_reason("  fixed   typo ") == "fixed typo" and len(rules.clean_reason("x" * 500)) == rules.REASON_MAX
    with pytest.raises(rules.Refused):
        rules.clean_level("admins")


def _edit(**over):
    args = dict(level=rules.MEMBERS, locked=False, is_content_gm=False, is_contributor=False, member_days=30,
                exiled=False, suspended=False, bot=False, blocked_with_founder=False, edits_today=0)
    args.update(over)
    return rules.edit_refusal(**args)


def test_who_edits_a_page():
    assert _edit() is None                                                   # a member of 30 days, members' page
    assert _edit(member_days=3) and _edit(member_days=None)                  # too new, or not a member
    assert _edit(member_days=None, is_contributor=True) is None              # approved members count as members
    assert _edit(level=rules.APPROVED) and _edit(level=rules.APPROVED, is_contributor=True) is None
    assert _edit(level=rules.MODS, is_contributor=True) and _edit(level=rules.MODS, is_content_gm=True) is None
    assert _edit(locked=True) and _edit(locked=True, is_content_gm=True) is None
    assert _edit(edits_today=rules.EDITS_PER_DAY) and _edit(edits_today=rules.EDITS_PER_DAY, is_content_gm=True) is None
    for bad in ("exiled", "suspended", "bot", "blocked_with_founder"):         # never, guildmaster or not
        assert _edit(is_content_gm=True, **{bad: True}) == rules.CANT_EDIT
    assert "block" not in rules.CANT_EDIT.lower()
    assert rules.page_refusal(rules.MAX_PAGES - 1) is None and rules.page_refusal(rules.MAX_PAGES)


def test_the_diff_and_resource_targets():
    assert rules.diff("a\nb\nc", "a\nB\nc") == [("same", "a"), ("removed", "b"), ("added", "B"), ("same", "c")]
    assert rules.diff("same", "same") == [("same", "same")]
    assert rules.clean_resource_target("link", "https://example.com/guide") == ("link", "https://example.com/guide")
    for bad in ("javascript:alert(1)", "https://user:pw@example.com", "ftp://example.com", ""):
        with pytest.raises(rules.Refused):
            rules.clean_resource_target("link", bad)
    assert rules.clean_resource_target("wiki", "Getting Started") == ("wiki", "getting-started")
    assert rules.clean_resource_target("post", "https://site.test/+pets/post/ab12/my-title") == ("post", "ab12")
    assert rules.clean_resource_target("post", "ab12") == ("post", "ab12")
    with pytest.raises(rules.Refused):
        rules.clean_resource_target("post", "not a post")
    with pytest.raises(rules.Refused):
        rules.clean_resource_target("script", "x")
    for bad in ("", "<i>x</i>", "x" * (rules.LABEL_MAX + 1)):
        with pytest.raises(rules.Refused):
            rules.clean_label(bad)
    assert rules.resources_refusal(rules.MAX_RESOURCES - 1) is None and rules.resources_refusal(rules.MAX_RESOURCES)


# --- the SQL ---------------------------------------------------------------------------------------------------------

def _tables():
    sql = read("scripts", "migrations", "2026-10-30_guild_wiki.sql")
    sql = "\n".join(line for line in sql.splitlines() if not line.strip().startswith("--"))
    for s in (s.strip() for s in sql.split(";")):
        if s.startswith("CREATE"):
            yield (s.replace("public.", "").replace("IF NOT EXISTS ", "")
                   .replace("SERIAL PRIMARY KEY", "INTEGER PRIMARY KEY AUTOINCREMENT"))


@pytest.fixture
def db():
    engine = create_engine("sqlite://")
    with engine.begin() as conn:
        conn.execute(text("CREATE TABLE boards (id INTEGER PRIMARY KEY)"))
        conn.execute(text("INSERT INTO boards (id) VALUES (1), (2)"))
        for statement in _tables():
            conn.execute(text(statement))
        # the columns added later (audit G-04)
        fixes = read("scripts", "migrations", "2026-11-02_audit_fixes.sql")
        for statement in fixes.split(";"):
            statement = "\n".join(l for l in statement.splitlines() if not l.strip().startswith("--")).strip()
            if statement.startswith("ALTER TABLE public.guild_"):
                conn.execute(text(statement.replace("public.", "").replace("IF NOT EXISTS ", "")))
    with Session(engine) as session:
        yield session


def test_revisions_history_and_restore(db):
    page = store.create_page(db, 1, "index", "Index", rules.MODS, NOW)
    assert store.create_page(db, 2, "index", "Theirs", rules.MODS, NOW)[1] == 2    # the same address in another guild
    first = store.save(db, 1, page[0], 7, "Index", "hello", "<p>hello</p>", 0, "", NOW)
    second = store.save(db, 1, page[0], 8, "Welcome", "hello world", "<p>hello world</p>", 1, "more", NOW + 60)
    page = store.page(db, 1, "index")
    assert page[5] == second and page[3] == "Welcome"
    assert [r[0] for r in store.revisions(db, page[0])] == [second, first]          # newest first, both kept
    old = store.revision(db, page[0], first)
    restored = store.save(db, 1, page[0], 9, page[3], old[3], old[4], old[5], "Restored", NOW + 120)
    assert len(store.revisions(db, page[0])) == 3 and store.page(db, 1, "index")[5] == restored
    assert store.edits_since(db, 1, 8, NOW) == 1 and store.edits_since(db, 2, 8, NOW) == 0
    store.remove_revision(db, page[0], first, NOW + 200)
    assert store.revision(db, page[0], first)[8] == NOW + 200                       # listed still, text taken down


def test_one_guild_never_reaches_anothers_page(db):
    page = store.create_page(db, 1, "rules", "Rules", rules.MEMBERS, NOW)
    store.set_settings(db, 2, page[0], rules.MODS, True)                            # another guild's id: no change
    assert store.page(db, 1, "rules")[4] == rules.MEMBERS
    store.save(db, 2, page[0], 5, "Hijacked", "x", "x", 0, "", NOW)
    assert store.page(db, 1, "rules")[3] == "Rules"
    store.delete_page(db, 2, page[0])
    assert store.page(db, 1, "rules") is not None
    store.set_settings(db, 1, page[0], rules.APPROVED, True)
    assert tuple(store.page(db, 1, "rules"))[4:8:3] == (rules.APPROVED, 1)
    store.delete_page(db, 1, page[0])
    assert store.page(db, 1, "rules") is None and store.page_count(db, 1) == 0


def test_resources_in_order_per_guild(db):
    store.add_resource(db, 1, "Guide", "link", "https://example.com")
    store.add_resource(db, 1, "Rules", "wiki", "rules")
    store.add_resource(db, 2, "Theirs", "post", "ab")
    a, b = [r[0] for r in store.resources_of(db, 1)]
    store.move_resource(db, 1, b, -1)
    assert [r[2] for r in store.resources_of(db, 1)] == ["Rules", "Guide"]
    store.delete_resource(db, 2, a)                                                 # another guild's id: no change
    assert len(store.resources_of(db, 1)) == 2
    store.delete_resource(db, 1, a)
    assert [r[2] for r in store.resources_of(db, 1)] == ["Rules"]
    with pytest.raises(Exception):
        db.execute(text("INSERT INTO guild_resources (board_id, label, kind, target, position) VALUES (1, 'x', 'js', 'x', 9)"))


# --- the wiring ------------------------------------------------------------------------------------------------------

def _routes(name):
    routes = read("ruqqus", "routes", name)
    return routes, re.findall(r'@app\.route\("([^"]+)", methods=\["(\w+)"\]\)\n((?:@[^\n]+\n)+)def (\w+)\(([^)]*)\):\n(.*?)(?=\n@app|\ndef |\Z)',
                              routes, re.S)


def test_every_wiki_route_reads_through_the_guild_and_guards_changes():
    routes, found = _routes("guild_wiki.py")
    assert len(found) >= 15
    for path, method, decorators, name, _args, body in found:
        if method == "POST":
            assert "@validate_formkey" in decorators, name
        if path.startswith("/+<guildname>/wiki"):
            assert "visible_guild(guildname, v)" in body, name
        if name in ("guild_wiki_restore", "guild_wiki_settings", "guild_wiki_delete"):
            assert "if not _content_gm(board, v):" in body, name
        if path.startswith("/mod/") or name == "board_resources_settings":
            assert '@is_guildmaster("config")' in decorators, name
    guard = routes.split("def visible_guild(", 1)[1].split("\ndef ", 1)[0]
    assert "board.can_view(v)" in guard and "board_hidden(board, v)" in guard and "board.is_banned" in guard
    assert "not board.wiki_enabled and not board.has_mod(v)" in guard
    save = routes.split("def guild_wiki_save(", 1)[1].split("\n@app", 1)[0]
    assert save.index("edit_refusal_for(board, page, v)") < save.index("wiki_store.save(")
    assert "_render(body)" in save and "_severity(body)" in save
    assert "html.escape(note)" in routes                                    # the mod log draws notes as HTML
    resources = routes.split("def resources_for(", 1)[1]
    assert "post.in_private_guild_for(v)" in resources and "board.wiki_enabled" in resources
    assert "filter_comment_html(" in routes


def test_wiki_templates_draw_safely():
    page = read("ruqqus", "templates", "guild", "wiki.html")
    assert page.count("| safe }}") == 1 and "{{ body_html | safe }}" in page
    history = read("ruqqus", "templates", "guild", "wiki_history.html")
    assert "| safe }}" not in history and "wiki-diff-sign" in history and "'added': '+'" in history
    tabs = read("ruqqus", "templates", "partials", "guild_tabs.html")
    assert 'rel="nofollow ugc noopener noreferrer"' in tabs
    base = read("ruqqus", "templates", "guild", "guild_tab_page.html")
    assert '<meta name="robots" content="noindex">' in base
    editor = read("ruqqus", "templates", "guild", "wiki_edit.html")
    assert '<div class="post-toolbar" data-target="wiki-body"></div>' in editor and 'class="post-preview d-none"' in editor
    assert "innerHTML" not in read("ruqqus", "assets", "js", "guild_pages.js")


def test_the_schema_mirrors_the_migration():
    migration, schema = read("scripts", "migrations", "2026-10-30_guild_wiki.sql"), read("schema.sql")
    for table in ("guild_wiki_pages", "guild_wiki_revisions", "guild_resources"):
        assert f"CREATE TABLE IF NOT EXISTS public.{table}" in migration and f"CREATE TABLE public.{table} (" in schema
    assert "    wiki_enabled boolean DEFAULT false NOT NULL," in schema
