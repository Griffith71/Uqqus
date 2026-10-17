"""The search operators' conditions (ruqqus/helpers/search_filters.py), run on SQLite with small stand-in models.

The promise that matters most: a search never tells anyone who wrote an anonymous post. `from:@a` leaves a's
anonymous posts out (except for a and admins), and `-from:@a` leaves them IN, so comparing `cats` with
`cats -from:@a` shows nothing that `cats from:@a` doesn't."""
import sys
import types
from dataclasses import replace
from types import SimpleNamespace as NS

import pytest
from sqlalchemy import Boolean, Column, Integer, String, create_engine
from sqlalchemy.orm import Session, declarative_base

from ruqqus.helpers import search_filters
from ruqqus.helpers import search_query as sq

JAN_1 = 1767225600
DAY = 86400
Base = declarative_base()


class Submission(Base):
    __tablename__ = "submissions"
    id = Column(Integer, primary_key=True)
    author_id = Column(Integer)
    board_id = Column(Integer)
    is_anonymous = Column(Boolean, default=False, nullable=False)
    repost_id = Column(Integer, default=0)
    language_code = Column(String)
    made_with_ai = Column(Boolean)
    paid_partnership = Column(Boolean)
    upvotes = Column(Integer, default=0)
    downvotes = Column(Integer, default=0)
    created_utc = Column(Integer)
    _own_is_image = Column("is_image", Boolean)


class SubmissionAux(Base):
    __tablename__ = "submissions_aux"
    id = Column(Integer, primary_key=True)
    title = Column(String)
    body = Column(String)
    url = Column(String)
    embed_url = Column(String)


class Poll(Base):
    __tablename__ = "polls"
    id = Column(Integer, primary_key=True)
    post_id = Column(Integer)


class MediaAsset(Base):
    __tablename__ = "media_assets"
    id = Column(Integer, primary_key=True)
    submission_id = Column(Integer)
    kind = Column(String)
    status = Column(String)


class Board(Base):
    __tablename__ = "boards"
    id = Column(Integer, primary_key=True)
    subcat_id = Column(Integer)


class CurationGuild(Base):
    __tablename__ = "curation_guilds"
    id = Column(Integer, primary_key=True)
    curation_id = Column(Integer)
    board_id = Column(Integer)


class CurationUser(Base):
    __tablename__ = "curation_users"
    id = Column(Integer, primary_key=True)
    curation_id = Column(Integer)
    target_user_id = Column(Integer)


class Comment(Base):
    __tablename__ = "comments"
    id = Column(Integer, primary_key=True)
    parent_submission = Column(Integer)
    is_banned = Column(Boolean, default=False)
    deleted_utc = Column(Integer, default=0)


A, B, OTHER = 1, 2, 3
PETS, NEWS = 10, 11
ADMIN = NS(id=99, admin_level=3)

# id: (author, anonymous, board, title, body, url, extra)
POSTS = {
    1: (A, False, PETS, "Cats are great", None, None, {}),
    2: (A, True, PETS, "cats again", "secret", None, {}),
    3: (B, False, NEWS, "News", "a cat story with 50% off", "https://www.example.com/news", {"language_code": "en"}),
    4: (B, False, NEWS, "Picture", None, "https://img.example.com/a.png", {"_own_is_image": True}),
    5: (B, False, PETS, "Poll time", None, None, {"upvotes": 12, "downvotes": 2}),
    6: (A, False, NEWS, "Poll time", None, None, {"repost_id": 5}),               # a forwarded copy of 5
    7: (B, False, PETS, "500 cats", None, None, {"language_code": "fr", "made_with_ai": True}),
}


@pytest.fixture
def db(monkeypatch):
    classes = types.ModuleType("ruqqus.classes")
    for model in (Submission, SubmissionAux, Poll, MediaAsset, Comment, Board, CurationGuild, CurationUser):
        setattr(classes, model.__name__, model)
    monkeypatch.setitem(sys.modules, "ruqqus.classes", classes)

    engine = create_engine("sqlite://")
    Base.metadata.create_all(engine)
    session = Session(engine)
    for pid, (author, anonymous, board, title, body, url, extra) in POSTS.items():
        session.add(Submission(id=pid, author_id=author, is_anonymous=anonymous, board_id=board,
                               created_utc=JAN_1 + pid * DAY, **extra))
        session.add(SubmissionAux(id=pid, title=title, body=body, url=url))
    session.add(Poll(id=1, post_id=5))
    session.add_all([Board(id=PETS, subcat_id=5), Board(id=NEWS, subcat_id=6)])
    # curation 1 holds the guild +news and the account b; curation 2 holds only the account a
    session.add_all([CurationGuild(curation_id=1, board_id=NEWS), CurationUser(curation_id=1, target_user_id=B),
                     CurationUser(curation_id=2, target_user_id=A)])
    session.add_all([Comment(id=i, parent_submission=5) for i in range(1, 4)])
    session.commit()
    yield session
    session.close()


