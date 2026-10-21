"""Turns a parsed search (helpers/search_query.py) into conditions on a post query.

`resolve` looks up the names in `from:` and `in:` as the viewer may see them. An account or a
guild the viewer may not see gets exactly the notice of one that does not exist, so a search
never confirms that a private or Circle guild, or a deleted, private, suspended or blocked
account, exists.

`apply` adds the query's conditions. It needs SubmissionAux joined (routes/search.py's
`searchlisting` does). Who may see which post is NOT decided here: `searchlisting` applies the
word filter, blocks, mutes, private guilds and Circles after it, for every search.

Anonymity: `from:@x` leaves out x's anonymous posts (unless the viewer is x or an admin), and
`-from:@x` leaves out only x's NON-anonymous posts. Otherwise comparing `cats` with
`cats -from:@x` would show which anonymous posts x wrote.
"""
import re
from dataclasses import replace

from sqlalchemy import and_, column, exists, func, not_, or_, select, table

from ruqqus.helpers import anonymity
from ruqqus.helpers import search_query as sq
from ruqqus.helpers.curation_feed import _comment_count, kind_conditions, member_condition
from ruqqus.helpers.feed_algorithm import like_pattern
from ruqqus.helpers.visibility import board_hidden, user_hidden

# as the account and guild searches: admins from level 3 find private, deleted and banned ones
ADMIN_FINDS_ALL = 3


def _admin(v):
    return v is not None and (v.admin_level or 0) >= ADMIN_FINDS_ALL


def find_account(name, v):
    """The account a `from:` names, or None when there is none the viewer may see."""
    from ruqqus.helpers.get import get_user

    user = get_user(name, v=v, graceful=True)
    if user is None:
        return None
    if (v is not None and user.id == v.id) or _admin(v):
        return user
    if user.is_deleted or user.is_private or user.is_suspended or user_hidden(user, v):
        return None
    if user.is_blocking or user.is_blocked:     # set by get_user for a signed-in viewer
        return None
    return user


def find_guild(name, v):
    """The guild an `in:` names, or None when there is none the viewer may see."""
    from ruqqus.classes.boards import PROFILE_BOARD_NAME
    from ruqqus.helpers.get import get_guild

    board = get_guild(name, v=v, graceful=True)
    if board is None or board.name.lower() == PROFILE_BOARD_NAME:
        return None
    if board.is_banned and not _admin(v):
        return None
    if board_hidden(board, v) or not board.can_view(v):      # private and Circle guilds: members only
        return None
    return board


def find_topic(name):
    """The topic an `in:#name` names (helpers/topics.py), or None."""
    from ruqqus.helpers import topics
    return topics.find(name)


def find_curation(slug, v):
    """The public curation an `in:&slug` names, or None. A private one (its owner's included), or one whose owner is
    gone or banned, reads as one that does not exist."""
    from ruqqus.helpers.get import get_curation

    curation = get_curation(slug, graceful=True)
    if curation is None or curation.is_private:
        return None
    owner = curation.owner
    if owner is None or owner.is_deleted or owner.is_banned:
        return None
    return curation


def resolve(query, v, b=None):
    """(the query with account and guild ids filled in, notes). Names nobody may see become
    problems on the query; `in:` inside one guild's search is dropped with a note."""
    parts, problems, notes = [], list(query.problems), []
    for part in query.parts:
        if part.kind == "from":
            user = find_account(part.value, v)
            if user is None:
                problems.append(sq.NO_ACCOUNT.format(part.value))
            else:
                part = replace(part, value=user.username, ref=user.id)
        elif part.kind == "in":
            if b is not None:
                notes.append(sq.IN_INSIDE_GUILD)
                continue
            if part.value[:1] == "#":
                topic = find_topic(part.value[1:])
                if topic is None:
                    problems.append(sq.NO_TOPIC.format(part.value[1:]))
                else:
                    part = replace(part, value="#" + topic.slug, refs=tuple(topic.subcat_ids))
            elif part.value[:1] == "&":
                curation = find_curation(part.value[1:], v)
                if curation is None:
                    problems.append(sq.NO_CURATION.format(part.value[1:]))
                else:
                    part = replace(part, value="&" + curation.slug, ref=curation.id)
            else:
                board = find_guild(part.value, v)
                if board is None:
                    problems.append(sq.NO_GUILD.format(part.value))
                else:
                    part = replace(part, value=board.name, ref=board.id)
        parts.append(part)
    query = replace(query, parts=tuple(parts), problems=tuple(dict.fromkeys(problems)))
    return query, list(dict.fromkeys(notes))


def _host_pattern(host):
    return r"^https?://([^/]*\.)?" + re.escape(host) + r"(/|:|\?|#|$)"


