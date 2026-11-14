"""The ban reveal (the Log plan's decisions 10, 17, 18 and 27; slice D3d): helpers/log_evidence.py on SQLite with the tables built from the migrations.

The promises: only the banned account's own items can be cited, and never one with a legal-level record, one erased inside the grace window or one in a
private place (those are counted, not listed); nothing shows until a different admin approves, except the level-4 owner who is the only admin; a
declined or unapproved reveal never shows; reversing the ban withdraws it; pointer-stripping never touches the evidence, so deleting the account or
taking your name off afterwards doesn't undo it; and what the Log draws is the author's username only for an item that is cited, approved and not withdrawn."""
from pathlib import Path

import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.orm import Session

from ruqqus.helpers import anonymise, log_evidence as ev, mass_edit

import log_tables

ROOT = Path(__file__).resolve().parent.parent
NOW = 1_793_000_000


def read(*parts):
    return ROOT.joinpath(*parts).read_text(encoding="utf-8")


def statements(sql):
    sql = "\n".join(line for line in sql.splitlines() if not line.strip().startswith("--"))
    return [part.strip().replace("public.", "").replace("IF NOT EXISTS ", "").replace("bigserial PRIMARY KEY", "INTEGER PRIMARY KEY AUTOINCREMENT")
            for part in sql.split(";") if part.strip().startswith("CREATE")]


@pytest.fixture
def db():
    engine = create_engine("sqlite://")
    with engine.begin() as conn:
        for statement in log_tables.migration_statements(read("scripts", "migrations", "2026-12-09_action_log.sql")):
            conn.execute(text(statement))
        log_tables.apply(conn, modactions=True)
        conn.execute(text("CREATE TABLE boards (id INTEGER PRIMARY KEY, is_private BOOLEAN DEFAULT 0, is_circle BOOLEAN DEFAULT 0)"))
        conn.execute(text("CREATE TABLE users (id INTEGER PRIMARY KEY, username VARCHAR(30), admin_level INTEGER DEFAULT 0, is_banned INTEGER DEFAULT 0, "
                          "is_deleted BOOLEAN DEFAULT 0)"))
        conn.execute(text("CREATE TABLE submissions (id INTEGER PRIMARY KEY, author_id INTEGER, repost_id INTEGER DEFAULT 0, board_id INTEGER DEFAULT 1, "
                          "post_public BOOLEAN DEFAULT 1, audience INTEGER DEFAULT 0, is_anonymous BOOLEAN DEFAULT 0, deleted_utc INTEGER DEFAULT 0, "
                          "created_utc INTEGER DEFAULT 0)"))
        conn.execute(text("CREATE TABLE submissions_aux (id INTEGER PRIMARY KEY, title VARCHAR(500))"))
        conn.execute(text("CREATE TABLE comments (id INTEGER PRIMARY KEY, author_id INTEGER, parent_submission INTEGER, is_anonymous BOOLEAN DEFAULT 0, "
                          "deleted_utc INTEGER DEFAULT 0, created_utc INTEGER DEFAULT 0)"))
        conn.execute(text("CREATE TABLE comments_aux (id INTEGER PRIMARY KEY, body VARCHAR(10000))"))
        for statement in statements(read("scripts", "migrations", "2027-01-04_log_evidence.sql")):
            conn.execute(text(statement))
        conn.execute(text("INSERT INTO boards VALUES (1, 0, 0), (2, 1, 0), (3, 0, 1)"))
        conn.execute(text("INSERT INTO users (id, username, admin_level) VALUES (1, 'site', 0), (7, 'banned_one', 0), (8, 'other', 0), "
                          "(20, 'admin_a', 3), (21, 'admin_b', 3), (30, 'owner', 4)"))
        # account 7's items: 1 public anonymous post, 2 a post in a private guild, 3 a Circle post, 4 someone else's, 5 public post with a legal entry,
        # 6 a post erased inside the grace window, 7 another public post; comment 1 on 1 (public), comment 2 on 2 (private place), comment 3 by 8
        for item, author, board, public, audience in ((1, 7, 1, 1, 0), (2, 7, 2, 1, 0), (3, 7, 1, 0, 4), (4, 8, 1, 1, 0), (5, 7, 1, 1, 0), (6, 7, 1, 1, 0), (7, 7, 1, 1, 0)):
            conn.execute(text("INSERT INTO submissions (id, author_id, board_id, post_public, audience, is_anonymous, created_utc) "
                              "VALUES (:i, :a, :b, :p, :u, 1, :c)"), {"i": item, "a": author, "b": board, "p": public, "u": audience, "c": NOW - item})
            conn.execute(text("INSERT INTO submissions_aux (id, title) VALUES (:i, :t)"), {"i": item, "t": f"post {item}"})
        for item, author, post in ((1, 7, 1), (2, 7, 2), (3, 8, 1)):
            conn.execute(text("INSERT INTO comments (id, author_id, parent_submission, is_anonymous, created_utc) VALUES (:i, :a, :p, 1, :c)"),
                         {"i": item, "a": author, "p": post, "c": NOW - 100 - item})
            conn.execute(text("INSERT INTO comments_aux (id, body) VALUES (:i, :b)"), {"i": item, "b": f"comment {item}"})
        conn.execute(text("INSERT INTO log_entries (id, created_utc, log_type, action, actor_id, actor_role, audience, target_submission_id) "
                          "VALUES (50, :n, 'suppression', 'suppress_content', 30, 'admin', 3, 5)"), {"n": NOW})
        conn.execute(text("INSERT INTO log_snapshots (author_id, kind, target_submission_id, created_utc) VALUES (7, 'grace', 6, :n)"), {"n": NOW})
        conn.execute(text("INSERT INTO log_entries (id, created_utc, log_type, action, actor_id, actor_role, audience, target_user_id) "
                          "VALUES (90, :n, 'sanction', 'ban_user', 20, 'admin', 0, 7)"), {"n": NOW})
    with Session(engine) as session:
        yield session


