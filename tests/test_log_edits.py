"""What a member edits, in the Log (slice D3b; the owner's decisions 4 and 19): one entry per tracked edit with the editor named unless the item is
anonymous (read when the page is drawn, so an item made anonymous later hides its earlier editors), filtering by an editor finds only those, an item's
consecutive edits fold into one line, and the edit window and the per-account limit are the ones the owner chose."""
from pathlib import Path

import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.orm import Session

from ruqqus.helpers import action_log as log, deletion, log_view

import log_tables

ROOT = Path(__file__).resolve().parent.parent
NOW = 1_793_000_000


def read(*parts):
    return ROOT.joinpath(*parts).read_text(encoding="utf-8")


@pytest.fixture
def db():
    engine = create_engine("sqlite://")
    with engine.begin() as conn:
        for statement in log_tables.migration_statements(read("scripts", "migrations", "2026-12-09_action_log.sql")):
            conn.execute(text(statement))
        log_tables.apply(conn)
        conn.execute(text("CREATE TABLE submissions (id INTEGER PRIMARY KEY, audience INTEGER DEFAULT 0, author_id INTEGER, repost_id INTEGER DEFAULT 0, is_anonymous BOOLEAN DEFAULT 0)"))
        conn.execute(text("CREATE TABLE comments (id INTEGER PRIMARY KEY, parent_submission INTEGER, author_id INTEGER, is_anonymous BOOLEAN DEFAULT 0)"))
        conn.execute(text("CREATE TABLE boards (id INTEGER PRIMARY KEY, is_private BOOLEAN DEFAULT 0, is_circle BOOLEAN DEFAULT 0)"))
        conn.execute(text("CREATE TABLE users (id INTEGER PRIMARY KEY, admin_level INTEGER DEFAULT 0)"))
        conn.execute(text("INSERT INTO boards VALUES (1, 0, 0), (2, 1, 0)"))
        conn.execute(text("INSERT INTO users VALUES (1, 0), (10, 0), (20, 3), (30, 0), (31, 0)"))
        conn.execute(text("INSERT INTO submissions (id, author_id, is_anonymous) VALUES (100, 30, 0), (101, 31, 1), (102, 30, 0)"))
        conn.execute(text("INSERT INTO comments (id, author_id, is_anonymous) VALUES (200, 30, 0), (201, 31, 1)"))
    with Session(engine) as session:
        yield session


def edit(db, post=None, comment=None, editor=30, summary="", when=NOW):
    action = "edit_post" if post else "edit_comment"
    return log.record(db, action, editor, role=log.AUTHOR, board_id=1, post_id=post, comment_id=comment, reason=summary, now=when)


def test_an_edit_is_an_entry_of_the_edit_type_and_has_a_sentence():
    assert log.type_of("edit_post") == log.type_of("edit_comment") == log.EDIT == "edit"
    table = read("ruqqus", "classes", "mod_logs.py")            # the app is never imported by the tests: the sentences are read from the source
    for action in ("edit_post", "edit_comment"):
        entry = table.split(f'"{action}": {{')[1].split("},\n")[0]
        assert '"icon": "fa-pen"' in entry and "N_('edited" in entry, action
    assert log.EDIT in log.TYPES and log_view.EDITED in log_view.TABS and log_view.parse_tab("edit") == "edit"


def test_the_editor_is_named_on_an_item_that_is_not_anonymous_and_never_on_one_that_is(db):
    named = edit(db, post=100, summary="Fixed a typo")
    hidden = edit(db, post=101, editor=31)
    comment_named, comment_hidden = edit(db, comment=200), edit(db, comment=201, editor=31)
    rows = {r["id"]: r for r in log.fetch(db, level=0)[0]}
    assert rows[named]["actor_id"] == 30 and rows[comment_named]["actor_id"] == 30
    assert rows[hidden]["actor_id"] is None and rows[comment_hidden]["actor_id"] is None
    assert {r["id"]: r["actor_id"] for r in log.fetch(db, level=3)[0]}[hidden] == 31, "staff see who"


def test_an_item_made_anonymous_later_hides_the_editors_of_its_earlier_edits(db):
    before = edit(db, post=100)
    assert {r["id"]: r["actor_id"] for r in log.fetch(db, level=0)[0]}[before] == 30
    db.execute(text("UPDATE submissions SET is_anonymous = 1 WHERE id = 100"))                 # "Remove my name", or a delete
    assert {r["id"]: r["actor_id"] for r in log.fetch(db, level=0)[0]}[before] is None
    assert log.fetch(db, level=0, actor_id=30)[0] == [], "and filtering by the editor no longer finds them"


def test_an_own_delete_and_a_post_authors_hide_never_name_the_author_whatever_the_item(db):
    delete = log.record(db, "author_delete_post", 30, role=log.AUTHOR, board_id=1, post_id=100, now=NOW)
    hide = log.record(db, "author_hide_comment", 30, role=log.AUTHOR, board_id=1, comment_id=200, now=NOW)
    rows = {r["id"]: r["actor_id"] for r in log.fetch(db, level=0)[0]}
    assert rows[delete] is None and rows[hide] is None
    assert log.fetch(db, level=0, actor_id=30)[0] == []


