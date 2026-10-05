from werkzeug.security import generate_password_hash, check_password_hash
import time
from sqlalchemy import *
from sqlalchemy.orm import relationship, deferred, joinedload, lazyload, contains_eager, aliased, Load
from os import environ
from secrets import token_hex
import random
import pyotp
from flask import session, g, request

from ruqqus.helpers.base36 import *
from ruqqus.helpers.security import *
from ruqqus.helpers.lazy import lazy
import ruqqus.helpers.aws as aws
from ruqqus.helpers.discord import add_role, delete_role, discord_log_event
#from ruqqus.helpers.alerts import send_notification
from .votes import Vote, CommentVote
from .alts import Alt
from .titles import Title
from .submission import Submission, SubmissionAux, SaveRelationship, RepostRelationship
from .comment import Comment, Notification, CommentSaveRelationship, CommentRepostRelationship
from .history import ViewHistory
from .boards import Board
from .board_relationships import *
from .mix_ins import *
from .subscriptions import *
from .userblock import *
from .badges import *
from .clients import *
from .paypal import PayPalTxn
from .flags import Report
from .regions import Region
from .curations import Curation, CurationFollow
from .chat import ChatConversation, ChatUnread
from ruqqus.__main__ import Base, cache, app


class User(Base, Stndrd, Age_times):

    __tablename__ = "users"
    id = Column(Integer, primary_key=True)
    username = Column(String, default=None)
    email = Column(String, default=None)
    passhash = deferred(Column(String, default=None))
    created_utc = Column(Integer, default=0)
    admin_level = Column(Integer, default=0)
    is_activated = Column(Boolean, default=False)
    creation_ip = Column(String, default=None)
    submissions = relationship(
        "Submission",
        lazy="dynamic",
        primaryjoin="Submission.author_id==User.id",
        back_populates="author",
        overlaps="author,author_rel"
    )
    comments = relationship(
        "Comment",
        lazy="dynamic",
        primaryjoin="Comment.author_id==User.id",
        back_populates="author",
        overlaps="author,comments"
    )
    votes = relationship("Vote", lazy="dynamic", back_populates="user", overlaps="user,votes")
    commentvotes = relationship("CommentVote", lazy="dynamic", back_populates="user", overlaps="user,commentvotes")
    notifications = relationship(
        "Notification",
        lazy="dynamic",
        back_populates="user",
        overlaps="user,notifications"
    )
    bio = Column(String, default="")
    bio_html = Column(String, default="")
    _badges = relationship("Badge", lazy="dynamic", backref="user", overlaps="_badges,user")
    real_id = Column(String, default=None)
    notifications = relationship(
        "Notification",
        lazy="dynamic",
        overlaps="user"
    )

    #unread_notifications_relationship=relationship(
    #    "Notification",
    #    primaryjoin="and_(Notification.user_id==User.id, Notification.read==False)")

    referred_by = Column(Integer, default=None)
    is_banned = Column(Integer, default=0)
    unban_utc = Column(Integer, default=0)
    ban_reason = Column(String, default="")
    defaultsorting = Column(String, default="hot")
    defaulttime = Column(String, default="all")
    feed_nonce = Column(Integer, default=0)
    login_nonce = Column(Integer, default=0)
    title_id = Column(Integer, ForeignKey("titles.id"), default=None)
    title = relationship("Title", lazy="joined")
    has_profile = Column(Boolean, default=False)
    has_banner = Column(Boolean, default=False)
    reserved = Column(String(256), default=None)
    is_nsfw = Column(Boolean, default=False)
    tos_agreed_utc = Column(Integer, default=0)
    profile_nonce = Column(Integer, default=0)
    banner_nonce = Column(Integer, default=0)
    last_siege_utc = Column(Integer, default=0)
    mfa_secret = deferred(Column(String(16), default=None))
    hide_offensive = Column(Boolean, default=True)  # superseded by filter_level
    # word filter the user browses with: 0 Off, 1 Standard, 2 Child
    filter_level = Column(SmallInteger, default=1)
    # word filter rating of the username / the bio
    name_severity = Column(SmallInteger, default=0)
    bio_severity = Column(SmallInteger, default=0)
    hide_bot = Column(Boolean, default=False)
    show_nsfl = Column(Boolean, default=False)
    is_private = Column(Boolean, default=False)
    unban_utc = Column(Integer, default=0)
    is_deleted = Column(Boolean, default=False)
    delete_reason = Column(String(500), default='')
    stored_karma = Column(Integer, default=0)
    stored_subscriber_count=Column(Integer, default=0)
    """posts_last_checked_utc = Column(Integer, default=0)
    replies_last_checked_utc = Column(Integer, default=0)
    mentions_last_checked_utc = Column(Integer, default=0)"""

    auto_join_chat=Column(Boolean, default=False)

    coin_balance=Column(Integer, default=0)
    premium_expires_utc=Column(Integer, default=0)
    premium_first_purchased_utc=Column(Integer, default=0)
    negative_balance_cents=Column(Integer, default=0)

    display_region=Column(String(32), default=None)
    region_settled_utc=Column(Integer, default=0)
    region_suspicion_flag=Column(Boolean, default=False)

    is_nofollow = Column(Boolean, default=False)
    custom_filter_list=Column(String(1000), default="")
    discord_id=Column(String(64), default=None)
    creation_region=Column(String(2), default=None)
    ban_evade=Column(Integer, default=0)

    profile_upload_ip=deferred(Column(String(255), default=None))
    banner_upload_ip=deferred(Column(String(255), default=None))
    profile_upload_region=deferred(Column(String(2)))
    banner_upload_region=deferred(Column(String(2)))
    
    color=Column(String(6), default="805ad5")
    secondary_color=Column(String(6), default="ffff00")
    signature=Column(String(280), default='')
    signature_html=Column(String(512), default="")

    #stuff to support name changes
    profile_set_utc=deferred(Column(Integer, default=0))
    banner_set_utc=deferred(Column(Integer, default=0))
    original_username=deferred(Column(String(255)))
    name_changed_utc=deferred(Column(Integer, default=0))


    moderates = relationship(
        "ModRelationship",
        primaryjoin="ModRelationship.user_id==User.id",
        overlaps="user"
    )
    banned_from = relationship(
        "BanRelationship",
        primaryjoin="BanRelationship.user_id==User.id",
        overlaps="user"
    )
    subscriptions = relationship("Subscription")
    boards_created = relationship("Board", lazy="dynamic")
    contributes = relationship(
        "ContributorRelationship",
        lazy="dynamic",
        primaryjoin="ContributorRelationship.user_id==User.id",
        overlaps="user,contributes"
    )
    board_blocks = relationship("BoardBlock", lazy="dynamic", overlaps="user,board_blocks")
    following = relationship("Follow", primaryjoin="Follow.user_id==User.id", overlaps="user,following")
    followers = relationship("Follow", primaryjoin="Follow.target_id==User.id", overlaps="target,followers")
    blocking = relationship("UserBlock", lazy="dynamic", primaryjoin="UserBlock.user_id==User.id", overlaps="user,blocking")
    blocked = relationship("UserBlock", lazy="dynamic", primaryjoin="UserBlock.target_id==User.id", overlaps="target,blocked")
    _applications = relationship("OauthApp", lazy="dynamic", overlaps="author,_applications")
    authorizations = relationship("ClientAuth", lazy="dynamic", overlaps="user,authorizations")
    _transactions = relationship(
        "PayPalTxn",
        lazy="dynamic",
        primaryjoin="PayPalTxn.user_id==User.id",
        overlaps="user,_transactions"
    )

    # properties defined as SQL server-side functions
    energy = deferred(Column(Integer, server_default=FetchedValue()))
    comment_energy = deferred(Column(Integer, server_default=FetchedValue()))
    forward_bonus_energy = deferred(Column(Integer, server_default=FetchedValue()))
    referral_count = deferred(Column(Integer, server_default=FetchedValue()))
    follower_count = deferred(Column(Integer, server_default=FetchedValue()))

    def __init__(self, **kwargs):

        if "password" in kwargs:

            kwargs["passhash"] = self.hash_password(kwargs["password"])
            kwargs.pop("password")

        kwargs["created_utc"] = int(time.time())

        super().__init__(**kwargs)

    def has_block(self, target):

        return g.db.query(UserBlock).filter_by(
            user_id=self.id, target_id=target.id).first()

    def is_blocked_by(self, user):

        return g.db.query(UserBlock).filter_by(
            user_id=user.id, target_id=self.id).first()

    def any_block_exists(self, other):

        return g.db.query(UserBlock).filter(or_(and_(UserBlock.user_id == self.id, UserBlock.target_id == other.id), and_(
            UserBlock.user_id == other.id, UserBlock.target_id == self.id))).first()

    def has_blocked_guild(self, board):

        return g.db.query(BoardBlock).filter_by(
            user_id=self.id, board_id=board.id).first()

    def validate_2fa(self, token):

        x = pyotp.TOTP(self.mfa_secret)
        return x.verify(token, valid_window=1)

    @property
    def mfa_removal_code(self):

        hashstr = f"{self.mfa_secret}+{self.id}+{self.original_username}"

        hashstr= generate_hash(hashstr)

        removal_code = base36encode(int(hashstr,16))

        #should be 25char long, left pad if needed
        while len(removal_code)<25:
            removal_code="0"+removal_code

        return removal_code

    @property
    def boards_subscribed(self):

        boards = [
            x.board for x in self.subscriptions if x.is_active and not x.board.is_banned]
        return boards

    @property
    def age(self):
        return int(time.time()) - self.created_utc

    @cache.memoize(timeout=300)
    def idlist(self, sort=None, page=1, t=None, filter_words="", **kwargs):

        posts = g.db.query(Submission.id).options(lazyload('*')).filter_by(is_banned=False,
                                                                           deleted_utc=0,
                                                                           stickied=False
                                                                           )

        if self.hide_offensive:
            posts = posts.filter_by(is_offensive=False)

        if self.hide_bot:
            posts = posts.filter_by(is_bot=False)

        board_ids = select(Subscription.board_id).filter_by(
            user_id=self.id,
            is_active=True
        )
        user_ids = select(Follow.user_id).filter_by(
            user_id=self.id
        ).join(Follow.target).where(
            User.is_private == False,
            User.is_nofollow == False
        )

        posts = posts.filter(
            or_(
                Submission.board_id.in_(board_ids),
                Submission.author_id.in_(user_ids)
            )
        )

        if self.admin_level < 4:
            # admins can see everything

            m = select(ModRelationship.board_id).filter_by(
                user_id=self.id,
                invite_rescinded=False
            )
            c = select(ContributorRelationship.board_id).filter_by(
                user_id=self.id
            )
            posts = posts.filter(
                or_(
                    Submission.author_id == self.id,
                    Submission.post_public == True,
                    Submission.board_id.in_(m),
                    Submission.board_id.in_(c)
                )
            )

            blocking = select(UserBlock.target_id).filter_by(
                user_id=self.id
            )
            # blocked = g.db.query(
            #     UserBlock.user_id).filter_by(
            #     target_id=self.id).subquery()

            posts = posts.filter(
                Submission.author_id.notin_(blocking) #,
                #Submission.author_id.notin_(blocked)
            ).join(Submission.board).filter(Board.is_banned==False)

        categories = kwargs.get("categories")
        if categories:
            board_ids_in_cats = select(Board.id).where(Board.subcat_id.in_(tuple(categories)))
            posts = posts.filter(Submission.board_id.in_(board_ids_in_cats))

        region = kwargs.get("region")
        if region:
            from ruqqus.routes.front import region_filter_condition
            region_list = region if isinstance(region, (list, tuple)) else [region]
            posts = posts.filter(region_filter_condition(region_list))

        language = kwargs.get("language")
        if language:
            language_list = language if isinstance(language, (list, tuple)) else [language]
            posts = posts.filter(Submission.language_code.in_(language_list))

        if filter_words:
            posts=posts.join(Submission.submission_aux)
            for word in filter_words:
                #print(word)
                posts=posts.filter(not_(SubmissionAux.title.ilike(f'%{word}%')))

        if t:
            now = int(time.time())
            if t == 'day':
                cutoff = now - 86400
            elif t == 'week':
                cutoff = now - 604800
            elif t == 'month':
                cutoff = now - 2592000
            elif t == 'year':
                cutoff = now - 31536000
            else:
                cutoff = 0
            posts = posts.filter(Submission.created_utc >= cutoff)

        gt = kwargs.get("gt")
        lt = kwargs.get("lt")

        if gt:
            posts = posts.filter(Submission.created_utc > gt)

        if lt:
            posts = posts.filter(Submission.created_utc < lt)

        if sort == None:
            sort= self.defaultsorting or "hot"

        if sort == "hot":
            posts = posts.order_by(Submission.score_best.desc())
        elif sort == "new":
            posts = posts.order_by(Submission.created_utc.desc())
        elif sort == "old":
            posts = posts.order_by(Submission.created_utc.asc())
        elif sort == "disputed":
            posts = posts.order_by(Submission.score_disputed.desc())
        elif sort == "top":
            posts = posts.order_by(Submission.score_top.desc())
        elif sort == "activity":
            posts = posts.order_by(Submission.score_activity.desc())
        else:
            abort(422)

        return [x[0] for x in posts.offset(25 * (page - 1)).limit(26).all()]

    @cache.memoize(timeout=3600)
    def interest_subcats(self):
        """
        Heuristic, non-ML category-affinity scoring for the For You feed:
        tallies subcats the user has already shown signal towards via
        subscriptions, followed accounts' subscriptions, and recent upvotes.
        Returns up to the top 10 subcat ids with positive affinity, ranked
        descending - or [] for a zero-signal account (for_you_idlist() then
        falls back to plain frontlist() at the route level).
        """

        scores = {}

        def add_scores(subcat_ids, weight):
            for subcat_id in subcat_ids:
                if subcat_id is None:
                    continue
                scores[subcat_id] = scores.get(subcat_id, 0) + weight

        sub_subcats = g.db.query(Board.subcat_id).join(
            Subscription, Subscription.board_id == Board.id
        ).filter(
            Subscription.user_id == self.id,
            Subscription.is_active == True
        ).all()
        add_scores([x[0] for x in sub_subcats], 3)

        followed_ids = select(Follow.target_id).filter_by(user_id=self.id)
        follow_subcats = g.db.query(Board.subcat_id).join(
            Subscription, Subscription.board_id == Board.id
        ).filter(
            Subscription.user_id.in_(followed_ids),
            Subscription.is_active == True
        ).all()
        add_scores([x[0] for x in follow_subcats], 2)

        CANDIDATE_CAP = 500
        recent_upvoted_submissions = select(Vote.submission_id).filter(
            Vote.user_id == self.id,
            Vote.vote_type == 1
        ).order_by(Vote.created_utc.desc()).limit(CANDIDATE_CAP)
        vote_subcats = g.db.query(Board.subcat_id).join(
            Submission, Submission.board_id == Board.id
        ).filter(
            Submission.id.in_(recent_upvoted_submissions)
        ).all()
        add_scores([x[0] for x in vote_subcats], 1)

        ranked = sorted(scores.items(), key=lambda kv: kv[1], reverse=True)
        return [subcat_id for subcat_id, score in ranked if score > 0][:10]

    @cache.memoize(timeout=300)
    def for_you_idlist(self, sort=None, page=1, t=None, filter_words='', **kwargs):
        """
        The For You feed: discovery content from subcats the viewer has
        shown affinity towards (see interest_subcats()), plus posts outside
        those subcats that the viewer's friends/followed accounts have
        upvoted enough to matter (see social_proof_post_ids()) - ranked
        with that same social proof as a tiebreaker-first boost. Excludes
        anything Following already covers (subscribed guilds / followed
        accounts) so For You stays additive rather than duplicating
        Following. Mirrors frontlist()'s visibility/mod/contributor/block/
        offensive/bot/opt-out filtering, with self in place of v. Callers
        should check interest_subcats() themselves first and fall back to
        frontlist() for a zero-signal account - this method assumes a
        non-empty subcat list.
        """

        subcats = self.interest_subcats()
        if not subcats:
            return []

        social_proof_ids = self.social_proof_post_ids()

        if sort == None:
            sort = self.defaultsorting or "hot"

        posts = g.db.query(Submission).options(
            lazyload('*'),
            Load(Board).lazyload('*')
        ).filter_by(
            is_banned=False,
            stickied=False
        ).filter(Submission.deleted_utc == 0)

        if self.hide_offensive:
            posts = posts.filter_by(is_offensive=False)

        if self.hide_bot:
            posts = posts.filter(Submission.is_bot == False)

        if self.admin_level >= 4:
            board_blocks = select(BoardBlock.board_id).filter_by(
                user_id=self.id
            ).subquery()
            posts = posts.filter(Submission.board_id.notin_(board_blocks))
        else:
            m = select(ModRelationship.board_id).filter_by(
                user_id=self.id, invite_rescinded=False
            ).subquery()
            c = select(ContributorRelationship.board_id).filter_by(
                user_id=self.id
            ).subquery()

            posts = posts.filter(
                or_(
                    Submission.author_id == self.id,
                    Submission.post_public == True,
                    Submission.board_id.in_(m),
                    Submission.board_id.in_(c)
                )
            )

            blocking = select(UserBlock.target_id).filter_by(
                user_id=self.id
            ).subquery()
            posts = posts.filter(Submission.author_id.notin_(blocking))

            board_blocks = select(BoardBlock.board_id).filter_by(
                user_id=self.id
            ).subquery()
            posts = posts.filter(Submission.board_id.notin_(board_blocks))

        posts = posts.join(Submission.board).filter(
            or_(
                Board.all_opt_out == False,
                Submission.board_id.in_(
                    select(Subscription.board_id).filter_by(
                        user_id=self.id,
                        is_active=True
                    ).subquery()
                )
            )
        )

        posts = posts.filter(or_(
            Board.subcat_id.in_(tuple(subcats)),
            Submission.id.in_(social_proof_ids)
        ))

        # explicit Categorical filter narrows the heuristic affinity set
        # (and anything let in via social proof) further - intersects
        # rather than replaces either
        explicit_categories = kwargs.get("categories")
        if explicit_categories:
            posts = posts.filter(Board.subcat_id.in_(tuple(explicit_categories)))

        region = kwargs.get("region")
        if region:
            from ruqqus.routes.front import region_filter_condition
            region_list = region if isinstance(region, (list, tuple)) else [region]
            posts = posts.filter(region_filter_condition(region_list))

        language = kwargs.get("language")
        if language:
            language_list = language if isinstance(language, (list, tuple)) else [language]
            posts = posts.filter(Submission.language_code.in_(language_list))

        if self.hide_offensive:
            posts = posts.filter(Board.subcat_id.notin_([44, 108]))

        posts = posts.filter(Submission.board_id != 1)

        # Following already covers subscribed guilds + followed accounts -
        # keep For You additive/discovery-oriented rather than duplicating it
        subscribed_board_ids = select(Subscription.board_id).filter_by(
            user_id=self.id, is_active=True
        )
        followed_user_ids = select(Follow.target_id).filter_by(user_id=self.id)
        posts = posts.filter(
            Submission.board_id.notin_(subscribed_board_ids),
            Submission.author_id.notin_(followed_user_ids)
        )

        posts = posts.options(contains_eager(Submission.board))

        if filter_words:
            posts = posts.join(Submission.submission_aux)
            for word in filter_words:
                posts = posts.filter(not_(SubmissionAux.title.ilike(f'%{word}%')))

        if t == None:
            t = self.defaulttime
        if t:
            now = int(time.time())
            if t == 'day':
                cutoff = now - 86400
            elif t == 'week':
                cutoff = now - 604800
            elif t == 'month':
                cutoff = now - 2592000
            elif t == 'year':
                cutoff = now - 31536000
            else:
                cutoff = 0
            posts = posts.filter(Submission.created_utc >= cutoff)

        gt = kwargs.get("gt")
        lt = kwargs.get("lt")
        if gt:
            posts = posts.filter(Submission.created_utc > gt)
        if lt:
            posts = posts.filter(Submission.created_utc < lt)

        social_proof_boost = Submission.id.in_(social_proof_ids)

        if sort == "hot":
            posts = posts.order_by(social_proof_boost.desc(), Submission.score_best.desc())
        elif sort == "new":
            posts = posts.order_by(social_proof_boost.desc(), Submission.created_utc.desc())
        elif sort == "old":
            posts = posts.order_by(social_proof_boost.desc(), Submission.created_utc.asc())
        elif sort == "disputed":
            posts = posts.order_by(social_proof_boost.desc(), Submission.score_disputed.desc())
        elif sort == "top":
            posts = posts.order_by(social_proof_boost.desc(), Submission.score_top.desc())
        elif sort == "activity":
            posts = posts.order_by(social_proof_boost.desc(), Submission.score_activity.desc())
        else:
            abort(400)

        return [x.id for x in posts.offset(25 * (page - 1)).limit(26).all()]

    @cache.memoize(300)
    def userpagelisting(self, v=None, page=1, sort="new", t="all"):

        now = int(time.time())
        if t == 'day':
            cutoff = now - 86400
        elif t == 'week':
            cutoff = now - 604800
        elif t == 'month':
            cutoff = now - 2592000
        elif t == 'year':
            cutoff = now - 31536000
        else:
            cutoff = 0

        if sort == "hot":
            sort_col = Submission.score_best
        elif sort == "disputed":
            sort_col = Submission.score_disputed
        elif sort == "top":
            sort_col = Submission.score_top
        elif sort == "activity":
            sort_col = Submission.score_activity
        elif sort in ("new", "old"):
            sort_col = None
        else:
            abort(422)

        if v and v.admin_level < 4:
            m = g.db.query(
                ModRelationship.board_id).filter_by(
                user_id=v.id,
                invite_rescinded=False).subquery()
            c = g.db.query(
                ContributorRelationship.board_id).filter_by(
                user_id=v.id).subquery()

        def apply_common_filters(q):
            if v and v.hide_offensive and v.id != self.id:
                q = q.filter(Submission.is_offensive == False)
            if v and v.hide_bot:
                q = q.filter(Submission.is_bot == False)
            if not (v and (v.admin_level >= 3)):
                q = q.filter(Submission.deleted_utc == 0)
            return q.filter(Submission.created_utc >= cutoff)

        forward_copies = g.db.query(ForwardRelationship.forward_submission_id).subquery()

        authored_sort_expr = sort_col if sort_col is not None else Submission.created_utc
        reposted_sort_expr = sort_col if sort_col is not None else RepostRelationship.created_utc

        # ---- Posts authored by this user ----
        authored = g.db.query(
            Submission.id,
            authored_sort_expr
        ).options(lazyload('*')).filter(
            Submission.author_id == self.id,
            Submission.id.notin_(forward_copies)
        )
        authored = apply_common_filters(authored)

        if not (v and (v.admin_level >= 3 or v.id == self.id)):
            authored = authored.filter_by(is_banned=False).join(Submission.board).filter(Board.is_banned == False)

        if v and v.admin_level >= 4:
            pass
        elif v:
            authored = authored.filter(
                or_(
                    Submission.author_id == v.id,
                    Submission.post_public == True,
                    Submission.board_id.in_(m),
                    Submission.board_id.in_(c)
                )
            )
        else:
            authored = authored.filter(Submission.post_public == True)

        # ---- Posts this user has reposted to their own profile (Twitter-
        # retweet style - no independent copy is made, so the same
        # underlying Submission is reused; ordered by when THIS user
        # reposted it for "new"/"old", or by the post's own metric for
        # every other sort mode, same as an authored post would be) ----
        reposted = g.db.query(
            Submission.id,
            reposted_sort_expr
        ).join(
            RepostRelationship, RepostRelationship.submission_id == Submission.id
        ).filter(RepostRelationship.user_id == self.id)
        reposted = apply_common_filters(reposted)

        if not (v and v.admin_level >= 3):
            reposted = reposted.filter(Submission.is_banned == False).join(
                Submission.board).filter(Board.is_banned == False)

        if v and v.admin_level >= 4:
            pass
        elif v:
            reposted = reposted.filter(
                or_(
                    Submission.post_public == True,
                    Submission.board_id.in_(m),
                    Submission.board_id.in_(c)
                )
            )
        else:
            reposted = reposted.filter(Submission.post_public == True)

        # Two different source tables can't be paginated with a single SQL
        # OFFSET/LIMIT, so a generous bounded candidate set from each is
        # merge-sorted in Python instead - correct at this site's scale,
        # though a profile with 1000s of posts+reposts could in theory miss
        # items past this cap on very deep pages.
        CANDIDATE_CAP = 300
        authored_rows = authored.order_by(
            authored_sort_expr.desc() if sort != "old" else authored_sort_expr.asc()
        ).limit(CANDIDATE_CAP).all()
        reposted_rows = reposted.order_by(
            reposted_sort_expr.desc() if sort != "old" else reposted_sort_expr.asc()
        ).limit(CANDIDATE_CAP).all()

        combined = list(authored_rows) + list(reposted_rows)
        combined.sort(key=lambda row: row[1], reverse=(sort != "old"))

        ids = [row[0] for row in combined]
        listing = ids[25 * (page - 1):25 * (page - 1) + 26]
        return listing

    @cache.memoize(300)
    def commentlisting(self, v=None, page=1, sort="new", t="all"):

        now = int(time.time())
        if t == 'day':
            cutoff = now - 86400
        elif t == 'week':
            cutoff = now - 604800
        elif t == 'month':
            cutoff = now - 2592000
        elif t == 'year':
            cutoff = now - 31536000
        else:
            cutoff = 0

        if sort == "hot":
            sort_col = Comment.score_hot
        elif sort == "disputed":
            sort_col = Comment.score_disputed
        elif sort == "top":
            sort_col = Comment.score_top
        elif sort in ("new", "old"):
            sort_col = None
        else:
            abort(422)

        if v and v.admin_level < 4:
            m = g.db.query(ModRelationship).filter_by(user_id=v.id, invite_rescinded=False).subquery()
            c = v.contributes.subquery()

        def apply_common_filters(q):
            if v and v.hide_offensive and v.id != self.id:
                q = q.filter(Comment.is_offensive == False)
            if v and v.hide_bot:
                q = q.filter(Comment.is_bot == False)
            q = q.filter(Submission.is_sensitive == False)
            if (not v) or v.admin_level < 3:
                q = q.filter(Comment.deleted_utc == 0)
            return q.filter(Comment.created_utc >= cutoff)

        def apply_visibility(q):
            if v and v.admin_level >= 4:
                return q
            elif v:
                q = q.join(m, m.c.board_id == Submission.board_id, isouter=True
                    ).join(c, c.c.board_id == Submission.board_id, isouter=True
                    ).join(Board, Board.id == Submission.board_id)
                return q.filter(or_(Comment.author_id == v.id,
                                     Submission.post_public == True,
                                     Board.is_private == False,
                                     m.c.board_id != None,
                                     c.c.board_id != None),
                                 Board.is_banned == False)
            else:
                return q.join(Board, Board.id == Submission.board_id).filter(
                    or_(Submission.post_public == True, Board.is_private == False), Board.is_banned == False)

        authored_sort_expr = sort_col if sort_col is not None else Comment.created_utc
        reposted_sort_expr = sort_col if sort_col is not None else CommentRepostRelationship.created_utc

        # ---- Comments authored by this user ----
        authored = self.comments.options(lazyload('*')).join(Comment.post)
        if not (v and (v.admin_level >= 3 or v.id == self.id)):
            authored = authored.filter(Comment.is_banned == False)
        authored = apply_common_filters(authored)
        authored = apply_visibility(authored)
        authored = authored.with_entities(Comment.id, authored_sort_expr)

        # ---- Comments this user has reposted to their own profile
        # (Twitter-retweet style - no independent copy, so the same
        # underlying Comment is reused; ordered by when THIS user
        # reposted it for "new"/"old", or by the comment's own metric for
        # every other sort mode, same as an authored comment would be) ----
        reposted = g.db.query(Comment.id, reposted_sort_expr).join(
            CommentRepostRelationship, CommentRepostRelationship.comment_id == Comment.id
        ).filter(CommentRepostRelationship.user_id == self.id).join(Comment.post)
        if not (v and v.admin_level >= 3):
            reposted = reposted.filter(Comment.is_banned == False)
        reposted = apply_common_filters(reposted)
        reposted = apply_visibility(reposted)

        # Two different source tables can't be paginated with a single SQL
        # OFFSET/LIMIT, so a generous bounded candidate set from each is
        # merge-sorted in Python instead (mirrors userpagelisting()).
        CANDIDATE_CAP = 300
        authored_rows = authored.order_by(
            authored_sort_expr.desc() if sort != "old" else authored_sort_expr.asc()
        ).limit(CANDIDATE_CAP).all()
        reposted_rows = reposted.order_by(
            reposted_sort_expr.desc() if sort != "old" else reposted_sort_expr.asc()
        ).limit(CANDIDATE_CAP).all()

        combined = list(authored_rows) + list(reposted_rows)
        combined.sort(key=lambda row: row[1], reverse=(sort != "old"))

        ids = [row[0] for row in combined]
        listing = ids[25 * (page - 1):25 * (page - 1) + 26]
        return listing

    @property
    @lazy
    def mods_anything(self):

        return bool([i for i in self.moderates if i.accepted])


    @property
    @lazy
    def subscribed_to_anything(self):
        return bool([i for i in self.subscriptions if i.is_active])

    @property
    @lazy
    def curations_owned(self):
        return g.db.query(Curation).filter_by(
            owner_id=self.id
        ).order_by(Curation.created_utc.desc()).all()

    @property
    @lazy
    def curations_followed(self):
        followed_ids = select(CurationFollow.curation_id).filter_by(user_id=self.id)
        return g.db.query(Curation).filter(Curation.id.in_(followed_ids)).all()

    @property
    @lazy
    def curations_anything(self):
        return bool(self.curations_owned or self.curations_followed)

    @property
    @lazy
    def following_ids(self):
        return set(x[0] for x in g.db.query(Follow.target_id).filter_by(user_id=self.id).all())

    @property
    @lazy
    def friend_ids(self):
        """Mutual follows - accounts self follows that also follow self back."""
        follower_ids = select(Follow.user_id).filter_by(target_id=self.id)
        return set(x[0] for x in g.db.query(Follow.target_id).filter(
            Follow.user_id == self.id,
            Follow.target_id.in_(follower_ids)
        ).all())

    @cache.memoize(timeout=300)
    def social_proof_post_ids(self):
        """Posts self's friends or followed accounts have upvoted enough
        (3+) to matter as a discovery signal in For You - see
        for_you_idlist()."""
        qualifying = set()
        for id_set in (self.friend_ids, self.following_ids - self.friend_ids):
            if not id_set:
                continue
            rows = g.db.query(Vote.submission_id).filter(
                Vote.vote_type == 1,
                Vote.user_id.in_(id_set)
            ).group_by(Vote.submission_id).having(func.count(Vote.user_id) >= 3).all()
            qualifying |= {r[0] for r in rows}
        return qualifying

    @property
    @lazy
    def chat_requests_count(self):
        """Number of pending message-request CONVERSATIONS (not messages)
        where self is the recipient, not the initiator."""
        return g.db.query(ChatConversation).filter(
            ChatConversation.status == "request",
            ChatConversation.initiator_id != self.id,
            or_(ChatConversation.user_a_id == self.id, ChatConversation.user_b_id == self.id)
        ).count()

    @property
    @lazy
    def chat_unread_count(self):
        """Number of Inbox CONVERSATIONS with at least one unread message
        (not a sum of unread message counts) - matches the "Requests (N)"
        conversation-count convention and X's own DM badge behavior."""
        return g.db.query(ChatUnread).filter(
            ChatUnread.user_id == self.id,
            ChatUnread.unread_count > 0
        ).count()

    @property
    @lazy
    def chat_unread_messages(self):
        """Total unread chat MESSAGES across all conversations (the number
        on the navbar chat button), unlike chat_unread_count above."""
        return ChatUnread.total_for_user(g.db, self.id)

    @property
    def boards_modded(self):

        z = [x.board for x in self.moderates if x and x.board and x.accepted and not x.board.is_banned]
        z = sorted(z, key=lambda x: x.name)

        return z

    @property
    @cache.memoize(timeout=3600)  # 1hr cache time for user rep
    def karma(self):
        return 503 if self.id==1 else int(self.energy) - self.post_count + int(self.forward_bonus_energy)

    @property
    @cache.memoize(timeout=3600)
    def comment_karma(self):
        return 0 if self.id==1 else int(self.comment_energy) - self.comments.filter(
            Comment.parent_submission is not None).filter_by(is_banned=False).count()

    @property
    @cache.memoize(timeout=3600)
    def true_score(self):
        self.stored_karma = max((self.karma + self.comment_karma), -5)
        return self.stored_karma

    @property
    def base36id(self):
        return base36encode(self.id)

    @property
    def fullname(self):
        return f"t1_{self.base36id}"

    @property
    #@cache.memoize(timeout=60)
    @lazy
    def has_report_queue(self):
        board_ids = select(ModRelationship.board_id).options(lazyload('*')).filter(
            ModRelationship.user_id == self.id,
            ModRelationship.accepted == True,
            or_(
                ModRelationship.perm_full == True,
                ModRelationship.perm_content == True
            )
        ).subquery()
        
        posts=g.db.query(Submission).options(lazyload('*')).filter(
            Submission.board_id.in_(
                board_ids
            ), 
            Submission.mod_approved == None, 
            Submission.is_banned == False,
            Submission.deleted_utc==0
            ).join(Report, Report.post_id==Submission.id)

        return bool(posts.first())
           

    @property
    def banned_by(self):

        if not self.is_banned:
            return None

        return g.db.query(User).filter_by(id=self.is_banned).first()

    def has_badge(self, badgedef_id):
        return self._badges.filter_by(badge_id=badgedef_id).first()

    def vote_status_on_post(self, post):

        return post.voted

    def vote_status_on_comment(self, comment):

        return comment.voted

    def hash_password(self, password):
        return generate_password_hash(
            password, method='pbkdf2:sha512', salt_length=8)

    def verifyPass(self, password):
        return check_password_hash(self.passhash, password)

    @property
    def feedkey(self):

        return generate_hash(f"{self.username}{self.id}{self.feed_nonce}{self.created_utc}")

    @property
    def formkey(self):

        if "session_id" not in session:
            session["session_id"] = token_hex(16)

        msg = f"{session['session_id']}+{self.id}+{self.login_nonce}"

        return generate_hash(msg)

    def validate_formkey(self, formkey):

        return validate_hash(f"{session['session_id']}+{self.id}+{self.login_nonce}", formkey)

    @property
    def url(self):
        return f"/@{self.username}"

    @property
    def permalink(self):
        return self.url

    @property
    def uid_permalink(self):
        return f"/uid/{self.base36id}"

    @property
    def original_link(self):
        return f"/@{self.original_username}"


    def __repr__(self):
        return f"<User(username={self.username})>"

    def notification_commentlisting(self, page=1, all_=False, comments_only=False, mentions_only=False, system_only=False):

        notifications = self.notifications.options(
            joinedload(Notification.comment).joinedload(Comment.comment_aux)
        ).join(
            Notification.comment
        ).filter(
            Comment.is_banned == False,
            Comment.deleted_utc == 0
        )

        if comments_only:
            cs = g.db.query(Comment.id).filter(Comment.author_id == self.id).subquery()
            ps = g.db.query(Submission.id).filter(Submission.author_id == self.id).subquery()
            notifications = notifications.filter(
                or_(
                    Comment.parent_comment_id.in_(cs),
                    and_(
                        Comment.level == 1,
                        Comment.parent_submission.in_(ps)
                    )
                )
            )
        elif mentions_only:
            cs = g.db.query(Comment.id).filter(Comment.author_id == self.id).subquery()
            ps = g.db.query(Submission.id).filter(Submission.author_id == self.id).subquery()
            notifications = notifications.filter(
                and_(
                    Comment.parent_comment_id.notin_(cs),
                    or_(
                        Comment.level > 1,
                        Comment.parent_submission.notin_(ps)
                    )
                )
            )
        elif system_only:
            notifications = notifications.filter(Comment.author_id == 1)
        elif not all_:
            notifications = notifications.filter(Notification.read == False)

        notifications = notifications.order_by(
            Notification.id.desc()
        ).offset(25 * (page - 1)).limit(26)

        output = []
        results = notifications.all()
        for x in results[0:25]:
            x.read = True
            g.db.add(x)
            output.append(x.comment_id)
        g.db.commit()
        return output

    def notification_postlisting(self, all_=False, page=1):

        notifications=self.notifications.join(
            Notification.post
            ).filter(
            Submission.is_banned==False, 
            Submission.deleted_utc==0
            )

        if not all_:
            notifications=notifications.filter(Notification.read==False)

        notifications=notifications.options(
                contains_eager(Notification.post)
            ).order_by(
                Notification.id.desc()
            ).offset(25*(page-1)).limit(26)

        output=[]
        for x in notifications[0:25]:
            x.read=True
            g.db.add(x)
            output.append(x.submission_id)

        g.db.commit()
        return output

    @property
    @lazy
    def mentions_count(self):
        cs=g.db.query(Comment.id).filter(Comment.author_id==self.id).subquery()
        ps=g.db.query(Submission.id).filter(Submission.author_id==self.id).subquery()
        return self.notifications.options(
            lazyload('*')
            ).join(
            Notification.comment
            ).filter(
            Notification.read==False,
            Comment.is_banned == False,
            Comment.deleted_utc == 0
            ).filter(
                and_(
                    Comment.parent_comment_id.notin_(cs),
                    or_(
                        Comment.level>1,
                        Comment.parent_submission.notin_(ps)
                    )
                )
            ).count()


    @property
    @lazy
    def comment_notifications_count(self):
        cs=g.db.query(Comment.id).filter(Comment.author_id==self.id).subquery()
        ps=g.db.query(Submission.id).filter(Submission.author_id==self.id).subquery()
        return self.notifications.options(
            lazyload('*')
            ).join(
            Notification.comment
            ).filter(
            Comment.is_banned == False,
            Comment.deleted_utc == 0
            ).filter(
            Notification.read==False,
            or_(
                Comment.parent_comment_id.in_(cs),
                and_(
                    Comment.level==1,
                    Comment.parent_submission.in_(ps)
                    )
                )
            ).count()

    @property
    @lazy
    def post_notifications_count(self):
        return self.notifications.filter(
            Notification.read==False
            ).join(
            Submission,
            Submission.id==Notification.submission_id
            ).filter(
            Submission.is_banned==False,
            Submission.deleted_utc==0
            ).count()

    @property
    @lazy
    def system_notif_count(self):
        return self.notifications.options(
            lazyload('*')
            ).join(
            Notification.comment
            ).filter(
            Notification.read==False,
            Comment.author_id==1
            ).count()

    @property
    @lazy
    def notifications_count(self):
        return self.notifications.options(
            lazyload('*')
            ).filter(
                Notification.read==False
            ).join(Notification.comment, isouter=True
            ).join(Notification.post, isouter=True
            ).filter(
                or_(
                    and_(
                        Comment.is_banned==False,
                        Comment.deleted_utc==0
                    ),
                    and_(
                        Submission.is_banned==False,
                        Submission.deleted_utc==0
                    )
                )
            ).count()

    @property
    def throttle_state(self):
        # Deliberately not cached (unlike true_score) - the boil/gear
        # throttle state has to reflect current Redis state on every
        # render, not an hourly snapshot.
        from ruqqus.helpers import throttle
        return throttle.get_display_state(self.id, request.remote_addr)


    @property
    def post_count(self):

        return self.submissions.filter_by(is_banned=False).count()

    @property
    def comment_count(self):

        return self.comments.filter(Comment.parent_submission!=None).filter_by(
            is_banned=False, deleted_utc=0).count()

    @property
    @lazy
    def alts(self):

        subq = g.db.query(Alt).filter(
            or_(
                Alt.user1==self.id,
                Alt.user2==self.id
                )
            ).subquery()

        data = g.db.query(
            User,
            aliased(Alt, alias=subq)
            ).join(
            subq,
            or_(
                subq.c.user1==User.id,
                subq.c.user2==User.id
                )
            ).filter(
            User.id != self.id
            ).order_by(User.username.asc()).all()

        data=[x for x in data]
        output=[]
        for x in data:
            user=x[0]
            user._is_manual=x[1].is_manual
            output.append(user)

        return output
    
    def alts_subquery(self):
        return g.db.query(User.id).filter(
            or_(
                User.id.in_(
                    g.db.query(Alt.user1).filter(
                        Alt.user2==self.id
                    ).subquery()
                ),
                User.id.in_(
                    g.db.query(Alt.user2).filter(
                        Alt.user1==self.id
                    ).subquery()
                ).subquery()
            )
        ).subquery()
        

    def alts_threaded(self, db):

        subq = db.query(Alt).filter(
            or_(
                Alt.user1==self.id,
                Alt.user2==self.id
                )
            ).subquery()

        data = db.query(
            User,
            aliased(Alt, alias=subq)
            ).join(
            subq,
            or_(
                subq.c.user1==User.id,
                subq.c.user2==User.id
                )
            ).filter(
            User.id != self.id
            ).order_by(User.username.asc()).all()

        data=[x for x in data]
        output=[]
        for x in data:
            user=x[0]
            user._is_manual=x[1].is_manual
            output.append(user)

        return output

    def has_follower(self, user):

        return g.db.query(Follow).filter_by(
            target_id=self.id, user_id=user.id).first()

    def set_profile(self, file):

        self.del_profile()
        self.profile_nonce += 1

        aws.upload_file(name=f"uid/{self.base36id}/profile-{self.profile_nonce}.png",
                        file=file,
                        resize=(100, 100)
                        )
        self.has_profile = True
        self.profile_upload_ip=request.remote_addr
        self.profile_set_utc=int(time.time())
        self.profile_upload_region=request.headers.get("cf-ipcountry")
        g.db.add(self)

    def set_banner(self, file):

        self.del_banner()
        self.banner_nonce += 1

        aws.upload_file(name=f"uid/{self.base36id}/banner-{self.banner_nonce}.png",
                        file=file)

        self.has_banner = True
        self.banner_upload_ip=request.remote_addr
        self.banner_set_utc=int(time.time())
        self.banner_upload_region=request.headers.get("cf-ipcountry")

        g.db.add(self)

    def del_profile(self):

        if self.profile_set_utc>1616443200:
            aws.delete_file(name=f"uid/{self.base36id}/profile-{self.profile_nonce}.png")
        else:
            aws.delete_file(name=f"users/{self.username}/profile-{self.profile_nonce}.png")
        self.has_profile = False
        try:
            g.db.add(self)
        except:
            pass

    def del_banner(self):

        if self.banner_set_utc>1616443200:
            aws.delete_file(name=f"uid/{self.base36id}/banner-{self.banner_nonce}.png")
        else:
            aws.delete_file(name=f"users/{self.username}/banner-{self.banner_nonce}.png")
        self.has_banner = False
        try:
            g.db.add(self)
        except:
            pass

    @property
    def banner_url(self):

        if self.has_banner:
            if self.banner_set_utc>1616443200:
                return f"https://i.ruqqus.com/uid/{self.base36id}/banner-{self.banner_nonce}.png"
            else:
                return f"https://i.ruqqus.com/users/{self.username}/banner-{self.banner_nonce}.png"
        else:
            return "/assets/images/profiles/default_bg.png"

    @property
    def profile_url(self):

        if self.has_profile and not self.is_deleted:
            if self.profile_set_utc>1616443200:
                return f"https://{app.config['S3_BUCKET']}/uid/{self.base36id}/profile-{self.profile_nonce}.png"
            else:
                return f"https://{app.config['S3_BUCKET']}/users/{self.username}/profile-{self.profile_nonce}.png"
        else:
            return f"http{'s' if app.config['FORCE_HTTPS'] else ''}://{app.config['SERVER_NAME']}/assets/images/profiles/default-profile-pic.png"

    @property
    def available_titles(self):

        locs = {"v": self,
                "Board": Board,
                "Submission": Submission
                }

        titles = [
            i for i in g.db.query(Title).order_by(
                text("id asc")).all() if eval(
                i.qualification_expr, {}, locs)]
        return titles

    @property
    def can_make_guild(self):
        return (self.has_premium or self.admin_level>=3 or self.true_score >= 250 or (self.created_utc <= 1592974538 and self.true_score >= 50)) and self.can_join_gms

    @property
    def can_join_gms(self):
        return len([x for x in self.boards_modded if x.is_siegable]) < 10

    @property
    def can_siege(self):

        if self.is_suspended:
            return False

        now = int(time.time())

        return now - max(self.last_siege_utc,
                         self.created_utc) > 60 * 60 * 24 * 7

    @property
    def can_submit_image(self):
        # Has premium
        # Has 1000 Rep, or 500 for older accounts
        # if connecting through Tor, must have verified email
        return (self.has_premium or self.true_score >= 500) and (self.is_activated or request.headers.get("cf-ipcountry")!="T1")

    @property
    def can_upload_avatar(self):
        return (self.has_premium or self.true_score >= 300 or self.created_utc <= 1592974538) and (self.is_activated or request.headers.get("cf-ipcountry")!="T1")

    @property
    def can_upload_banner(self):
        return (self.has_premium or self.true_score >= 500 or self.created_utc <= 1592974538) and (self.is_activated or request.headers.get("cf-ipcountry")!="T1")

    @property
    def json_raw(self):
        data= {'username': self.username,
                'permalink': self.permalink,
                'is_banned': self.is_suspended,
                'is_premium': self.has_premium_no_renew,
                'created_utc': self.created_utc,
                'id': self.base36id,
                'is_private': self.is_private,
                'profile_url': self.profile_url,
                'banner_url': self.banner_url,
                'title': self.title.json if self.title else None,
                'bio': self.bio,
                'bio_html': self.bio_html
                }

        if self.real_id:
            data['real_id']=self.real_id

        return data
    

    @property
    def json_core(self):

        now=int(time.time())
        if self.is_suspended:
            return {'username': self.username,
                    'permalink': self.permalink,
                    'is_banned': True,
                    'is_permanent_ban':not bool(self.unban_utc),
                    'ban_reason': self.ban_reason,
                    'id': self.base36id
                    }

        elif self.is_deleted:
            return {'username': self.username,
                    'permalink': self.permalink,
                    'is_deleted': True,
                    'id': self.base36id
                    }
        return self.json_raw
        


    @property
    def json(self):
        data= self.json_core

        if self.is_suspended or self.is_deleted:
            return data

        data["badges"]=[x.json_core for x in self.badges]
        data['post_rep']= int(self.karma)
        data['comment_rep']= int(self.comment_karma)
        data['post_count']=self.post_count
        data['comment_count']=self.comment_count

        return data
    

    @property
    def total_karma(self):

        return 503 if self.id==1 else max(self.karma + self.comment_karma, -5)

    @property
    def can_use_darkmode(self):
        return True
        # return self.referral_count or self.has_earned_darkmode or
        # self.has_badge(16) or self.has_badge(17)

    @property
    def is_valid(self):
        if self.is_banned and self.unban_utc==0:
            return False

        elif self.is_deleted:
            return False

        else:
            return True
    

    def ban(self, admin=None, reason=None,  days=0):
        
        admin=admin if admin else g.db.query(User).filter_by(id=1).first()

        self.is_banned = admin.id if admin else 1
        if reason:
            self.ban_reason = reason

        g.db.add(self)
        g.db.flush()

        if days > 0:
            ban_time = int(time.time()) + (days * 86400)
            self.unban_utc = ban_time

        else:
            # Takes care of all functions needed for account termination
            self.unban_utc = 0
            if self.has_banner:
                self.del_banner()
            if self.has_profile:
                self.del_profile()
            add_role(self, "banned")
            delete_role(self, "member")

            #unprivate guilds if no mods remaining
            for b in self.boards_modded:
                if b.mods_count == 0:
                    b.is_private = False
                    b.restricted_forwarding = False
                    #b.all_opt_out = False
                    g.db.add(b)

        try:
            g.db.add(self)
        except:
            pass
        
        discord_ban_action = f"{days} Day Ban" if days else "Perm Ban"
        discord_log_event(discord_ban_action, self, admin, reason=reason)

    def unban(self):

        # Takes care of all functions needed for account reinstatement.

        self.is_banned = 0
        self.unban_utc = 0

        delete_role(self, "banned")

        g.db.add(self)
        
        discord_log_event("Unban", self, g.v, reason=self.ban_reason)


    @property
    def is_suspended(self):
        return (self.is_banned and (self.unban_utc ==
                                    0 or self.unban_utc > time.time()))

    @property
    def is_blocking(self):
        return self.__dict__.get('_is_blocking', 0)

    @property
    def is_blocked(self):
        return self.__dict__.get('_is_blocked', 0)

    def refresh_selfset_badges(self):

        # check self-setting badges
        badge_types = g.db.query(BadgeDef).filter(
            BadgeDef.qualification_expr.isnot(None)).all()
        for badge in badge_types:
            if eval(badge.qualification_expr, {}, {'v': self}):
                if not self.has_badge(badge.id):
                    new_badge = Badge(user_id=self.id,
                                      badge_id=badge.id,
                                      created_utc=int(time.time())
                                      )
                    g.db.add(new_badge)

            else:
                bad_badge = self.has_badge(badge.id)
                if bad_badge:
                    g.db.delete(bad_badge)

        try:
            g.db.add(self)
        except:
            pass

    @property
    def applications(self):
        return [x for x in self._applications.order_by(
            OauthApp.id.asc()).all()]


    def saved_idlist(self, page=1):

        posts = g.db.query(Submission.id).options(lazyload('*')).filter_by(is_banned=False,
                                                                           deleted_utc=0
                                                                           )

        posts = posts.join(
            SaveRelationship, SaveRelationship.submission_id == Submission.id
        ).filter(SaveRelationship.user_id == self.id)

        if self.admin_level < 4:
            # admins can see everything

            m = g.db.query(
                ModRelationship.board_id).filter_by(
                user_id=self.id,
                invite_rescinded=False).subquery()
            c = g.db.query(
                ContributorRelationship.board_id).filter_by(
                user_id=self.id).subquery()
            posts = posts.filter(
                or_(
                    Submission.author_id == self.id,
                    Submission.post_public == True,
                    Submission.board_id.in_(m),
                    Submission.board_id.in_(c)
                )
            )

            blocking = g.db.query(
                UserBlock.target_id).filter_by(
                user_id=self.id).subquery()
            blocked = g.db.query(
                UserBlock.user_id).filter_by(
                target_id=self.id).subquery()

            posts = posts.filter(
                Submission.author_id.notin_(blocking),
                Submission.author_id.notin_(blocked)
            )

        posts=posts.order_by(SaveRelationship.created_utc.desc())

        return [x[0] for x in posts.offset(25 * (page - 1)).limit(26).all()]


    def saved_comment_idlist(self, page=1):

        comments = g.db.query(Comment.id).options(lazyload('*')).join(Comment.post).filter(
            Comment.is_banned == False,
            Comment.deleted_utc == 0
        )

        comments = comments.join(
            CommentSaveRelationship, CommentSaveRelationship.comment_id == Comment.id
        ).filter(CommentSaveRelationship.user_id == self.id)

        if self.admin_level < 4:
            # admins can see everything

            m = g.db.query(
                ModRelationship.board_id).filter_by(
                user_id=self.id,
                invite_rescinded=False).subquery()
            c = g.db.query(
                ContributorRelationship.board_id).filter_by(
                user_id=self.id).subquery()
            comments = comments.filter(
                or_(
                    Comment.author_id == self.id,
                    Submission.post_public == True,
                    Submission.board_id.in_(m),
                    Submission.board_id.in_(c)
                )
            )

            blocking = g.db.query(
                UserBlock.target_id).filter_by(
                user_id=self.id).subquery()
            blocked = g.db.query(
                UserBlock.user_id).filter_by(
                target_id=self.id).subquery()

            comments = comments.filter(
                Comment.author_id.notin_(blocking),
                Comment.author_id.notin_(blocked)
            )

        comments = comments.order_by(CommentSaveRelationship.created_utc.desc())

        return [x[0] for x in comments.offset(25 * (page - 1)).limit(26).all()]


    def forwarded_idlist(self, v=None, page=1):
        """Posts this user has personally forwarded to a guild, or comments
        they've promoted into a new post - as the actor, regardless of who
        authored the original content. Ordered by when they did it, most
        recent first. Public activity, visible to any viewer subject to
        the same content-visibility rules as any other listing."""

        fwd = g.db.query(
            ForwardRelationship.forward_submission_id.label('sid'),
            ForwardRelationship.created_utc.label('ts')
        ).filter(ForwardRelationship.forwarded_by_id == self.id)

        promoted = g.db.query(
            CommentForwardRelationship.forwarded_submission_id.label('sid'),
            CommentForwardRelationship.created_utc.label('ts')
        ).filter(CommentForwardRelationship.forwarded_by_id == self.id)

        activity = fwd.union_all(promoted).subquery()

        posts = g.db.query(Submission, activity.c.ts).join(
            activity, activity.c.sid == Submission.id
        )

        if not (v and v.admin_level >= 3):
            posts = posts.filter(Submission.deleted_utc == 0, Submission.is_banned == False)
            posts = posts.join(Board, Board.id == Submission.board_id).filter(Board.is_banned == False)

        if v and v.admin_level >= 4:
            pass
        elif v:
            m = g.db.query(
                ModRelationship.board_id).filter_by(
                user_id=v.id,
                invite_rescinded=False).subquery()
            c = g.db.query(
                ContributorRelationship.board_id).filter_by(
                user_id=v.id).subquery()
            posts = posts.filter(
                or_(
                    Submission.author_id == v.id,
                    Submission.post_public == True,
                    Submission.board_id.in_(m),
                    Submission.board_id.in_(c)
                )
            )
        else:
            posts = posts.filter(Submission.post_public == True)

        posts = posts.order_by(activity.c.ts.desc())

        return [row[0].id for row in posts.offset(25 * (page - 1)).limit(26).all()]


    def history_idlist(self, page=1):
        """Posts viewed by this user, most recently viewed first - for the History tab."""

        vh = select(ViewHistory).filter_by(user_id=self.id).subquery()

        posts = g.db.query(Submission).options(lazyload('*')).filter_by(
            is_banned=False,
            deleted_utc=0
        ).join(vh, vh.c.submission_id == Submission.id)

        if self.admin_level < 4:
            m = g.db.query(
                ModRelationship.board_id).filter_by(
                user_id=self.id,
                invite_rescinded=False).subquery()
            c = g.db.query(
                ContributorRelationship.board_id).filter_by(
                user_id=self.id).subquery()
            posts = posts.filter(
                or_(
                    Submission.author_id == self.id,
                    Submission.post_public == True,
                    Submission.board_id.in_(m),
                    Submission.board_id.in_(c)
                )
            )

            blocking = g.db.query(
                UserBlock.target_id).filter_by(
                user_id=self.id).subquery()
            blocked = g.db.query(
                UserBlock.user_id).filter_by(
                target_id=self.id).subquery()

            posts = posts.filter(
                Submission.author_id.notin_(blocking),
                Submission.author_id.notin_(blocked)
            )

        posts = posts.order_by(vh.c.viewed_utc.desc())

        return [x.id for x in posts.offset(25 * (page - 1)).limit(26).all()]


    def _voted_post_idlist(self, vote_type, page=1, exclude_self=False):
        """Posts this user upvoted/downvoted, most recently voted first."""

        vt = select(Vote).filter_by(user_id=self.id, vote_type=vote_type).subquery()

        posts = g.db.query(Submission).options(lazyload('*')).filter_by(
            is_banned=False,
            deleted_utc=0
        ).join(vt, vt.c.submission_id == Submission.id)

        if exclude_self:
            posts = posts.filter(Submission.author_id != self.id)

        if self.admin_level < 4:
            m = g.db.query(
                ModRelationship.board_id).filter_by(
                user_id=self.id,
                invite_rescinded=False).subquery()
            c = g.db.query(
                ContributorRelationship.board_id).filter_by(
                user_id=self.id).subquery()
            posts = posts.filter(
                or_(
                    Submission.author_id == self.id,
                    Submission.post_public == True,
                    Submission.board_id.in_(m),
                    Submission.board_id.in_(c)
                )
            )

            blocking = g.db.query(
                UserBlock.target_id).filter_by(
                user_id=self.id).subquery()
            blocked = g.db.query(
                UserBlock.user_id).filter_by(
                target_id=self.id).subquery()

            posts = posts.filter(
                Submission.author_id.notin_(blocking),
                Submission.author_id.notin_(blocked)
            )

        posts = posts.order_by(vt.c.created_utc.desc())

        return [x.id for x in posts.offset(25 * (page - 1)).limit(26).all()]


    def upvoted_idlist(self, page=1):
        return self._voted_post_idlist(1, page=page, exclude_self=True)


    def downvoted_idlist(self, page=1):
        return self._voted_post_idlist(-1, page=page)


    def _voted_comment_idlist(self, vote_type, page=1, exclude_self=False):
        """Comments this user upvoted/downvoted, most recently voted first."""

        posts = g.db.query(Submission).options(
            lazyload('*')).join(Submission.board)

        if self.admin_level >= 4:
            pass
        else:
            m = g.db.query(ModRelationship.board_id).filter_by(
                user_id=self.id, invite_rescinded=False).subquery()
            c = g.db.query(
                ContributorRelationship.board_id).filter_by(
                user_id=self.id).subquery()

            posts = posts.filter(
                or_(
                    Submission.author_id == self.id,
                    Submission.post_public == True,
                    Submission.board_id.in_(m),
                    Submission.board_id.in_(c),
                    Board.is_private == False
                )
            )

        posts = posts.subquery()

        cv = select(CommentVote).filter_by(user_id=self.id, vote_type=vote_type).subquery()

        comments = g.db.query(Comment).options(lazyload('*')).join(
            cv, cv.c.comment_id == Comment.id
        ).join(posts, Comment.parent_submission == posts.c.id)

        if exclude_self:
            comments = comments.filter(Comment.author_id != self.id)

        if self.hide_offensive:
            comments = comments.filter(Comment.is_offensive == False)

        if self.hide_bot:
            comments = comments.filter(Comment.is_bot == False)

        if self.admin_level <= 3:
            blocking = g.db.query(
                UserBlock.target_id).filter_by(
                user_id=self.id).subquery()
            blocked = g.db.query(
                UserBlock.user_id).filter_by(
                target_id=self.id).subquery()

            comments = comments.filter(
                Comment.author_id.notin_(blocking),
                Comment.author_id.notin_(blocked)
            )

        if self.admin_level < 3:
            comments = comments.filter(Comment.is_banned == False).filter(Comment.deleted_utc == 0)

        comments = comments.order_by(cv.c.created_utc.desc())

        return [x.id for x in comments.offset(25 * (page - 1)).limit(26).all()]


    def upvoted_comment_idlist(self, page=1):
        return self._voted_comment_idlist(1, page=page, exclude_self=True)


    def downvoted_comment_idlist(self, page=1):
        return self._voted_comment_idlist(-1, page=page)



    def guild_rep(self, guild, recent=0):

        

        posts=g.db.query(Submission.score_top).filter_by(
            is_banned=False,
            original_board_id=guild.id,
            is_bot=False)

        if recent:
            cutoff=int(time.time())-60*60*24*recent
            posts=posts.filter(Submission.created_utc>cutoff)

        posts=posts.all()

        post_rep= sum([x[0] for x in posts]) - len(posts)


        comments=g.db.query(Comment.score_top).filter_by(
            is_banned=False,
            original_board_id=guild.id,
            is_bot=False)

        if recent:
            cutoff=int(time.time())-60*60*24*recent
            comments=comments.filter(Comment.created_utc>cutoff)

        comments=comments.all()

        comment_rep=sum([x[0] for x in comments]) - len(comments)

        return int(post_rep + comment_rep)

    @property
    def has_premium(self):
        
        now=int(time.time())

        if self.negative_balance_cents:
            return False

        elif self.premium_expires_utc > now:
            return True

        elif self.coin_balance >=1:
            self.coin_balance -=1
            self.premium_expires_utc = now + 60*60*24*7

            if not self.premium_first_purchased_utc:
                self.premium_first_purchased_utc = now

            add_role(self, "premium")

            g.db.add(self)

            return True

        else:

            if self.premium_expires_utc:
                delete_role(self, "premium")
                self.premium_expires_utc=0
                g.db.add(self)

            return False

    @property
    def has_premium_no_renew(self):
        
        now=int(time.time())

        if self.negative_balance_cents:
            return False
        elif self.premium_expires_utc > now:
            return True
        elif self.coin_balance>=1:
            return True
        else:
            return False
    
    
    @property
    def renew_premium_time(self):
        return time.strftime("%d %b %Y at %H:%M:%S",
                             time.gmtime(self.premium_expires_utc))

    @property
    def premium_first_purchased_date(self):
        if not self.premium_first_purchased_utc:
            return None
        return time.strftime("%d %B %Y", time.gmtime(self.premium_first_purchased_utc))

    @property
    def display_region_name(self):
        if not self.display_region:
            return "Unknown"
        region = g.db.query(Region).filter_by(code=self.display_region).first()
        return region.current_name if region else "Unknown"

    @property
    def filter_words(self):
        l= [i.lstrip().rstrip() for i in self.custom_filter_list.split('\n')] if self.custom_filter_list else []
        l=[i for i in l if i]
        return l
                             
    @property
    def boards_modded_ids(self):
        return [x.id for x in self.boards_modded]

    @property
    def txn_history(self):
        
        return self._transactions.filter(PayPalTxn.status!=1).order_by(PayPalTxn.created_utc.desc()).all()
    

    @property
    def json_admin(self):
        data=self.json_raw

        data['creation_ip']=self.creation_ip
        data['creation_region']=self.creation_region
        data['email']=self.email
        data['email_verified']=self.is_activated

        return data

    @property
    def can_upload_comment_image(self):
        return self.has_premium and (request.headers.get("cf-ipcountry")!="T1" or self.is_activated)

    @property
    def can_change_name(self):
        return self.name_changed_utc < int(time.time())-60*60*24*7 and self.coin_balance>=20

    @property
    @cache.memoize(60*60*24)
    def badges(self):
        self.refresh_selfset_badges()
        g.db.commit()
        return self._badges.all()

    @property
    def is_following(self):
        return self.__dict__.get('_is_following',None)
    
    @property
    def unban_string(self):
        if self.unban_utc==0:
            return "Permanent Ban"

        wait = self.unban_utc - int(time.time())

        if wait<60:
            text="just a moment"
        else:
            days = wait // (60*60*24)
            wait -= days*60*60*24

            hours=wait // (60*60)
            wait -= hours*60*60

            minutes=wait//60

            text=f"{days}d {hours:02d}h {minutes:02d}m"

        return f"Unban in {text}"

