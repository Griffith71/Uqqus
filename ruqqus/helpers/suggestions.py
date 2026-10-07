"""Who to follow: users, guilds and curations suggested to a member.

Suggestions come from signals the site already keeps, all of them indexed: who the
member follows follows, which guilds those accounts joined, the categories the
member's own joins and votes point at (User.interest_subcats) and plain popularity
for a member with no signal yet. Nothing is learned or stored.

How it is split:
* the scoring (`Scores`, `top`) and the eligibility rules (`user_eligible`,
  `guild_eligible`, `curation_eligible`) are pure, so they are unit tested;
* `*_candidates` run the queries and are memoized per member for a few minutes.
  They return UNFILTERED (id, score, reason) rows;
* `users_for`, `guilds_for` and `curations_for` apply the eligibility rules and the
  word filter (helpers/visibility.py) when the list is read, against live data, so a
  follow or a block shows at once even though the ranking is cached.

Rules this module keeps (CLAUDE.md):
* nothing here reads who wrote an anonymous post. The only authorship signal is
  "active in guilds you joined", and it leaves anonymous posts out;
* a reason never says more than a count of people the member already follows.
"""
import math
import time

SIDEBAR_COUNT = 3        # one of each kind in the sidebar box
PAGE_COUNT = 50          # the full page
CACHE_SECONDS = 300
RECENT_SECONDS = 30 * 86400
SPECIAL_SUBCATS = (108,)          # never suggested (as /browse)
CHILD_HIDDEN_SUBCATS = (44,)      # and these when the word filter is on
PROFILE_BOARD = "systemprofile"


class Scores:
    """Adds up weighted reasons per id and keeps the strongest one as the label."""

    def __init__(self):
        self._total = {}
        self._best = {}

    def add(self, key, weight, reason):
        self._total[key] = self._total.get(key, 0.0) + weight
        if key not in self._best or weight > self._best[key][0]:
            self._best[key] = (weight, reason)

    def rows(self):
        """[(id, score, reason)], best first; ties go to the lower id so the order is stable."""
        return sorted(((k, round(s, 4), self._best[k][1]) for k, s in self._total.items()),
                      key=lambda row: (-row[1], row[0]))


def top(rows, eligible, limit):
    """The first `limit` rows whose id passes `eligible(id)`."""
    out = []
    for row in rows:
        if eligible(row[0]):
            out.append(row)
            if len(out) >= limit:
                break
    return out


def popularity(count):
    """A weight that grows slowly with a count and is capped below one mutual
    follow (3), so popularity only breaks ties between social signals."""
    return min(math.log1p(max(count, 0)) * 0.25, 2.5)


def plural(n, one, many):
    return f"{n} {one if n == 1 else many}"


# --- eligibility (pure) --------------------------------------------------------

def user_eligible(u, viewer_id, following, blocked_ids):
    """May this account be suggested to the viewer?"""
    if u is None or u.id == viewer_id or u.id in following or u.id in blocked_ids:
        return False
    return not (u.is_private or u.is_nofollow or u.is_banned or u.is_deleted)


def guild_eligible(b, joined, blocked_board_ids, filter_on):
    if b is None or b.id in joined or b.id in blocked_board_ids:
        return False
    if b.is_banned or b.is_private or b.all_opt_out or b.name == PROFILE_BOARD:
        return False
    hidden = SPECIAL_SUBCATS + (CHILD_HIDDEN_SUBCATS if filter_on else ())
    return b.subcat_id not in hidden


def curation_eligible(c, viewer_id, followed, blocked_ids, owner_ok):
    """`owner_ok`: the owner exists, is not banned or deleted and passes the word filter."""
    if c is None or c.is_private or c.owner_id == viewer_id or c.id in followed:
        return False
    return owner_ok and c.owner_id not in blocked_ids


# --- candidates (queries; cached per member) -----------------------------------

def _cache():
    from ruqqus.__main__ import cache
    return cache


def _memo(fn):
    """memoize lazily: the cache object only exists once the app has started."""
    holder = {}

    def wrapper(*args, **kwargs):
        if "f" not in holder:
            holder["f"] = _cache().memoize(CACHE_SECONDS)(fn)
        return holder["f"](*args, **kwargs)
    wrapper.__name__ = fn.__name__
    wrapper.uncached = fn
    return wrapper


