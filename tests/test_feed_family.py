"""One card per post in the feeds (helpers/feed_family.py, audit F-01): a guild copy leaves when its original is live,
public and in the feed; otherwise it stays."""
from sqlalchemy import Boolean, Column, Integer, create_engine
from sqlalchemy.orm import Session, declarative_base

from ruqqus.helpers.feed_family import one_per_family

Base = declarative_base()


class Post(Base):
    __tablename__ = "posts"
    id = Column(Integer, primary_key=True)
    repost_id = Column(Integer)
    deleted_utc = Column(Integer, default=0)
    is_banned = Column(Boolean, default=False)
    audience = Column(Integer, default=0)
    author_id = Column(Integer)


def _db():
    engine = create_engine("sqlite://")
    Base.metadata.create_all(engine)
    db = Session(engine)
    db.add_all([Post(id=1, repost_id=0, author_id=7), Post(id=2, repost_id=1, author_id=7), Post(id=3, repost_id=1, author_id=7),
                Post(id=4, repost_id=0, author_id=8, deleted_utc=5), Post(id=5, repost_id=4, author_id=8),
                Post(id=6, repost_id=0, author_id=9, audience=4), Post(id=7, repost_id=6, author_id=9),
                Post(id=8, repost_id=None, author_id=9)])
    db.commit()
    return db


def ids(query):
    return sorted(p.id for p in query)


def test_copies_leave_when_their_original_is_shown():
    db = _db()
    assert ids(one_per_family(db.query(Post), Post)) == [1, 4, 5, 6, 7, 8]   # 2, 3 are copies of the live public 1


def test_a_copy_stays_when_the_original_is_not_in_this_feed():
    db = _db()
    def only_author_8(original):                                                  # a feed following author 8 only
        return original.author_id == 8
    assert ids(one_per_family(db.query(Post), Post, only_author_8)) == [1, 2, 3, 4, 5, 6, 7, 8]
