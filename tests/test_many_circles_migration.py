"""The data half of scripts/migrations/2026-10-20_many_circles.sql (the statements between `-- data: begin` and
`-- data: end`), run on SQLite over rows in the old shape. The promise: nobody gains or loses access. Close Friends
become a custom circle "Close Friends", the old Circle the paid circle "Subscribers" (dropped when it never asked a
price and nobody ever paid), posts and stories for Subscribers go to both and those for Close Friends to Close Friends,
a Circle guild's circle is untouched, and running it again changes nothing."""
from pathlib import Path

import pytest
from sqlalchemy import create_engine, text

ROOT = Path(__file__).resolve().parent.parent
MIGRATION = (ROOT / "scripts" / "migrations" / "2026-10-20_many_circles.sql").read_text(encoding="utf-8")
DATA = MIGRATION.split("-- data: begin")[1].split("\n", 1)[1].split("-- data: end")[0]
STATEMENTS = [s.strip() for s in DATA.split(";\n") if s.strip()]

A, B, C, D, FOUNDER = 1, 2, 3, 4, 5
FRIEND_A, SUB_A, FRIEND_B, ENDED_D, GUILD_SUB = 11, 12, 13, 14, 15


@pytest.fixture
def conn():
    engine = create_engine("sqlite://")
    with engine.begin() as conn:
        # the tables as the migration finds them once its ADD COLUMNs ran (kind still defaults to 'paid')
        conn.execute(text("CREATE TABLE circles (id integer PRIMARY KEY AUTOINCREMENT, user_id integer, board_id integer, "
                          "price_coins integer NOT NULL DEFAULT 0, created_utc integer NOT NULL DEFAULT 0, "
                          "name text NOT NULL DEFAULT '', kind text NOT NULL DEFAULT 'paid')"))
        conn.execute(text("CREATE TABLE circle_members (id integer PRIMARY KEY AUTOINCREMENT, circle_id integer NOT NULL, user_id integer NOT NULL, "
                          "tier text NOT NULL, status text NOT NULL DEFAULT 'active', renews_utc integer NOT NULL DEFAULT 0, "
                          "UNIQUE (circle_id, user_id))"))
        conn.execute(text("CREATE TABLE circle_payments (id integer PRIMARY KEY AUTOINCREMENT, circle_id integer, payer_id integer, owner_id integer, coins integer)"))
        conn.execute(text("CREATE TABLE submissions (id integer PRIMARY KEY, author_id integer, audience integer NOT NULL DEFAULT 0)"))
        conn.execute(text("CREATE TABLE stories (id integer PRIMARY KEY, user_id integer, audience integer NOT NULL DEFAULT 0)"))
        conn.execute(text("CREATE TABLE post_circles (post_id integer NOT NULL, circle_id integer NOT NULL, PRIMARY KEY (post_id, circle_id))"))
        conn.execute(text("CREATE TABLE story_circles (story_id integer NOT NULL, circle_id integer NOT NULL, PRIMARY KEY (story_id, circle_id))"))
        # A: a price, a close friend, a subscriber who paid
        conn.execute(text("INSERT INTO circles (id, user_id, price_coins, created_utc) VALUES (1, :a, 10, 100)"), {"a": A})
        conn.execute(text("INSERT INTO circle_members (circle_id, user_id, tier) VALUES (1, :f, 'friend'), (1, :s, 'subscriber')"), {"f": FRIEND_A, "s": SUB_A})
        conn.execute(text("INSERT INTO circle_payments (circle_id, payer_id, owner_id, coins) VALUES (1, :s, :a, 10)"), {"s": SUB_A, "a": A})
        # B: Close Friends only, never a price
        conn.execute(text("INSERT INTO circles (id, user_id, price_coins) VALUES (2, :b, 0)"), {"b": B})
        conn.execute(text("INSERT INTO circle_members (circle_id, user_id, tier) VALUES (2, :f, 'friend')"), {"f": FRIEND_B})
        # C: a Circle with nobody and no price
        conn.execute(text("INSERT INTO circles (id, user_id, price_coins) VALUES (3, :c, 0)"), {"c": C})
        # D: no price now, but someone subscribed once
        conn.execute(text("INSERT INTO circles (id, user_id, price_coins) VALUES (4, :d, 0)"), {"d": D})
        conn.execute(text("INSERT INTO circle_members (circle_id, user_id, tier, status) VALUES (4, :e, 'subscriber', 'ended')"), {"e": ENDED_D})
        # a Circle guild's circle (the migration marks it 'guild' before the data half)
        conn.execute(text("INSERT INTO circles (id, board_id, price_coins, kind) VALUES (5, 50, 7, 'guild')"))
        conn.execute(text("INSERT INTO circle_members (circle_id, user_id, tier) VALUES (5, :g, 'subscriber')"), {"g": GUILD_SUB})
        for pid, author, audience in ((101, A, 1), (102, A, 2), (103, A, 0), (104, B, 1), (105, B, 2), (106, C, 2), (107, D, 1)):
            conn.execute(text("INSERT INTO submissions (id, author_id, audience) VALUES (:i, :a, :u)"), {"i": pid, "a": author, "u": audience})
        for sid, user, audience in ((201, A, 1), (202, A, 2), (203, A, 0)):
            conn.execute(text("INSERT INTO stories (id, user_id, audience) VALUES (:i, :u, :a)"), {"i": sid, "u": user, "a": audience})
    with engine.begin() as conn:
        for statement in STATEMENTS:
            conn.execute(text(statement))
    with engine.connect() as conn:
        yield conn


