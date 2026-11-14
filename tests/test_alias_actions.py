"""Actions on an alias: a guildmaster exiles or chat-mutes "Anonymous #3" without learning who it is (the Log's slice D3f, decision 24): the rules and the SQL
without Postgres, and the wiring of every place that lists, logs or lifts one. The real app is in tests/pg/test_log_d3f_aliases.py.

The promises: the server maps an anonymous item to its author only for an anonymous item made in THIS guild whose author has a number in its thread; the Log's
entry for an action on an alias carries the number and never an account below staff (no `target_user_id`, so the `to=` filter and the account's own log can't
find it; the account is `subject_user_id`, staff and appeal only); the appeal is the account's; lifting by name never finds an alias row and the list that draws
it never names it."""
from pathlib import Path
from types import SimpleNamespace

import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.orm import Session

from ruqqus.helpers import action_log as log, alias_actions, anon_aliases, log_appeals as appeals, guild_channel_store as store

import log_tables

ROOT = Path(__file__).resolve().parent.parent
NOW = 1_793_000_000
BOARD = 3


def read(*parts):
    return ROOT.joinpath(*parts).read_text(encoding="utf-8")


def create_statements(migration):
    sql = "\n".join(line for line in read("scripts", "migrations", migration).splitlines() if not line.strip().startswith("--"))
    return [part.strip().replace("public.", "").replace("IF NOT EXISTS ", "") for part in sql.split(";") if part.strip().startswith("CREATE")]


@pytest.fixture
def db():
    engine = create_engine("sqlite://")
    with engine.begin() as conn:
        for statement in log_tables.migration_statements(read("scripts", "migrations", "2026-12-09_action_log.sql")):
            conn.execute(text(statement))
        log_tables.apply(conn, modactions=True)
        for statement in create_statements("2027-01-05_anon_aliases.sql"):
            conn.execute(text(statement))
        conn.execute(text("CREATE TABLE submissions (id INTEGER PRIMARY KEY, author_id INTEGER, repost_id INTEGER DEFAULT 0, is_anonymous BOOLEAN DEFAULT 0)"))
        conn.execute(text("CREATE TABLE comments (id INTEGER PRIMARY KEY, author_id INTEGER, is_anonymous BOOLEAN DEFAULT 0)"))
        conn.execute(text("CREATE TABLE forwardrels (id INTEGER PRIMARY KEY, forward_submission_id INTEGER, forwarded_by_id INTEGER)"))
        conn.execute(text("CREATE TABLE comment_forwardrels (id INTEGER PRIMARY KEY, promoted_submission_id INTEGER, promoted_by_id INTEGER)"))
        conn.execute(text("CREATE TABLE boards (id INTEGER PRIMARY KEY, is_private BOOLEAN DEFAULT 0, is_circle BOOLEAN DEFAULT 0)"))
        conn.execute(text("CREATE TABLE users (id INTEGER PRIMARY KEY, admin_level INTEGER DEFAULT 0, is_banned INTEGER DEFAULT 0, "
                          "unban_utc INTEGER DEFAULT 0, is_deleted BOOLEAN DEFAULT 0)"))
        conn.execute(text("CREATE TABLE mods (id INTEGER PRIMARY KEY, user_id INTEGER, board_id INTEGER, accepted BOOLEAN DEFAULT 1, "
                          "invite_rescinded BOOLEAN DEFAULT 0, perm_full BOOLEAN DEFAULT 0, perm_content BOOLEAN DEFAULT 0, perm_access BOOLEAN DEFAULT 0)"))
        conn.execute(text("CREATE TABLE chatbans (id INTEGER PRIMARY KEY AUTOINCREMENT, user_id INTEGER, board_id INTEGER, created_utc INTEGER, "
                          "banning_mod_id INTEGER, alias_post_id INTEGER, alias_n INTEGER)"))
        conn.execute(text("INSERT INTO boards VALUES (1, 0, 0), (3, 0, 0), (4, 0, 0)"))
        conn.execute(text("INSERT INTO users (id, admin_level) VALUES (1, 0), (10, 0), (11, 0), (20, 3), (30, 0), (31, 0), (32, 0)"))
        conn.execute(text("INSERT INTO mods (user_id, board_id, perm_access) VALUES (10, 3, 1)"))
        conn.execute(text("INSERT INTO mods (user_id, board_id, perm_access) VALUES (11, 3, 1)"))
        conn.execute(text("INSERT INTO submissions (id, author_id) VALUES (7, 32)"))
        conn.execute(text("INSERT INTO comments (id, author_id, is_anonymous) VALUES (50, 30, 1)"))
    with Session(engine) as session:
        yield session