def text_condition(part):
    """A word or phrase in the title or the text. The positive form leaves the columns bare so
    the trigram indexes can answer it; the negative one needs coalesce (NOT NULL is NULL).
    The feed filters' "any of these words" uses it too (helpers/feed_filter_sql.py)."""
    from ruqqus.classes import SubmissionAux

    pattern = like_pattern(part.value)
    if part.negated:
        return not_(or_(func.coalesce(SubmissionAux.title, "").ilike(pattern, escape="\\"),
                        func.coalesce(SubmissionAux.body, "").ilike(pattern, escape="\\")))
    return or_(SubmissionAux.title.ilike(pattern, escape="\\"), SubmissionAux.body.ilike(pattern, escape="\\"))


def _has(kind):
    from ruqqus.classes import Poll, Submission, SubmissionAux

    if kind == "poll":
        # a poll belongs to the primary post, so a forwarded copy has the poll of its original
        primary = func.coalesce(func.nullif(Submission.repost_id, 0), Submission.id)
        return exists(select(Poll.id).where(Poll.post_id == primary))
    kinds = kind_conditions()
    if kind == "link":
        return and_(func.coalesce(SubmissionAux.url, "") != "", not_(kinds["image"]))
    return kinds[kind]


def _condition(part, v):
    """One part as a condition that is already negated where the part is."""
    from ruqqus.classes import Submission, SubmissionAux

    k, value, negated = part.kind, part.value, part.negated
    if k in sq.TEXT:
        return text_condition(part)
    if k == "from":
        if negated:
            return not_(and_(Submission.author_id == part.ref, not_(Submission.is_anonymous)))
        return and_(Submission.author_id == part.ref, anonymity.hide_anonymous(Submission, v))
    if k == "in":
        if value[:1] == "#":
            # a topic: any guild in one of its subcategories (an empty list matches nothing)
            from ruqqus.classes import Board
            in_topic = Submission.board_id.in_(select(Board.id).where(Board.subcat_id.in_(part.refs or (-1,))))
            return not_(in_topic) if negated else in_topic
        if value[:1] == "&":
            # a public curation: its guilds, and its accounts' posts that are not anonymous (the feed's own rule)
            in_curation = member_condition(part.ref)
            return not_(in_curation) if negated else in_curation
        return Submission.board_id != part.ref if negated else Submission.board_id == part.ref
    if k == "lang":
        if negated:
            return func.coalesce(Submission.language_code, "") != value
        return Submission.language_code == value
    if k == "domain":
        if negated:
            return not_(func.coalesce(SubmissionAux.url, "").op("~*")(_host_pattern(value)))
        return SubmissionAux.url.op("~*")(_host_pattern(value))
    if k == "url":
        pattern = like_pattern(value)
        if negated:
            return not_(func.coalesce(SubmissionAux.url, "").ilike(pattern, escape="\\"))
        return SubmissionAux.url.ilike(pattern, escape="\\")
    if k == "flair":
        # a guild's copy carries the flair (helpers/guild_flair.py): its flair is one with this name, ignoring case
        flairs = table("guild_flairs", column("id"), column("name"))
        named = select(flairs.c.id).where(func.lower(flairs.c.name) == value.lower())
        if negated:
            return or_(Submission.guild_flair_id.is_(None), not_(Submission.guild_flair_id.in_(named)))
        return Submission.guild_flair_id.in_(named)
    if k == "min_votes":
        return func.coalesce(Submission.upvotes, 0) - func.coalesce(Submission.downvotes, 0) >= int(value)
    if k == "min_comments":
        return _comment_count() >= int(value)
    if k in sq.DATE_KEYS:
        return None                     # all the dates together, in apply()
    if k == "has":
        condition = _has(value)
    elif k == "is":
        condition = {"forward": func.coalesce(Submission.repost_id, 0) != 0,
                     "ai": func.coalesce(Submission.made_with_ai, False) == True,
                     "partnership": func.coalesce(Submission.paid_partnership, False) == True}[value]
    else:
        raise ValueError(k)
    return not_(condition) if negated else condition


def apply(posts, query, v):
    """The query's conditions on a query over Submission with SubmissionAux joined. Only for a
    query that came out of `resolve` with no problems (every `from:` and `in:` has its id)."""
    from ruqqus.classes import Submission

    for part in query.parts:
        condition = _condition(part, v)
        if condition is not None:
            posts = posts.filter(condition)
    low, high = sq.time_bounds(query)
    if low is not None:
        posts = posts.filter(Submission.created_utc >= low)
    if high is not None:
        posts = posts.filter(Submission.created_utc < high)
    return posts
