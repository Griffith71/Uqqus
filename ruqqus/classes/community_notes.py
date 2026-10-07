from sqlalchemy import *

from .mix_ins import *
from ruqqus.__main__ import Base


class NoteRequest(Base, Age_times):
    """Someone asked, from the flag menu, for a community note on a post or comment
    (helpers/community_notes.py). Not a Flag: a flag says "this breaks the rules" and joins
    the admins' policy queue; this says "this needs context". For a post, `post_id` is the
    PRIMARY post (a forwarded copy's request counts for the post it was copied from)."""

    __tablename__ = "note_requests"

    id = Column(Integer, primary_key=True)
    post_id = Column(Integer, default=None)
    comment_id = Column(Integer, default=None)
    user_id = Column(Integer, nullable=False)
    created_utc = Column(Integer, nullable=False, default=0)

    def __repr__(self):
        return f"<NoteRequest(post_id={self.post_id}, comment_id={self.comment_id}, user_id={self.user_id})>"


class CommunityNote(Base, Age_times):
    """A note an admin wrote under a post or comment, shown to everyone who sees it. One
    live note per target: replacing it marks the old one removed (`removed_utc`) and
    keeps it. For a post, `post_id` is the primary post, so the note shows on every copy."""

    __tablename__ = "community_notes"

    id = Column(Integer, primary_key=True)
    post_id = Column(Integer, default=None)
    comment_id = Column(Integer, default=None)
    body = Column(String(600), nullable=False)
    admin_id = Column(Integer, nullable=False)
    created_utc = Column(Integer, nullable=False, default=0)
    removed_utc = Column(Integer, nullable=False, default=0)

    def __repr__(self):
        return f"<CommunityNote(id={self.id}, post_id={self.post_id}, comment_id={self.comment_id})>"
