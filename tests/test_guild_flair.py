"""Guild flair (helpers/guild_flair.py, helpers/guild_flair_store.py, routes/guild_flair.py): a guild's flairs, the flair
on a guild's copy of a post, required flair, filtering the guild by flair, the flair: search operator. The promises:
colours are a fixed palette with a rule in both stylesheets; one guild's guildmasters never touch another's flairs;
only the forwarder or a content guildmaster sets a post's flair; a flair from another guild is refused; deleting a flair
never deletes a post; the filter is part of the listing's cache key."""
import re
from pathlib import Path

import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.orm import Session

from ruqqus.helpers import guild_flair as rules
from ruqqus.helpers import guild_flair_store as store

ROOT = Path(__file__).resolve().parent.parent
NOW = 1_800_000_000


def read(*parts):
    return ROOT.joinpath(*parts).read_text(encoding="utf-8")


# --- the rules -------------------------------------------------------------------------------------------------------

def test_names_and_colours():
    assert rules.clean_name("  Question   time ") == "Question time" and rules.clean_name("a\tb") == "a b"
    for bad in ("", "   ", "x" * (rules.NAME_MAX + 1), "<b>bold</b>", "{c:red}", "bell\x07here"):
        with pytest.raises(rules.Refused):
            rules.clean_name(bad)
    assert rules.clean_colour("") == rules.DEFAULT_COLOUR and rules.clean_colour("Teal") == "teal"
    with pytest.raises(rules.Refused):
        rules.clean_colour("#ff0000")                                            # never free CSS
    assert rules.make_refusal(rules.MAX_FLAIRS - 1) is None and rules.make_refusal(rules.MAX_FLAIRS)


def test_the_filter_keeps_only_this_guilds_flairs_sorted():
    assert rules.parse_filter(["7", "3", "7", "99", "abc", "٣", ""], known_ids=[3, 7, 8]) == (3, 7)
    assert rules.parse_filter([], known_ids=[1]) == ()
    many = [str(i) for i in range(1, 40)]
    assert len(rules.parse_filter(many, known_ids=range(1, 40))) == rules.MAX_FILTER


def test_who_sets_a_posts_flair_and_what_a_forward_may_carry():
    assert rules.may_set(5, 5, False) and rules.may_set(9, 5, True)
    assert not rules.may_set(9, 5, False) and not rules.may_set(None, 5, True)
    assert rules.forward_refusal(False, None, [1, 2], "pets") is None
    assert rules.forward_refusal(False, 3, [1, 2], "pets") == "That flair isn't one of +pets's."
    assert rules.forward_refusal(True, None, [1, 2], "pets") == "+pets needs a flair on every forward. Pick one."
    assert rules.forward_refusal(True, 2, [1, 2], "pets") is None
    assert rules.forward_refusal(True, None, [], "pets") is None              # required, but it has no flairs yet


# --- the SQL ---------------------------------------------------------------------------------------------------------

def _tables():
    sql = read("scripts", "migrations", "2026-10-29_guild_flair.sql")
    sql = "\n".join(line for line in sql.splitlines() if not line.strip().startswith("--"))
    for s in (s.strip() for s in sql.split(";")):
        if s.startswith("CREATE"):
            yield (s.replace("public.", "").replace("IF NOT EXISTS ", "")
                   .replace("SERIAL PRIMARY KEY", "INTEGER PRIMARY KEY AUTOINCREMENT"))


@pytest.fixture
def db():
    engine = create_engine("sqlite://")
    with engine.begin() as conn:
        conn.execute(text("CREATE TABLE boards (id INTEGER PRIMARY KEY, require_flair BOOLEAN DEFAULT 0)"))
        conn.execute(text("CREATE TABLE submissions (id INTEGER PRIMARY KEY, board_id INTEGER, guild_flair_id INTEGER)"))
        for statement in _tables():
            conn.execute(text(statement))
    with Session(engine) as session:
        yield session


def test_a_guilds_flairs_in_order_unique_names_and_moves(db):
    a = store.add(db, 1, "Question", "blue", 0, NOW)
    b = store.add(db, 1, "News", "red", 0, NOW)
    assert store.add(db, 1, "question", "green", 0, NOW) is None           # the name is taken, ignoring case
    assert store.add(db, 1, "QUESTIÖN", "green", 0, NOW) is not None        # not the same name
    other = store.add(db, 2, "Question", "blue", 0, NOW)                    # another guild may use it
    assert other is not None and [r[2] for r in store.flairs_of(db, 1)] == ["Question", "News", "QUESTIÖN"]
    store.move(db, 1, b, -1)
    assert [r[0] for r in store.flairs_of(db, 1)][:2] == [b, a]
    assert store.edit(db, 1, a, "News", "blue", 0) is False                 # renaming onto another's name
    assert store.edit(db, 1, a, "Questions", "teal", 1) is True
    assert tuple(store.flair(db, 1, a))[2:] == ("Questions", "teal", 2, 1)
    assert store.flair(db, 2, a) is None                                    # one guild never reaches another's flair
    store.edit(db, 2, a, "hijack", "red", 0)
    assert store.flair(db, 1, a)[2] == "Questions"


def test_deleting_a_flair_clears_it_from_posts_and_deletes_no_post(db):
    f = store.add(db, 1, "Meme", "pink", 0, NOW)
    db.execute(text("INSERT INTO submissions (id, board_id, guild_flair_id) VALUES (10, 1, :f), (11, 1, NULL)"), {"f": f})
    store.delete(db, 2, f)                                                   # another guild's id: nothing happens
    assert store.flair(db, 1, f) is not None
    store.delete(db, 1, f)
    assert store.flair(db, 1, f) is None
    assert db.execute(text("SELECT COUNT(*), COUNT(guild_flair_id) FROM submissions")).fetchone() == (2, 0)


