"""Turns a curation's algorithm (helpers/feed_algorithm.py) into the query for its feed.

Only a spec that came out of `feed_algorithm.clean` is ever passed in, so every value
here is a fixed name, a bounded number or a plain word. Words go into the query as LIKE
text (`like_pattern`), site names as host names made of [a-z0-9.-]: nothing a member
typed is ever run as a pattern or as SQL.

`routes/curations.py` builds who may see what (the word filter, private guilds, blocks);
this module only adds which posts the curation wants and how it orders them.
"""
import re

from sqlalchemy import and_, case, exists, func, not_, or_, select, text
from sqlalchemy.exc import DBAPIError

from ruqqus.helpers import feed_algorithm as fa

HIDDEN_SUBCATS = (44, 108)        # as frontlist(): not on All for a viewer with the word filter on
PROFILE_BOARD = "systemprofile"
TIME_LIMIT_MS = 3000
TOO_SLOW = "This curation's rules took too long to run. Its owner can make them narrower."


class FeedTooSlow(Exception):
    """The curation's query hit the time limit."""


def member_condition(curation_id):
    """A post from one of the curation's guilds or accounts. An anonymous post never
    matches through its author (a curation can hold one account, which would name them);
    it still shows through a member guild."""
    from ruqqus.classes import CurationGuild, CurationUser, Submission

    board_ids = select(CurationGuild.board_id).filter_by(curation_id=curation_id)
    user_ids = select(CurationUser.target_user_id).filter_by(curation_id=curation_id)
    return or_(
        Submission.board_id.in_(board_ids),
        and_(Submission.author_id.in_(user_ids), not_(Submission.is_anonymous))
    )


def site_pool(posts, v, level):
    """Every public post, as All shows it (frontlist): not the guilds that opted out of All
    unless the viewer joined them, and not the categories the word filter keeps off All."""
    from ruqqus.classes import Board, Submission, Subscription

    posts = posts.join(Board, Board.id == Submission.board_id).filter(
        Board.is_banned == False,
        Submission.board_id != 1,
    )
    if v:
        joined = select(Subscription.board_id).filter_by(user_id=v.id, is_active=True)
        posts = posts.filter(or_(Board.all_opt_out == False, Submission.board_id.in_(joined)))
    else:
        posts = posts.filter(Board.all_opt_out == False)
    if level > 0:
        posts = posts.filter(or_(Board.subcat_id.is_(None), Board.subcat_id.notin_(HIDDEN_SUBCATS)))
    return posts


# --- what a post is --------------------------------------------------------------------

def _comment_count():
    from ruqqus.classes import Comment, Submission

    return select(func.count(Comment.id)).where(
        Comment.parent_submission == Submission.id, Comment.is_banned == False, Comment.deleted_utc == 0
    ).correlate(Submission).scalar_subquery()


def _has_asset(kind):
    from ruqqus.classes import MediaAsset, Submission

    return exists(select(MediaAsset.id).where(
        MediaAsset.submission_id == Submission.id, MediaAsset.kind == kind, MediaAsset.status == "ready"))


def kind_conditions():
    """{kind: condition}. Needs SubmissionAux joined."""
    from ruqqus.classes import Submission, SubmissionAux

    has_link = func.coalesce(SubmissionAux.url, "") != ""
    image = or_(func.coalesce(Submission._own_is_image, False), _has_asset("image"))
    video = func.coalesce(SubmissionAux.embed_url, "") != ""
    return {
        "image": image,
        "video": video,
        "audio": _has_asset("audio"),
        "link": and_(has_link, not_(image), not_(video)),
        "text": and_(not_(has_link), not_(image)),
    }


def _mentions(word):
    from ruqqus.classes import SubmissionAux

    pattern = fa.like_pattern(word)
    return or_(func.coalesce(SubmissionAux.title, "").ilike(pattern, escape="\\"),
               func.coalesce(SubmissionAux.body, "").ilike(pattern, escape="\\"))


def _links_to(host):
    from ruqqus.classes import SubmissionAux

    return func.coalesce(SubmissionAux.url, "").op("~*")(r"^https?://([^/]*\.)?" + re.escape(host) + r"(/|:|\?|#|$)")


