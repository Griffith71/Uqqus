"""Comment search: which operators apply to a comment, and the conditions they become.

A comment search reads the same text as a post search (words, "phrases", -words, operators; helpers/search_query.py parses
it, helpers/search_filters.py `resolve` looks the names up as the viewer may see them) but searches `comments_aux.body`,
the column `comment_body_trgm_idx` covers (never `body_html`).

Operators that describe a comment: `from:`, `in:` (where the comment sits: the guild, topic or curation its post is in),
the dates, `min_votes:`, and `is:ai` / `is:partnership`. Operators about a POST's own content (`has:`, `lang:`,
`domain:`, `url:`, `flair:`, `is:forward`, `min_comments:`) are dropped with a notice, so a copied search never quietly
means something else.

Who may see which comment is NOT decided here: `routes/search.py` `comment_searchlisting` applies the word filter, mutes,
blocks, private guilds, Circles and removed items, as the profile's and the site's comment lists do. Anonymity is the post
search's rule: `from:@x` leaves out x's anonymous comments (unless the viewer is x or an admin), `-from:@x` leaves out
only x's non-anonymous ones.
"""
from sqlalchemy import and_, func, not_, select

from ruqqus.helpers import anonymity
from ruqqus.helpers import search_query as sq
from ruqqus.helpers.curation_feed import member_condition
from ruqqus.helpers.feed_algorithm import like_pattern

MIN_WORD = 3
NOT_USED = "{} is not used when searching comments."
TOO_BROAD = f"Add a word of at least {MIN_WORD} letters, or from: or in:, to search comments."

KEPT = ("from", "in", "since", "until", "after", "before", "on", "min_votes", "is")
IS_KEPT = ("ai", "partnership")


def prepare(query):
    """(the query with the operators comments don't use taken out, notices naming them)."""
    from dataclasses import replace

    parts, notes = [], []
    for part in query.parts:
        if part.kind in sq.TEXT or part.kind in KEPT:
            if part.kind == "is" and part.value not in IS_KEPT:
                notes.append(NOT_USED.format(f"is:{part.value}"))
                continue
            parts.append(part)
        else:
            notes.append(NOT_USED.format(part.kind + ":"))
    return replace(query, parts=tuple(parts)), list(dict.fromkeys(notes))


def problem(query):
    """A sentence when the search would read every comment, else None. A word of at least MIN_WORD letters, a phrase, or
    a positive from: / in: narrows it; dates and votes alone do not."""
    for part in query.parts:
        if part.negated:
            continue
        if part.kind in sq.TEXT and len(part.value) >= MIN_WORD:
            return None
        if part.kind in ("from", "in"):
            return None
    return TOO_BROAD


def text_condition(part):
    from ruqqus.classes import CommentAux

    pattern = like_pattern(part.value)
    if part.negated:
        return not_(func.coalesce(CommentAux.body, "").ilike(pattern, escape="\\"))
    return CommentAux.body.ilike(pattern, escape="\\")


def _condition(part, v):
    from ruqqus.classes import Board, Comment, Submission

    k, value, negated = part.kind, part.value, part.negated
    if k in sq.TEXT:
        return text_condition(part)
    if k == "from":
        if negated:
            return not_(and_(Comment.author_id == part.ref, not_(Comment.is_anonymous)))
        return and_(Comment.author_id == part.ref, anonymity.hide_anonymous(Comment, v))
    if k == "in":
        if value[:1] == "#":
            in_topic = Submission.board_id.in_(select(Board.id).where(Board.subcat_id.in_(part.refs or (-1,))))
            return not_(in_topic) if negated else in_topic
        if value[:1] == "&":
            in_curation = member_condition(part.ref)
            return not_(in_curation) if negated else in_curation
        return Submission.board_id != part.ref if negated else Submission.board_id == part.ref
    if k == "min_votes":
        return func.coalesce(Comment.upvotes, 0) - func.coalesce(Comment.downvotes, 0) >= int(value)
    if k in sq.DATE_KEYS:
        return None                       # all the dates together, in apply()
    if k == "is":
        column = {"ai": Comment.made_with_ai, "partnership": Comment.paid_partnership}[value]
        condition = column.is_(True)
        return not_(condition) if negated else condition
    raise ValueError(k)


def apply(comments, query, v):
    """The query's conditions on a query over Comment joined to CommentAux and the comment's post (Submission, and its
    Board). Only for a query that came out of `search_filters.resolve` and `prepare` with no problems."""
    from ruqqus.classes import Comment

    for part in query.parts:
        condition = _condition(part, v)
        if condition is not None:
            comments = comments.filter(condition)
    low, high = sq.time_bounds(query)
    if low is not None:
        comments = comments.filter(Comment.created_utc >= low)
    if high is not None:
        comments = comments.filter(Comment.created_utc < high)
    return comments
