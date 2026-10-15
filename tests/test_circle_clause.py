"""The SQL condition that lets the people in an author's circles see the author's circle posts in their own lists
(ruqqus/helpers/circle_clause.py): the same expression the lists use, run on SQLite (tables in tests/circle_tables.py).
The promise under test: it picks exactly the circle posts helpers/circle_guard.py `may_see` lets each viewer see, and
nothing else."""
from types import SimpleNamespace as NS

import pytest
from sqlalchemy import column, create_engine, select, table, text
from sqlalchemy.orm import Session

from circle_tables import add_users, block, create, follow
from ruqqus.helpers import circle_clause
from ruqqus.helpers import circle_guard as guard
from ruqqus.helpers import circle_store as store
from ruqqus.helpers import circles as c

NOW = 2_000_000_000
DAY = 86400
A, B = 1, 2                                                     # two authors with circles
FAMILY, FAN, MUTUAL, ONE_WAY, EXPIRED, ENDED, STRANGER, BLOCKED = 10, 11, 12, 13, 14, 15, 16, 17
VIEWERS = (A, B, FAMILY, FAN, MUTUAL, ONE_WAY, EXPIRED, ENDED, STRANGER, BLOCKED)
SUB = table("submissions", column("id"), column("author_id"), column("audience"))


@pytest.fixture
def db():
    engine = create_engine("sqlite://")
    with engine.begin() as conn:
        create(conn)
        conn.execute(text("CREATE TABLE submissions (id integer PRIMARY KEY, author_id integer, audience integer NOT NULL DEFAULT 0, board_id integer)"))
        add_users(conn, {uid: (f"u{uid}", 50) for uid in VIEWERS})
    session = Session(engine)
    ids = {"friends": store.ensure_friends(session, A, NOW),
           "family": store.create(session, A, c.CUSTOM, "Family", 0, NOW)[0],
           "fans": store.create(session, A, c.PAID, "Fans", 10, NOW)[0],
           "b_mates": store.create(session, B, c.CUSTOM, "Mates", 0, NOW)[0]}
    store.add_member(session, A, ids["family"], FAMILY, NOW)
    store.add_member(session, A, ids["family"], BLOCKED, NOW)
    store.add_member(session, B, ids["b_mates"], FAMILY, NOW)
    store.subscribe(session, ids["fans"], FAN, NOW)
    for uid, when, status in ((EXPIRED, NOW - 40 * DAY, "active"), (ENDED, NOW, "ended")):
        session.execute(text("INSERT INTO circle_members (circle_id, user_id, tier, status, renews_utc, price_coins, created_utc) "
                             "VALUES (:c, :u, 'subscriber', :s, :r, 10, :t)"), {"c": ids["fans"], "u": uid, "s": status, "r": when + c.PERIOD, "t": when})
    follow(session, A, MUTUAL)
    follow(session, MUTUAL, A)
    follow(session, ONE_WAY, A)
    follow(session, A, FAMILY)                                  # A follows FAMILY, but not back: not Friends
    block(session, BLOCKED, A)
    # posts: id -> (author, audience, circles)
    posts = {1: (A, 0, ()), 2: (A, 4, ("family",)), 3: (A, 4, ("fans",)), 4: (A, 4, ("friends",)), 5: (A, 4, ("family", "fans")),
             6: (A, 3, ()), 7: (A, 4, ()), 8: (B, 4, ("b_mates",)), 9: (B, 4, ("family",))}   # 9: B naming A's circle can't happen; never counts
    for pid, (author, audience, names) in posts.items():
        session.execute(text("INSERT INTO submissions (id, author_id, audience, board_id) VALUES (:i, :a, :u, 2)"),
                        {"i": pid, "a": author, "u": audience})
        store.set_post_circles(session, pid, [ids[n] for n in names])
    session.ids = ids
    yield session
    session.close()


def picked(db, who, now=NOW):
    condition = circle_clause.member_clause(NS(id=who) if who else None, SUB.c, now)
    return {r[0] for r in db.execute(select(SUB.c.id).where(condition)).fetchall()}


def guard_says(db, who, now=NOW):
    rows = db.execute(text("SELECT id, author_id, audience, board_id FROM submissions WHERE audience = 4")).fetchall()
    return {r.id for r in rows if r.author_id != who and guard.may_see_post(db, NS(id=r.id, author_id=r.author_id, audience=r.audience, board_id=r.board_id), NS(id=who, admin_level=0), now)}


@pytest.mark.parametrize("who, expected", [
    (FAMILY, {2, 5, 8}), (FAN, {3, 5}), (MUTUAL, {4}), (ONE_WAY, set()), (EXPIRED, set()), (ENDED, set()),
    (STRANGER, set()), (BLOCKED, set()),
])
def test_each_viewer_gets_the_circle_posts_of_the_circles_they_are_in(db, who, expected):
    assert picked(db, who) == expected


@pytest.mark.parametrize("who", VIEWERS)
def test_the_sql_and_the_guard_agree_for_everyone(db, who):
    """The lists (this clause) and the post page (the guard) must never disagree. The author's own posts reach their
    lists another way (author_id), so they are left out of the comparison."""
    assert picked(db, who) == guard_says(db, who)


def test_a_visitor_gets_nothing_and_the_condition_is_false(db):
    assert picked(db, None) == set()


def test_public_and_circle_guild_posts_are_never_matched_here(db):
    for who in VIEWERS:
        assert not picked(db, who) & {1, 6}


def test_a_post_with_no_circles_left_matches_nobody(db):
    assert all(7 not in picked(db, who) for who in VIEWERS)


def test_a_circle_only_counts_for_its_own_owners_posts(db):
    assert 9 not in picked(db, FAMILY)


def test_a_subscription_runs_out_on_the_date(db):
    assert picked(db, FAN, NOW + c.PERIOD - 1) == {3, 5}
    assert picked(db, FAN, NOW + c.PERIOD) == set()


def test_someone_taken_out_or_blocked_loses_the_posts_at_once(db):
    store.remove_member(db, A, db.ids["family"], FAMILY)
    assert picked(db, FAMILY) == {8}
    block(db, A, MUTUAL)
    assert picked(db, MUTUAL) == set()


def test_for_viewer_uses_the_real_model():
    source = circle_clause.for_viewer.__code__.co_names
    assert "member_clause" in source and "Submission" in source
