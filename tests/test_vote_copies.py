"""One vote per person across the copies of a post (ruqqus/helpers/vote_copies.py): the rules, and the real
SQL on an in-memory SQLite database with the three tables it reads (columns as in schema.sql)."""
import time
from types import SimpleNamespace as NS

import pytest
from sqlalchemy import create_engine, event, text
from sqlalchemy.orm import Session

from ruqqus.helpers import vote_copies as vc
from ruqqus.helpers.base36 import base36encode

NOW = int(time.time())
DAY = 86400
PROFILE = "systemprofile"

AUTHOR, ME, OTHER = 10, 20, 21
ORIGINAL, COPY_A, COPY_B, UNRELATED = 1, 2, 3, 4


# --- the rules ------------------------------------------------------------------------------------------

def test_only_the_posts_author_is_exempt():
    assert vc.exempt(10, 10) and not vc.exempt(20, 10)


def test_archived_is_the_same_age_as_the_posts_own_rule():
    assert vc.ARCHIVE_AFTER_SECONDS == 60 * 60 * 24 * 180
    assert not vc.is_archived(NOW - vc.ARCHIVE_AFTER_SECONDS, NOW)
    assert vc.is_archived(NOW - vc.ARCHIVE_AFTER_SECONDS - 1, NOW)


def test_a_place_reads_as_the_original_post_or_a_guild():
    assert vc.place_label("systemprofile", PROFILE) == "the original post"
    assert vc.place_label("SystemProfile", PROFILE) == "the original post"
    assert vc.place_label("general", PROFILE) == "+general"


def test_the_messages_say_where_and_what_to_do():
    assert vc.message("+general", False) == "You already voted on this post, in +general. Remove that vote to vote here."
    assert vc.message("the original post", False) == "You already voted on this post, in the original post. Remove that vote to vote here."
    assert vc.message("+general", True) == "You already voted on this post, in +general, and that vote can't be changed any more."


# --- the database -----------------------------------------------------------------------------------------

@pytest.fixture
def db(monkeypatch):
    monkeypatch.setattr(vc, "_profile_name", lambda: PROFILE)
    engine = create_engine("sqlite://")
    with engine.begin() as conn:
        conn.execute(text("CREATE TABLE boards (id integer PRIMARY KEY, name text, is_banned boolean DEFAULT 0)"))
        conn.execute(text("CREATE TABLE submissions (id integer PRIMARY KEY, repost_id integer, board_id integer, author_id integer, "
                          "is_banned boolean DEFAULT 0, deleted_utc integer DEFAULT 0, created_utc integer DEFAULT 0)"))
        conn.execute(text("CREATE TABLE votes (id integer PRIMARY KEY AUTOINCREMENT, user_id integer, submission_id integer, "
                          "vote_type integer, created_utc integer)"))
        conn.execute(text("INSERT INTO boards (id, name) VALUES (1, 'systemprofile'), (2, 'general'), (3, 'news')"))
        # one post on the author's profile, a copy in each of two guilds, and a post that has nothing to do with them
        conn.execute(text(f"INSERT INTO submissions (id, repost_id, board_id, author_id, created_utc) VALUES "
                          f"({ORIGINAL}, NULL, 1, {AUTHOR}, {NOW}), ({COPY_A}, {ORIGINAL}, 2, {AUTHOR}, {NOW}), "
                          f"({COPY_B}, {ORIGINAL}, 3, {AUTHOR}, {NOW}), ({UNRELATED}, 0, 2, {AUTHOR}, {NOW})"))
    session = Session(engine)
    session.engine = engine
    yield session
    session.close()


def vote(db, user, post, kind=1, at=None):
    db.execute(text("INSERT INTO votes (user_id, submission_id, vote_type, created_utc) VALUES (:u, :p, :k, :t)"),
               {"u": user, "p": post, "k": kind, "t": at or NOW})


def post(id, repost_id=None, author=AUTHOR, **extra):
    return NS(id=id, repost_id=repost_id, author_id=author, **extra)


def the(id):
    """The post object for one of the fixture posts."""
    return {ORIGINAL: post(ORIGINAL), COPY_A: post(COPY_A, ORIGINAL), COPY_B: post(COPY_B, ORIGINAL), UNRELATED: post(UNRELATED)}[id]


def ask(db, user, id):
    return vc.refusal(db, NS(id=user), the(id), NOW)


def test_with_no_other_vote_there_is_nothing_to_refuse(db):
    for id in (ORIGINAL, COPY_A, COPY_B, UNRELATED):
        assert ask(db, ME, id) is None