def item(**kw):
    """An anonymous comment (or a post) as `resolve` reads it."""
    base = dict(is_anonymous=True, original_board_id=BOARD, author_id=30, id=50, parent_submission=7, post=SimpleNamespace(id=7, repost_id=0))
    base.update(kw)
    return SimpleNamespace(**base)


# --- the rules: what the server maps, and what it never does --------------------------------------------------------------------------

def test_an_anonymous_item_of_this_guild_maps_to_its_author_the_thread_and_the_number(db):
    anon_aliases.alias_for(db, ("post", 7), 31)                         # someone else became anonymous first
    anon_aliases.alias_for(db, ("post", 7), 30)
    assert alias_actions.resolve(db, BOARD, item()) == (30, 7, 2)
    post = SimpleNamespace(is_anonymous=True, original_board_id=BOARD, author_id=31, id=7, repost_id=0)
    assert alias_actions.resolve(db, BOARD, post) == (31, 7, 1), "a post resolves too: the thread is its own"
    copy = SimpleNamespace(is_anonymous=True, original_board_id=BOARD, author_id=31, id=99, repost_id=7)
    assert alias_actions.resolve(db, BOARD, copy) == (31, 7, 1), "a guild copy shares its primary post's thread"


@pytest.mark.parametrize("change", [
    {"is_anonymous": False},                    # a named item: nothing to act on, and nothing said
    {"original_board_id": 4},                   # made in another guild: not this guildmaster's to act on
    {"author_id": 99},                          # an author with no number in the thread
    {"post": None},                             # a comment with no post (a system notice)
])
def test_nothing_resolves_for_a_named_item_another_guilds_item_an_unnumbered_author_or_no_post(db, change):
    anon_aliases.alias_for(db, ("post", 7), 30)
    assert alias_actions.resolve(db, BOARD, item(**change)) is None


def test_nothing_resolves_for_nothing(db):
    assert alias_actions.resolve(db, BOARD, None) is None


def test_the_label_is_the_number_the_thread_draws():
    assert alias_actions.label(3) == "Anonymous #3" and alias_actions.label("12") == "Anonymous #12"


# --- the Log: the number is public, the account is the staff's and the appeal's -------------------------------------------------------

def exile_by_alias(db, actor=10, subject=30, number=2, reason="Harassing members"):
    db.execute(text("INSERT INTO modactions (user_id, board_id, kind, _note, created_utc, target_comment_id) VALUES (:a, :b, 'exile_alias', :n, :t, 50)"),
               {"a": actor, "b": BOARD, "n": alias_actions.label(number), "t": NOW})
    modaction = db.execute(text("SELECT MAX(id) FROM modactions")).scalar()
    return log.annotate(db, modaction, rule="guild_rule", reason=reason, alias_n=number, subject_user_id=subject, staff_note="by alias, the account is @someone")


def test_an_exile_by_alias_is_a_sanction_with_the_number_and_never_the_account(db):
    entry = exile_by_alias(db)
    row = db.execute(text("SELECT log_type, action, actor_role, target_user_id, alias_n, subject_user_id, reason, audience FROM log_entries WHERE id = :e"), {"e": entry}).mappings().one()
    assert (row["log_type"], row["action"], row["actor_role"], row["audience"]) == ("sanction", "exile_alias", "guildmaster", 0)
    assert row["target_user_id"] is None and row["alias_n"] == 2 and row["subject_user_id"] == 30
    assert row["reason"] == "Harassing members", "the written reason, and not the label the guild log keeps as its note"


def test_without_a_reason_the_label_is_never_taken_for_one(db):
    entry = exile_by_alias(db, reason="")
    assert db.execute(text("SELECT reason FROM log_entries WHERE id = :e"), {"e": entry}).scalar() == ""


