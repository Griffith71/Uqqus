"""Similar guilds' SQL (ruqqus/helpers/similar_guilds_store.py) on SQLite: guilds that members have in common count only from
enough shared members and between guilds of enough members, only accounts that are not banned, deleted or private are counted,
private, Circle and banned guilds and profiles never appear, a mega-guild does not win by size, the same topic fills in for a
small guild, and a reason carries no number."""
import math

from sqlalchemy import create_engine, text
from sqlalchemy.orm import Session

from ruqqus.helpers import similar_guilds_store as store
from ruqqus.helpers.suggestions import reason_text

TABLES = """
CREATE TABLE users (id INTEGER PRIMARY KEY, is_banned INTEGER DEFAULT 0, is_deleted BOOLEAN DEFAULT 0, is_private BOOLEAN DEFAULT 0);
CREATE TABLE boards (id INTEGER PRIMARY KEY, name TEXT, is_private BOOLEAN DEFAULT 0, is_banned BOOLEAN DEFAULT 0,
    is_circle BOOLEAN DEFAULT 0, all_opt_out BOOLEAN DEFAULT 0, subcat_id INTEGER DEFAULT 0, cat_id INTEGER,
    stored_subscriber_count INTEGER DEFAULT 0);
CREATE TABLE subscriptions (id INTEGER PRIMARY KEY, user_id INTEGER, board_id INTEGER, is_active BOOLEAN DEFAULT 1);
"""

ANCHOR = 1


def make(boards=(), members=None, users=()):
    """`members`: {guild id: iterable of user ids}; every user named there gets an ordinary account unless `users` says
    otherwise ({id: {"is_banned": 1}})."""
    db = Session(create_engine("sqlite://"))
    for statement in TABLES.split(";"):
        if statement.strip():
            db.execute(text(statement))
    for id, name, extra in boards:
        row = {"id": id, "name": name, "is_private": 0, "is_banned": 0, "is_circle": 0, "all_opt_out": 0, "subcat_id": 0,
               "cat_id": None, "stored_subscriber_count": 0, **extra}
        db.execute(text("INSERT INTO boards (id, name, is_private, is_banned, is_circle, all_opt_out, subcat_id, cat_id, "
                        "stored_subscriber_count) VALUES (:id, :name, :is_private, :is_banned, :is_circle, :all_opt_out, "
                        ":subcat_id, :cat_id, :stored_subscriber_count)"), row)
    people = {u for ids in (members or {}).values() for u in ids}
    special = dict(users) if not isinstance(users, dict) else users
    for uid in sorted(people):
        row = {"id": uid, "is_banned": 0, "is_deleted": 0, "is_private": 0, **special.get(uid, {})}
        db.execute(text("INSERT INTO users (id, is_banned, is_deleted, is_private) VALUES (:id, :is_banned, :is_deleted, :is_private)"), row)
    for board, ids in (members or {}).items():
        for uid in ids:
            db.execute(text("INSERT INTO subscriptions (user_id, board_id, is_active) VALUES (:u, :b, 1)"), {"u": uid, "b": board})
    return db


def people(first, count):
    return list(range(first, first + count))


def ids(rows):
    return [r[0] for r in rows]


def test_guilds_need_five_members_in_common_and_twenty_five_members_each():
    anchor = people(100, 40)
    boards = [(1, "anchor", {}), (2, "twin", {}), (3, "four", {}), (4, "small", {}), (5, "five", {})]
    members = {
        1: anchor,
        2: anchor[:30] + people(500, 5),          # 30 shared, 35 members: shown
        3: anchor[:4] + people(600, 30),          # only 4 in common: never
        4: anchor[:10] + people(700, 10),         # 10 in common but only 20 members: never (the size floor)
        5: anchor[:5] + people(800, 25),          # exactly 5 in common and 30 members: shown
    }
    rows = store.co_members(make(boards, members), ANCHOR)
    assert ids(rows) == [2, 5]


def test_an_anchor_under_the_size_floor_has_no_members_in_common():
    small = people(100, 20)
    boards = [(1, "anchor", {}), (2, "twin", {})]
    members = {1: small, 2: small + people(500, 20)}
    assert store.co_members(make(boards, members), ANCHOR) == []


def test_a_mega_guild_does_not_win_by_size():
    anchor = people(100, 30)
    boards = [(1, "anchor", {}), (2, "close", {}), (3, "huge", {})]
    members = {1: anchor, 2: anchor[:20] + people(500, 5), 3: anchor[:20] + people(1000, 400)}
    rows = store.co_members(make(boards, members), ANCHOR)
    assert ids(rows) == [2, 3]
    assert math.isclose(rows[0][1], 20 / math.sqrt(30 * 25))