def circles_of(conn, owner):
    return {(r.kind, r.name): r.id for r in conn.execute(text("SELECT id, kind, name FROM circles WHERE user_id = :o"), {"o": owner})}


def members(conn, circle_id):
    return {(r.user_id, r.tier) for r in conn.execute(text("SELECT user_id, tier FROM circle_members WHERE circle_id = :c"), {"c": circle_id})}


def for_post(conn, pid):
    return {r[0] for r in conn.execute(text("SELECT circle_id FROM post_circles WHERE post_id = :p"), {"p": pid})}


def test_the_data_half_is_found_in_the_migration():
    assert len(STATEMENTS) == 8 and all("--" not in s.split("\n")[0] for s in STATEMENTS)


def test_an_owner_with_a_price_gets_subscribers_and_close_friends(conn):
    found = circles_of(conn, A)
    assert set(found) == {("paid", "Subscribers"), ("custom", "Close Friends")}
    assert found[("paid", "Subscribers")] == 1                                       # the same row: payments and dates stay
    assert members(conn, 1) == {(SUB_A, "subscriber")}
    assert members(conn, found[("custom", "Close Friends")]) == {(FRIEND_A, "member")}
    assert conn.execute(text("SELECT circle_id FROM circle_payments")).scalar() == 1


def test_posts_and_stories_keep_exactly_their_readers(conn):
    found = circles_of(conn, A)
    paid, close = found[("paid", "Subscribers")], found[("custom", "Close Friends")]
    assert for_post(conn, 101) == {paid, close}                                     # Subscribers: subscribers and friends
    assert for_post(conn, 102) == {close}                                           # Close Friends: friends only
    assert for_post(conn, 103) == set()
    audiences = dict(conn.execute(text("SELECT id, audience FROM submissions")).fetchall())
    assert audiences == {101: 4, 102: 4, 103: 0, 104: 4, 105: 4, 106: 4, 107: 4}
    stories = dict(conn.execute(text("SELECT id, audience FROM stories")).fetchall())
    assert stories == {201: 4, 202: 4, 203: 0}
    assert {r[0] for r in conn.execute(text("SELECT circle_id FROM story_circles WHERE story_id = 201"))} == {paid, close}
    assert {r[0] for r in conn.execute(text("SELECT circle_id FROM story_circles WHERE story_id = 202"))} == {close}


def test_a_circle_that_never_asked_a_price_and_was_never_paid_leaves_only_its_friends(conn):
    assert set(circles_of(conn, B)) == {("custom", "Close Friends")}
    close = circles_of(conn, B)[("custom", "Close Friends")]
    assert members(conn, close) == {(FRIEND_B, "member")}
    assert for_post(conn, 104) == {close} and for_post(conn, 105) == {close}


def test_an_empty_circle_goes_and_its_posts_stay_the_authors_alone(conn):
    assert circles_of(conn, C) == {}
    assert for_post(conn, 106) == set()


def test_a_circle_someone_once_paid_for_stays_a_paid_circle(conn):
    assert circles_of(conn, D) == {("paid", "Subscribers"): 4}
    assert members(conn, 4) == {(ENDED_D, "subscriber")}
    assert for_post(conn, 107) == {4}


def test_a_circle_guilds_circle_is_untouched(conn):
    row = conn.execute(text("SELECT kind, name, price_coins, board_id FROM circles WHERE id = 5")).fetchone()
    assert tuple(row) == ("guild", "", 7, 50)
    assert members(conn, 5) == {(GUILD_SUB, "subscriber")}


def test_no_old_tier_or_audience_is_left(conn):
    assert conn.execute(text("SELECT COUNT(*) FROM circle_members WHERE tier NOT IN ('member', 'subscriber')")).scalar() == 0
    assert conn.execute(text("SELECT COUNT(*) FROM submissions WHERE audience IN (1, 2)")).scalar() == 0
    assert conn.execute(text("SELECT COUNT(*) FROM stories WHERE audience IN (1, 2)")).scalar() == 0
    assert conn.execute(text("SELECT COUNT(*) FROM circles WHERE user_id IS NOT NULL AND name = '' AND kind <> 'friends'")).scalar() == 0


def test_running_it_again_changes_nothing(conn):
    def snapshot():
        return [conn.execute(text(f"SELECT * FROM {t} ORDER BY 1, 2")).fetchall()
                for t in ("circles", "circle_members", "post_circles", "story_circles", "submissions", "stories")]
    before = snapshot()
    for statement in STATEMENTS:
        conn.execute(text(statement))
    assert snapshot() == before


def test_the_schema_half_makes_the_rules_the_database_keeps():
    assert "ALTER TABLE circles ALTER COLUMN kind DROP DEFAULT;" in MIGRATION         # every insert must say its kind
    assert "CHECK (tier IN ('member', 'subscriber'))" in MIGRATION
    assert "CHECK (audience IN (0, 4))" in MIGRATION
    assert "circles_friends_key ON circles USING btree (user_id) WHERE (kind = 'friends')" in MIGRATION
    assert "ON DELETE SET NULL" in MIGRATION and "DROP INDEX IF EXISTS circles_user_key;" in MIGRATION
    drafts = MIGRATION.split("-- data: end")[1]
    assert "UPDATE post_drafts" in drafts and "'circles'" in drafts
