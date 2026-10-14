from sqlalchemy import *

from .mix_ins import *
from ruqqus.__main__ import Base


class PostFeedback(Base, Age_times):
    """`user_id` asked to see fewer posts like `post_id` (helpers/post_feedback.py). `post_id` is the PRIMARY
    post, so the original and every copy leave together. `author_id` is NULL for an anonymous post: the author
    is neither penalised nor recoverable from this row. Private to the member; nothing ever lists it."""

    __tablename__ = "post_feedback"
    __table_args__ = (UniqueConstraint("user_id", "post_id", name="post_feedback_pair_key"),)

    id = Column(Integer, primary_key=True)
    user_id = Column(Integer, nullable=False)
    post_id = Column(Integer, nullable=False)
    board_id = Column(Integer)
    author_id = Column(Integer)
    created_utc = Column(Integer, nullable=False, default=0)

    def __repr__(self):
        return f"<PostFeedback(user_id={self.user_id}, post_id={self.post_id})>"
