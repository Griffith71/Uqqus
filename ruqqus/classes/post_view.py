from sqlalchemy import *

from ruqqus.__main__ import Base


class PostViewDay(Base):
    """How many times the page of `post_id` was opened on `day` (days since the epoch), by anyone
    but its author, a robot or the same viewer within half an hour (helpers/insights.py).

    Only a count: nothing here says who looked. It is written by helpers/insights_store.count_view
    with an upsert, not through the ORM, so a busy post does not fight over one row's version."""

    __tablename__ = "post_view_days"
    __table_args__ = (UniqueConstraint("post_id", "day", name="post_view_days_key"),)

    id = Column(Integer, primary_key=True)
    post_id = Column(Integer, ForeignKey("submissions.id"), nullable=False)
    day = Column(Integer, nullable=False)
    views = Column(Integer, nullable=False, default=0)
