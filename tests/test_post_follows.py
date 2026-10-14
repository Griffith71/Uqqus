"""Follow a post (ruqqus/helpers/post_follows.py): who is told of a comment, and the real SQL on an in-memory
SQLite database with the tables it reads (columns as in schema.sql)."""
from types import SimpleNamespace as NS

import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.orm import Session

from ruqqus.helpers import post_follows as pf

AUTHOR, FORWARDER, ALICE, BOB, CAROL, COMMENTER = 1, 2, 3, 4, 5, 6
PROFILE_POST, COPY, COMMENT_COPY, OTHER = 100, 101, 102, 103


@pytest.fixture
def db():
    engine = create_engine("sqlite://")
    with engine.begin() as conn:
        conn.execute(text("CREATE TABLE users (id integer PRIMARY KEY, is_deleted boolean DEFAULT 0, is_banned integer DEFAULT 0, unban_utc integer DEFAULT 0)"))
        conn.execute(text("CREATE TABLE userblocks (id integer PRIMARY KEY AUTOINCREMENT, user_id integer, target_id integer)"))
        conn.execute(text("CREATE TABLE usermutes (id integer PRIMARY KEY AUTOINCREMENT, user_id integer, target_id integer)"))
        conn.execute(text("CREATE TABLE post_follows (id integer PRIMARY KEY AUTOINCREMENT, user_id integer NOT NULL, post_id integer NOT NULL, "
                          "created_utc integer DEFAULT 0, UNIQUE (user_id, post_id))"))
        conn.execute(text("CREATE TABLE submissions (id integer PRIMARY KEY, author_id integer)"))
        conn.execute(text("CREATE TABLE forwardrels (id integer PRIMARY KEY AUTOINCREMENT, primary_submission_id integer, board_id integer, "
                          "forward_submission_id integer, forwarded_by_id integer)"))
        conn.execute(text("CREATE TABLE comment_forwardrels (id integer PRIMARY KEY AUTOINCREMENT, comment_id integer, board_id integer, "
                          "promoted_submission_id integer, promoted_by_id integer)"))
        for uid in (AUTHOR, FORWARDER, ALICE, BOB, CAROL, COMMENTER):
            conn.execute(text("INSERT INTO users (id) VALUES (:i)"), {"i": uid})
        # a post on the author's profile, a copy FORWARDER put in a guild (it keeps the author's author_id),
        # a comment forwarded into a post by FORWARDER, and a post nobody forwarded
        conn.execute(text("INSERT INTO submissions (id, author_id) VALUES (100, 1), (101, 1), (102, 5), (103, 1)"))
        conn.execute(text("INSERT INTO forwardrels (primary_submission_id, board_id, forward_submission_id, forwarded_by_id) VALUES (100, 9, 101, 2)"))
        conn.execute(text("INSERT INTO comment_forwardrels (comment_id, board_id, promoted_submission_id, promoted_by_id) VALUES (50, 9, 102, 2)"))
    session = Session(engine)
    yield session
    session.close()


def post(id, author=AUTHOR, **extra):
    return NS(id=id, author_id=author, audience=0, **extra)


def block(db, user, target):
    db.execute(text("INSERT INTO userblocks (user_id, target_id) VALUES (:u, :t)"), {"u": user, "t": target})


def mute(db, user, target):
    db.execute(text("INSERT INTO usermutes (user_id, target_id) VALUES (:u, :t)"), {"u": user, "t": target})


# --- who is responsible ---------------------------------------------------------------------------------

def test_a_profile_post_is_the_authors_responsibility(db):
    assert pf.responsible_id(db, post(PROFILE_POST)) == AUTHOR


def test_a_forward_copy_is_the_forwarders_not_the_original_authors(db):
    assert pf.responsible_id(db, post(COPY)) == FORWARDER


def test_a_comment_forwarded_into_a_post_is_the_forwarders(db):
    assert pf.responsible_id(db, post(COMMENT_COPY, author=CAROL)) == FORWARDER