def cite(db, items, entry=90, proposer=20, target=7):
    return ev.propose(db, entry, target, proposer, items, NOW)


# --- what can be cited -------------------------------------------------------------------------------------------------------------------

def test_only_the_banned_accounts_own_public_items_can_be_cited(db):
    problems = ev.refusals(db, 7, [("post", 1), ("post", 2), ("post", 3), ("post", 4), ("post", 5), ("post", 6), ("post", 99), ("comment", 1), ("comment", 2), ("comment", 3)])
    assert [k for k in problems] == [("post", 2), ("post", 3), ("post", 4), ("post", 5), ("post", 6), ("post", 99), ("comment", 2), ("comment", 3)]
    assert problems[("post", 2)] is ev.NOT_PUBLIC and problems[("post", 3)] is ev.NOT_PUBLIC and problems[("comment", 2)] is ev.NOT_PUBLIC
    assert problems[("post", 4)] is ev.NOT_THEIRS and problems[("comment", 3)] is ev.NOT_THEIRS and problems[("post", 99)] is ev.NOT_THEIRS
    assert problems[("post", 5)] is ev.LEGAL, "an item with a legal-level entry has no public line to attach to"
    assert problems[("post", 6)] is ev.ERASED, "erased inside the grace window: no public record"
    assert ("post", 1) not in problems and ("comment", 1) not in problems


def test_the_picker_lists_their_items_newest_first_and_only_counts_the_private_ones(db):
    rows, more, private = ev.items_of(db, 7)
    assert not more and private == 3, "post 2, post 3 and comment 2 are in private places: counted, never listed"
    listed = [(r["kind"], r["id"]) for r in rows]
    assert listed == [("post", 1), ("post", 5), ("post", 6), ("post", 7), ("comment", 1)], "newest first"
    assert ("post", 2) not in listed and ("post", 3) not in listed and ("comment", 2) not in listed and ("post", 4) not in listed and ("comment", 3) not in listed
    by = {(r["kind"], r["id"]): r for r in rows}
    assert by[("post", 1)]["citable"] and by[("comment", 1)]["citable"] and by[("post", 7)]["citable"]
    assert not by[("post", 5)]["citable"] and by[("post", 5)]["why"] is ev.LEGAL and by[("post", 6)]["why"] is ev.ERASED
    assert by[("post", 1)]["anonymous"] and by[("post", 1)]["title"] == "post 1" and by[("comment", 1)]["title"] == "comment 1"
    # a deleted account's items are reachable (decision 18): the picker is by the account's id, never by whether it is still open
    db.execute(text("UPDATE users SET is_deleted = 1 WHERE id = 7"))
    assert len(ev.items_of(db, 7)[0]) == 5 and [(r["kind"], r["id"]) for r in ev.items_of(db, 8)[0]] == [("post", 4), ("comment", 3)]


