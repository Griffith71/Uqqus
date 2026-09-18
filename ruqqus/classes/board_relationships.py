from ruqqus.helpers.base36 import *
from ruqqus.helpers.security import *
from sqlalchemy import *
from sqlalchemy.orm import relationship
from ruqqus.__main__ import Base, cache
from .mix_ins import *
import time


class ModRelationship(Base, Age_times):
    __tablename__ = "mods"
    id = Column(BigInteger, primary_key=True)
    user_id = Column(Integer, ForeignKey("users.id"))
    board_id = Column(Integer, ForeignKey("boards.id"))
    created_utc = Column(Integer, default=0)
    accepted = Column(Boolean, default=False)
    invite_rescinded = Column(Boolean, default=False)

    perm_content = Column(Boolean, default=False)
    perm_appearance = Column(Boolean, default=False)
    perm_config = Column(Boolean, default=False)
    perm_access = Column(Boolean, default=False)
    perm_full = Column(Boolean, default=False)
    #perm_chat = Column(Boolean, default=False)
    #permRules = Column(Boolean, default=False)
    #permTitles = Column(Boolean, default=False)
    #permLodges = Column(Boolean, default=False)

    user = relationship("User", lazy="joined", overlaps="user")
    board = relationship("Board", lazy="joined", overlaps="board")

    def __init__(self, *args, **kwargs):
        if "created_utc" not in kwargs:
            kwargs["created_utc"] = int(time.time())

        super().__init__(*args, **kwargs)

    def __repr__(self):
        return f"<Mod(id={self.id}, uid={self.user_id}, board_id={self.board_id})>"

    @property
    def permlist(self):
        if self.perm_full:
            return "full"

        output=[]
        for p in ["access","appearance", "config","content"]:
            if self.__dict__[f"perm_{p}"]:
                output.append(p)

        
        return ", ".join(output) if output else "none"

    @property
    def permchangelist(self):
        output=[]
        for p in ["full", "access","appearance","config","content"]:
            if self.__dict__.get(f"perm_{p}"):
                output.append(f"+{p}")
            else:
                output.append(f"-{p}")

        return ", ".join(output)


    @property
    def json_core(self):
        return {
            'user_id':self.user_id,
            'board_id':self.board_id,
            'created_utc':self.created_utc,
            'accepted':self.accepted,
            'invite_rescinded':self.invite_rescinded,
            'perm_content':self.perm_full or self.perm_content,
            'perm_config':self.perm_full or self.perm_config,
            'perm_access':self.perm_full or self.perm_access,
            'perm_appearance':self.perm_full or self.perm_appearance,
            'perm_full':self.perm_full
            #'perm_chat': self.perm_full or self.perm_chat
        }


    @property
    def json(self):
        data=self.json_core

        data["user"]=self.user.json_core
        #data["guild"]=self.board.json_core
    
        return data
    
    


class BanRelationship(Base, Stndrd, Age_times):

    __tablename__ = "bans"
    id = Column(BigInteger, primary_key=True)
    user_id = Column(Integer, ForeignKey("users.id"))
    board_id = Column(Integer, ForeignKey("boards.id"))
    created_utc = Column(BigInteger, default=0)
    banning_mod_id = Column(Integer, ForeignKey("users.id"))
    is_active = Column(Boolean, default=False)
    mod_note = Column(String(128), default="")

    user = relationship(
        "User",
        lazy="joined",
        primaryjoin="User.id==BanRelationship.user_id")
    banning_mod = relationship(
        "User",
        lazy="joined",
        primaryjoin="User.id==BanRelationship.banning_mod_id")
    board = relationship("Board")

    def __init__(self, *args, **kwargs):
        if "created_utc" not in kwargs:
            kwargs["created_utc"] = int(time.time())

        super().__init__(*args, **kwargs)

    def __repr__(self):
        return f"<Ban(id={self.id}, uid={self.uid}, board_id={self.board_id})>"

    @property
    def json_core(self):
        return {
            'user_id':self.user_id,
            'board_id':self.board_id,
            'created_utc':self.created_utc,
            'mod_id':self.banning_mod_id
        }


    @property
    def json(self):
        data=self.json_core

        data["user"]=self.user.json_core
        data["mod"]=self.banning_mod.json_core
        data["guild"]=self.board.json_core

        return data

class ChatBan(Base, Stndrd, Age_times):

    __tablename__ = "chatbans"
    id = Column(BigInteger, primary_key=True)
    user_id = Column(Integer, ForeignKey("users.id"))
    board_id = Column(Integer, ForeignKey("boards.id"))
    created_utc = Column(BigInteger, default=0)
    banning_mod_id = Column(Integer, ForeignKey("users.id"))

    user = relationship(
        "User",
        lazy="joined",
        primaryjoin="User.id==ChatBan.user_id")
    banning_mod = relationship(
        "User",
        lazy="joined",
        primaryjoin="User.id==ChatBan.banning_mod_id")
    board = relationship("Board")

    def __init__(self, *args, **kwargs):
        if "created_utc" not in kwargs:
            kwargs["created_utc"] = int(time.time())

        super().__init__(*args, **kwargs)

    def __repr__(self):
        return f"<Ban(id={self.id}, uid={self.uid}, board_id={self.board_id})>"

    @property
    def json_core(self):
        return {
            'user_id':self.user_id,
            'board_id':self.board_id,
            'created_utc':self.created_utc,
            'mod_id':self.banning_mod_id
        }


    @property
    def json(self):
        data=self.json_core

        data["user"]=self.user.json_core
        data["mod"]=self.banning_mod.json_core
        data["guild"]=self.board.json_core

        return data
