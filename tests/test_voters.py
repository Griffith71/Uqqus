"""Who upvoted (ruqqus/helpers/voters.py): the real SQL on in-memory SQLite (tables as in schema.sql), and the
small pure rule. The promise under test: the author sees an upvote only from someone they follow AND who
follows them back, and a downvote is never in any answer."""
from types import SimpleNamespace as NS

import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.orm import Session

from ruqqus.helpers import voters

AUTHOR, OTHER_AUTHOR = 1, 50
POST, OTHER_POST, COMMENT = 100, 101, 200

# people
MUTUAL, ONE_WAY_OUT, ONE_WAY_IN, DOWNVOTER, REMOVED, SWITCHED = 2, 3, 4, 5, 6, 7
BANNED, DELETED, BLOCKED_BY_AUTHOR, BLOCKED_AUTHOR, ELSEWHERE, NEWER, STRANGER = 8, 9, 10, 11, 12, 13, 14


@pytest.fixture
def db():
    engine = create_engine("sqlite://")
    with engine.begin() as conn:
        conn.execute(text("CREATE TABLE users (id integer PRIMARY KEY, is_banned integer DEFAULT 0, is_deleted boolean DEFAULT 0)"))
        conn.execute(text("CREATE TABLE follows (id integer PRIMARY KEY AUTOINCREMENT, user_id integer, target_id integer)"))
        conn.execute(text("CREATE TABLE userblocks (id integer PRIMARY KEY AUTOINCREMENT, user_id integer, target_id integer)"))
        conn.execute(text("CREATE TABLE votes (id integer PRIMARY KEY AUTOINCREMENT, user_id integer, submission_id integer, vote_type integer, created_utc integer)"))
        conn.execute(text("CREATE TABLE commentvotes (id integer PRIMARY KEY AUTOINCREMENT, user_id integer, comment_id integer, vote_type integer, created_utc integer)"))
        for uid in range(1, 15):
            conn.execute(text("INSERT INTO users (id) VALUES (:i)"), {"i": uid})
        conn.execute(text("UPDATE users SET is_banned = 1 WHERE id = :i"), {"i": BANNED})
        conn.execute(text("UPDATE users SET is_deleted = 1 WHERE id = :i"), {"i": DELETED})
    session = Session(engine)
    yield session
    session.close()


def follow(db, who, whom):
    db.execute(text("INSERT INTO follows (user_id, target_id) VALUES (:a, :b)"), {"a": who, "b": whom})


def friends(db, *people, author=AUTHOR):
    for person in people:
        follow(db, author, person)
        follow(db, person, author)


def vote(db, user, kind=1, post=POST, at=1000):
    db.execute(text("INSERT INTO votes (user_id, submission_id, vote_type, created_utc) VALUES (:u, :p, :k, :t)"),
               {"u": user, "p": post, "k": kind, "t": at})


def comment_vote(db, user, kind=1, comment=COMMENT, at=1000):
    db.execute(text("INSERT INTO commentvotes (user_id, comment_id, vote_type, created_utc) VALUES (:u, :c, :k, :t)"),
               {"u": user, "c": comment, "k": kind, "t": at})


def listed(db, kind="post", item=None, author=AUTHOR, **kw):
    return voters.upvoter_ids(db, kind, item or (POST if kind == "post" else COMMENT), author, **kw)


# --- the rule ----------------------------------------------------------------------------------------

def test_a_mutual_followers_upvote_is_listed(db):
    friends(db, MUTUAL)
    vote(db, MUTUAL)
    assert listed(db) == [MUTUAL]


def test_following_in_only_one_direction_is_not_enough(db):
    follow(db, AUTHOR, ONE_WAY_OUT)          # the author follows them, they don't follow back
    follow(db, ONE_WAY_IN, AUTHOR)           # they follow the author, who doesn't follow back
    vote(db, ONE_WAY_OUT)
    vote(db, ONE_WAY_IN)
    vote(db, STRANGER)                       # no follow at all
    assert listed(db) == []


def test_a_downvote_is_never_listed_even_from_a_mutual_follower(db):
    friends(db, DOWNVOTER, MUTUAL)
    vote(db, DOWNVOTER, kind=-1)
    vote(db, MUTUAL)
    assert listed(db) == [MUTUAL]
    assert DOWNVOTER not in listed(db)


def test_a_removed_vote_and_a_vote_changed_to_a_downvote_are_not_listed(db):
    friends(db, REMOVED, SWITCHED)
    vote(db, REMOVED, kind=0)                # taken back
    vote(db, SWITCHED, kind=-1)              # changed from up to down: the row now says -1
    assert listed(db) == []


def test_a_vote_with_no_type_is_not_listed(db):
    friends(db, MUTUAL)
    db.execute(text("INSERT INTO votes (user_id, submission_id, vote_type, created_utc) VALUES (:u, :p, NULL, 1)"), {"u": MUTUAL, "p": POST})
    assert listed(db) == []


def test_banned_and_deleted_accounts_are_left_out(db):
    friends(db, BANNED, DELETED, MUTUAL)
    for person in (BANNED, DELETED, MUTUAL):
        vote(db, person)
    assert listed(db) == [MUTUAL]


