from sqlalchemy import *
from sqlalchemy.orm import relationship

from .mix_ins import *
from ruqqus.__main__ import Base


class PostCoauthor(Base, Age_times):
    """Someone invited to share a post (helpers/coauthors.py). `post_id` is the PRIMARY post (the
    one on the author's profile): a copy forwarded to a guild resolves to it. `status` is
    'pending' until the invited account accepts, then 'accepted' (the post is then listed on
    their profile too and carries their name); declining or stepping off deletes the row."""

    __tablename__ = "post_coauthors"
    __table_args__ = (UniqueConstraint("post_id", "user_id", name="post_coauthors_pair_key"),)

    id = Column(Integer, primary_key=True)
    post_id = Column(Integer, nullable=False)
    user_id = Column(Integer, nullable=False)
    invited_by_id = Column(Integer, nullable=False)
    status = Column(String(8), nullable=False, default="pending")
    created_utc = Column(Integer, nullable=False, default=0)
    accepted_utc = Column(Integer, nullable=False, default=0)

    user = relationship("User", primaryjoin="User.id==PostCoauthor.user_id", foreign_keys=[user_id])

    def __repr__(self):
        return f"<PostCoauthor(post_id={self.post_id}, user_id={self.user_id}, status={self.status})>"
