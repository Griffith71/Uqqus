from sqlalchemy import *
from sqlalchemy.orm import relationship
from ruqqus.__main__ import Base
from .mix_ins import *
import time


class ChatIdentity(Base):
    """Website user <-> Matrix user mapping. One row per provisioned user,
    created lazily on first /chat visit or first 'Message' click - see
    ruqqus.helpers.matrix_client.provision_user(). Holds no Matrix password,
    access token, or device id - those live only in the browser
    (localStorage), never server-side.

    recovery_key_encrypted DOES hold a secret (encrypted at rest - see
    ruqqus.helpers.secret_box): the account's Matrix secret-storage
    recovery key, saved automatically so E2EE unlocks on any device
    without the user managing a key themselves. Storing this server-side
    means the operator can decrypt a user's message history given this
    value and MASTER_KEY - an explicit, deliberate tradeoff against pure
    zero-knowledge E2EE, made for UX reasons. See routes/chat.py's
    recovery_key routes for the only code paths that read/write it."""

    __tablename__ = "chat_identities"

    user_id = Column(Integer, ForeignKey("users.id"), primary_key=True)
    matrix_user_id = Column(String(255), nullable=False)
    provisioned_utc = Column(Integer, default=0)
    recovery_key_encrypted = Column(Text, nullable=True)

    user = relationship("User", primaryjoin="User.id==ChatIdentity.user_id")

    def __init__(self, *args, **kwargs):
        if "provisioned_utc" not in kwargs:
            kwargs["provisioned_utc"] = int(time.time())
        super().__init__(*args, **kwargs)

    def __repr__(self):
        return f"<ChatIdentity(user_id={self.user_id})>"


class ChatConversation(Base, Stndrd, Age_times):
    """The single logical 1:1 conversation between two website users.
    Normalized so user_a_id < user_b_id always (enforced here in Python,
    backed by a CHECK + UNIQUE constraint in schema.sql as a race
    backstop) - this is what prevents duplicate-room creation when the
    'Message' button is clicked more than once.

    status: 'request' until the non-initiator joins the Matrix room,
    then 'inbox' permanently - it never reverts, even if the two users
    later unfollow each other (conversation state and follow state are
    related but not identical, per spec). For the INITIATOR, the
    conversation behaves like Inbox from the moment it's created
    regardless of this column - see tab_for()."""

    __tablename__ = "chat_conversations"

    id = Column(BigInteger, primary_key=True)
    user_a_id = Column(Integer, ForeignKey("users.id"), nullable=False)
    user_b_id = Column(Integer, ForeignKey("users.id"), nullable=False)
    initiator_id = Column(Integer, ForeignKey("users.id"), nullable=False)
    matrix_room_id = Column(String(255), nullable=False)
    status = Column(String(16), nullable=False, default="request")  # 'request' | 'inbox'
    created_utc = Column(Integer, default=0)
    last_activity_utc = Column(Integer, default=0)

    user_a = relationship("User", primaryjoin="User.id==ChatConversation.user_a_id")
    user_b = relationship("User", primaryjoin="User.id==ChatConversation.user_b_id")
    initiator = relationship("User", primaryjoin="User.id==ChatConversation.initiator_id")

    def __init__(self, *args, **kwargs):
        if "created_utc" not in kwargs:
            kwargs["created_utc"] = int(time.time())
        if "last_activity_utc" not in kwargs:
            kwargs["last_activity_utc"] = kwargs["created_utc"]
        super().__init__(*args, **kwargs)

    def other_id(self, viewer_id):
        return self.user_b_id if viewer_id == self.user_a_id else self.user_a_id

    def other_user(self, viewer_id):
        return self.user_b if viewer_id == self.user_a_id else self.user_a

    def tab_for(self, viewer_id):
        """'inbox' or 'request', from this specific viewer's perspective."""
        if viewer_id == self.initiator_id:
            return "inbox"
        return self.status

    def __repr__(self):
        return f"<ChatConversation(id={self.id})>"


class ChatUnread(Base):
    """Per-(conversation, user) unread MESSAGE count. Only ever populated/
    incremented once a conversation's status is 'inbox' for that user's
    perspective (see the appservice webhook handler in routes/chat.py) -
    pending Requests are deliberately not tracked here; the Requests
    badge is a conversation COUNT (User.chat_requests_count), not a
    message count."""

    __tablename__ = "chat_unread"

    conversation_id = Column(BigInteger, ForeignKey("chat_conversations.id"), primary_key=True)
    user_id = Column(Integer, ForeignKey("users.id"), primary_key=True)
    unread_count = Column(Integer, default=0)
    last_read_utc = Column(Integer, default=0)

    conversation = relationship("ChatConversation")

    @staticmethod
    def total_for_user(db, user_id):
        """Total unread MESSAGES (sum of unread_count) across all of a
        user's conversations - what the navbar chat badge shows."""
        total = db.query(func.coalesce(func.sum(ChatUnread.unread_count), 0)).filter(
            ChatUnread.user_id == user_id,
            ChatUnread.unread_count > 0
        ).scalar()
        return int(total or 0)

    def __repr__(self):
        return f"<ChatUnread(conversation_id={self.conversation_id}, user_id={self.user_id})>"
