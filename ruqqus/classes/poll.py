from sqlalchemy import *

from .mix_ins import *
from ruqqus.__main__ import Base


class Poll(Base, Age_times):
    """A poll on a post (helpers/polls.py). `post_id` is always the PRIMARY post: a forwarded copy
    (`repost_id`) and a repost resolve to it, so the poll is voted and counted once, from wherever
    it is shown."""

    __tablename__ = "polls"
    __table_args__ = (UniqueConstraint("post_id", name="polls_post_key"),)

    id = Column(Integer, primary_key=True)
    post_id = Column(Integer, ForeignKey("submissions.id"), nullable=False)
    closes_utc = Column(Integer, nullable=False)
    created_utc = Column(Integer, nullable=False, default=0)

    def __repr__(self):
        return f"<Poll(id={self.id}, post_id={self.post_id})>"


class PollOption(Base):
    __tablename__ = "poll_options"
    __table_args__ = (UniqueConstraint("poll_id", "ordinal", name="poll_options_ordinal_key"),)

    id = Column(Integer, primary_key=True)
    poll_id = Column(Integer, ForeignKey("polls.id", ondelete="CASCADE"), nullable=False)
    ordinal = Column(SmallInteger, nullable=False)
    label = Column(String(40), nullable=False)


class PollVote(Base):
    """One member's vote. Never shown to anyone: only the counts are."""

    __tablename__ = "poll_votes"
    __table_args__ = (UniqueConstraint("poll_id", "user_id", name="poll_votes_one_per_member"),)

    id = Column(Integer, primary_key=True)
    poll_id = Column(Integer, ForeignKey("polls.id", ondelete="CASCADE"), nullable=False)
    option_id = Column(Integer, ForeignKey("poll_options.id", ondelete="CASCADE"), nullable=False)
    user_id = Column(Integer, ForeignKey("users.id"), nullable=False)
    created_utc = Column(Integer, nullable=False, default=0)