def test_attaching_a_pages_flairs(db):
    from types import SimpleNamespace as NS
    f = store.add(db, 1, "Guide", "green", 2, NOW)
    other = store.add(db, 2, "Elsewhere", "red", 0, NOW)
    posts = [NS(board_id=1, guild_flair_id=f), NS(board_id=1, guild_flair_id=None), NS(board_id=1, guild_flair_id=other)]
    store.attach(db, posts)
    assert posts[0].guild_flair == {"id": f, "name": "Guide", "colour": "green", "severity": 2}
    assert posts[1].guild_flair is None and posts[2].guild_flair is None    # another guild's flair is never shown


# --- the wiring ------------------------------------------------------------------------------------------------------

def test_every_colour_has_a_rule_in_both_stylesheets():
    for name in ("main.scss", "main_dark.scss"):
        css = read("ruqqus", "assets", "style", name)
        palette = re.search(r"@each \$name, \$bg, \$fg in \(([^{]+)\)\s*\{\s*\.flair-#\{\$name\}", css)
        assert palette, name
        colours = re.findall(r"([a-z]+) #[0-9a-f]{6} #[0-9a-f]{6}", palette.group(1))
        assert tuple(colours) == rules.COLOURS, name
        assert ".flair-chip {" in css and ".flair-filter {" in css


def test_routes_guard_every_change():
    routes = read("ruqqus", "routes", "guild_flair.py")
    found = re.findall(r'@app\.route\("([^"]+)", methods=\["(\w+)"\]\)\n((?:@[^\n]+\n)+)def (\w+)', routes)
    names = {name for *_, name in found}
    assert names == {"board_flair_settings", "mod_flair_add", "mod_flair_edit", "mod_flair_move", "mod_flair_delete",
                     "mod_flair_require", "guild_flairs_api", "post_flair_set"}
    for path, method, decorators, name in found:
        if method == "POST":
            assert "@validate_formkey" in decorators, name
        if name.startswith("mod_") or name == "board_flair_settings":
            assert '@is_guildmaster("config")' in decorators, name
    post_set = routes.split("def post_flair_set(", 1)[1].split("\ndef ", 1)[0]
    assert "flair_rules.may_set(v.id, _forwarder_id(post), is_content_gm)" in post_set
    assert "post.in_private_guild_for(v)" in post_set and "flair_store.ids_of(g.db, board.id)" in post_set
    api = routes.split("def guild_flairs_api(", 1)[1].split("\ndef ", 1)[0]
    assert "board.can_view(v)" in api and "board.is_banned" in api
    assert "html.escape(note)" in routes                                    # the mod log draws notes as HTML


def test_the_listing_the_forward_and_the_composer():
    boards = read("ruqqus", "classes", "boards.py")
    assert "v=None, flairs=(), **kwargs):" in boards                         # an argument: part of the cache key
    assert "Submission.guild_flair_id.in_(list(flairs))" in boards
    page = read("ruqqus", "routes", "boards.py")
    assert 'guild_flair.parse_filter(request.args.getlist("flair"), [f[0] for f in flair_rows])' in page
    assert "flairs=chosen_flairs," in page
    posts = read("ruqqus", "routes", "posts.py")
    forward = posts.split("def forward_post(", 1)[1].split("\n@app.route", 1)[0]
    assert forward.index("flair_rules.forward_refusal(target.require_flair") < forward.index("create_forward_post(")
    assert "flair_rules.COMPOSER_REQUIRED.format(guild=target.name)" in posts
    assert "guild_flair_store.attach(g.db, posts)" in read("ruqqus", "helpers", "get.py")
    for name in ("submission_listing.html", "submission.html"):
        t = read("ruqqus", "templates", name)
        assert "{{ flair_chip(flair_of(p)) }}" in t and "{{ flair_item(p, v) }}" in t and "{{ flair_button(p, v) }}" in t
    assert "{{ flair_filter(b, guild_flairs or [], chosen_flairs or (), sort_method, time_filter) }}" in read(
        "ruqqus", "templates", "board.html")
    assert 'id="forward-flair"' in read("ruqqus", "templates", "forward_post_modal.html")
    assert 'name="guild_flair"' in read("ruqqus", "templates", "partials", "inline_composer.html")
    script = read("ruqqus", "assets", "js", "guild_flair.js")
    assert "innerHTML" not in script and "option.textContent = f.name;" in script


def test_the_schema_mirrors_the_migration():
    migration, schema = read("scripts", "migrations", "2026-10-29_guild_flair.sql"), read("schema.sql")
    assert "CREATE TABLE IF NOT EXISTS public.guild_flairs" in migration and "CREATE TABLE public.guild_flairs (" in schema
    assert "guild_flairs_name_key ON public.guild_flairs (board_id, lower(name))" in schema
    assert "    guild_flair_id integer," in schema and "    require_flair boolean DEFAULT false NOT NULL," in schema


def test_a_bot_forward_refused_for_flair_posts_again_without_forwarding():
    """Audit G-02: the composer's refusal was a 409, which the bot runner reads as "already posted", so the item
    vanished. A 403 naming the guild is a guild refusal: posted again on the profile alone."""
    from ruqqus.helpers import bot_runner
    message = rules.COMPOSER_REQUIRED.format(guild="pets")
    assert bot_runner.classify(403, None, {"error": message}, True)[0] == "guild"
    posts = read("ruqqus", "routes", "posts.py")
    assert '"api": lambda fe=flair_error: ({"error": fe}, 403)}' in posts