@_memo
def user_candidates(viewer_id):
    from flask import g
    from sqlalchemy import func
    from ruqqus.classes import Follow, Subscription, Submission, Board

    s = Scores()

    # accounts followed by the accounts you follow
    f1, f2 = Follow.__table__.alias("f1"), Follow.__table__.alias("f2")
    rows = g.db.query(f2.c.target_id, func.count()).select_from(f1).join(
        f2, f2.c.user_id == f1.c.target_id
    ).filter(f1.c.user_id == viewer_id).group_by(f2.c.target_id).order_by(func.count().desc()).limit(200).all()
    for uid, n in rows:
        s.add(uid, 3 * n, f"Followed by {plural(n, 'person', 'people')} you follow")

    # accounts that follow you
    for (uid,) in g.db.query(Follow.user_id).filter(Follow.target_id == viewer_id).limit(200).all():
        s.add(uid, 2, "Follows you")

    # taste: who posts in the guilds you joined (anonymous posts are left out)
    since = int(time.time()) - RECENT_SECONDS
    rows = g.db.query(Submission.author_id, func.count()).join(
        Subscription, Subscription.board_id == Submission.board_id
    ).join(Board, Board.id == Submission.board_id).filter(
        Subscription.user_id == viewer_id, Subscription.is_active == True,
        Submission.created_utc > since, Submission.is_banned == False,
        Submission.deleted_utc == 0, Submission.is_anonymous == False,
        Board.name != PROFILE_BOARD,
    ).group_by(Submission.author_id).order_by(func.count().desc()).limit(200).all()
    for uid, n in rows:
        s.add(uid, min(n, 5) * 0.8, "Active in guilds you joined")

    # popularity, for a member with little signal
    for uid, n in g.db.query(Follow.target_id, func.count()).group_by(Follow.target_id).order_by(
            func.count().desc()).limit(50).all():
        s.add(uid, popularity(n), "Popular on the site")

    # and a small site with few follows: who has been posting lately (never anonymous posts)
    for uid, n in g.db.query(Submission.author_id, func.count()).filter(
            Submission.created_utc > since, Submission.is_banned == False,
            Submission.deleted_utc == 0, Submission.is_anonymous == False,
    ).group_by(Submission.author_id).order_by(func.count().desc()).limit(50).all():
        s.add(uid, min(n, 5) * 0.2, "Active recently")

    return s.rows()[:300]


@_memo
def guild_candidates(viewer_id):
    from flask import g
    from sqlalchemy import func, select
    from ruqqus.classes import Follow, Subscription, Board, User

    s = Scores()
    followed = select(Follow.target_id).filter(Follow.user_id == viewer_id)

    # guilds the accounts you follow joined
    for bid, n in g.db.query(Subscription.board_id, func.count()).filter(
            Subscription.user_id.in_(followed), Subscription.is_active == True
    ).group_by(Subscription.board_id).order_by(func.count().desc()).limit(200).all():
        s.add(bid, 3 * n, f"{plural(n, 'person', 'people')} you follow {'is a member' if n == 1 else 'are members'}")

    # guilds in the categories your joins and votes point at
    viewer = g.db.query(User).filter(User.id == viewer_id).first()
    subcats = viewer.interest_subcats() if viewer else []
    for rank, subcat in enumerate(subcats):
        weight = 2.5 - 0.15 * rank
        for bid, in g.db.query(Board.id).filter(
                Board.subcat_id == subcat, Board.is_banned == False, Board.is_private == False
        ).order_by(Board.stored_subscriber_count.desc()).limit(25).all():
            s.add(bid, weight, "Matches your interests")

    # popularity
    for bid, n in g.db.query(Board.id, Board.stored_subscriber_count).filter(
            Board.is_banned == False, Board.is_private == False, Board.all_opt_out == False
    ).order_by(Board.stored_subscriber_count.desc()).limit(50).all():
        s.add(bid, popularity(n or 0), "Popular guild")

    return s.rows()[:300]


@_memo
def curation_candidates(viewer_id):
    from flask import g
    from sqlalchemy import func, select
    from ruqqus.classes import Follow, Subscription, User
    from ruqqus.classes.curations import Curation, CurationGuild, CurationUser, CurationFollow

    s = Scores()
    mine = [c for c, in g.db.query(Curation.id).filter(Curation.is_private == False).limit(500).all()]
    if not mine:
        return []

    joined = select(Subscription.board_id).filter(Subscription.user_id == viewer_id, Subscription.is_active == True)
    followed = select(Follow.target_id).filter(Follow.user_id == viewer_id)

    guild_overlap = dict(g.db.query(CurationGuild.curation_id, func.count()).filter(
        CurationGuild.curation_id.in_(mine), CurationGuild.board_id.in_(joined)
    ).group_by(CurationGuild.curation_id).all())
    user_overlap = dict(g.db.query(CurationUser.curation_id, func.count()).filter(
        CurationUser.curation_id.in_(mine), CurationUser.target_user_id.in_(followed)
    ).group_by(CurationUser.curation_id).all())
    followers = dict(g.db.query(CurationFollow.curation_id, func.count()).filter(
        CurationFollow.curation_id.in_(mine)).group_by(CurationFollow.curation_id).all())

    viewer = g.db.query(User).filter(User.id == viewer_id).first()
    subcats = set(str(x) for x in (viewer.interest_subcats() if viewer else []))

    for c in g.db.query(Curation).filter(Curation.id.in_(mine)).all():
        if guild_overlap.get(c.id):
            s.add(c.id, 3 * guild_overlap[c.id], "Covers guilds you joined")
        if user_overlap.get(c.id):
            s.add(c.id, 3 * user_overlap[c.id], "Includes accounts you follow")
        if subcats and subcats.intersection(str(x) for x in c.category_filter_list):
            s.add(c.id, 2, "Matches your interests")
        n = followers.get(c.id, 0)
        s.add(c.id, popularity(n), plural(n, "follower", "followers") if n else "New curation")

    return s.rows()[:300]