class ContributorRelationship(Base, Stndrd, Age_times):

    __tablename__ = "contributors"
    id = Column(BigInteger, primary_key=True)
    user_id = Column(Integer, ForeignKey("users.id"))
    board_id = Column(Integer, ForeignKey("boards.id"))
    created_utc = Column(BigInteger, default=0)
    is_active = Column(Boolean, default=True)
    approving_mod_id = Column(Integer, ForeignKey("users.id"))

    user = relationship(
        "User",
        lazy="joined",
        primaryjoin="User.id==ContributorRelationship.user_id",
        overlaps="contributes,user"
    )
    approving_mod = relationship(
        "User",
        lazy='joined',
        primaryjoin="User.id==ContributorRelationship.approving_mod_id")
    board = relationship("Board", lazy="subquery")

    def __init__(self, *args, **kwargs):
        if "created_utc" not in kwargs:
            kwargs["created_utc"] = int(time.time())

        super().__init__(*args, **kwargs)

    def __repr__(self):
        return f"<Contributor(id={self.id}, uid={self.uid}, board_id={self.board_id})>"


class PostRelationship(Base):

    __tablename__ = "postrels"
    id = Column(BigInteger, primary_key=True)
    post_id = Column(Integer, ForeignKey("submissions.id"))
    board_id = Column(Integer, ForeignKey("boards.id"))

    post = relationship("Submission", lazy="subquery")
    board = relationship("Board", lazy="subquery")

    def __repr__(self):
        return f"<PostRel(id={self.id}, pid={self.post_id}, board_id={self.board_id})>"


class ForwardRelationship(Base):
    """Tracks which guilds a primary (profile) post has been Forwarded to.
    Each forward is also its own independent Submission row (own votes,
    own comments) linked back via Submission.repost_id - this table exists
    so the 5-guild cap and duplicate-guild prevention can be enforced with
    a DB-level unique constraint rather than just an application count."""

    __tablename__ = "forwardrels"
    __table_args__ = (UniqueConstraint('primary_submission_id', 'board_id', name='forward_unique'),)
    id = Column(BigInteger, primary_key=True)
    primary_submission_id = Column(Integer, ForeignKey("submissions.id"))
    board_id = Column(Integer, ForeignKey("boards.id"))
    forward_submission_id = Column(Integer, ForeignKey("submissions.id"))
    forwarded_by_id = Column(Integer, ForeignKey("users.id"))
    created_utc = Column(Integer, default=0)

    primary_submission = relationship(
        "Submission", lazy="subquery", foreign_keys=[primary_submission_id])
    forward_submission = relationship(
        "Submission", lazy="subquery", foreign_keys=[forward_submission_id])
    board = relationship("Board", lazy="subquery")

    def __init__(self, **kwargs):
        kwargs["created_utc"] = int(time.time())
        super().__init__(**kwargs)

    def __repr__(self):
        return f"<ForwardRel(id={self.id}, primary={self.primary_submission_id}, board_id={self.board_id})>"


class CommentForwardRelationship(Base):
    """Tracks which guilds a reply/comment has been promoted into as an
    independent new post (its own votes/comment thread), quote-tweet
    style. Unlike ForwardRelationship this has no "primary" content row
    to point back to on the comment side - comment_id IS the source of
    truth, and the new Submission carries a link back via this table."""

    __tablename__ = "comment_forwardrels"
    __table_args__ = (UniqueConstraint('comment_id', 'board_id', name='comment_forward_unique'),)
    id = Column(BigInteger, primary_key=True)
    comment_id = Column(Integer, ForeignKey("comments.id"))
    board_id = Column(Integer, ForeignKey("boards.id"))
    promoted_submission_id = Column(Integer, ForeignKey("submissions.id"))
    promoted_by_id = Column(Integer, ForeignKey("users.id"))
    created_utc = Column(Integer, default=0)

    comment = relationship("Comment", lazy="subquery")
    promoted_submission = relationship("Submission", lazy="subquery")
    board = relationship("Board", lazy="subquery")

    def __init__(self, **kwargs):
        kwargs["created_utc"] = int(time.time())
        super().__init__(**kwargs)

    def __repr__(self):
        return f"<CommentForwardRel(id={self.id}, comment={self.comment_id}, board_id={self.board_id})>"

"""class PostNotificationSubscriptions(Base):

    __tablename__ = "post_notification_subscriptions"
    id = Column(BigInteger, primary_key=True)
    user_id = Column(Integer, ForeignKey("users.id"))
    board_id = Column(Integer,ForeignKey("boards.id"), default=0)
    subbed_to_user_id = Column(Integer, ForeignKey("users.id"), default=0)
    #post_id = Column(Integer,ForeignKey("submissions.id"), default=0)

    #user = relationship("User", lazy="subquery")
    board = relationship("Board", lazy="subquery")
    #post = relationship("Submission", lazy="subquery")

    def __repr__(self):
        return f"<PostNotificationSubscription(id={self.id}"
"""


class BoardBlock(Base, Stndrd, Age_times):

    __tablename__ = "boardblocks"

    id = Column(BigInteger, primary_key=True)
    user_id = Column(Integer, ForeignKey("users.id"))
    board_id = Column(Integer, ForeignKey("boards.id"))
    created_utc = Column(Integer)

    user = relationship("User", overlaps="board_blocks,user")
    board = relationship("Board")

    def __repr__(self):
        return f"<BoardBlock(id={self.id}, uid={self.user_id}, board_id={self.board_id})>"
