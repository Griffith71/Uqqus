import time

from sqlalchemy import *
from sqlalchemy.orm import relationship

from ruqqus.__main__ import Base
from flask import g


class ViewHistory(Base):

    __tablename__ = "view_history"

    id = Column(Integer, primary_key=True)
    user_id = Column(Integer, ForeignKey("users.id"))
    submission_id = Column(Integer, ForeignKey("submissions.id"))
    viewed_utc = Column(Integer, default=0)


def record_view(v, post):
    """
    Record that user `v` viewed `post` just now, for the History tab.
    Deduplicated per (user, post) - repeat views just bump the timestamp
    rather than piling up one row per view. Best-effort: a failure here
    should never break rendering the post itself.
    """

    try:
        existing = g.db.query(ViewHistory).filter_by(
            user_id=v.id, submission_id=post.id).first()

        now = int(time.time())

        if existing:
            existing.viewed_utc = now
            g.db.add(existing)
        else:
            g.db.add(ViewHistory(
                user_id=v.id,
                submission_id=post.id,
                viewed_utc=now
            ))

        g.db.commit()
    except BaseException:
        g.db.rollback()
