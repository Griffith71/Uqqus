"""An account's choice to keep its profile posts out of All (ruqqus/helpers/all_feed.py, the owner, 2026-10-27), on SQLite:
its named posts leave All, its anonymous posts never do (or the gap would say which are its), the guild copies of its posts
follow their guild (a copy shows when its original is out), and the viewer's own posts stay in their own All."""
from sqlalchemy import Boolean, Column, Integer, create_engine
from sqlalchemy.orm import Session, declarative_base

from ruqqus.helpers.all_feed import in_all
from ruqqus.helpers.feed_family import one_per_family

Base = declarative_base()
OUT, IN, VIEWER = 1, 2, 3


class User(Base):
    __tablename__ = "users"
    id = Column(Integer, primary_key=True)
    all_opt_out = Column(Boolean, default=False)


class Post(Base):
    __tablename__ = "submissions"
    id = Column(Integer, primary_key=True)
    author_id = Column(Integer)
    is_anonymous = Column(Boolean, default=False)
    repost_id = Column(Integer, default=0)
    deleted_utc = Column(Integer, default=0)
    is_banned = Column(Boolean, default=False)
    audience = Column(Integer, default=0)


class Viewer:
    def __init__(self, id):
        self.id = id


def make():
    db = Session(create_engine("sqlite://"))
    Base.metadata.create_all(db.get_bind())
    db.add_all([User(id=OUT, all_opt_out=True), User(id=IN), User(id=VIEWER, all_opt_out=True)])
    db.add_all([
        Post(id=1, author_id=OUT),                        # opted out: leaves All
        Post(id=2, author_id=OUT, is_anonymous=True),     # anonymous: always stays
        Post(id=3, author_id=OUT, repost_id=1),           # a guild copy of 1: shows, since 1 is out
        Post(id=4, author_id=IN),                         # in All
        Post(id=5, author_id=IN, repost_id=4),            # a copy of 4: 4 stands for it
        Post(id=6, author_id=VIEWER),                     # the viewer's own, though they opted out
    ])
    db.commit()
    return db


def all_ids(db, viewer):
    q = db.query(Post).filter(in_all(Post, viewer, User))
    q = one_per_family(q, Post, lambda original: in_all(original, viewer, User))
    return sorted(p.id for p in q)


def test_an_opted_out_accounts_named_posts_leave_all_and_its_anonymous_ones_never_do():
    db = make()
    assert all_ids(db, None) == [2, 3, 4]
    assert all_ids(db, Viewer(IN)) == [2, 3, 4]


def test_your_own_posts_stay_in_your_all():
    db = make()
    assert all_ids(db, Viewer(VIEWER)) == [2, 3, 4, 6]


def test_with_nobody_opted_out_all_is_as_it_was():
    db = make()
    db.query(User).update({User.all_opt_out: False})
    db.commit()
    assert all_ids(db, None) == [1, 2, 4, 6]          # copies 3 and 5 stand behind their originals, as before
