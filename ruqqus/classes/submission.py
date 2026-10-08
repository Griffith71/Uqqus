from flask import render_template, request, abort, g
import re
import time
from sqlalchemy import *
from sqlalchemy.orm import relationship, deferred
import math
from urllib.parse import urlparse
import random
from os import environ
import requests
from .mix_ins import *
from ruqqus.helpers.base36 import *
from ruqqus.helpers.lazy import lazy
from ruqqus.helpers import comment_permission as cperm
from ruqqus.helpers import anonymity
from ruqqus.helpers import community_notes
import ruqqus.helpers.aws as aws
from ruqqus.__main__ import Base, cache, app
from .votes import Vote, CommentVote
from .domains import Domain
from .flags import Flag
from .comment import Comment
from .titles import Title


class SubmissionAux(Base):

    __tablename__ = "submissions_aux"

    # we don't care about this ID
    key_id = Column(BigInteger, primary_key=True)
    id = Column(BigInteger, ForeignKey("submissions.id"))
    title = Column(String(500), default=None)
    url = Column(String(500), default=None)
    body = Column(String(25000), default="")
    body_html = Column(String(50000), default="")
    ban_reason = Column(String(128), default="")
    hidden_reason = Column(String(128), default="")
    embed_url = Column(String(10000), default="")
    embed_type = Column(String(16), default=None)
    meta_title=Column(String(512), default="")
    meta_description=Column(String(1024), default="")
    preview_image_url=Column(String(1024), default="")