# --- what a member is shown (live eligibility on the cached ranking) -----------

def _blocked_user_ids(viewer_id):
    """Accounts never suggested to the viewer: blocked either way, and muted by them."""
    from flask import g
    from ruqqus.classes.userblock import UserBlock
    from ruqqus.classes.usermute import UserMute
    rows = g.db.query(UserBlock.user_id, UserBlock.target_id).filter(
        (UserBlock.user_id == viewer_id) | (UserBlock.target_id == viewer_id)).all()
    muted = {t for (t,) in g.db.query(UserMute.target_id).filter_by(user_id=viewer_id).all()}
    return {b if a == viewer_id else a for a, b in rows} | muted


def users_for(v, limit=PAGE_COUNT):
    from flask import g
    from ruqqus.classes import User
    from ruqqus.helpers.visibility import filter_users

    rows = user_candidates(v.id)
    following, blocked = v.following_ids, _blocked_user_ids(v.id)
    ids = [r[0] for r in rows]
    users = {u.id: u for u in filter_users(g.db.query(User).filter(User.id.in_(ids)), v).all()}
    picked = top(rows, lambda i: user_eligible(users.get(i), v.id, following, blocked), limit)
    return [(users[i], reason) for i, _, reason in picked]


def guilds_for(v, limit=PAGE_COUNT):
    from flask import g
    from ruqqus.classes import Board, Subscription
    from ruqqus.classes.board_relationships import BoardBlock
    from ruqqus.helpers.visibility import filter_boards, viewer_level

    rows = guild_candidates(v.id)
    ids = [r[0] for r in rows]
    boards = {b.id: b for b in filter_boards(g.db.query(Board).filter(Board.id.in_(ids)), v).all()}
    joined = {x for x, in g.db.query(Subscription.board_id).filter(
        Subscription.user_id == v.id, Subscription.is_active == True).all()}
    blocked = {x for x, in g.db.query(BoardBlock.board_id).filter(BoardBlock.user_id == v.id).all()}
    filter_on = viewer_level(v) > 0
    picked = top(rows, lambda i: guild_eligible(boards.get(i), joined, blocked, filter_on), limit)
    return [(boards[i], reason) for i, _, reason in picked]


def curations_for(v, limit=PAGE_COUNT):
    from flask import g
    from ruqqus.classes import User
    from ruqqus.classes.curations import Curation, CurationFollow
    from ruqqus.helpers.visibility import filter_users

    rows = curation_candidates(v.id)
    ids = [r[0] for r in rows]
    curations = {c.id: c for c in g.db.query(Curation).filter(Curation.id.in_(ids)).all()}
    owner_ids = {c.owner_id for c in curations.values()}
    owners = {u.id: u for u in filter_users(g.db.query(User).filter(User.id.in_(owner_ids)), v).all()}
    followed = {x for x, in g.db.query(CurationFollow.curation_id).filter(CurationFollow.user_id == v.id).all()}
    blocked = _blocked_user_ids(v.id)

    def ok(i):
        c = curations.get(i)
        owner = owners.get(c.owner_id) if c else None
        owner_ok = bool(owner) and not (owner.is_banned or owner.is_deleted)
        return curation_eligible(c, v.id, followed, blocked, owner_ok)

    picked = top(rows, ok, limit)
    return [(curations[i], reason) for i, _, reason in picked]


def sidebar_suggestions(v):
    """One of each kind for the sidebar box: [(kind, item, reason)]."""
    out = []
    for kind, fn in (("users", users_for), ("guilds", guilds_for), ("curations", curations_for)):
        found = fn(v, limit=1)
        if found:
            out.append((kind, found[0][0], found[0][1]))
    return out