def test_only_counted_accounts_count_and_never_a_private_or_circle_or_banned_guild_or_profiles():
    anchor = people(100, 30)
    boards = [(1, "anchor", {}), (2, "banned_members", {}), (3, "private", {"is_private": 1}), (4, "circle", {"is_circle": 1}),
              (5, "gone", {"is_banned": 1}), (6, "systemprofile", {}), (7, "fine", {})]
    shared = anchor[:30]
    members = {1: anchor, 2: shared + people(500, 5), 3: shared + people(600, 5), 4: shared + people(700, 5),
               5: shared + people(800, 5), 6: shared + people(900, 5), 7: shared + people(1000, 5)}
    # 26 of the shared accounts are banned, deleted or private: 4 left in common with guild 2, under the floor of 5
    damaged = {uid: {"is_banned": 1} for uid in anchor[:10]} | {uid: {"is_deleted": 1} for uid in anchor[10:18]} \
        | {uid: {"is_private": 1} for uid in anchor[18:26]}
    db = make(boards, members, damaged)
    assert store.co_members(db, ANCHOR) == []                    # the anchor itself drops to 4 counted accounts: under 25 and under 5
    healthy = make(boards, members)
    assert ids(store.co_members(healthy, ANCHOR)) == [2, 7]      # (3 private, 4 Circle, 5 banned, 6 profiles are left out)


def test_a_banned_account_does_not_make_a_pair_reach_the_floor():
    anchor = people(100, 30)
    boards = [(1, "anchor", {}), (2, "twin", {})]
    members = {1: anchor, 2: anchor[:5] + people(500, 25)}
    assert ids(store.co_members(make(boards, members), ANCHOR)) == [2]
    one_banned = make(boards, members, {anchor[0]: {"is_banned": 7}})
    assert store.co_members(one_banned, ANCHOR) == []             # 4 counted in common


def test_the_same_topic_fills_in_and_never_matches_uncategorized_or_the_unlisted_subcategory():
    topic = store.Topic(10, "Cooking", 3, "Food", (10, 11, 12))
    boards = [(1, "anchor", {"subcat_id": 10, "cat_id": None}),
              (2, "sub", {"subcat_id": 10, "stored_subscriber_count": 7}),
              (3, "sibling", {"subcat_id": 11, "stored_subscriber_count": 50}),
              (4, "alone", {"subcat_id": 0, "cat_id": 3, "stored_subscriber_count": 9}),
              (5, "other", {"subcat_id": 20}),
              (6, "uncategorized", {"subcat_id": 0, "cat_id": None}),
              (7, "unlisted", {"subcat_id": 108}),
              (8, "private", {"subcat_id": 10, "is_private": 1}),
              (9, "optout", {"subcat_id": 10, "all_opt_out": 1}),
              (10, "circle", {"subcat_id": 11, "is_circle": 1})]
    rows = store.candidates(make(boards), ANCHOR, "anchor", topic, members=[])
    assert ids(rows) == [2, 3, 4]                                  # the same subcategory first, then the category
    assert reason_text(rows[0][2]) == "Also about Cooking" and reason_text(rows[1][2]) == "Also about Food"
    assert store.candidates(make(boards), ANCHOR, "anchor", None, members=[]) == []     # an Uncategorized guild has none


def test_a_guild_filed_under_its_category_alone_matches_that_category_only():
    topic = store.Topic(None, None, 3, "Food", (10, 11))
    boards = [(1, "anchor", {"subcat_id": 0, "cat_id": 3}), (2, "sub", {"subcat_id": 10}), (3, "alone", {"subcat_id": 0, "cat_id": 3}),
              (4, "elsewhere", {"subcat_id": 40, "cat_id": None})]
    assert sorted(ids(store.candidates(make(boards), ANCHOR, "anchor", topic, members=[]))) == [2, 3]


def test_members_in_common_rank_above_the_same_topic_and_no_reason_has_a_number():
    anchor = people(100, 30)
    topic = store.Topic(10, "Cooking", 3, "Food", (10,))
    boards = [(1, "anchor", {"subcat_id": 10}), (2, "twin", {"subcat_id": 0}), (3, "topical", {"subcat_id": 10, "stored_subscriber_count": 900})]
    members = {1: anchor, 2: anchor[:20] + people(500, 10)}
    rows = store.candidates(make(boards, members), ANCHOR, "anchor", topic)
    assert ids(rows) == [2, 3]
    assert [reason_text(r[2]) for r in rows] == ["Shares members with +anchor", "Also about Cooking"]
    assert not any(ch.isdigit() for r in rows for ch in reason_text(r[2]))


def test_the_anchor_is_never_its_own_suggestion():
    anchor = people(100, 30)
    boards = [(1, "anchor", {"subcat_id": 10})]
    rows = store.candidates(make(boards, {1: anchor}), ANCHOR, "anchor", store.Topic(10, "Cooking", 3, "Food", (10,)))
    assert rows == []