def test_below_staff_a_reader_gets_the_number_and_no_account(db):
    exile_by_alias(db)
    rows, _ = log.fetch(db, level=0)
    (public,) = rows
    assert public["alias_n"] == 2 and public.get("target_user_id") is None
    assert "subject_user_id" not in public and "staff_note" not in public, "the staff's columns are never sent to the public"
    staff_rows, _ = log.fetch(db, level=3)
    assert staff_rows[0]["subject_user_id"] == 30 and "@someone" in staff_rows[0]["staff_note"]


def test_the_by_account_filter_and_the_actor_filter_cannot_find_it(db):
    exile_by_alias(db)
    for kw in ({"target_user_id": 30}, {"target_user_id": 31}):
        assert log.fetch(db, level=0, **kw)[0] == [], kw
    assert len(log.fetch(db, level=0, actor_id=10)[0]) == 1, "the guildmaster who acted is named as ever"


def test_the_public_columns_name_the_number_and_never_the_subject():
    assert "alias_n" in log.PUBLIC_COLUMNS and "subject_user_id" not in log.PUBLIC_COLUMNS and "subject_user_id" in log.STAFF_COLUMNS


def test_the_four_kinds_are_alias_sanctions():
    assert set(log.ALIAS_ACTIONS) == {"exile_alias", "unexile_alias", "chatban_alias", "unchatban_alias"}
    assert set(log.ALIAS_ACTIONS) <= log._SANCTION


# --- the appeal is the account's ------------------------------------------------------------------------------------------------------

def test_only_the_account_behind_the_alias_may_appeal_and_it_is_decided_by_the_guilds_access_guildmasters(db):
    entry = appeals.entry_of(db, exile_by_alias(db))
    assert appeals.subject_of(db, entry) == 30
    assert appeals.can_appeal(db, entry, 30, NOW) is None
    for stranger in (1, 10, 20, 31, 32):
        assert appeals.can_appeal(db, entry, stranger, NOW) is not None, stranger
    assert appeals.APPEALABLE["exile_alias"] == ("unexile_alias", "access") and appeals.permission_for(entry) == "access"
    assert appeals.board_for(db, entry) == BOARD


def test_an_exile_by_alias_is_undone_only_by_the_lifting_of_that_account_in_that_guild(db):
    entry = appeals.entry_of(db, exile_by_alias(db))
    assert appeals.is_undone(db, entry) is False
    db.execute(text("INSERT INTO log_entries (created_utc, log_type, action, actor_id, actor_role, audience, board_id, alias_n, subject_user_id) "
                    "VALUES (:n, 'sanction', 'unexile_alias', 11, 'guildmaster', 0, 4, 2, 30)"), {"n": NOW + 1})     # the same account, another guild
    db.execute(text("INSERT INTO log_entries (created_utc, log_type, action, actor_id, actor_role, audience, board_id, alias_n, subject_user_id) "
                    "VALUES (:n, 'sanction', 'unexile_alias', 11, 'guildmaster', 0, :b, 2, 31)"), {"n": NOW + 2, "b": BOARD})   # this guild, another account
    assert appeals.is_undone(db, entry) is False
    db.execute(text("INSERT INTO log_entries (created_utc, log_type, action, actor_id, actor_role, audience, board_id, alias_n, subject_user_id) "
                    "VALUES (:n, 'sanction', 'unexile_alias', 11, 'guildmaster', 0, :b, 2, 30)"), {"n": NOW + 3, "b": BOARD})
    assert appeals.is_undone(db, entry) is True


# --- the chat mutes' store: a list that names nobody it was not asked to name -----------------------------------------------------------

