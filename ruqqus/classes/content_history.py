from sqlalchemy import *
from sqlalchemy import event
from sqlalchemy.orm import relationship
from ruqqus.__main__ import Base
from .mix_ins import *
import time


class ContentEditHistory(Base, Stndrd):
    """The public history of one post or comment (`/post/<id>/history`, `/comment/<id>/history`): an author editing it, deleting it, or a
    guild's moderators or a post's author hiding and unhiding it. **Only an edit's row keeps text**: `previous_*` is the version the author
    edited away, and the page draws it. A delete, a hide or a removal keeps its text as a private copy in the Log (`log_snapshots`, helpers/
    action_log.py: 90 days, who may open it depends on its kind), never here, so the history names who did what and when and never the words.
    `summary` and `reason` are a person's words and carry the word filter's rating together (`text_severity`).

    The `previous_*` columns must be scrubbed (set to NULL) whenever the underlying content is later obliterated - see admin_obliterate_post/
    admin_obliterate_comment in routes/admin_api.py - and an admin can empty one version (`hidden_utc`, every row of the same `edit_group`), so no
    earlier edited-out version of destroyed content can resurface here."""

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
    summary = Column(String(256), default=None)     # what the editor said they changed (optional)
    hidden_utc = Column(BigInteger, default=0)      # an admin hid this revision's text (the row stays)
    hidden_by = Column(Integer, default=None)
    edit_group = Column(BigInteger, default=None)   # the rows of ONE edit across a post and its copies: hiding one version hides all
    text_severity = Column(SmallInteger, default=0)  # the word filter's rating of `reason` and `summary` together (the audit's M8)

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


@event.listens_for(ContentEditHistory, "before_insert")
def _rate_the_words(mapper, connection, target):
    """The reason and the edit summary are a person's words that the history page draws to other readers, so they carry the word filter's
    rating, as every member text does and a reader's filter level hides (the audit's M8). Written for every writer of a row, since there
    are many; it never fails the row: an unrated text reads as clean, as one from before the column was."""
    from ruqqus.helpers import action_log
    words = " ".join(x for x in (target.reason, target.summary) if x)
    target.text_severity = action_log.rate(connection, words) if words else 0


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