def test_an_author_who_forwards_their_own_post_is_responsible_for_the_copy(db):
    db.execute(text("INSERT INTO submissions (id, author_id) VALUES (104, 1)"))
    db.execute(text("INSERT INTO forwardrels (primary_submission_id, board_id, forward_submission_id, forwarded_by_id) VALUES (100, 8, 104, 1)"))
    assert pf.responsible_id(db, post(104)) == AUTHOR


def test_a_page_of_posts_is_resolved_in_one_go(db):
    posts = [post(PROFILE_POST), post(COPY), post(COMMENT_COPY, author=CAROL)]
    assert pf.responsible_ids(db, posts) == {PROFILE_POST: AUTHOR, COPY: FORWARDER, COMMENT_COPY: FORWARDER}
    assert pf.responsible_ids(db, []) == {}


# --- who is told of a comment ---------------------------------------------------------------------------

def test_the_original_author_is_not_told_of_comments_on_a_copy_they_did_not_forward(db):
    assert pf.recipients(db, post(COPY), COMMENTER) == [FORWARDER]


def test_the_author_is_told_of_comments_on_their_profile_post(db):
    assert pf.recipients(db, post(PROFILE_POST), COMMENTER) == [AUTHOR]


def test_followers_are_told_as_well_and_the_responsible_person_comes_first(db):
    pf.follow(db, ALICE, post(COPY))
    pf.follow(db, BOB, post(COPY))
    assert pf.recipients(db, post(COPY), COMMENTER) == [FORWARDER, ALICE, BOB]


def test_the_original_author_can_follow_the_copy_to_be_told(db):
    pf.follow(db, AUTHOR, post(COPY))
    assert pf.recipients(db, post(COPY), COMMENTER) == [FORWARDER, AUTHOR]


def test_the_commenter_is_never_told_of_their_own_comment(db):
    pf.follow(db, COMMENTER, post(PROFILE_POST))
    assert pf.recipients(db, post(PROFILE_POST), COMMENTER) == [AUTHOR]
    assert pf.recipients(db, post(PROFILE_POST), AUTHOR) == [COMMENTER]      # the author's own comment: only the follower


def test_a_follow_belongs_to_one_post_not_to_its_copies(db):
    pf.follow(db, ALICE, post(PROFILE_POST))
    assert ALICE in pf.recipients(db, post(PROFILE_POST), COMMENTER)
    assert ALICE not in pf.recipients(db, post(COPY), COMMENTER)


def test_a_block_in_either_direction_stops_the_notification(db):
    pf.follow(db, ALICE, post(OTHER))
    pf.follow(db, BOB, post(OTHER))
    block(db, ALICE, COMMENTER)       # ALICE blocked the commenter
    block(db, COMMENTER, BOB)         # the commenter blocked BOB
    assert pf.followers(db, OTHER, COMMENTER) == []


def test_a_muted_commenter_is_not_told_unless_the_comment_is_anonymous(db):
    pf.follow(db, ALICE, post(OTHER))
    mute(db, ALICE, COMMENTER)
    assert pf.followers(db, OTHER, COMMENTER) == []
    assert pf.followers(db, OTHER, COMMENTER, anonymous=True) == [ALICE]


def test_deleted_and_banned_accounts_are_skipped_but_a_temporary_ban_is_not(db):
    for uid in (ALICE, BOB, CAROL):
        pf.follow(db, uid, post(OTHER))
    db.execute(text("UPDATE users SET is_deleted = 1 WHERE id = :i"), {"i": ALICE})
    db.execute(text("UPDATE users SET is_banned = 7, unban_utc = 0 WHERE id = :i"), {"i": BOB})
    db.execute(text("UPDATE users SET is_banned = 7, unban_utc = 99 WHERE id = :i"), {"i": CAROL})
    assert pf.followers(db, OTHER, COMMENTER) == [CAROL]


def test_a_follower_who_may_no_longer_read_the_post_is_not_told(db):
    pf.follow(db, ALICE, post(OTHER))
    pf.follow(db, BOB, post(OTHER))
    chosen = pf.recipients(db, post(OTHER), COMMENTER, visible=lambda ids: {i for i in ids if i != ALICE})
    assert chosen == [AUTHOR, BOB]


