"""The counts under a post (ruqqus/helpers/post_counts.py): shares and bookmarks, with the real SQL on an in-memory
SQLite database (columns as in schema.sql)."""
from types import SimpleNamespace as NS

import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.orm import Session

from ruqqus.helpers import post_counts as pc

ORIGINAL, COPY_A, COPY_B, PRIVATE_COPY, REMOVED_COPY, OTHER = 1, 2, 3, 4, 5, 6


@pytest.fixture
def db():
    engine = create_engine("sqlite://")
    with engine.begin() as conn:
        conn.execute(text("CREATE TABLE submissions (id integer PRIMARY KEY, repost_id integer, deleted_utc integer DEFAULT 0, "
                          "is_banned boolean DEFAULT 0, post_public boolean DEFAULT 1)"))
        conn.execute(text("CREATE TABLE save_relationship (id integer PRIMARY KEY AUTOINCREMENT, user_id integer, submission_id integer)"))
        conn.execute(text("CREATE TABLE repost_relationship (id integer PRIMARY KEY AUTOINCREMENT, user_id integer, submission_id integer)"))
        conn.execute(text("INSERT INTO submissions (id, repost_id) VALUES (1, 0), (2, 1), (3, 1), (6, NULL)"))
        conn.execute(text("INSERT INTO submissions (id, repost_id, post_public) VALUES (4, 1, 0)"))
        conn.execute(text("INSERT INTO submissions (id, repost_id, is_banned) VALUES (5, 1, 1)"))
    session = Session(engine)
    yield session
    session.close()


def post(id, repost_id=None):
    return NS(id=id, repost_id=repost_id)


def add(db, table, *pairs):
    for user, post_id in pairs:
        db.execute(text(f"INSERT INTO {table} (user_id, submission_id) VALUES (:u, :p)"), {"u": user, "p": post_id})


def test_shares_are_the_family_s_reposts_and_its_live_public_forwards(db):
    add(db, "repost_relationship", (10, ORIGINAL), (11, ORIGINAL), (12, COPY_A))
    found = pc.counts(db, [post(ORIGINAL), post(COPY_A, ORIGINAL), post(OTHER)])
    # 2 live public copies (A, B) + 3 reposts across the family; the private and the removed copy are not counted
    assert found[ORIGINAL][0] == 5 and found[COPY_A][0] == 5
    assert found[OTHER] == (0, 0)


def test_bookmarks_are_the_post_s_own(db):
    add(db, "save_relationship", (10, ORIGINAL), (11, ORIGINAL), (12, COPY_A))
    found = pc.counts(db, [post(ORIGINAL), post(COPY_A, ORIGINAL)])
    assert found[ORIGINAL][1] == 2 and found[COPY_A][1] == 1


def test_a_page_gets_its_counts_attached_and_an_empty_page_costs_nothing(db):
    posts = [post(ORIGINAL), post(OTHER)]
    pc.attach(db, posts)
    assert (posts[0]._share_count, posts[0]._bookmark_count) == (2, 0)
    assert (posts[1]._share_count, posts[1]._bookmark_count) == (0, 0)
    assert pc.counts(db, []) == {} and pc.counts(db, [None]) == {}