def test_a_vote_on_one_copy_stops_a_vote_on_another_and_names_where_it_is(db):
    vote(db, ME, COPY_A)
    refused = ask(db, ME, COPY_B)
    assert refused["error"] == "You already voted on this post, in +general. Remove that vote to vote here."
    assert refused["voted_in"] == {"id": base36encode(COPY_A), "guild": "general", "label": "+general"}


def test_the_original_is_one_of_the_copies_both_ways(db):
    vote(db, ME, ORIGINAL, -1)
    refused = ask(db, ME, COPY_A)
    assert "in the original post" in refused["error"] and refused["voted_in"]["label"] == "the original post"
    db.execute(text("DELETE FROM votes"))
    vote(db, ME, COPY_B)
    assert "in +news" in ask(db, ME, ORIGINAL)["error"]


def test_a_vote_on_the_post_itself_is_not_another_vote(db):
    vote(db, ME, COPY_A)
    assert ask(db, ME, COPY_A) is None                 # changing or repeating your own vote is fine


def test_a_removed_vote_frees_the_person(db):
    vote(db, ME, COPY_A, kind=0)
    assert ask(db, ME, COPY_B) is None and ask(db, ME, ORIGINAL) is None


def test_a_vote_with_no_type_is_not_active(db):
    db.execute(text("INSERT INTO votes (user_id, submission_id, vote_type, created_utc) VALUES (:u, :p, NULL, :t)"), {"u": ME, "p": COPY_A, "t": NOW})
    assert ask(db, ME, COPY_B) is None


@pytest.mark.parametrize("change", [
    "UPDATE submissions SET is_banned = 1 WHERE id = 2",
    "UPDATE submissions SET deleted_utc = 5 WHERE id = 2",
    "UPDATE boards SET is_banned = 1 WHERE id = 2",
])
def test_a_vote_on_a_removed_deleted_or_banned_guild_copy_no_longer_counts(db, change):
    vote(db, ME, COPY_A)
    assert ask(db, ME, COPY_B) is not None
    db.execute(text(change))
    assert ask(db, ME, COPY_B) is None


def test_a_vote_on_an_archived_copy_still_counts_and_says_it_cannot_be_changed(db):
    old = NOW - vc.ARCHIVE_AFTER_SECONDS - DAY
    vote(db, ME, COPY_A)
    db.execute(text("UPDATE submissions SET created_utc = :t WHERE id = 2"), {"t": old})    # it is the POST that is archived
    refused = ask(db, ME, COPY_B)
    assert refused["error"].endswith("and that vote can't be changed any more.")


def test_another_posts_votes_and_another_persons_votes_do_not_count(db):
    vote(db, ME, UNRELATED)                            # a different post altogether
    vote(db, OTHER, ORIGINAL)                          # someone else's vote
    assert ask(db, ME, COPY_A) is None and ask(db, ME, ORIGINAL) is None
    assert ask(db, ME, UNRELATED) is None


def test_a_forwarded_comment_has_no_family(db):
    # a post made from a comment has no repost_id, so a vote on it never blocks another post
    db.execute(text("UPDATE submissions SET repost_id = 0 WHERE id = 4"))
    vote(db, ME, UNRELATED)
    assert ask(db, ME, ORIGINAL) is None and ask(db, ME, COPY_A) is None


def test_the_author_is_exempt(db):
    vote(db, AUTHOR, ORIGINAL)
    vote(db, AUTHOR, COPY_A)
    assert ask(db, AUTHOR, COPY_B) is None
    assert vc.refusal(db, NS(id=AUTHOR), the(COPY_B), NOW) is None


def test_with_two_older_votes_it_names_the_earliest(db):
    vote(db, ME, COPY_B, at=NOW - 50)
    vote(db, ME, COPY_A, at=NOW - 500)                  # the legacy case: votes on two copies already
    assert ask(db, ME, ORIGINAL)["voted_in"]["guild"] == "general"
    # changing either one is refused while the other is active (removing one is never asked, so it is always allowed)
    assert ask(db, ME, COPY_A) is not None and ask(db, ME, COPY_B) is not None


# --- the lock -----------------------------------------------------------------------------------------------

def test_the_lock_is_a_per_person_per_family_advisory_lock_on_postgres_only(db):
    seen = []
    fake = NS(get_bind=lambda: NS(dialect=NS(name="postgresql")), execute=lambda sql, params: seen.append((str(sql), params)))
    vc.lock(fake, 20, 7)
    assert seen == [("SELECT pg_advisory_xact_lock(:user, :family)", {"user": 20, "family": 7})]
    # on SQLite (these tests) it does nothing at all
    counted = []
    event.listen(db.engine, "before_cursor_execute", lambda *a: counted.append(a[2]))
    vc.lock(db, 20, 7)
    assert counted == []