def test_at_most_the_cap_of_followers_is_told(db, monkeypatch):
    monkeypatch.setattr(pf, "MAX_NOTIFIED", 2)
    for uid in (ALICE, BOB, CAROL):
        pf.follow(db, uid, post(OTHER))
    assert pf.followers(db, OTHER, COMMENTER) == [ALICE, BOB]


# --- following -------------------------------------------------------------------------------------------

def test_you_cannot_follow_a_post_you_are_told_of_anyway(db):
    with pytest.raises(pf.FollowError) as error:
        pf.follow(db, AUTHOR, post(PROFILE_POST))
    assert error.value.message == "You already get notifications for this post."
    with pytest.raises(pf.FollowError):
        pf.follow(db, FORWARDER, post(COPY))


def test_following_twice_and_unfollowing_what_you_do_not_follow_are_refused(db):
    pf.follow(db, ALICE, post(OTHER))
    with pytest.raises(pf.FollowError):
        pf.follow(db, ALICE, post(OTHER))
    pf.unfollow(db, ALICE, post(OTHER))
    with pytest.raises(pf.FollowError):
        pf.unfollow(db, ALICE, post(OTHER))


def test_there_is_a_cap_on_followed_posts(db, monkeypatch):
    monkeypatch.setattr(pf, "MAX_FOLLOWED", 2)
    pf.follow(db, ALICE, post(100))
    pf.follow(db, ALICE, post(101))
    with pytest.raises(pf.FollowError) as error:
        pf.follow(db, ALICE, post(103))
    assert "2 posts at most" in error.value.message


def test_the_menu_state_of_a_page(db):
    pf.follow(db, ALICE, post(OTHER))
    posts = [post(PROFILE_POST), post(COPY), post(OTHER)]
    pf.attach(db, posts, NS(id=ALICE))
    assert [p._follow_state for p in posts] == ["can_follow", "can_follow", "following"]

    posts = [post(PROFILE_POST), post(COPY)]
    pf.attach(db, posts, NS(id=AUTHOR))
    assert [p._follow_state for p in posts] == ["own", "can_follow"]       # the author is not told of the copy

    pf.attach(db, posts, NS(id=FORWARDER))
    assert [p._follow_state for p in posts] == ["can_follow", "own"]

    pf.attach(db, posts, None)
    assert [p._follow_state for p in posts] == [None, None]


# --- the notification lists ------------------------------------------------------------------------------

def test_the_posts_a_member_counts_as_theirs(db):
    pf.follow(db, ALICE, post(OTHER))
    assert pf.owned_ids(db, AUTHOR) == {PROFILE_POST, OTHER}      # not COPY: FORWARDER forwarded it
    assert pf.owned_ids(db, FORWARDER) == {COPY, COMMENT_COPY}
    assert pf.owned_ids(db, ALICE) == {OTHER}
    assert pf.owned_ids(db, CAROL) == set()                       # authored COMMENT_COPY, but FORWARDER forwarded it


def test_a_followed_copy_is_counted_for_the_original_author_who_follows_it(db):
    pf.follow(db, AUTHOR, post(COPY))
    assert COPY in pf.owned_ids(db, AUTHOR)


def test_the_subquery_runs_inside_another_query(db):
    db.execute(text("CREATE TABLE comments (id integer PRIMARY KEY, parent_submission integer)"))
    db.execute(text("INSERT INTO comments (id, parent_submission) VALUES (1, 100), (2, 101), (3, 103)"))
    rows = db.execute(text("SELECT id FROM comments WHERE parent_submission IN (SELECT id FROM (SELECT 1 AS id) WHERE 0)")).fetchall()
    assert rows == []
    from sqlalchemy import Column, Integer, MetaData, Table, select
    comments = Table("comments", MetaData(), Column("id", Integer), Column("parent_submission", Integer))
    ids = [r[0] for r in db.execute(select(comments.c.id).where(comments.c.parent_submission.in_(pf.owned_post_ids(AUTHOR))).order_by(comments.c.id))]
    assert ids == [1, 3]
