"""The feed filters (helpers/feed_filters.py) as conditions on a feed's post query.

`prepare` runs once per request: the Advanced filter's search text is parsed and its names looked up as the viewer
may see them (helpers/search_filters.resolve). A name nobody may see, or one that does not exist, is dropped with
one notice that is the same either way, so a feed never confirms that a private guild or a blocked account exists.
What comes back is a frozen `FeedFilter`, made of tuples, which the memoized id-lists take as an argument: it is
part of their cache key.

`apply` only ever narrows. The feed decides who may see what before it is called; `from:` keeps search's rule that
an account's anonymous posts are never found through it.
"""
import re
import time
from dataclasses import dataclass

from sqlalchemy import and_, column, exists, false, func, not_, or_, select, table, true

from ruqqus.helpers import feed_filters as ff
from ruqqus.helpers import search_filters
from ruqqus.helpers import search_query as sq
from ruqqus.helpers.feed_algorithm import like_pattern

PROFILE_BOARD_NAME = "systemprofile"          # classes/boards.py: the reserved guild every profile post is on

_boards = table("boards", column("id"), column("name"), column("subcat_id"), column("is_private"), column("is_banned"))
_forwards = table("forwardrels", column("primary_submission_id"), column("forward_submission_id"), column("board_id"))
_coauthors = table("post_coauthors", column("post_id"), column("status"))
_collab = table("collab_docs", column("post_id"))
_spaces = table("spaces", column("post_id"), column("status"), column("started_utc"), column("record"),
                column("replay_asset_id"), column("kind"), column("replay_link"))

LEFT_OUT = "{} It is left out of the filter."
DATES_CLASH = "The dates leave no days."

# the conditions that read SubmissionAux (the feed must join it, once)
AUX_TYPES = ("text", "link", "image", "video")
AUX_KINDS = sq.TEXT + ("domain", "url", "has")


@dataclass(frozen=True)
class FeedFilter:
    types: tuple = ()
    query: sq.Query = sq.Query()
    any_words: tuple = ()
    mentions: tuple = ()


def prepare(spec, types, v):
    """(FeedFilter or None, notices) for this viewer. None when nothing is set."""
    types = tuple(ff.parse_types(types or ()))
    spec = ff.clean(spec or {})
    query = sq.parse(ff.query_text(spec))
    notices = []
    no_days = sq.NO_DAYS in query.problems
    if no_days:
        notices.append(LEFT_OUT.format(DATES_CLASH))
    parts = []
    for part in query.parts:
        if no_days and part.kind in sq.DATE_KEYS:
            continue
        if part.kind in ("from", "in"):
            # one name at a time: a name that does not resolve is left out, and its notice says so
            one, _ = search_filters.resolve(sq.Query(parts=(part,)), v)
            if one.problems:
                notices += [LEFT_OUT.format(problem) for problem in one.problems]
                continue
            part = one.parts[0]
        parts.append(part)
    feed_filter = FeedFilter(types=types, query=sq.Query(parts=tuple(parts)), any_words=tuple(spec["any_words"]),
                             mentions=tuple(n.lower() for n in spec["mentioning"]))
    if feed_filter == FeedFilter():
        return None, list(dict.fromkeys(notices))
    return feed_filter, list(dict.fromkeys(notices))


def needs_aux(feed_filter):
    """True when a condition reads SubmissionAux, which the feed then joins (it must be joined only once)."""
    if feed_filter is None:
        return False
    return (any(t in AUX_TYPES for t in feed_filter.types) or bool(feed_filter.any_words) or bool(feed_filter.mentions)
            or any(p.kind in AUX_KINDS for p in feed_filter.query.parts))


def _primary():
    from ruqqus.classes import Submission
    return func.coalesce(func.nullif(Submission.repost_id, 0), Submission.id)


def _space(*where):
    return exists(select(_spaces.c.post_id).where(_spaces.c.post_id == _primary(), *where))


def _live_copy(*where):
    """The post has a live copy in a guild (forwardrels), narrowed by `where` on the forward's guild."""
    from ruqqus.classes import Submission

    copy = Submission.__table__.alias("guild_copy")
    return exists(select(_forwards.c.forward_submission_id).where(
        _forwards.c.primary_submission_id == Submission.id,
        _forwards.c.forward_submission_id == copy.c.id,
        copy.c.deleted_utc == 0, copy.c.is_banned == false(), *where))


def _open_guilds(*where):
    """Guilds that are neither private nor banned: a copy in a private guild never makes its public original match,
    or the filter would hint that the guild exists (the share counts follow the same rule)."""
    return select(_boards.c.id).where(_boards.c.is_private == false(), _boards.c.is_banned == false(), *where)


