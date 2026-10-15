"""Creator Studio (ruqqus/helpers/studio.py rules, helpers/studio_store.py counting): the rules, and the real SQL on
in-memory SQLite. The promises: a member's coins are counted by where they came from, only what reached them counts
(not a tip they gave, not a held payout), and a paid circle's subscribers are counted once each, never listed."""
import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.orm import Session

from circle_tables import create
from ruqqus.helpers import studio as rules
from ruqqus.helpers import studio_store as store

NOW = 2_000_000_000
DAY = 86400
ME, FAN, OTHER = 1, 2, 3


@pytest.fixture
def db():
    engine = create_engine("sqlite://")
    with engine.begin() as conn:
        create(conn)
        conn.execute(text("CREATE TABLE submissions (id integer PRIMARY KEY, author_id integer, repost_id integer)"))
        conn.execute(text("CREATE TABLE comments (id integer PRIMARY KEY, author_id integer)"))
        conn.execute(text("CREATE TABLE award_relationships (id integer PRIMARY KEY AUTOINCREMENT, user_id integer, submission_id integer, "
                          "comment_id integer, coins integer NOT NULL DEFAULT 1, created_utc integer NOT NULL DEFAULT 0)"))
        conn.execute(text("CREATE TABLE creator_fund_payouts (id integer PRIMARY KEY AUTOINCREMENT, month text, user_id integer, points integer, "
                          "coins integer, status text, created_utc integer, paid_utc integer NOT NULL DEFAULT 0)"))
        # my post 10, its forward copy 11 (a copy keeps my author_id), someone else's post 12; my comment 20, theirs 21
        conn.execute(text("INSERT INTO submissions (id, author_id, repost_id) VALUES (10, :me, NULL), (11, :me, 10), (12, :o, NULL)"), {"me": ME, "o": OTHER})
        conn.execute(text("INSERT INTO comments (id, author_id) VALUES (20, :me), (21, :o)"), {"me": ME, "o": OTHER})
        tips = ((FAN, 10, None, 5, NOW - 2 * DAY), (FAN, 11, None, 3, NOW - 40 * DAY), (OTHER, None, 20, 2, NOW - DAY),
                (ME, 12, None, 7, NOW - DAY),                                   # a tip I gave: not mine to count
                (FAN, 12, None, 9, NOW - DAY), (FAN, None, 21, 4, NOW - DAY),   # tips to someone else
                (FAN, 10, None, 1, 0))                                          # an old row from before tips had dates
        for giver, post, comment, coins, when in tips:
            conn.execute(text("INSERT INTO award_relationships (user_id, submission_id, comment_id, coins, created_utc) VALUES (:g, :p, :c, :n, :t)"),
                         {"g": giver, "p": post, "c": comment, "n": coins, "t": when})
        for coins, owner, when in ((10, ME, NOW - DAY), (10, ME, NOW - 45 * DAY), (6, OTHER, NOW - DAY)):
            conn.execute(text("INSERT INTO circle_payments (circle_id, payer_id, owner_id, coins, kind, created_utc) VALUES (NULL, :p, :o, :c, 'subscribe', :t)"),
                         {"p": FAN, "o": owner, "c": coins, "t": when})
        for month, coins, status, paid in (("2033-04", 40, "paid", NOW - 10 * DAY), ("2033-03", 25, "paid", NOW - 50 * DAY), ("2033-05", 99, "held", 0)):
            conn.execute(text("INSERT INTO creator_fund_payouts (month, user_id, points, coins, status, created_utc, paid_utc) VALUES (:m, :u, 1, :c, :s, :t, :p)"),
                         {"m": month, "u": ME, "c": coins, "s": status, "t": NOW, "p": paid})
    session = Session(engine)
    yield session
    session.close()


def test_coins_are_counted_by_where_they_came_from(db):
    assert store.earnings(db, ME) == {"tips": 5 + 3 + 2 + 1, "circles": 20, "fund": 65}
    assert store.earnings(db, ME, since=NOW - 30 * DAY) == {"tips": 7, "circles": 10, "fund": 40}


def test_a_tip_i_gave_and_tips_to_others_are_never_mine(db):
    assert store.earnings(db, OTHER) == {"tips": 7 + 9 + 4, "circles": 6, "fund": 0}
    assert store.earnings(db, FAN) == {"tips": 0, "circles": 0, "fund": 0}


def test_a_held_creator_fund_payout_counts_only_once_it_is_paid(db):
    db.execute(text("UPDATE creator_fund_payouts SET status = 'paid', paid_utc = :t WHERE month = '2033-05'"), {"t": NOW})
    assert store.earnings(db, ME, since=NOW - DAY)["fund"] == 99


def test_subscribers_are_counted_once_each_and_only_while_they_have_access(db):
    for cid, kind in ((1, "paid"), (2, "paid"), (3, "custom")):
        db.execute(text("INSERT INTO circles (id, user_id, kind, name) VALUES (:i, :o, :k, :n)"), {"i": cid, "o": ME, "k": kind, "n": f"c{cid}"})
    db.execute(text("INSERT INTO circles (id, user_id, kind, name) VALUES (4, :o, 'paid', 'theirs')"), {"o": OTHER})
    rows = ((1, FAN, "subscriber", "active", NOW + DAY), (2, FAN, "subscriber", "active", NOW + DAY),     # one person, two circles
            (1, OTHER, "subscriber", "active", NOW - 1),                                                # their days ran out
            (3, OTHER, "member", "active", 0),                                                          # a custom circle: never counted
            (4, FAN, "subscriber", "active", NOW + DAY))                                                # someone else's circle
    for cid, uid, tier, status, renews in rows:
        db.execute(text("INSERT INTO circle_members (circle_id, user_id, tier, status, renews_utc) VALUES (:c, :u, :t, :s, :r)"),
                   {"c": cid, "u": uid, "t": tier, "s": status, "r": renews})
    assert store.subscribers_now(db, ME, NOW) == 1
    assert store.subscribers_now(db, ME, NOW + 2 * DAY) == 0


def test_the_summary_keeps_the_sources_in_order_and_adds_them_up():
    rows, recent, total = rules.summary({"tips": 7, "fund": 40}, {"tips": 11, "circles": 20, "fund": 65})
    assert [r["key"] for r in rows] == ["tips", "circles", "fund"]
    assert [r["recent"] for r in rows] == [7, 0, 40] and (recent, total) == (47, 96)
    assert all(r["label"] and r["hint"] for r in rows)


def test_coins_are_written_as_the_site_writes_numbers():
    assert rules.coins(0) == "0 coins" and rules.coins(1) == "1 coin" and rules.coins(1234) == "1,234 coins"


def test_no_source_is_ad_money():
    assert {key for key, _, _ in rules.SOURCES} == {"tips", "circles", "fund"}
    assert not any("ad" in label.lower().split() for _, label, _ in rules.SOURCES)
