"""Sensitive content and All / For You (ruqqus/helpers/sensitive.py): the rules, and the filter run on SQLite."""
from types import SimpleNamespace as NS

import pytest
from sqlalchemy import Boolean, Column, Integer, create_engine, select
from sqlalchemy.orm import Session, declarative_base

from ruqqus.helpers import sensitive

Base = declarative_base()


class Post(Base):
    __tablename__ = "posts"
    id = Column(Integer, primary_key=True)
    author_id = Column(Integer)
    board_id = Column(Integer)
    is_sensitive = Column(Boolean, default=False)
    is_anonymous = Column(Boolean, default=False)


class Guild(Base):
    __tablename__ = "guilds"
    id = Column(Integer, primary_key=True)
    is_sensitive = Column(Boolean, default=False)


class Account(Base):
    __tablename__ = "accounts"
    id = Column(Integer, primary_key=True)
    is_sensitive = Column(Boolean, default=False)


# accounts: 1 ordinary, 2 sensitive. guilds: 10 ordinary, 20 sensitive
POSTS = {
    "plain": dict(id=1, author_id=1, board_id=10),
    "marked": dict(id=2, author_id=1, board_id=10, is_sensitive=True),
    "in sensitive guild": dict(id=3, author_id=1, board_id=20),
    "by sensitive account": dict(id=4, author_id=2, board_id=10),
    "anonymous by sensitive account": dict(id=5, author_id=2, board_id=10, is_anonymous=True),
    "anonymous marked": dict(id=6, author_id=2, board_id=10, is_anonymous=True, is_sensitive=True),
    "own sensitive post": dict(id=7, author_id=3, board_id=20, is_sensitive=True),
}


@pytest.fixture
def db(monkeypatch):
    engine = create_engine("sqlite://")
    Base.metadata.create_all(engine)
    session = Session(engine)
    session.add_all([Account(id=1), Account(id=2, is_sensitive=True), Account(id=3),
                     Guild(id=10), Guild(id=20, is_sensitive=True)] + [Post(**p) for p in POSTS.values()])
    session.commit()
    monkeypatch.setattr(sensitive, "sensitive_board_ids", lambda: select(Guild.id).where(Guild.is_sensitive.is_(True)))
    monkeypatch.setattr(sensitive, "sensitive_user_ids", lambda: select(Account.id).where(Account.is_sensitive.is_(True)))
    return session


def shown(db, viewer):
    ids = {row.id for row in sensitive.hide_sensitive(db.query(Post), viewer, Post)}
    return {name for name, p in POSTS.items() if p["id"] in ids}


def member(uid=9, opted_in=False, level=1):
    return NS(id=uid, all_shows_sensitive=opted_in, filter_level=level)


def test_a_visitor_sees_nothing_sensitive_in_the_feeds(db):
    assert shown(db, None) == {"plain", "anonymous by sensitive account"}


def test_an_anonymous_post_is_never_left_out_because_of_who_wrote_it(db):
    # "anonymous by sensitive account" stays: dropping it would say whose it is. Marked, it leaves by its own mark.
    assert "anonymous by sensitive account" in shown(db, member())
    assert "anonymous marked" not in shown(db, member())


def test_a_member_who_chose_to_sees_it_all(db):
    assert shown(db, member(opted_in=True)) == set(POSTS)


def test_the_child_filter_overrides_the_choice(db):
    assert shown(db, member(opted_in=True, level=2)) == {"plain", "anonymous by sensitive account"}


def test_an_author_always_sees_their_own(db):
    assert "own sensitive post" in shown(db, member(uid=3))
    assert "own sensitive post" not in shown(db, member(uid=1))


def test_who_sees_sensitive_content():
    assert not sensitive.shows_sensitive(None)
    assert not sensitive.shows_sensitive(member())
    assert sensitive.shows_sensitive(member(opted_in=True))
    assert sensitive.shows_sensitive(member(opted_in=True, level=0))
    assert not sensitive.shows_sensitive(member(opted_in=True, level=2))


def test_an_owner_cannot_undo_what_an_admin_forced():
    assert sensitive.owner_refusal(forced=False, wanted=False) is None
    assert sensitive.owner_refusal(forced=False, wanted=True) is None
    assert sensitive.owner_refusal(forced=True, wanted=True) is None
    assert sensitive.owner_refusal(forced=True, wanted=False) == sensitive.FORCED


def test_a_sensitive_accounts_new_posts_are_marked():
    assert sensitive.marks_post(False, NS(is_sensitive=True))
    assert sensitive.marks_post(True, NS(is_sensitive=False))
    assert not sensitive.marks_post(False, NS(is_sensitive=False))
    assert not sensitive.marks_post("", None)
