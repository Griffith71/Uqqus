from sqlalchemy import *

from .mix_ins import *
from ruqqus.__main__ import Base


class PostFollow(Base, Age_times):
    """`user_id` follows `post_id`: they are told of the comments the person responsible for the post is told of
    (helpers/post_follows.py). Per post id: a forward copy is its own thread. No foreign key to users, as
    `stories`; nothing ever lists who follows a post."""

    __tablename__ = "post_follows"
    __table_args__ = (UniqueConstraint("user_id", "post_id", name="post_follows_pair_key"),)

    id = Column(Integer, primary_key=True)
    user_id = Column(Integer, nullable=False)
    post_id = Column(Integer, nullable=False)
    created_utc = Column(Integer, nullable=False, default=0)

    def __repr__(self):
        return f"<PostFollow(user_id={self.user_id}, post_id={self.post_id})>"
