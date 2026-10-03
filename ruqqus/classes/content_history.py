from sqlalchemy import *
from sqlalchemy.orm import relationship
from ruqqus.__main__ import Base
from .mix_ins import *
import time


class ContentEditHistory(Base, Stndrd):
    """Tier 1/2 audit trail: an author editing/deleting their own post or
    comment, or a guild's moderators hiding/unhiding it for breaking that
    guild's rules. previous_* columns snapshot the content immediately
    before the action, for every action type except plain restores
    ("guild_unhide"), which don't change anything.

    These snapshot columns must be scrubbed (set to NULL) whenever the
    underlying content is later obliterated - see admin_obliterate_post/
    admin_obliterate_comment in routes/admin_api.py - so no earlier edited-
    out version of destroyed content can resurface here."""

    __tablename__ = "content_edit_history"

    id = Column(BigInteger, primary_key=True)

    actor_id = Column(Integer, ForeignKey("users.id"))
    target_submission_id = Column(Integer, ForeignKey("submissions.id"), default=0)
    target_comment_id = Column(Integer, ForeignKey("comments.id"), default=0)
    board_id = Column(Integer, ForeignKey("boards.id"), default=0)

    action = Column(String(32))  # "edit" | "delete" | "guild_hide" | "guild_unhide"
    reason = Column(String(256), default=None)

    previous_title = Column(String(500), default=None)
    previous_url = Column(String(500), default=None)
    previous_body = Column(String(25000), default=None)
    previous_body_html = Column(String(50000), default=None)

    created_utc = Column(Integer, default=0)

    actor = relationship("User", lazy="joined", primaryjoin="User.id==ContentEditHistory.actor_id")
    board = relationship("Board", lazy="joined")
    target_post = relationship("Submission", lazy="joined")
    target_comment = relationship("Comment", lazy="joined")

    def __init__(self, *args, **kwargs):
        if "created_utc" not in kwargs:
            kwargs["created_utc"] = int(time.time())
        super().__init__(*args, **kwargs)

    def __repr__(self):
        return f"<ContentEditHistory(id={self.id})>"


class ObliterationRecord(Base, Stndrd):
    """Tier 3 audit trail ONLY, for the admin-only obliterate action.
    Deliberately has no title/url/body/body_html columns anywhere in this
    schema - not nullable-and-unused, structurally absent - so no future
    code change can leak permanently destroyed content into it. Only who
    authored the content, who obliterated it, when, and why survive."""

    __tablename__ = "obliteration_records"

    id = Column(BigInteger, primary_key=True)

    actor_id = Column(Integer, ForeignKey("users.id"))
    author_id = Column(Integer, ForeignKey("users.id"))
    target_submission_id = Column(Integer, ForeignKey("submissions.id"), default=0)
    target_comment_id = Column(Integer, ForeignKey("comments.id"), default=0)
    board_id = Column(Integer, ForeignKey("boards.id"), default=0)

    reason_category = Column(String(32))
    reason = Column(String(512))

    created_utc = Column(Integer, default=0)

    actor = relationship("User", lazy="joined", primaryjoin="User.id==ObliterationRecord.actor_id")
    author = relationship("User", lazy="joined", primaryjoin="User.id==ObliterationRecord.author_id")
    board = relationship("Board", lazy="joined")
    target_post = relationship("Submission", lazy="joined")
    target_comment = relationship("Comment", lazy="joined")

    def __init__(self, *args, **kwargs):
        if "created_utc" not in kwargs:
            kwargs["created_utc"] = int(time.time())
        super().__init__(*args, **kwargs)

    def __repr__(self):
        return f"<ObliterationRecord(id={self.id})>"
