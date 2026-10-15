"""A profile's lists (ruqqus/helpers/follow_lists.py): who may open one, and the SQL on SQLite. Every row needs the
other account's own setting too; blocked, banned, deleted and word-filtered accounts and Close Friends are never
listed; counts are the full numbers."""
from sqlalchemy import create_engine, text
from sqlalchemy.orm import Session

from ruqqus.helpers import follow_lists as fl

NOW = 1_800_000_000

TABLES = """
CREATE TABLE users (id INTEGER PRIMARY KEY, is_deleted BOOLEAN DEFAULT 0, is_banned INTEGER DEFAULT 0,
    name_severity INTEGER DEFAULT 0, following_visibility INTEGER DEFAULT 0, followers_visibility INTEGER DEFAULT 0,
    subscribers_visibility INTEGER DEFAULT 0, subscriptions_visibility INTEGER DEFAULT 0);
CREATE TABLE follows (id INTEGER PRIMARY KEY, user_id INTEGER, target_id INTEGER, created_utc INTEGER DEFAULT 0);
CREATE TABLE userblocks (id INTEGER PRIMARY KEY, user_id INTEGER, target_id INTEGER);
CREATE TABLE circles (id INTEGER PRIMARY KEY, user_id INTEGER, board_id INTEGER, price_coins INTEGER DEFAULT 0, kind TEXT NOT NULL);
CREATE TABLE circle_members (id INTEGER PRIMARY KEY, circle_id INTEGER, user_id INTEGER, tier TEXT, status TEXT DEFAULT 'active',
    started_utc INTEGER DEFAULT 0, renews_utc INTEGER DEFAULT 0);
"""
OWNER = 1


def make(users, follows=(), blocks=(), circles=(), members=()):
    db = Session(create_engine("sqlite://"))
    for statement in TABLES.split(";"):
        if statement.strip():
            db.execute(text(statement))
    for uid, cols in users.items():
        db.execute(text("INSERT INTO users (id) VALUES (:i)"), {"i": uid})
        for col, val in cols.items():
            db.execute(text(f"UPDATE users SET {col} = :v WHERE id = :i"), {"v": val, "i": uid})
    for n, (a, b) in enumerate(follows):
        db.execute(text("INSERT INTO follows (user_id, target_id, created_utc) VALUES (:a, :b, :t)"), {"a": a, "b": b, "t": n})
    for a, b in blocks:
        db.execute(text("INSERT INTO userblocks (user_id, target_id) VALUES (:a, :b)"), {"a": a, "b": b})
    for cid, owner, board, price, *kind in circles:
        db.execute(text("INSERT INTO circles (id, user_id, board_id, price_coins, kind) VALUES (:c, :o, :b, :p, :k)"),
                   {"c": cid, "o": owner, "b": board, "p": price, "k": kind[0] if kind else ("guild" if board else "paid")})
    for cid, uid, tier, status, renews in members:
        db.execute(text("INSERT INTO circle_members (circle_id, user_id, tier, status, renews_utc, started_utc) "
                        "VALUES (:c, :u, :t, :s, :r, 1)"), {"c": cid, "u": uid, "t": tier, "s": status, "r": renews})
    return db


def ids(db, name, viewer, cut=None):
    return sorted(fl.page(db, name, OWNER, viewer, cut, NOW)[0])


def test_who_may_open_a_list():
    assert fl.may_open(fl.ONLY_ME, is_owner=True, follows_owner=False)
    assert fl.may_open(fl.EVERYONE, is_owner=False, follows_owner=False)
    assert fl.may_open(fl.FOLLOWERS, is_owner=False, follows_owner=True)
    assert not fl.may_open(fl.FOLLOWERS, is_owner=False, follows_owner=False)
    assert not fl.may_open(fl.ONLY_ME, is_owner=False, follows_owner=True)
    assert not fl.may_open(7, is_owner=False, follows_owner=True)            # unknown: as Only me
    assert [fl.parse_choice(x) for x in ("0", "1", "2", "3", "", None, "x")] == [0, 1, 2, None, None, None, None]