def test_anyone_blocked_either_way_is_left_out(db):
    friends(db, BLOCKED_BY_AUTHOR, BLOCKED_AUTHOR, MUTUAL)
    db.execute(text("INSERT INTO userblocks (user_id, target_id) VALUES (:a, :b)"), {"a": AUTHOR, "b": BLOCKED_BY_AUTHOR})
    db.execute(text("INSERT INTO userblocks (user_id, target_id) VALUES (:a, :b)"), {"a": BLOCKED_AUTHOR, "b": AUTHOR})
    for person in (BLOCKED_BY_AUTHOR, BLOCKED_AUTHOR, MUTUAL):
        vote(db, person)
    assert listed(db) == [MUTUAL]


def test_a_duplicate_follow_row_lists_the_person_once(db):
    friends(db, MUTUAL)
    follow(db, AUTHOR, MUTUAL)
    follow(db, MUTUAL, AUTHOR)
    vote(db, MUTUAL)
    assert listed(db) == [MUTUAL]


# --- only this item, only this author --------------------------------------------------------------------

def test_another_posts_voters_and_another_authors_friends_do_not_leak(db):
    friends(db, MUTUAL, ELSEWHERE)
    vote(db, ELSEWHERE, post=OTHER_POST)                       # a friend, but on another post
    friends(db, STRANGER, author=OTHER_AUTHOR)
    vote(db, STRANGER)                                         # someone else's friend, on this post
    assert listed(db) == []
    assert listed(db, author=OTHER_AUTHOR) == [STRANGER]


def test_the_author_themselves_is_never_listed(db):
    follow(db, AUTHOR, AUTHOR)
    vote(db, AUTHOR)
    assert listed(db) == []


def test_newest_vote_first_and_the_limit_is_kept(db):
    friends(db, MUTUAL, NEWER, ELSEWHERE)
    vote(db, MUTUAL, at=1000)
    vote(db, ELSEWHERE, at=2000)
    vote(db, NEWER, at=3000)
    assert listed(db) == [NEWER, ELSEWHERE, MUTUAL]
    assert listed(db, limit=2) == [NEWER, ELSEWHERE]
    assert voters.LIMIT == 100


# --- comments ---------------------------------------------------------------------------------------------

def test_the_comment_list_follows_the_same_rule(db):
    friends(db, MUTUAL, DOWNVOTER, REMOVED, BANNED)
    comment_vote(db, MUTUAL)
    comment_vote(db, DOWNVOTER, kind=-1)
    comment_vote(db, REMOVED, kind=0)
    comment_vote(db, BANNED)
    comment_vote(db, STRANGER)
    assert listed(db, "comment") == [MUTUAL]


def test_a_post_vote_never_shows_up_as_a_comment_vote_or_the_reverse(db):
    friends(db, MUTUAL)
    vote(db, MUTUAL, post=7)
    comment_vote(db, MUTUAL, comment=7)
    assert listed(db, "post", 7) == [MUTUAL] and listed(db, "comment", 7) == [MUTUAL]
    assert listed(db, "post", 8) == [] and listed(db, "comment", 8) == []


# --- what the statements may say --------------------------------------------------------------------------

def test_the_statements_only_ever_select_upvotes():
    for kind, statement in voters._STATEMENTS.items():
        sql = str(statement)
        assert "v.vote_type = 1" in sql, kind
        for forbidden in ("-1", "vote_type <>", "vote_type !=", "vote_type = 0", "vote_type IN", "OR v.vote_type", "vote_type >", "vote_type <"):
            assert forbidden not in sql, (kind, forbidden)
        assert sql.count("vote_type") == 1, kind


# --- the accounts -------------------------------------------------------------------------------------------

def test_the_accounts_keep_the_order_and_skip_any_that_are_gone(db, monkeypatch):
    friends(db, MUTUAL, NEWER, ELSEWHERE)
    vote(db, MUTUAL, at=1000)
    vote(db, ELSEWHERE, at=2000)
    vote(db, NEWER, at=3000)
    seen = {}

    def fake_users(_db, ids, viewer):
        seen["ids"], seen["viewer"] = list(ids), viewer
        return {i: NS(id=i, username=f"u{i}") for i in ids if i != ELSEWHERE}     # one is filtered out for this viewer

    monkeypatch.setattr(voters, "_users", fake_users)
    people = voters.friends_who_upvoted(db, "post", POST, AUTHOR, NS(id=AUTHOR))
    assert [p.id for p in people] == [NEWER, MUTUAL]
    assert seen["ids"] == [NEWER, ELSEWHERE, MUTUAL] and seen["viewer"].id == AUTHOR


def test_nobody_to_list_never_loads_any_accounts(db, monkeypatch):
    monkeypatch.setattr(voters, "_users", lambda *a: pytest.fail("no ids, no query"))
    assert voters.friends_who_upvoted(db, "post", POST, AUTHOR, NS(id=AUTHOR)) == []


# --- who may see it -------------------------------------------------------------------------------------------

def item(author=AUTHOR, banned=False, deleted=0):
    return NS(author_id=author, is_banned=banned, deleted_utc=deleted)


def test_only_the_author_of_a_live_item_may_see_it():
    me = NS(id=AUTHOR)
    assert voters.may_see(item(), me)
    assert not voters.may_see(item(author=99), me)          # anyone else, a forwarder included (a copy keeps the original author's id)
    assert not voters.may_see(item(), NS(id=99))
    assert not voters.may_see(item(), None)                  # a visitor
    assert not voters.may_see(item(banned=True), me)
    assert not voters.may_see(item(deleted=5), me)
