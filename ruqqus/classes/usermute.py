from sqlalchemy import *
from sqlalchemy.orm import relationship

from .mix_ins import *
from ruqqus.__main__ import Base


class UserMute(Base, Age_times):
    """`user_id` has muted `target_id`: the target's posts and comments are left out of
    the muter's feeds and threads and their notifications stop (helpers/muting.py).

    Deliberately NOT a UserBlock: a block also stops the target commenting on the
    blocker's content and messaging them, and every such check reads `userblocks`.
    A mute changes only what the muter sees, and the target is never told."""

    __tablename__ = "usermutes"
    __table_args__ = (UniqueConstraint("user_id", "target_id", name="usermutes_pair_key"),)

    id = Column(Integer, primary_key=True)
    user_id = Column(Integer, ForeignKey("users.id"), nullable=False)
    target_id = Column(Integer, ForeignKey("users.id"), nullable=False)
    created_utc = Column(Integer, nullable=False, default=0)

    target = relationship("User", primaryjoin="User.id==UserMute.target_id")

    def __repr__(self):
        return f"<UserMute(user_id={self.user_id}, target_id={self.target_id})>"
