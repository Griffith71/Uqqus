"""Anonymous credits on a post written with others (the Log's slice D3f, decision 21): the rules and SQL without Postgres, and the wiring of every surface that
draws or counts a credit.

The promises: an edit by a co-author whose credit is anonymous never names them in the Log and can't be listed under their name; everyone else's edits are
named as before; the credit's flag is what the byline, the lists, the counts, a block and the shared-post pages read."""
from pathlib import Path
from types import SimpleNamespace

import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.orm import Session

from ruqqus.helpers import action_log as log, coauthors

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
        log_tables.apply(conn, modactions=True)
        conn.execute(text("CREATE TABLE submissions (id INTEGER PRIMARY KEY, author_id INTEGER, repost_id INTEGER DEFAULT 0, is_anonymous BOOLEAN DEFAULT 0)"))
        conn.execute(text("CREATE TABLE comments (id INTEGER PRIMARY KEY, author_id INTEGER, is_anonymous BOOLEAN DEFAULT 0)"))
        conn.execute(text("CREATE TABLE boards (id INTEGER PRIMARY KEY, is_private BOOLEAN DEFAULT 0, is_circle BOOLEAN DEFAULT 0)"))
        conn.execute(text("INSERT INTO submissions VALUES (1, 7, 0, 0), (2, 7, 1, 0)"))                  # 2 is a guild copy of 1
        conn.execute(text("INSERT INTO post_coauthors (post_id, user_id, invited_by_id, status, is_anonymous) VALUES "
                          "(1, 20, 7, 'accepted', 1), (1, 21, 7, 'accepted', 0)"))                         # 20's credit is anonymous, 21's is named
        for entry, actor, post in ((1, 20, 1), (2, 21, 1), (3, 7, 1), (4, 20, 2)):
            conn.execute(text("INSERT INTO log_entries (id, created_utc, log_type, action, actor_id, actor_role, audience, target_submission_id) "
                              "VALUES (:e, :n, 'edit', 'edit_post', :a, 'author', 0, :p)"), {"e": entry, "n": NOW - entry, "a": actor, "p": post})
    with Session(engine) as session:
        yield session


def actors(db, **kw):
    rows, _ = log.fetch(db, level=0, **kw)
    return {r["id"]: r["actor_id"] for r in rows}


def test_an_edit_by_an_anonymous_credit_never_names_the_editor_and_the_others_still_do(db):
    assert actors(db) == {1: None, 2: 21, 3: 7, 4: None}, "20's credit is anonymous (also on the guild copy); 21 and the author are named"


def test_the_by_filter_cannot_list_an_anonymous_credits_edits(db):
    assert actors(db, actor_id=20) == {}, "filtering by the anonymous co-author finds nothing: it would list exactly their edits"
    assert set(actors(db, actor_id=21)) == {2} and set(actors(db, actor_id=7)) == {3}


def test_staff_see_the_editor_as_before(db):
    rows, _ = log.fetch(db, level=3)
    assert {r["id"]: r["actor_id"] for r in rows}[1] == 20


# --- who may be told a credit ----------------------------------------------------------------------------------------------------------

def test_a_credit_is_told_to_admins_and_the_co_author_and_not_to_a_limited_sign_in_or_anyone_else():
    owner = SimpleNamespace(id=20)
    assert coauthors.sees_credit(owner, SimpleNamespace(id=20, admin_level=0, qr_limited=False))
    assert coauthors.sees_credit(owner, SimpleNamespace(id=99, admin_level=3, qr_limited=False))
    assert not coauthors.sees_credit(owner, SimpleNamespace(id=99, admin_level=0, qr_limited=False))
    assert not coauthors.sees_credit(owner, SimpleNamespace(id=20, admin_level=0, qr_limited=True)), "a relayed code is a stranger to it"
    assert not coauthors.sees_credit(owner, None)


# --- the wiring ------------------------------------------------------------------------------------------------------------------------

def test_every_surface_that_draws_or_counts_a_credit_reads_the_flag():
    user = read("ruqqus", "classes", "user.py")
    assert user.count("PostCoauthor.is_anonymous == False") == 3, "the Following feed, a profile's list and the profile's count"
    assert "coauthored = coauthored.where(PostCoauthor.is_anonymous == False)" in user and "(v.admin_level or 0) >= 3" in user.split("coauthored = coauthored.where")[0][-200:]
    assert 'PostCoauthor.is_anonymous == False,\n                               PostCoauthor.user_id.in_(user_ids)' in read("ruqqus", "helpers", "blocks.py")
    routes = read("ruqqus", "routes", "coauthors.py")
    assert "return coauthors.credits_of(post," in routes, "the byline draws credits, not accounts"
    assert "coauthors.sees_credit(user, v)" in routes and '"alias": True' in routes, "the Co-authors sheet shows an anonymous credit by its alias to everyone but its author"
    assert "coauthors.named_of(primary)" in read("ruqqus", "routes", "live_threads.py"), "a live thread's header never names an anonymous credit"
    collab = read("ruqqus", "routes", "collab.py")
    assert collab.count("_name_of(") >= 1 and "_hidden_credits(post, v)" in collab and collab.count("hidden") >= 8
    assert "if uid not in current and not row.is_anonymous:" in collab, "leaving the draft never drops the flag that keeps what they typed anonymous"
    assert "is_anonymous=bool(post.is_anonymous)" in collab
    assert "NOT {_ACTOR_CREDIT_ANONYMOUS}" in read("ruqqus", "helpers", "action_log.py")


def test_the_rules_that_forbade_it_are_lifted_and_an_accounts_deletion_hides_its_credit_instead_of_dropping_it():
    rules = read("ruqqus", "helpers", "coauthors.py")
    assert "anonymous" not in rules.split("def refusal(")[1].split(")")[0] and "A co-authored post can't be anonymous." not in rules
    cleanup = read("ruqqus", "helpers", "account_cleanup.py")
    assert "DELETE FROM post_coauthors WHERE user_id = :u AND status <> 'accepted'" in cleanup
    assert "UPDATE post_coauthors SET is_anonymous = :yes WHERE user_id = :u AND status = 'accepted'" in cleanup
    assert 'DELETE FROM post_coauthors WHERE user_id = :u"' not in cleanup
    assert "credits(db, user_id)" in read("ruqqus", "helpers", "account_anonymise.py")
    assert '"anonymous"' in read("ruqqus", "helpers", "collab_rules.py").split("FLAG_OPTIONS =")[1].split("\n")[0]
    posts = read("ruqqus", "routes", "posts.py")
    assert "shared=False" in posts.split("def unlink_post(")[1].split("def delete_post_as")[0], "'Remove my name' on a shared post hides this author's credit, it isn't refused"
    migration = read("scripts", "migrations", "2027-01-06_coauthor_credits.sql")
    assert "ADD COLUMN IF NOT EXISTS is_anonymous boolean DEFAULT false NOT NULL" in migration and "REFERENCES" not in migration