def test_the_picker_pages_by_25(db):
    for n in range(100, 140):
        db.execute(text("INSERT INTO submissions (id, author_id, created_utc, is_anonymous) VALUES (:i, 7, :c, 1)"), {"i": n, "c": NOW + n})
    first, more, _ = ev.items_of(db, 7, 1)
    second, more_after, _ = ev.items_of(db, 7, 2)
    assert more and not more_after
    assert len([r for r in first if r["kind"] == "post"]) == 25 and len([r for r in second if r["kind"] == "post"]) == 19, "46 posts, two of them private"
    assert {(r["kind"], r["id"]) for r in first}.isdisjoint({(r["kind"], r["id"]) for r in second})
    assert first[0]["created"] > second[0]["created"] and first[0]["id"] == 139
    assert ev.items_of(db, 7, 0)[0] == first, "a page before the first is the first"


def test_the_ticked_items_are_read_as_tokens_up_to_fifty_and_none_ticked_is_no_evidence():
    assert ev.parse_items([]) == ([], None) and ev.parse_items(["", "  "]) == ([], None) and ev.parse_items(None) == ([], None)
    assert ev.parse_items(["p1a", "c2b", "p1a"]) == ([("post", "1a"), ("comment", "2b")], None)
    many = [f"p{n:x}" for n in range(1, 52)]
    assert ev.parse_items(many) == ([], (ev.TOO_MANY, {"max": 50})) and ev.parse_items(many[:-1])[1] is None
    assert ev.parse_items(["zz!"])[1][0] is mass_edit.NOTHING, "only junk is a problem, not 'no evidence'"


# --- two admins ---------------------------------------------------------------------------------------------------------------------------

def test_a_different_admin_confirms_the_same_admin_never_does(db):
    cite(db, [("post", 1), ("comment", 1)])
    assert ev.decide(db, 90, 20, 3, True, NOW + 1) == (0, ev.SAME_ADMIN), "the banning admin can't confirm their own proposal"
    assert ev.revealed(db, [1], [1]) == {}, "and until a different admin does, nothing is shown"
    assert ev.decide(db, 90, 5, 2, True, NOW + 2) == (0, ev.NOT_ADMIN), "a moderator isn't an admin"
    assert ev.decide(db, 90, 21, 3, True, NOW + 3) == (2, None)
    assert ev.revealed(db, [1], [1]) == {("post", 1): "banned_one", ("comment", 1): "banned_one"}
    row = db.execute(text("SELECT status, decided_by, sole_admin FROM log_evidence WHERE post_id = 1")).first()
    assert tuple(row) == ("approved", 21, 0)
    assert ev.decide(db, 90, 21, 3, True, NOW + 4) == (0, ev.NOTHING_TO_DECIDE), "decided once"
    assert ev.revealed(db, [7], [3]) == {}, "an item not cited shows nothing"


def test_a_declined_reveal_never_shows(db):
    cite(db, [("post", 1)])
    assert ev.decide(db, 90, 21, 3, False, NOW + 1) == (1, None)
    assert ev.revealed(db, [1]) == {} and ev.listed(db, [90]) == {} and ev.author_of_entry(db, [90]) == {}
    assert ev.waiting(db) == []


def test_the_sole_admin_owner_may_confirm_their_own_and_a_sole_level_three_admin_may_not(db):
    # the only admin is the owner (level 4): the others are made ordinary
    db.execute(text("UPDATE users SET admin_level = 0 WHERE id IN (20, 21)"))
    cite(db, [("post", 1)], proposer=30)
    assert ev.another_admin_exists(db, 30) is False
    assert ev.decide(db, 90, 30, 4, True, NOW + 1) == (1, None)
    row = db.execute(text("SELECT status, sole_admin FROM log_evidence WHERE post_id = 1")).first()
    assert tuple(row) == ("approved", 1), "the staff log records a sole-admin approval"
    # a level-3 admin who is the only admin has no one to confirm: the reveal stays unpublished
    db.execute(text("DELETE FROM log_evidence"))
    db.execute(text("UPDATE users SET admin_level = 3 WHERE id = 20"))
    db.execute(text("UPDATE users SET admin_level = 0 WHERE id = 30"))
    cite(db, [("post", 1)], proposer=20)
    assert ev.decide(db, 90, 20, 3, True, NOW + 2) == (0, ev.SAME_ADMIN)
    assert ev.revealed(db, [1]) == {}


def test_the_owner_may_not_confirm_their_own_while_another_admin_exists_that_is_not_banned_or_deleted(db):
    cite(db, [("post", 1)], proposer=30)
    assert ev.another_admin_exists(db, 30) is True
    assert ev.decide(db, 90, 30, 4, True, NOW + 1) == (0, ev.SAME_ADMIN)
    db.execute(text("UPDATE users SET is_banned = 1 WHERE id = 20"))
    db.execute(text("UPDATE users SET is_deleted = 1 WHERE id = 21"))
    assert ev.another_admin_exists(db, 30) is False, "a banned or deleted admin is nobody to confirm"
    assert ev.decide(db, 90, 30, 4, True, NOW + 2) == (1, None)