def found(db, text, viewer=None):
    query = sq.parse(text)
    assert not query.problems, query.problems
    ids = {"a": A, "b": B, "pets": PETS, "news": NEWS}
    topics = {"#animals": (5,), "#headlines": (6,), "#nothing": ()}        # subcategory ids, as search_filters.resolve sets them
    curations = {"&mix": 1, "&justa": 2}

    def resolved(p):
        if p.kind == "in" and p.value in topics:
            return replace(p, refs=topics[p.value])
        if p.kind == "in" and p.value in curations:
            return replace(p, ref=curations[p.value])
        return replace(p, ref=ids[p.value]) if p.kind in ("from", "in") else p
    query = replace(query, parts=tuple(resolved(p) for p in query.parts))
    posts = db.query(Submission.id).join(SubmissionAux, SubmissionAux.id == Submission.id)
    return sorted(x[0] for x in search_filters.apply(posts, query, viewer).all())


STRANGER = NS(id=OTHER, admin_level=0)


def test_from_leaves_out_anonymous_posts_except_for_their_author_and_admins(db):
    assert found(db, "cats from:@a", STRANGER) == [1]
    assert found(db, "cats from:@a", None) == [1]
    assert found(db, "cats from:@a", NS(id=A, admin_level=0)) == [1, 2]
    assert found(db, "cats from:@a", ADMIN) == [1, 2]


def test_minus_from_never_hides_an_anonymous_post(db):
    for viewer in (STRANGER, None, NS(id=A, admin_level=0), ADMIN):
        assert 2 in found(db, "cats -from:@a", viewer)


def test_the_difference_between_two_searches_reveals_nothing_from_does_not(db):
    everything = set(found(db, "cats", STRANGER))
    without_a = set(found(db, "cats -from:@a", STRANGER))
    assert everything - without_a == set(found(db, "cats from:@a", STRANGER)) == {1}


def test_words_are_found_in_the_title_or_the_text_and_wildcards_are_text(db):
    assert found(db, "cat") == [1, 2, 3, 7]
    assert found(db, "secret") == [2]
    assert found(db, '"cat story"') == [3]
    assert found(db, "50%") == [3]          # not "500 cats"
    assert found(db, "_") == []


def test_a_minus_word_keeps_posts_with_no_text(db):
    assert found(db, "-secret") == [1, 3, 4, 5, 6, 7]
    assert found(db, "cats -again") == [1, 7]


def test_guilds(db):
    assert found(db, "in:+pets") == [1, 2, 5, 7]
    assert found(db, "-in:+pets") == [3, 4, 6]


def test_topics_are_the_guilds_of_their_subcategories(db):
    assert found(db, "in:#animals") == [1, 2, 5, 7]
    assert found(db, "-in:#animals") == [3, 4, 6]
    assert found(db, "in:#headlines cat") == [3]
    assert found(db, "in:#nothing") == []              # a topic with no subcategory matches nothing, never everything
    assert found(db, "-in:#nothing") == [1, 2, 3, 4, 5, 6, 7]


def test_a_curation_is_its_guilds_and_the_non_anonymous_posts_of_its_accounts(db):
    assert found(db, "in:&mix") == [3, 4, 5, 6, 7]     # +news (3, 4, 6) and b's posts (3, 4, 5, 7)
    assert found(db, "-in:&mix") == [1, 2]
    # a's anonymous post (2) never matches through the account, so a curation can't say which posts a wrote
    assert found(db, "in:&justa") == [1, 6]
    assert 2 not in found(db, "in:&justa", ADMIN)
    assert 2 in found(db, "-in:&justa", STRANGER)


def test_has(db):
    assert found(db, "has:link") == [3]                 # the picture's address is not a link
    assert found(db, "has:image") == [4]
    assert found(db, "-has:link -has:image") == [1, 2, 5, 6, 7]
    assert found(db, "has:poll") == [5, 6]              # a forwarded copy has its original's poll
    assert found(db, "-has:poll") == [1, 2, 3, 4, 7]


def test_language_and_a_minus_language_keeps_untagged_posts(db):
    assert found(db, "lang:en") == [3]
    assert found(db, "-lang:en") == [1, 2, 4, 5, 6, 7]


def test_is_and_numbers(db):
    assert found(db, "is:forward") == [6]
    assert found(db, "-is:forward") == [1, 2, 3, 4, 5, 7]
    assert found(db, "is:ai") == [7]
    assert found(db, "min_votes:10") == [5]
    assert found(db, "min_comments:3") == [5]


def test_urls(db):
    assert found(db, "url:example.com/news") == [3]
    assert found(db, "-url:example") == [1, 2, 5, 6, 7]


def test_dates(db):
    assert found(db, "since:2026-01-03") == [2, 3, 4, 5, 6, 7]
    assert found(db, "before:2026-01-03") == [1]
    assert found(db, "after:2026-01-03 until:2026-01-05") == [3, 4]
    assert found(db, "on:2026-01-07") == [6]