def test_the_refusal_takes_the_lock_before_it_looks_and_not_for_the_author(db):
    order = []
    original_lock, original_other = vc.lock, vc.other_vote
    vc.lock = lambda *a: order.append("lock")
    vc.other_vote = lambda *a: (order.append("look"), None)[1]
    try:
        vc.refusal(db, NS(id=ME), the(COPY_A), NOW)
        assert order == ["lock", "look"]
        order.clear()
        vc.refusal(db, NS(id=AUTHOR), the(COPY_A), NOW)
        assert order == []
    finally:
        vc.lock, vc.other_vote = original_lock, original_other


# --- a page ---------------------------------------------------------------------------------------------------

def page(*ids):
    return [the(i) for i in ids]


def queries(db):
    seen = []
    event.listen(db.engine, "before_cursor_execute", lambda *a: seen.append(a[2]))
    return seen


def elsewhere(posts):
    return {p.id: (p._voted_elsewhere or {}).get("label") for p in posts}


def test_a_page_marks_the_other_copies_of_a_post_the_viewer_voted_on(db):
    vote(db, ME, COPY_A)
    posts = page(ORIGINAL, COPY_A, COPY_B, UNRELATED)
    posts[1]._voted = 1                                  # the loader has set the viewer's own vote on that post
    vc.attach(db, posts, NS(id=ME), NOW)
    assert elsewhere(posts) == {ORIGINAL: "+general", COPY_A: None, COPY_B: "+general", UNRELATED: None}
    assert posts[0]._voted_elsewhere["id"] == base36encode(COPY_A)
    assert posts[0]._voted_elsewhere["message"] == "You already voted on this post, in +general. Remove that vote to vote here."


def test_a_page_costs_one_query_and_a_visitor_or_the_author_none(db):
    vote(db, ME, COPY_A)
    seen = queries(db)
    vc.attach(db, page(ORIGINAL, COPY_A, COPY_B, UNRELATED), NS(id=ME), NOW)
    assert len(seen) == 1
    seen.clear()

    visitor = page(ORIGINAL, COPY_A)
    vc.attach(db, visitor, None, NOW)
    authors = page(ORIGINAL, COPY_A)
    vc.attach(db, authors, NS(id=AUTHOR), NOW)
    vc.attach(db, [], NS(id=ME), NOW)
    assert seen == []
    assert elsewhere(visitor) == {ORIGINAL: None, COPY_A: None} and elsewhere(authors) == {ORIGINAL: None, COPY_A: None}


def test_a_post_the_viewer_voted_on_is_never_locked_even_with_older_votes_elsewhere(db):
    vote(db, ME, COPY_A, at=NOW - 500)
    vote(db, ME, COPY_B, at=NOW - 50)
    posts = page(ORIGINAL, COPY_A, COPY_B)
    posts[1]._voted, posts[2]._voted = 1, 1              # they can still change or remove either
    vc.attach(db, posts, NS(id=ME), NOW)
    assert elsewhere(posts) == {ORIGINAL: "+general", COPY_A: None, COPY_B: None}


def test_a_removed_vote_and_a_removed_copy_lock_nothing(db):
    vote(db, ME, COPY_A, kind=0)
    posts = page(ORIGINAL, COPY_B)
    vc.attach(db, posts, NS(id=ME), NOW)
    assert elsewhere(posts) == {ORIGINAL: None, COPY_B: None}
    db.execute(text("DELETE FROM votes"))
    vote(db, ME, COPY_A)
    db.execute(text("UPDATE submissions SET is_banned = 1 WHERE id = 2"))
    posts = page(ORIGINAL, COPY_B)
    vc.attach(db, posts, NS(id=ME), NOW)
    assert elsewhere(posts) == {ORIGINAL: None, COPY_B: None}


def test_a_vote_in_one_family_never_locks_another_family_on_the_page(db):
    vote(db, ME, UNRELATED)
    posts = page(ORIGINAL, COPY_A, UNRELATED)
    vc.attach(db, posts, NS(id=ME), NOW)
    assert elsewhere(posts) == {ORIGINAL: None, COPY_A: None, UNRELATED: None}


def test_another_persons_vote_locks_nothing(db):
    vote(db, OTHER, COPY_A)
    posts = page(ORIGINAL, COPY_B)
    vc.attach(db, posts, NS(id=ME), NOW)
    assert elsewhere(posts) == {ORIGINAL: None, COPY_B: None}
