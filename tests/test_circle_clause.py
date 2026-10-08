"""The SQL condition that lets a Circle's members see its posts in their own lists (ruqqus/helpers/circle_clause.py):
the same expression the lists use, run on SQLite with small stand-in models. The promise under test: it is true for
exactly the posts a friend or a paid-up subscriber may see, and never for anyone else."""
from types import SimpleNamespace as NS

import pytest
from sqlalchemy import Column, Integer, String, Boolean, create_engine, select
from sqlalchemy.orm import Session, declarative_base

from ruqqus.helpers import circle_clause

NOW = 2_000_000_000
DAY = 86400
Base = declarative_base()


class Sub(Base):
    __tablename__ = "submissions"
    id = Column(Integer, primary_key=True)
    author_id = Column(Integer)
    audience = Column(Integer)


class Circ(Base):
    __tablename__ = "circles"
    id = Column(Integer, primary_key=True)
    user_id = Column(Integer)
    board_id = Column(Integer)


class Mem(Base):
    __tablename__ = "circle_members"
    id = Column(Integer, primary_key=True)
    circle_id = Column(Integer)
    user_id = Column(Integer)
    tier = Column(String)
    status = Column(String)
    renews_utc = Column(Integer)
    cancelled = Column(Boolean)


A, B = 1, 2                                  # two account owners with Circles
FRIEND, FAN, EXPIRED, ENDED, STRANGER = 10, 11, 12, 13, 14
# posts: id -> (author, audience)
POSTS = {1: (A, 0), 2: (A, 1), 3: (A, 2), 4: (A, 3), 5: (B, 1), 6: (B, 2)}


@pytest.fixture
def db():
    engine = create_engine("sqlite://")
    Base.metadata.create_all(engine)
    session = Session(engine)
    for pid, (author, audience) in POSTS.items():
        session.add(Sub(id=pid, author_id=author, audience=audience))
    session.add_all([Circ(id=1, user_id=A), Circ(id=2, user_id=B), Circ(id=3, user_id=None, board_id=9)])
    members = [
        (1, FRIEND, "friend", "active", 0), (1, FAN, "subscriber", "active", NOW + DAY),
        (1, EXPIRED, "subscriber", "active", NOW - DAY), (1, ENDED, "subscriber", "ended", NOW + DAY),
        (2, FAN, "friend", "ended", 0),                                          # B removed FAN from their friends
        (3, STRANGER, "friend", "active", 0),                                    # a guild's Circle: not an account's
    ]
    for i, (circle, user, tier, status, renews) in enumerate(members, 1):
        session.add(Mem(id=i, circle_id=circle, user_id=user, tier=tier, status=status, renews_utc=renews, cancelled=False))
    session.commit()
    yield session
    session.close()


def seen(db, who):
    viewer = None if who is None else NS(id=who)
    clause = circle_clause.member_clause(viewer, Sub, Circ, Mem, NOW)
    return sorted(db.execute(select(Sub.id).where(clause)).scalars().all())


def test_a_close_friend_gets_both_kinds_of_circle_posts_of_that_circle_only(db):
    assert seen(db, FRIEND) == [2, 3]


def test_a_paid_up_subscriber_gets_the_subscribers_posts_and_not_the_close_friends_ones(db):
    assert seen(db, FAN) == [2]


def test_an_expired_or_ended_subscriber_and_a_stranger_get_nothing(db):
    assert seen(db, EXPIRED) == [] and seen(db, ENDED) == [] and seen(db, STRANGER) == []


def test_a_visitor_gets_nothing_and_the_condition_is_false(db):
    assert seen(db, None) == []


def test_a_public_post_is_never_matched_here(db):
    # public posts are shown by the lists' own post_public condition; this one is only for Circle posts
    for who in (FRIEND, FAN, EXPIRED, ENDED, STRANGER, None):
        assert 1 not in seen(db, who)


def test_a_circle_guilds_posts_are_not_matched_by_membership(db):
    # audience 3 belongs to the guild's own rules, and a guild's Circle (no user_id) gives nobody an account's posts
    for who in (FRIEND, FAN, STRANGER):
        assert 4 not in seen(db, who)


def test_a_subscription_runs_out_on_the_date(db):
    later = circle_clause.member_clause(NS(id=FAN), Sub, Circ, Mem, NOW + DAY)
    assert db.execute(select(Sub.id).where(later)).scalars().all() == []
    just_before = circle_clause.member_clause(NS(id=FAN), Sub, Circ, Mem, NOW + DAY - 1)
    assert db.execute(select(Sub.id).where(just_before)).scalars().all() == [2]


def test_someone_removed_from_a_circle_loses_its_posts_at_once(db):
    assert seen(db, FRIEND) == [2, 3]
    db.query(Mem).filter_by(user_id=FRIEND).delete()
    db.commit()
    assert seen(db, FRIEND) == []
