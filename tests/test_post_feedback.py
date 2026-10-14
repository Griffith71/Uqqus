"""Show fewer posts like this (ruqqus/helpers/post_feedback.py): the rules, and the real SQL on an in-memory
SQLite database with the tables it reads (columns as in schema.sql)."""
import time
from types import SimpleNamespace as NS

import pytest
from sqlalchemy import Boolean, Column, Integer, MetaData, Table, create_engine, select, text
from sqlalchemy.orm import Session

from ruqqus.helpers import post_feedback as pf

NOW = int(time.time())
DAY = 86400
ME, AUTHOR, OTHER_AUTHOR = 1, 10, 11
ORIGINAL, COPY, UNRELATED = 100, 101, 102


@pytest.fixture
def db():
    engine = create_engine("sqlite://")
    with engine.begin() as conn:
        conn.execute(text("CREATE TABLE boards (id integer PRIMARY KEY, subcat_id integer)"))
        conn.execute(text("CREATE TABLE post_feedback (id integer PRIMARY KEY AUTOINCREMENT, user_id integer NOT NULL, "
                          "post_id integer NOT NULL, board_id integer, author_id integer, created_utc integer DEFAULT 0, "
                          "UNIQUE (user_id, post_id))"))
        conn.execute(text("INSERT INTO boards (id, subcat_id) VALUES (1, 5), (2, 6), (3, NULL)"))
    session = Session(engine)
    yield session
    session.close()


def post(id, repost_id=None, author=AUTHOR, board=1, anonymous=False):
    return NS(id=id, repost_id=repost_id, author_id=author, board_id=board, is_anonymous=anonymous)


def rows(db):
    return db.execute(text("SELECT user_id, post_id, board_id, author_id FROM post_feedback ORDER BY id")).fetchall()


# --- clicking ---------------------------------------------------------------------------------------------

def test_the_feedback_is_kept_under_the_primary_post_so_the_copies_go_together(db):
    pf.add(db, ME, post(COPY, repost_id=ORIGINAL), NOW)
    assert [(r.post_id, r.board_id, r.author_id) for r in rows(db)] == [(ORIGINAL, 1, AUTHOR)]


def test_asking_twice_is_the_same_answer(db):
    pf.add(db, ME, post(ORIGINAL), NOW)
    pf.add(db, ME, post(ORIGINAL), NOW)
    assert len(rows(db)) == 1


def test_an_anonymous_posts_author_is_never_stored(db):
    pf.add(db, ME, post(ORIGINAL, anonymous=True), NOW)
    assert rows(db)[0].author_id is None
    assert rows(db)[0].board_id == 1          # the guild is still lowered


def test_a_members_own_post_is_refused(db):
    with pytest.raises(pf.FeedbackError):
        pf.add(db, ME, post(ORIGINAL, author=ME), NOW)
    assert rows(db) == []


def test_undo_takes_it_back_through_any_copy(db):
    pf.add(db, ME, post(ORIGINAL), NOW)
    pf.remove(db, ME, post(COPY, repost_id=ORIGINAL))
    assert rows(db) == []
    pf.remove(db, ME, post(COPY, repost_id=ORIGINAL))         # not having asked is not an error


def test_a_member_keeps_at_most_the_cap_and_the_oldest_go(db, monkeypatch):
    monkeypatch.setattr(pf, "MAX_ROWS", 3)
    for i in range(5):
        pf.add(db, ME, post(200 + i), NOW + i)
    assert [r.post_id for r in rows(db)] == [202, 203, 204]


def test_reset_clears_only_that_members_rows(db):
    pf.add(db, ME, post(ORIGINAL), NOW)
    pf.add(db, 2, post(ORIGINAL), NOW)
    pf.clear(db, ME)
    assert [r.user_id for r in rows(db)] == [2]


# --- what it lowers ---------------------------------------------------------------------------------------

def test_only_the_last_sixty_days_lower_a_category_guild_or_author(db):
    pf.add(db, ME, post(300, board=1), NOW - 10 * DAY)
    pf.add(db, ME, post(301, board=2, author=OTHER_AUTHOR), NOW - 61 * DAY)
    lowered = pf.penalties(db, ME, NOW)
    assert lowered.subcat_ids == [5] and lowered.board_ids == {1} and lowered.author_ids == {AUTHOR}


def test_a_click_lowers_its_category_once_per_click(db):
    pf.add(db, ME, post(300, board=1), NOW)
    pf.add(db, ME, post(301, board=1, author=OTHER_AUTHOR), NOW)
    pf.add(db, ME, post(302, board=3), NOW)             # a guild without a category
    assert sorted(pf.penalties(db, ME, NOW).subcat_ids) == [5, 5]


def test_one_member_never_lowers_for_another(db):
    pf.add(db, 2, post(300), NOW)
    lowered = pf.penalties(db, ME, NOW)
    assert lowered.subcat_ids == [] and lowered.board_ids == set() and lowered.author_ids == set()


def test_the_sink_clause_matches_a_guild_and_an_author_but_never_an_anonymous_post(db):
    posts = Table("submissions", MetaData(), Column("id", Integer), Column("board_id", Integer),
                  Column("author_id", Integer), Column("is_anonymous", Boolean))
    db.execute(text("CREATE TABLE submissions (id integer, board_id integer, author_id integer, is_anonymous boolean)"))
    db.execute(text("INSERT INTO submissions VALUES (1, 2, 20, 0), (2, 2, 10, 0), (3, 3, 10, 0), (4, 3, 10, 1), (5, 1, 20, 0)"))
    pf.add(db, ME, post(400, board=1, author=AUTHOR), NOW)         # lowers guild 1 and author 10
    clause = pf.sink_clause(db, ME, posts.c, NOW)
    got = dict(db.execute(select(posts.c.id, clause).order_by(posts.c.id)).fetchall())
    # 1: neither; 2: author 10 not anonymous; 3: author 10; 4: author 10 but anonymous; 5: guild 1
    assert got == {1: 0, 2: 1, 3: 1, 4: 0, 5: 1}
    assert pf.sink_clause(db, 99, posts.c, NOW) is None


# --- hiding in the feeds ---------------------------------------------------------------------------------

def test_the_post_and_its_copies_leave_the_feed_for_that_member_only(db):
    posts = Table("submissions", MetaData(), Column("id", Integer), Column("repost_id", Integer))
    db.execute(text("CREATE TABLE submissions (id integer, repost_id integer)"))
    db.execute(text("INSERT INTO submissions VALUES (100, NULL), (101, 100), (102, 0), (103, 100)"))
    pf.add(db, ME, post(COPY, repost_id=ORIGINAL), NOW)

    def feed(v):
        q = pf.hide_fewer(select(posts.c.id).order_by(posts.c.id), v, posts.c)
        return [r[0] for r in db.execute(q)]

    assert feed(NS(id=ME)) == [102]
    assert feed(NS(id=2)) == [100, 101, 102, 103]
    assert pf.hide_fewer("query", None, posts.c) == "query"           # a visitor has asked for nothing