class Submission(Base, Stndrd, Age_times, Scores, Fuzzing):

    __tablename__ = "submissions"

    id = Column(BigInteger, primary_key=True)
    submission_aux = relationship(
        "SubmissionAux",
        lazy="joined",
        uselist=False,
        innerjoin=True,
        primaryjoin="Submission.id==SubmissionAux.id")
    author_id = Column(BigInteger, ForeignKey("users.id"))
    repost_id = Column(BigInteger, ForeignKey("submissions.id"), default=0)
    edited_utc = Column(BigInteger, default=0)
    created_utc = Column(BigInteger, default=0)
    is_banned = Column(Boolean, default=False)
    deleted_utc = Column(Integer, default=0)
    purged_utc = Column(Integer, default=0)
    distinguish_level = Column(Integer, default=0)
    gm_distinguish = Column(Integer, ForeignKey("boards.id"), default=0)
    distinguished_board = relationship("Board", lazy="joined", primaryjoin="Board.id==Submission.gm_distinguish")
    created_str = Column(String(255), default=None)
    stickied = Column(Boolean, default=False)
    _comments = relationship(
        "Comment",
        lazy="dynamic",
        primaryjoin="Comment.parent_submission==Submission.id",
        backref="submissions",
        overlaps="post"
    )
    domain_ref = Column(Integer, ForeignKey("domains.id"))
    domain_obj = relationship("Domain")
    flags = relationship("Flag", backref="submission")
    is_approved = Column(Integer, ForeignKey("users.id"), default=0)
    approved_utc = Column(Integer, default=0)
    board_id = Column(Integer, ForeignKey("boards.id"), default=None)
    original_board_id = Column(Integer, ForeignKey("boards.id"), default=None)
    original_board = relationship(
        "Board", primaryjoin="Board.id==Submission.original_board_id")
    creation_ip = Column(String(64), default="")
    mod_approved = Column(Integer, default=None)
    accepted_utc = Column(Integer, default=0)
    _own_is_image = Column("is_image", Boolean, default=False)
    has_thumb = Column(Boolean, default=False)
    post_public = Column(Boolean, default=True)
    score_hot = Column(Float, default=0)
    score_disputed = Column(Float, default=0)
    score_top = Column(Float, default=1)
    score_activity = Column(Float, default=0)
    is_offensive = Column(Boolean, default=False)
    is_sensitive = Column(Boolean, default=False)
    # word filter rating of the title + text: 0 clean, 1 profanity, 2 extreme
    # (see helpers/word_filter_store.py); the version says which list rated it
    word_severity = Column(SmallInteger, default=0)
    word_filter_version = Column(String(12), default=None)
    # who may comment (helpers/comment_permission.py): 0 everyone, 1 accounts the
    # author follows, 2 Premium accounts. Only read on a post on the author's
    # own profile; forwarded copies follow their guild's rules.
    comment_permission = Column(SmallInteger, default=0)
    # content disclosure, set by the author (and copied onto forwards)
    paid_partnership = Column(Boolean, default=False)
    made_with_ai = Column(Boolean, default=False)
    # posted anonymously: author_id stays, but only the author and admins are
    # told who it is. Never changes after creation. See helpers/anonymity.py.
    is_anonymous = Column(Boolean, default=False)
    hidden_by_guild = Column(Boolean, default=False)
    board = relationship(
        "Board",
        lazy="joined",
        innerjoin=True,
        primaryjoin="Submission.board_id==Board.id",
        overlaps="submissions"
    )
    author = relationship(
        "User",
        lazy="joined",
        innerjoin=True,
        primaryjoin="Submission.author_id==User.id",
        back_populates="submissions",
        overlaps="submissions,author"
    )
    is_pinned = Column(Boolean, default=False)
    score_best = Column(Float, default=0)
    reports = relationship("Report", backref="submission")
    is_bot = Column(Boolean, default=False)

    upvotes = Column(Integer, default=1)
    downvotes = Column(Integer, default=0)
    creation_region=Column(String(2), default=None)
    language_code=Column(String(5), default=None)

    app_id=Column(Integer, ForeignKey("oauth_apps.id"), default=None)
    oauth_app=relationship("OauthApp")

    approved_by = relationship(
        "User",
        uselist=False,
        primaryjoin="Submission.is_approved==User.id")

    # not sure if we need this
    reposts = relationship("Submission", lazy="joined", remote_side=[id])

    # These are virtual properties handled as postgres functions server-side
    # There is no difference to SQLAlchemy, but they cannot be written to

    ups = deferred(Column(Integer, server_default=FetchedValue()))
    downs = deferred(Column(Integer, server_default=FetchedValue()))
    #age=deferred(Column(Integer, server_default=FetchedValue()))
    comment_count = Column(Integer, server_default=FetchedValue())
    #flag_count=deferred(Column(Integer, server_default=FetchedValue()))
    #report_count=deferred(Column(Integer, server_default=FetchedValue()))
    score = deferred(Column(Float, server_default=FetchedValue()))
    #is_public=deferred(Column(Boolean, server_default=FetchedValue()))

    awards = relationship("AwardRelationship", lazy="joined")

    rank_hot = deferred(Column(Float, server_default=FetchedValue()))
    rank_fiery = deferred(Column(Float, server_default=FetchedValue()))
    rank_activity = deferred(Column(Float, server_default=FetchedValue()))
    rank_best = deferred(Column(Float, server_default=FetchedValue()))

    votes = relationship("Vote", back_populates="post", overlaps="post,votes")

    def __init__(self, *args, **kwargs):

        if "created_utc" not in kwargs:
            kwargs["created_utc"] = int(time.time())
            kwargs["created_str"] = time.strftime(
                "%I:%M %p on %d %b %Y", time.gmtime(
                    kwargs["created_utc"]))

        kwargs["creation_ip"] = request.remote_addr

        super().__init__(*args, **kwargs)

    def __repr__(self):
        return f"<Submission(id={self.id})>"

    @property
    @lazy
    def board_base36id(self):
        return base36encode(self.board_id)

    @property
    @lazy
    def is_deleted(self):
        return bool(self.deleted_utc)
    

    @property
    def is_repost(self):
        return bool(self.repost_id)

    @property
    def is_profile_post(self):
        # deferred import - boards.py imports from this module, so this
        # can't be a top-level import without a circular-import error
        from .boards import get_profile_board_id
        return self.board_id == get_profile_board_id()

    @property
    def forwards(self):
        """Independent per-guild copies of this primary post, each with its
        own votes/comments (see ForwardRelationship). Empty for a post that
        is itself a forward (only primary posts can be forwarded further)."""
        return g.db.query(Submission).filter_by(
            repost_id=self.id).order_by(Submission.created_utc.asc()).all()

    @property
    def is_forward_copy(self):
        """True if this row was created by the Forward feature (as opposed
        to a legacy pre-Forward resubmit, which also sets repost_id but
        keeps its own frozen "repost" badge/behavior)."""
        from .board_relationships import ForwardRelationship
        return g.db.query(ForwardRelationship).filter_by(
            forward_submission_id=self.id).first() is not None

    @property
    def forwarded_by(self):
        """The user who forwarded this into its guild, if that was someone
        other than the post's own author - so the byline can read "X
        forwarded Y's post" and X carries responsibility for it being in
        this guild. Returns None for primaries, legacy reposts, and
        forwards the author made themselves (plain "by <author>" byline)."""
        from .board_relationships import ForwardRelationship
        from .user import User
        rel = g.db.query(ForwardRelationship).filter_by(
            forward_submission_id=self.id).first()
        if not rel or rel.forwarded_by_id == self.author_id:
            return None
        return g.db.query(User).filter_by(id=rel.forwarded_by_id).first()

    @property
    def is_comment_forward(self):
        from .board_relationships import CommentForwardRelationship
        return g.db.query(CommentForwardRelationship).filter_by(
            forwarded_submission_id=self.id).first() is not None

    @property
    def forwarded_from_comment(self):
        """The original Comment this post was promoted from, or None."""
        from .board_relationships import CommentForwardRelationship
        rel = g.db.query(CommentForwardRelationship).filter_by(
            forwarded_submission_id=self.id).first()
        return rel.comment if rel else None

    @property
    def comment_forwarded_by(self):
        """Mirrors forwarded_by: the user who forwarded this comment into a
        post, if different from the post's own author (who stays the
        original comment's author, preserving their delete rights)."""
        from .board_relationships import CommentForwardRelationship
        from .user import User
        rel = g.db.query(CommentForwardRelationship).filter_by(
            forwarded_submission_id=self.id).first()
        if not rel or rel.forwarded_by_id == self.author_id:
            return None
        return g.db.query(User).filter_by(id=rel.forwarded_by_id).first()

    @property
    def is_archived(self):
        return int(time.time()) - self.created_utc > 60 * 60 * 24 * 180

    @property
    @lazy
    def fullname(self):
        return f"t2_{self.base36id}"    
        
    @property
    @lazy
    def permalink(self):

        output = self.title.lower()

        output = re.sub(r'&\w{2,3};', '', output)
        output = [re.sub(r'\W', '', word) for word in output.split()]
        output = [x for x in output if x][0:6]

        output = '-'.join(output)

        if not output:
            output = '-'

        if self.is_profile_post:
            return f"/post/{self.base36id}/{output}"

        return f"/+{self.board.name}/post/{self.base36id}/{output}"

    @property
    def is_archived(self):

        now = int(time.time())

        cutoff = now - (60 * 60 * 24 * 180)

        return self.created_utc < cutoff

    def rendered_page(self, comment=None, comment_info=None, v=None):

        # check for banned
        if self.purged_utc > 0 or self.deleted_utc > 0:
            template = "submission_deleted.html"
        elif v and v.admin_level >= 3:
            template = "submission.html"
        elif self.is_banned:
            template = "submission_banned.html"
        elif self.hidden_by_guild and not (v and self.board.has_mod(v, 'content')):
            template = "submission_banned.html"
        else:
            template = "submission.html"

        private = not self.is_public and not self.is_pinned and not self.board.can_view(
            v)

        if private and (not v or not self.author_id == v.id):
            abort(403)
        elif private:
            self.__dict__["nested_comments"] = []
        else:
            # load and tree comments
            # calling this function with a comment object will do a comment
            # permalink thing
            if "nested_comments" not in self.__dict__ and "_preloaded_comments" in self.__dict__:
                self.tree_comments(comment=comment)

        # return template
        is_allowed_to_comment = self.board.can_comment(
            v) and not self.is_archived
        # why a signed-in viewer may not write a comment here although the
        # guild allows it: the author limited the post (None when they may)
        comment_restriction = cperm.restriction(self, v) if v else None
        
    #    if request.args.get("sort", "Hot") != "new":
    #        self.nested_comments = [x for x in self.nested_comments if x.is_pinned] + [x for x in self.nested_comments if not x.is_pinned]

        return render_template(template,
                               v=v,
                               p=self,
                               sort_method=request.args.get(
                                   "sort", "Hot").capitalize(),
                               linked_comment=comment,
                               comment_info=comment_info,
                               is_allowed_to_comment=is_allowed_to_comment,
                               comment_restriction=comment_restriction,
                               render_child_comments=True,
                               b=None if self.is_profile_post else self.board
                               )

    @property
    @lazy
    def domain(self):

        if not self.url:
            return "text post"
        domain = urlparse(self.url).netloc
        if domain.startswith("www."):
            domain = domain[4:]
        return domain

    def tree_comments(self, comment=None, v=None):

        comments = self.__dict__.get('_preloaded_comments',[])
        if not comments:
            return

        # the word filter hides a comment together with everything under it:
        # a hidden comment is simply never attached, so neither is its subtree
        from ruqqus.helpers.visibility import comment_hidden
        viewer = v if v is not None else getattr(g, 'v', None)
        comments = [c for c in comments if not comment_hidden(c, viewer)]

        pinned_comment=[]

        index = {}
        for c in comments:

            if c.is_pinned and c.parent_fullname==self.fullname:
                pinned_comment=[c]
                continue

            if c.parent_fullname in index:
                index[c.parent_fullname].append(c)
            else:
                index[c.parent_fullname] = [c]

        for c in comments:
            c.__dict__["nested_comments"] = index.get(c.fullname, [])

        if comment:
            self.__dict__["nested_comments"] = [comment]
        else:
            self.__dict__["nested_comments"] = pinned_comment + index.get(self.fullname, [])

    @property
    def active_flags(self):
        if self.is_approved:
            return 0
        else:
            return len(self.flags)

    @property
    def active_reports(self):
        if self.mod_approved:
            return 0
        else:
            return self.reports.filter(
                Report.created_utc > self.accepted_utc).count()

    @property
    #@lazy
    def thumb_url(self):

        if self.has_thumb:
            return f"https://i.ruqqus.com/posts/{self.base36id}/thumb.png"
        elif self.is_image:
            return self.url
        elif self.is_repost:
            # a forward's thumbnail generation isn't run separately -
            # inherit the primary post's once it's ready
            return self.reposts.thumb_url
        else:
            return None

    def visibility_reason(self, v):


        if not v or self.author_id == v.id:
            return "this is your content."
        elif self.is_pinned:
            return "a guildmaster has pinned it."
        elif self.board.has_mod(v):
            return f"you are a guildmaster of +{self.board.name}."
        elif self.board.has_contributor(v):
            return f"you are an approved contributor in +{self.board.name}."
        elif v.admin_level >= 4:
            return "you are a Ruqqus admin."

    @property
    @lazy
    def is_crosspost(self):
        return bool((self.domain==app.config["SERVER_NAME"]) and re.match(r"^https?://[a-zA-Z0-9_.-]+/\+\w+/post/(\w+)(/[a-zA-Z0-9_-]+/?)?$", self.url))
    

    @property

    def json_raw(self):
        data = {'author_name': None if (self.author.is_deleted or anonymity.identity_hidden(self, anonymity.current_viewer())) else self.author.username,
                'is_anonymous': bool(self.is_anonymous),
                'permalink': self.permalink,
                'is_banned': bool(self.is_banned),
                'is_deleted': self.is_deleted,
                'created_utc': self.created_utc,
                'id': self.base36id,
                'fullname': self.fullname,
                'title': self.title,
                'is_sensitive': self.is_sensitive,
                'comment_permission': cperm.mode_of(self),
                'paid_partnership': bool(self.paid_partnership),
                'made_with_ai': bool(self.made_with_ai),
                'community_note': community_notes.text_of(self),
                'is_bot': self.is_bot,
                'thumb_url': self.thumb_url,
                'domain': self.domain,
                'is_archived': self.is_archived,
                'url': self.url,
                'body': self.body,
                'body_html': self.body_html,
                'created_utc': self.created_utc,
                'edited_utc': self.edited_utc or 0,
                'guild_name': self.board.name,
                'original_guild_name': self.original_board.name if not self.board_id == self.original_board_id else None,
                'original_guild_id': self.original_board.id if not self.board_id == self.original_board_id else None,
                'guild_id': base36encode(self.board_id),
                'comment_count': self.comment_count,
                'score': self.score_fuzzed,
                'upvotes': self.upvotes_fuzzed,
                'downvotes': self.downvotes_fuzzed,
                'award_count': self.award_count,
                'is_offensive': self.is_offensive,
                'meta_title': self.meta_title,
                'meta_description': self.meta_description,
                'is_pinned': self.is_pinned,
                'is_distinguished': bool(self.distinguish_level),
                'is_heralded': bool(self.gm_distinguish),
                'is_crosspost': self.is_crosspost,
                'herald_guild': self.distinguished_board.name if self.gm_distinguish else None
                }
        if self.ban_reason:
            data["ban_reason"]=self.ban_reason

        if self.board_id != self.original_board_id and self.original_board:
            data['original_guild_name'] = self.original_board.name
            data['original_guild_id'] = base36encode(self.original_board_id)
        return data

    @property
    def json_core(self):

        if self.is_banned:
            return {'is_banned': True,
                    'is_deleted': self.is_deleted,
                    'ban_reason': self.ban_reason,
                    'id': self.base36id,
                    'title': self.title,
                    'permalink': self.permalink,
                    'guild_name': self.board.name,
                    'is_pinned': self.is_pinned
                    }
        elif self.is_deleted:
            return {'is_banned': bool(self.is_banned),
                    'is_deleted': True,
                    'id': self.base36id,
                    'title': self.title,
                    'permalink': self.permalink,
                    'guild_name': self.board.name
                    }

        return self.json_raw

    @property
    def json(self):

        data=self.json_core
        
        if self.deleted_utc > 0 or self.is_banned:
            return data

        data["author"]=None if anonymity.identity_hidden(self, anonymity.current_viewer()) else self.author.json_core
        data["guild"]=self.board.json_core
        data["original_guild"]=self.original_board.json_core if not self.board_id==self.original_board_id else None
        data["comment_count"]: self.comment_count

    
        if "nested_comments" in self.__dict__:
            data["replies"]=[x.json_core for x in self.nested_comments]

        from ruqqus.helpers import poll_store
        poll = poll_store.json_of(self)
        if poll:
            data["poll"] = poll

        if "_voted" in self.__dict__:
            data["voted"] = self._voted

        if "_saved" in self.__dict__:
            data["saved"] = bool(self._saved)

        return data

    @property
    def voted(self):
        return self._voted if "_voted" in self.__dict__ else 0

    @property
    def voted_elsewhere(self):
        """Where the viewer's vote in this post's family is, when it is not on this post: None, or
        {"id", "guild", "label", "message"} (helpers/vote_copies.py). The arrows are drawn locked."""
        return self.__dict__.get("_voted_elsewhere")

    @property
    def saved(self):
        return bool(self._saved) if "_saved" in self.__dict__ else False

    @property
    def reposted(self):
        return bool(self._reposted) if "_reposted" in self.__dict__ else False

    @property
    def social_proof_label(self):
        return self.__dict__.get('_social_proof_label', None)

    @property
    def user_title(self):
        return self._title if "_title" in self.__dict__ else self.author.title

    @property
    def title(self):
        return self.submission_aux.title

    @title.setter
    def title(self, x):
        self.submission_aux.title = x
        g.db.add(self.submission_aux)

    @property
    def url(self):
        return self.submission_aux.url

    @url.setter
    def url(self, x):
        self.submission_aux.url = x
        g.db.add(self.submission_aux)

    @property
    def body(self):
        return self.submission_aux.body

    @body.setter
    def body(self, x):
        self.submission_aux.body = x
        g.db.add(self.submission_aux)

    @property
    def body_html(self):
        return self.submission_aux.body_html

    @body_html.setter
    def body_html(self, x):
        self.submission_aux.body_html = x
        g.db.add(self.submission_aux)

    @property
    def ban_reason(self):
        return self.submission_aux.ban_reason

    @ban_reason.setter
    def ban_reason(self, x):
        self.submission_aux.ban_reason = x
        g.db.add(self.submission_aux)

    @property
    def hidden_reason(self):
        return self.submission_aux.hidden_reason

    @hidden_reason.setter
    def hidden_reason(self, x):
        self.submission_aux.hidden_reason = x
        g.db.add(self.submission_aux)

    @property
    def embed_url(self):
        # a forwarded copy shows the original's embed (the embed is detected
        # in the background for the original only, and changes with its link)
        return self.submission_aux.embed_url or (self.reposts.embed_url if self.is_repost and self.reposts else None)

    @embed_url.setter
    def embed_url(self, x):
        self.submission_aux.embed_url = x
        g.db.add(self.submission_aux)

    @property
    def meta_title(self):
        return self.submission_aux.meta_title or (self.reposts.meta_title if self.is_repost else "")

    @meta_title.setter
    def meta_title(self, x):
        self.submission_aux.meta_title=x
        g.db.add(self.submission_aux)

    @property
    def meta_description(self):
        return self.submission_aux.meta_description or (self.reposts.meta_description if self.is_repost else "")

    @meta_description.setter
    def meta_description(self, x):
        self.submission_aux.meta_description=x
        g.db.add(self.submission_aux)

    @property
    def preview_image_url(self):
        return self.submission_aux.preview_image_url or (self.reposts.preview_image_url if self.is_repost else "")

    @preview_image_url.setter
    def preview_image_url(self, x):
        self.submission_aux.preview_image_url=x
        g.db.add(self.submission_aux)


    def is_guildmaster(self, perm=None):
        mod=self.__dict__.get('_is_guildmaster', False)

        if not mod:
            return False
        elif not perm:
            return True
        else:
            return mod.perm_full or mod.__dict__[f"perm_{perm}"]

        return output

    @property
    def is_blocking_guild(self):
        return self.__dict__.get('_is_blocking_guild', False)

    @property
    def is_blocked(self):
        return self.__dict__.get('_is_blocked', False)

    @property
    def is_blocking(self):
        return self.__dict__.get('_is_blocking', False)

    @property
    def is_subscribed(self):
        return self.__dict__.get('_is_subscribed', False)

    @property
    def is_public(self):
        return self.post_public or not self.board.is_private

    @property
    def flag_count(self):
        return len(self.flags)

    @property
    def repost_count(self):
        return g.db.query(RepostRelationship).filter_by(submission_id=self.id).count()

    @property
    def report_count(self):
        return len(self.reports)

    @property
    def award_count(self):
        return len(self.awards)

    @property
    def embed_template(self):
        return f"site_embeds/{self.domain_obj.embed_template}.html"

    @property
    def embed_kind(self):
        """Unifies the legacy per-domain embed system with the generic
        detection in ruqqus.helpers.embed.detect_video_embed for
        rendering: 'video' (native <video> tag), 'iframe' (bare iframe
        src - trusted, either a named platform from KNOWN_PROVIDER_HANDLERS
        or an allowlisted oEmbed provider discovered generically),
        'iframe_untrusted' (bare iframe src scraped from an arbitrary
        page's own og:video tag - render with a strict sandbox), 'widget'
        (a trusted first-party script/card snippet - X, Instagram, TikTok -
        rendered via site_embeds/widget.html, not autoplayed), or None (no
        embed at all, or a domain with a manually-configured legacy
        Domain.embed_template not covered by either system above).
        """
        if self.submission_aux.embed_type:
            return self.submission_aux.embed_type
        if self.is_repost and self.reposts and not self.submission_aux.embed_url:
            return self.reposts.embed_kind
        if self.domain_obj and self.domain_obj.embed_template in ("youtube", "bitchute", "rumble_embed"):
            return "iframe"
        return None

    @property
    def self_download_json(self):

        #This property should never be served to anyone but author and admin
        if not self.is_banned and self.deleted_utc == 0:
            return self.json_core

        data= {
            "title":self.title,
            "author": self.author.username,
            "url": self.url,
            "body": self.body,
            "body_html": self.body_html,
            "is_banned": bool(self.is_banned),
            "deleted_utc": self.deleted_utc,
            'created_utc': self.created_utc,
            'id': self.base36id,
            'fullname': self.fullname,
            'guild_name': self.board.name,
            'comment_count': self.comment_count,
            'permalink': self.permalink
        }

        if self.original_board_id and (self.original_board_id!= self.board_id):

            data['original_guild_name'] = self.original_board.name

        return data

    @property
    def json_admin(self):

        data=self.json_raw

        data["creation_ip"]=self.creation_ip
        data["creation_region"]=self.creation_region

        return data

    @property
    def is_exiled_for(self):
        return self.__dict__.get('_is_exiled_for', None)

    @property
    def has_uploaded_image(self):
        """True when the post's link is an image uploaded to our own bucket
        (it can be replaced or removed), not a link to somewhere else."""
        bucket = app.config.get("S3_BUCKET", "i.ruqqus.com")
        if not self.url:
            return False
        if self.url.startswith(f"https://{bucket}/post/"):
            return True
        # or a picture in the author's linked storage (helpers/media)
        from ruqqus.helpers.media.attach import is_media_url
        return bool(self._own_is_image) and is_media_url(self.url, app.config["SERVER_NAME"])

    @property
    def is_image(self):
        return bool(self._own_is_image) or bool(self.is_repost and self.reposts.is_image)

    @is_image.setter
    def is_image(self, other):
        self._own_is_image = bool(other)
    
    @property
    def shortlink(self):
        
        protocol="https" if app.config["FORCE_HTTPS"] else "http"
        
        if app.config["SHORT_DOMAIN"]:
            return f"{protocol}://{app.config['SHORT_DOMAIN']}/{self.base36id}"
        else:
            return f"{protocol}://{app.config['SERVER_NAME']}/post/{self.base36id}"
    