def test_waiting_proposals_are_grouped_by_ban_oldest_first(db):
    cite(db, [("post", 1), ("comment", 1)])
    db.execute(text("INSERT INTO log_entries (id, created_utc, log_type, action, actor_id, actor_role, audience, target_user_id) "
                    "VALUES (91, :n, 'sanction', 'ban_user', 21, 'admin', 0, 8)"), {"n": NOW})
    ev.propose(db, 91, 8, 21, [("post", 4)], NOW + 5)
    waiting = ev.waiting(db)
    assert [w["entry_id"] for w in waiting] == [90, 91]
    assert waiting[0]["items"] == [("post", 1), ("comment", 1)] and waiting[0]["target_name"] == "banned_one" and waiting[0]["by_name"] == "admin_a"
    assert ev.propose(db, 90, 7, 20, [("post", 1)], NOW) == 0, "an item cited twice for one ban is one row"


# --- withdrawal, deletion and what the Log draws ----------------------------------------------------------------------------------------

def test_reversing_the_ban_withdraws_the_reveal_pending_or_approved(db):
    cite(db, [("post", 1)])
    ev.decide(db, 90, 21, 3, True, NOW + 1)
    cite(db, [("post", 7)], entry=91, proposer=20)
    assert ev.revealed(db, [1, 7]) == {("post", 1): "banned_one"}
    assert ev.withdraw(db, 7, NOW + 9) == 2
    assert ev.revealed(db, [1, 7]) == {} and ev.listed(db, [90]) == {}, "the Log draws Anonymous again"
    assert [r[0] for r in db.execute(text("SELECT status FROM log_evidence ORDER BY id"))] == ["withdrawn", "withdrawn"]
    assert ev.withdraw(db, 8, NOW) == 0, "somebody else's evidence is not touched"


def test_stripping_pointers_and_deleting_the_account_never_undo_a_reveal(db):
    cite(db, [("post", 1), ("comment", 1)])
    ev.decide(db, 90, 21, 3, True, NOW + 1)
    db.execute(text("INSERT INTO log_entries (id, created_utc, log_type, action, actor_id, actor_role, audience, target_user_id, target_submission_id) "
                    "VALUES (92, :n, 'sanction', 'exile_user', 3, 'guildmaster', 0, 7, 1)"), {"n": NOW})
    anonymise.strip_pointers(db, author_id=7, post_ids=[1], comment_ids=[1])
    assert db.execute(text("SELECT target_submission_id FROM log_entries WHERE id = 92")).scalar() is None, "the ordinary pointers do go"
    db.execute(text("UPDATE users SET is_deleted = 1 WHERE id = 7"))
    assert ev.revealed(db, [1], [1]) == {("post", 1): "banned_one", ("comment", 1): "banned_one"}, "a deleted account is named by its username (decision 18)"
    source = read("ruqqus", "helpers", "anonymise.py") + read("ruqqus", "helpers", "account_anonymise.py") + read("ruqqus", "helpers", "account_cleanup.py")
    assert "log_evidence" not in source.replace("# log_evidence", ""), "nothing that anonymises, strips or closes an account touches the evidence"


def test_the_bans_line_lists_its_approved_items_and_names_the_account(db):
    cite(db, [("post", 1), ("comment", 1)])
    assert ev.listed(db, [90, 91]) == {} and ev.author_of_entry(db, [90]) == {}, "nothing before a second admin agrees"
    ev.decide(db, 90, 21, 3, True, NOW + 1)
    assert ev.listed(db, [90, 91]) == {90: [("post", 1), ("comment", 1)]} and ev.author_of_entry(db, [90]) == {90: "banned_one"}


# --- the wiring ----------------------------------------------------------------------------------------------------------------------------

def test_the_table_is_in_the_migration_the_schema_and_the_ledger_with_no_foreign_keys():
    migration = read("scripts", "migrations", "2027-01-04_log_evidence.sql")
    assert "CREATE TABLE IF NOT EXISTS public.log_evidence" in migration and "REFERENCES" not in migration
    schema = read("schema.sql")
    assert "CREATE TABLE public.log_evidence" in schema and "('2027-01-04_log_evidence.sql')" in schema
    assert "CHECK ((post_id IS NULL) <> (comment_id IS NULL))" in migration and "CHECK ((post_id IS NULL) <> (comment_id IS NULL))" in schema