def test_a_mute_by_alias_is_listed_by_number_and_lifted_only_from_that_list(db):
    store.mute(db, BOARD, 31, 10, NOW)                                      # by name
    db.execute(text("INSERT INTO chatbans (user_id, board_id, created_utc, banning_mod_id, alias_post_id, alias_n) VALUES (30, :b, :n, 10, 7, 2)"), {"b": BOARD, "n": NOW})
    assert store.muted_ids(db, BOARD) == [31, 30]
    assert store.muted_ids(db, BOARD, by_name_only=True) == [31], "the channel manager's list of names never holds the alias"
    (listed,) = store.muted_aliases(db, BOARD)
    assert listed[1] == 2 and store.alias_muted(db, BOARD, 30) and not store.alias_muted(db, BOARD, 31)
    assert store.unmute(db, BOARD, 30) is False, "lifting by name finds no alias mute"
    assert store.is_muted(db, BOARD, 30)
    assert store.unmute(db, BOARD, 31) is True


# --- the wiring ----------------------------------------------------------------------------------------------------------------------

def test_the_routes_are_limited_checked_and_ordered_as_every_guildmaster_route():
    source = read("ruqqus", "routes", "alias_actions_pages.py")
    for route, perm in (("mod_exile_alias", "access"), ("mod_chatban_alias", "content")):
        assert f'@limiter.limit(alias_actions.PER_HOUR, key_func=member_limit_key)\n@auth_required\n@is_guildmaster("{perm}")\n@validate_formkey\ndef {route}(' in source, route
    for route, perm in (("mod_unexile_alias", "access"), ("mod_unchatban_alias", "content")):
        assert f'@auth_required\n@is_guildmaster("{perm}")\n@validate_formkey\ndef {route}(' in source, route
    assert 'from .alias_actions_pages import *' in read("ruqqus", "routes", "__init__.py")
    assert alias_actions.PER_HOUR == "20/hour"


def test_every_refusal_answers_the_one_accepted_message_and_a_target_is_checked_before_anything_is_written():
    source = read("ruqqus", "routes", "alias_actions_pages.py")
    exile = source[source.index("def mod_exile_alias"):source.index('@app.route("/mod/unexile_alias')]
    assert exile.index("_target(board, v)") < exile.index("BanRelationship(") and "return _accepted()" in exile
    target = source[source.index("def _target"):source.index("def _logged")]
    for refusal in ("board.has_mod(user)", "board.has_contributor(user)", "admin_level", "user.id == v.id", "is_deleted"):
        assert refusal in target, refusal
    assert "abort(" not in target and "jsonify(" not in target, "a refusal never has an answer of its own"


def test_the_lists_never_name_an_alias_row_and_the_menus_offer_only_the_number():
    for template in ("bans.html", "chatbans.html"):
        page = read("ruqqus", "templates", "guild", template)
        assert "ban.alias_n" in page and "ban.alias_thread" in page, template
    channels = read("ruqqus", "templates", "guild", "channels.html")
    assert "muted_aliases" in channels and "unchatban_alias" in channels
    for template in ("comments.html", "submission.html"):
        page = read("ruqqus", "templates", template)
        assert "exile_alias" in page and "chatban_alias" in page and "author_of(" in page, template
    models = read("ruqqus", "classes", "board_relationships.py")
    assert models.count('data.pop(key, None)') == 2 and models.count('alias_actions.label(self.alias_n)') == 2


def test_the_name_based_routes_neither_confirm_nor_lift_an_alias_row():
    boards = read("ruqqus", "routes", "boards.py")
    assert "if active and active.alias_n:" in boards and "if not x or x.alias_n:" in boards
    assert "if not x or x.alias_n" in boards[boards.index("def mod_unban_bid_user"):boards.index("def mod_unban_bid_user") + 2500]
    channels = read("ruqqus", "routes", "guild_channels.py")
    assert "store.alias_muted(g.db, board.id, target.id)" in channels and "by_name_only=True" in channels
    assert "alias_n IS NULL" in read("ruqqus", "helpers", "guild_channel_store.py")


def test_the_staff_columns_and_the_migration_agree():
    sql = read("scripts", "migrations", "2027-01-07_alias_actions.sql")
    for column in ("bans.alias_post_id", "bans.alias_n", "chatbans.alias_post_id", "chatbans.alias_n", "log_entries.alias_n", "log_entries.subject_user_id"):
        table, name = column.split(".")
        assert f"ALTER TABLE public.{table} ADD COLUMN IF NOT EXISTS {name} integer;" in sql, column
    assert "IF NOT EXISTS" in sql and "2027-01-07_alias_actions.sql" in read("schema.sql")