def apply(posts, spec, now):
    """The spec's conditions, on a query over Submission that has SubmissionAux joined."""
    from ruqqus.classes import Submission

    if spec["any"]:
        posts = posts.filter(or_(*[_mentions(w) for w in spec["any"]]))
    for word in spec["all"]:
        posts = posts.filter(_mentions(word))
    if spec["none"]:
        posts = posts.filter(not_(or_(*[_mentions(w) for w in spec["none"]])))
    if spec["kinds"]:
        kinds = kind_conditions()
        posts = posts.filter(or_(*[kinds[k] for k in spec["kinds"]]))
    if spec["only_sites"]:
        posts = posts.filter(or_(*[_links_to(h) for h in spec["only_sites"]]))
    if spec["never_sites"]:
        posts = posts.filter(not_(or_(*[_links_to(h) for h in spec["never_sites"]])))
    if spec["min_votes"]:
        posts = posts.filter(func.coalesce(Submission.upvotes, 0) - func.coalesce(Submission.downvotes, 0) >= spec["min_votes"])
    if spec["min_comments"]:
        posts = posts.filter(_comment_count() >= spec["min_comments"])
    if spec["age"]:
        posts = posts.filter(Submission.created_utc >= now - fa.AGES[spec["age"]])
    if "bots" in spec["hide"]:
        posts = posts.filter(func.coalesce(Submission.is_bot, False) == False)
    if "ai" in spec["hide"]:
        posts = posts.filter(func.coalesce(Submission.made_with_ai, False) == False)
    if "paid" in spec["hide"]:
        posts = posts.filter(func.coalesce(Submission.paid_partnership, False) == False)
    if "forwards" in spec["hide"]:
        posts = posts.filter(func.coalesce(Submission.repost_id, 0) == 0)
    return posts


# --- its own order ---------------------------------------------------------------------

def mix_score(spec, curation_id, now):
    """feed_algorithm.mix_score as SQL. Needs SubmissionAux joined."""
    from ruqqus.classes import Submission, SubmissionAux

    mix = spec["mix"]
    votes = func.greatest(func.coalesce(Submission.upvotes, 0) - func.coalesce(Submission.downvotes, 0), 0)
    score = 1.0 + mix["votes"] * func.ln(1 + votes) + mix["comments"] * func.ln(1 + _comment_count())
    if mix["members"] and spec["source"] == fa.SITE:
        score = score * (1.0 + 0.2 * mix["members"] * case((member_condition(curation_id), 1), else_=0))
    if mix["media"]:
        media = or_(func.coalesce(Submission._own_is_image, False), func.coalesce(SubmissionAux.embed_url, "") != "",
                    _has_asset("image"), _has_asset("audio"))
        score = score * (1.0 + 0.2 * mix["media"] * case((media, 1), else_=0))
    age_hours = func.greatest(now - Submission.created_utc, 0) / 3600.0
    return score * func.power(0.5, age_hours / float(mix["fade"]))


def mix_ids(db, posts, spec, curation_id, now):
    """Every id the mix ranks, best first: the newest `MIX_POOL` posts of `posts` (a query
    over Submission.id with SubmissionAux joined), scored, then spread for variety."""
    from ruqqus.classes import Board, Submission, SubmissionAux

    pool = posts.order_by(None).order_by(Submission.created_utc.desc()).limit(fa.MIX_POOL).subquery()
    score = mix_score(spec, curation_id, now)
    rows = db.query(
        Submission.id, Submission.author_id, Submission.is_anonymous, Submission.board_id
    ).join(
        SubmissionAux, SubmissionAux.id == Submission.id
    ).filter(
        Submission.id.in_(select(pool.c.id))
    ).order_by(score.desc(), Submission.id.desc()).all()

    limit = spec["mix"]["variety"]
    if not limit:
        return [r.id for r in rows]
    profile = db.query(Board.id).filter(Board.name == PROFILE_BOARD).scalar()
    # an anonymous post is nobody's for the limit, and a post only on a profile is in no guild
    return fa.spread([(r.id, None if r.is_anonymous else r.author_id, None if r.board_id == profile else r.board_id)
                      for r in rows], limit)


# --- a time limit ----------------------------------------------------------------------

def _hit_the_limit(error):
    """Postgres cancelled the statement for running too long (RetryingQuery re-raises
    database errors as DatabaseOverload, so the real one is looked for behind it)."""
    seen = set()
    while error is not None and id(error) not in seen:
        seen.add(id(error))
        if "statement timeout" in str(error) or "statement timeout" in str(getattr(error, "orig", "")):
            return True
        error = error.__cause__ or error.__context__
    return False


def limited(db, fn, ms=TIME_LIMIT_MS):
    """Run `fn()` (a query built from a member's rules) with a statement time limit, inside
    a savepoint so that hitting the limit only undoes this, not the request. Any other
    database error is raised as it is."""
    from ruqqus.classes.custom_errors import DatabaseOverload

    nested = db.begin_nested()
    try:
        db.execute(text(f"SET LOCAL statement_timeout = {int(ms)}"))
        result = fn()
        db.execute(text("SET LOCAL statement_timeout TO DEFAULT"))
        nested.commit()
        return result
    except (DBAPIError, DatabaseOverload) as error:
        nested.rollback()
        if _hit_the_limit(error):
            raise FeedTooSlow(TOO_SLOW) from error
        raise