def test_every_row_needs_the_other_accounts_own_setting():
    users = {OWNER: {}, 2: {}, 3: {"following_visibility": 2}, 4: {"following_visibility": 1}, 9: {}}
    db = make(users, follows=[(2, OWNER), (3, OWNER), (4, OWNER), (9, 4)])
    # Followers of the owner: 3 keeps who they follow to themselves; 4 shows it only to their own followers
    assert ids(db, "followers", 9) == [2, 4]          # 9 follows 4
    assert ids(db, "followers", 2) == [2]             # 2 sees itself, not 3 or 4
    assert ids(db, "followers", 3) == [2, 3]          # 3 sees itself
    assert ids(db, "followers", None) == [2]          # a visitor
    assert ids(db, "followers", OWNER) == [2]         # no exception for the owner
    # the counts are the full numbers, whoever looks
    assert fl.counts(db, OWNER, NOW)["followers"] == 3


def test_following_rows_need_the_followed_accounts_followers_setting():
    users = {OWNER: {}, 2: {}, 3: {"followers_visibility": 2}}
    db = make(users, follows=[(OWNER, 2), (OWNER, 3)])
    assert ids(db, "following", 5) == [2] and ids(db, "following", 3) == [2, 3]


def test_blocked_banned_deleted_and_filtered_accounts_are_never_listed():
    users = {OWNER: {}, 2: {}, 3: {"is_banned": 1}, 4: {"is_deleted": True}, 5: {"name_severity": 2}, 6: {}, 7: {}}
    db = make(users, follows=[(x, OWNER) for x in (2, 3, 4, 5, 6, 7)], blocks=[(9, 6), (7, 9)])
    assert ids(db, "followers", 9) == [2, 5]          # 6 blocked by the viewer, 7 blocks the viewer
    assert ids(db, "followers", 9, cut=2) == [2]      # the Standard filter hides severity 2
    assert fl.counts(db, OWNER, NOW)["followers"] == 4   # banned and deleted accounts are not counted


def test_only_active_subscribers_of_a_persons_paid_circles_count_never_custom_ones():
    users = {OWNER: {}, 2: {}, 3: {}, 4: {}, 5: {}, 6: {"subscriptions_visibility": 2}}
    circles = [(10, OWNER, None, 5), (11, None, 77, 5), (12, 2, None, 3), (13, OWNER, None, 0, "custom"), (14, OWNER, None, 8)]
    members = [
        (10, 2, "subscriber", "active", NOW + 99),     # yes
        (14, 2, "subscriber", "active", NOW + 99),     # and to a second paid circle: still one person
        (10, 3, "subscriber", "active", NOW - 1),      # lapsed
        (13, 4, "member", "active", 0),                # in a custom circle: never (nobody is told)
        (10, 5, "subscriber", "ended", NOW + 99),      # ended
        (10, 6, "subscriber", "active", NOW + 99),     # yes, but keeps their subscriptions to themselves
        (11, OWNER, "subscriber", "active", NOW + 99),  # a guild's Circle: never on a person's list
        (12, OWNER, "subscriber", "active", NOW + 99),  # the owner subscribes to 2's paid circle
    ]
    db = make(users, circles=circles, members=members)
    numbers = fl.counts(db, OWNER, NOW)
    assert numbers["subscribers"] == 2 and numbers["subscriptions"] == 1
    assert ids(db, "subscribers", 9) == [2] and ids(db, "subscribers", 6) == [2, 6]
    assert ids(db, "subscriptions", 9) == [2]
    assert fl.circle_price(db, OWNER) == 5 and fl.circle_price(db, 3) == 0


def test_pages_and_the_count_of_what_is_shown():
    users = {OWNER: {}, **{i: {} for i in range(2, 30)}}
    db = make(users, follows=[(i, OWNER) for i in range(2, 30)])
    first, more, shown = fl.page(db, "followers", OWNER, None, None, NOW, 1)
    second, more2, _ = fl.page(db, "followers", OWNER, None, None, NOW, 2)
    assert len(first) == fl.PAGE_SIZE and more and shown == 28
    assert len(second) == 3 and not more2 and not set(first) & set(second)