def test_filtering_by_an_editor_finds_only_the_edits_of_items_that_are_not_anonymous(db):
    mine, other, hidden = edit(db, post=100), edit(db, post=102, editor=10), edit(db, post=101, editor=31)
    assert [r["id"] for r in log.fetch(db, level=0, actor_id=30)[0]] == [mine]
    assert [r["id"] for r in log.fetch(db, level=0, actor_id=10)[0]] == [other]
    assert log.fetch(db, level=0, actor_id=31)[0] == [], "filtering by the account behind an anonymous item's edit finds nothing"
    admin = log.record(db, "ban_post", 20, role=log.ADMIN, board_id=1, post_id=100, now=NOW)
    assert admin not in [r["id"] for r in log.fetch(db, level=0, actor_id=20)[0]]
    assert {r["id"] for r in log.fetch(db, level=3, actor_id=31)[0]} == {hidden}, "staff may filter by anyone"


def test_the_tabs_posts_or_comments_filter_selects_the_action(db):
    post_edit, comment_edit = edit(db, post=100), edit(db, comment=200)
    both = [r["id"] for r in log.fetch(db, level=0, log_type="edit")[0]]
    assert set(both) == {post_edit, comment_edit}
    assert [r["id"] for r in log.fetch(db, level=0, log_type="edit", action=log_view.action_for("edit", "post"))[0]] == [post_edit]
    assert [r["id"] for r in log.fetch(db, level=0, log_type="edit", action=log_view.action_for("edit", "comment"))[0]] == [comment_edit]
    assert log_view.action_for("edit", None) is None and log_view.action_for("content", "post") is None
    assert log_view.parse_what("post") == "post" and log_view.parse_what("edit_post") is None


def test_a_private_guilds_edit_is_the_guilds_and_a_circle_items_is_staff_only(db):
    private = log.record(db, "edit_post", 30, role=log.AUTHOR, board_id=2, post_id=100, now=NOW)
    circle = log.record(db, "edit_post", 30, role=log.AUTHOR, board_id=1, post_id=100, floor=deletion.entry_floor(circle=True), now=NOW)
    assert log.fetch(db, level=0, log_type="edit")[0] == []
    assert [r["id"] for r in log.fetch(db, level=0, log_type="edit", member_boards=[2])[0]] == [private]
    assert {r["id"] for r in log.fetch(db, level=3, log_type="edit")[0]} == {private, circle}


def test_an_items_consecutive_edits_are_one_line_and_another_item_between_breaks_the_run():
    line = lambda n, kind, i: {"id": n, "item": (kind, i), "created_utc": n}
    rows = [line(6, "post", 1), line(5, "post", 1), line(4, "post", 1), line(3, "post", 2), line(2, "post", 1), line(1, "comment", 1)]
    grouped = log_view.group_edits(rows)
    assert [g["id"] for g in grouped] == [6, 3, 2, 1]
    assert [m["id"] for m in grouped[0]["more"]] == [5, 4] and "more" not in grouped[1] and "more" not in grouped[3]
    assert log_view.group_edits([]) == [] and rows[0].get("more") is None, "the input is not changed"
    assert log_view.group_edits([{"id": 1, "created_utc": 1}, {"id": 2, "created_utc": 2}])[1]["id"] == 2, "a line with no item is never folded"


def test_the_edit_window_is_the_grace_window_and_edits_are_limited_per_account():
    posts, comments = read("ruqqus", "routes", "posts.py"), read("ruqqus", "routes", "comments.py")
    assert deletion.GRACE_SECONDS == 300 and deletion.EDITS_PER_HOUR == "30/hour"
    assert "tracked = now - primary.created_utc > deletion.GRACE_SECONDS" in posts and "if now - c.created_utc > deletion.GRACE_SECONDS:" in comments
    assert "> 90" not in posts.split("def apply_post_edit(")[1].split("def ")[0] and "now - c.created_utc > 90" not in comments
    for source, route in ((posts, "def edit_post("), (comments, "def edit_comment(")):
        head = source.split(route)[0].rsplit("@app.route", 1)[1]
        assert "@limiter.limit(deletion.EDITS_PER_HOUR, key_func=member_limit_key)" in head, route      # per account, not per address
    assert 'record_quietly(\n                g.db, "edit_post", v.id, role=action_log.AUTHOR' in posts and "one entry" in posts.lower()
    assert 'g.db, "edit_comment", v.id, role=action_log.AUTHOR' in comments


def test_the_migration_and_schema_carry_the_edit_type():
    migration, schema = read("scripts", "migrations", "2027-01-02_log_edits.sql"), read("schema.sql")
    assert "'edit'" in migration and "'deletion', 'edit'" in schema and "('2027-01-02_log_edits.sql')" in schema
    assert "pg_get_constraintdef(oid) LIKE '%''edit''%'" in migration, "widened only while it lacks the type, so any order of re-runs leaves it whole"