def _in_guilds(board_ids, copies_in):
    """A post in these guilds, or one whose live copy is in one of `copies_in`. A feed shows one card per post (a copy
    leaves when its original is in the feed: helpers/feed_family.py), so "in +pets" must find the original too."""
    from ruqqus.classes import Submission

    return or_(Submission.board_id.in_(board_ids), _live_copy(_forwards.c.board_id.in_(copies_in)))


def _guild_condition(part):
    """`in:+guild` and `in:#topic` for a feed (a curation, `in:&news`, is search's own condition)."""
    if part.value[:1] == "#":
        in_topic = select(_boards.c.id).where(_boards.c.subcat_id.in_(part.refs or (-1,)))
        condition = _in_guilds(in_topic, _open_guilds(_boards.c.subcat_id.in_(part.refs or (-1,))))
    else:
        # the viewer named the guild and may see it (search_filters.find_guild), so any copy there counts
        condition = _in_guilds((part.ref,), (part.ref,))
    return not_(condition) if part.negated else condition


def type_condition(key, now=None):
    """One post type, as a condition on Submission (with SubmissionAux joined for the content types)."""
    from ruqqus.classes import Submission
    from ruqqus.helpers.curation_feed import kind_conditions
    from ruqqus.helpers.spaces import MAX_SECONDS

    if key in ("text", "link", "image", "video", "audio"):
        return kind_conditions()[key]
    if key == "poll":
        return search_filters._has("poll")
    if key == "guilds":
        # a guild's copy, or a post with a live copy in an open guild (forwarded by its author or anyone)
        on_profile = select(_boards.c.id).where(_boards.c.name == PROFILE_BOARD_NAME).scalar_subquery()
        return or_(Submission.board_id != on_profile, _live_copy(_forwards.c.board_id.in_(_open_guilds())))
    if key == "circles":
        return Submission.audience.in_((3, 4))
    if key == "collab":
        return exists(select(_collab.c.post_id).where(_collab.c.post_id == _primary()))
    if key == "coauthored":
        # a collaborative post's authors are its co-authors too (routes/collab.py): those are their own type
        coauthored = exists(select(_coauthors.c.post_id).where(
            _coauthors.c.post_id == _primary(), _coauthors.c.status == "accepted"))
        return and_(coauthored, not_(type_condition("collab")))
    if key == "anonymous":
        return Submission.is_anonymous == true()
    if key == "space_live":
        now = int(time.time()) if now is None else now
        return _space(_spaces.c.status == "live", _spaces.c.started_utc >= now - MAX_SECONDS)
    if key == "space_upcoming":
        return _space(_spaces.c.status == "scheduled")
    if key == "space_recording":
        # the Spaces area's own rule (space_store.RECORDING): a call with its replay saved; a recorded board or live
        # thread is its own record
        return _space(_spaces.c.status == "ended", _spaces.c.record == true(),
                      or_(_spaces.c.kind != "call", _spaces.c.replay_asset_id.isnot(None), _spaces.c.replay_link.isnot(None)))
    raise ValueError(key)


def _mentions(name):
    """`@name` in the title or the text, as a whole name (`@ann` is not `@anna`). The ILIKE narrows first, so the
    trigram indexes answer it; the regular expression then keeps whole names only."""
    from ruqqus.classes import SubmissionAux

    pattern = like_pattern("@" + name)
    plain = or_(SubmissionAux.title.ilike(pattern, escape="\\"), SubmissionAux.body.ilike(pattern, escape="\\"))
    whole = r"(^|[^A-Za-z0-9_])@" + re.escape(name) + r"([^A-Za-z0-9_-]|$)"
    return and_(plain, or_(func.coalesce(SubmissionAux.title, "").op("~*")(whole),
                           func.coalesce(SubmissionAux.body, "").op("~*")(whole)))


def apply(posts, feed_filter, v, now=None):
    """The filter's conditions on a feed's query over Submission (SubmissionAux joined when `needs_aux`)."""
    if feed_filter is None:
        return posts
    if feed_filter.types:
        posts = posts.filter(or_(*[type_condition(t, now) for t in feed_filter.types]))
    # a guild or a topic also finds the original of a copy there; everything else is search's own condition
    in_guild = [p for p in feed_filter.query.parts if p.kind == "in" and p.value[:1] != "&"]
    rest = sq.Query(parts=tuple(p for p in feed_filter.query.parts if p not in in_guild))
    for part in in_guild:
        posts = posts.filter(_guild_condition(part))
    posts = search_filters.apply(posts, rest, v)
    if feed_filter.any_words:
        posts = posts.filter(or_(*[search_filters.text_condition(sq.Part("word", w)) for w in feed_filter.any_words]))
    for name in feed_filter.mentions:
        posts = posts.filter(_mentions(name))
    return posts