class SaveRelationship(Base, Stndrd):

    __tablename__="save_relationship"

    id=Column(Integer, primary_key=true)
    user_id=Column(Integer, ForeignKey("users.id"))
    submission_id=Column(Integer, ForeignKey("submissions.id"))
    created_utc = Column(Integer, default=0)
    # the member's folder for this bookmark (classes/bookmark_folder.py), or none: unsorted
    folder_id = Column(Integer, ForeignKey("bookmark_folders.id", ondelete="SET NULL"), nullable=True)


class RepostRelationship(Base, Stndrd):
    """Records that `user` reposted `submission` to their own profile,
    Twitter-retweet style. Unlike Forward, this creates no independent
    copy - votes/comments/authorship all stay on the one shared
    submission. The row exists only so the reposter's profile can show
    the post (ordered by when they reposted it, not when it was
    originally posted) with an inline "Repost" tag next to its normal
    "by <author>" byline, and so it can be un-reposted."""

    __tablename__ = "repost_relationship"
    __table_args__ = (UniqueConstraint('user_id', 'submission_id', name='repost_unique'),)

    id = Column(Integer, primary_key=True)
    user_id = Column(Integer, ForeignKey("users.id"))
    submission_id = Column(Integer, ForeignKey("submissions.id"))
    created_utc = Column(Integer, default=0)
