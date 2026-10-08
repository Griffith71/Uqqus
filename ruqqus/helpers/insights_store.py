"""Post insights: counting a view, and the report (the rules are helpers/insights.py).

Everything here is a count. Nothing returned or stored names a viewer, a voter or someone who voted in a
poll. The insights of a post add up the post and its guild forwards (each guild copy has its own votes and
comments, but it is one post to its author), and are read only by the original author."""
import time

from flask import g, request
from sqlalchemy import Integer, cast, func, text

from ruqqus.classes import (Board, Comment, ForwardRelationship, RepostRelationship, SaveRelationship, Submission,
                            Vote)
from ruqqus.classes.history import ViewHistory
from ruqqus.classes.post_view import PostViewDay
from ruqqus.helpers import insights as rules
from ruqqus.helpers import poll_store
from ruqqus.__main__ import app, r

_UPSERT = text(
    "INSERT INTO post_view_days (post_id, day, views) VALUES (:post_id, :day, 1) "
    "ON CONFLICT (post_id, day) DO UPDATE SET views = post_view_days.views + 1"
)


def count_view(viewer, post):
    """Count one open of `post`'s page, when it counts (see helpers/insights.py). Best effort: whatever goes
    wrong here, the page still opens. The write is inside a savepoint, so a failure cannot spoil the page's
    own transaction."""
    try:
        if r is None or post.is_banned or post.deleted_utc:
            return
        if viewer is not None and viewer.id == post.author_id:
            return
        agent = request.headers.get("User-Agent", "")
        if rules.is_robot(agent) or rules.is_prefetch(request.headers):
            return

        key = rules.viewer_key(viewer.id if viewer else None, request.remote_addr, agent, app.config.get("SECRET_KEY") or "")
        if not r.set(f"pv:{post.id}:{key}", "1", nx=True, ex=rules.DEBOUNCE_SECONDS):
            return                                  # the same viewer, a moment ago

        with g.db.begin_nested():
            g.db.execute(_UPSERT, {"post_id": post.id, "day": rules.day_of(time.time())})
    except Exception:
        pass


def _day_counts(db, column, filters):
    """{day number: rows} for rows matching `filters`, by the day of `column`."""
    day = cast(func.floor(column / 86400.0), Integer)
    return {int(d): int(n) for d, n in db.query(day, func.count()).filter(*filters).group_by(day).all()}


def _copies(db, primary_ids):
    """[(copy id, primary id, guild name)] of the live guild forwards of the primary posts."""
    if not primary_ids:
        return []
    return db.query(Submission.id, Submission.repost_id, Board.name).join(Board, Board.id == Submission.board_id).filter(
        Submission.repost_id.in_(primary_ids), Submission.is_banned == False, Submission.deleted_utc == 0  # noqa: E712
    ).all()


def _per_post(db, ids):
    """Counts per post id over all time: views, upvotes, downvotes, comments."""
    views = dict(db.query(PostViewDay.post_id, func.sum(PostViewDay.views)).filter(PostViewDay.post_id.in_(ids)).group_by(PostViewDay.post_id).all())
    ups, downs = {}, {}
    for post_id, vote_type, count in db.query(Vote.submission_id, Vote.vote_type, func.count()).filter(Vote.submission_id.in_(ids)).group_by(Vote.submission_id, Vote.vote_type).all():
        (ups if vote_type == 1 else downs if vote_type == -1 else {})[post_id] = count
    comments = dict(db.query(Comment.parent_submission, func.count()).filter(
        Comment.parent_submission.in_(ids), Comment.is_banned == False, Comment.deleted_utc == 0  # noqa: E712
    ).group_by(Comment.parent_submission).all())
    return views, ups, downs, comments


def overview(db, author, now):
    """The author's own posts of the last 28 days with what each got, newest first. A post's numbers add up
    the post and its guild forwards."""
    since = int(now) - 28 * rules.SECONDS_PER_DAY
    posts = db.query(Submission).filter(
        Submission.author_id == author.id, Submission.is_banned == False, Submission.deleted_utc == 0,  # noqa: E712
        (Submission.repost_id == None) | (Submission.repost_id == 0), Submission.created_utc >= since  # noqa: E711
    ).order_by(Submission.created_utc.desc()).limit(100).all()
    if not posts:
        return []

    owner = {post.id: post.id for post in posts}
    for copy_id, primary_id, _ in _copies(db, list(owner)):
        owner[copy_id] = primary_id
    views, ups, downs, comments = _per_post(db, list(owner))

    totals = {post.id: {"views": 0, "ups": 0, "downs": 0, "comments": 0} for post in posts}
    for post_id, primary_id in owner.items():
        t = totals[primary_id]
        t["views"] += int(views.get(post_id, 0))
        t["ups"] += ups.get(post_id, 0)
        t["downs"] += downs.get(post_id, 0)
        t["comments"] += comments.get(post_id, 0)

    return [{"post": post, **totals[post.id], "net": rules.net(totals[post.id]["ups"], totals[post.id]["downs"])} for post in posts]


def _chart(counts, days, today):
    rows = rules.series(counts, days, today)
    heights = rules.bar_heights([n for _, n in rows])
    return [{"day": day, "label": rules.label(day), "value": n, "height": h} for (day, n), h in zip(rows, heights)]


def report(db, primary, viewer, days, now):
    """The insights of one primary post over `days` days: totals (this window and all time), two daily charts,
    a row per place it is (your profile and each guild), and its poll if it has one. Counts only."""
    now = int(now)
    today = rules.day_of(now)
    start_day = today - days + 1
    start_ts = rules.day_start(start_day)

    copies = _copies(db, [primary.id])
    ids = [primary.id] + [copy_id for copy_id, _, _ in copies]
    views_all, ups_all, downs_all, comments_all = _per_post(db, ids)

    view_days = {}
    for post_id, day, count in db.query(PostViewDay.post_id, PostViewDay.day, PostViewDay.views).filter(PostViewDay.post_id.in_(ids), PostViewDay.day >= start_day).all():
        view_days[day] = view_days.get(day, 0) + count

    up_days = _day_counts(db, Vote.created_utc, [Vote.submission_id.in_(ids), Vote.vote_type == 1, Vote.created_utc >= start_ts])
    comment_days = _day_counts(db, Comment.created_utc, [Comment.parent_submission.in_(ids), Comment.is_banned == False,  # noqa: E712
                                                         Comment.deleted_utc == 0, Comment.created_utc >= start_ts])
    save_days = _day_counts(db, SaveRelationship.created_utc, [SaveRelationship.submission_id.in_(ids), SaveRelationship.created_utc >= start_ts])
    engagement = {day: up_days.get(day, 0) + comment_days.get(day, 0) + save_days.get(day, 0) for day in set(up_days) | set(comment_days) | set(save_days)}

    viewers = lambda since: int(db.query(func.count(func.distinct(ViewHistory.user_id))).filter(  # noqa: E731
        ViewHistory.submission_id.in_(ids), ViewHistory.user_id != primary.author_id, ViewHistory.viewed_utc >= since).scalar() or 0)

    ups, downs = sum(ups_all.values()), sum(downs_all.values())
    window_views = sum(view_days.values())
    window_ups, window_comments, window_saves = sum(up_days.values()), sum(comment_days.values()), sum(save_days.values())

    places = [{"name": None, "views": int(views_all.get(primary.id, 0)),
               "net": rules.net(ups_all.get(primary.id, 0), downs_all.get(primary.id, 0)), "comments": comments_all.get(primary.id, 0)}]
    for copy_id, _, guild in copies:
        places.append({"name": guild, "views": int(views_all.get(copy_id, 0)),
                       "net": rules.net(ups_all.get(copy_id, 0), downs_all.get(copy_id, 0)), "comments": comments_all.get(copy_id, 0)})

    return {
        "days": days,
        "start": rules.label(start_day),
        "end": rules.label(today),
        "window": {"views": window_views, "viewers": viewers(start_ts), "upvotes": window_ups,
                   "comments": window_comments, "bookmarks": window_saves},
        "all_time": {"views": int(sum(views_all.values())), "viewers": viewers(0), "upvotes": ups, "downvotes": downs,
                     "net": rules.net(ups, downs), "comments": sum(comments_all.values()),
                     "bookmarks": db.query(func.count(SaveRelationship.id)).filter(SaveRelationship.submission_id.in_(ids)).scalar() or 0,
                     "forwards": len(copies),
                     "reposts": db.query(func.count(RepostRelationship.id)).filter(RepostRelationship.submission_id == primary.id).scalar() or 0},
        "views_chart": _chart(view_days, days, today),
        "engagement_chart": _chart(engagement, days, today),
        "places": places,
        "poll": poll_store.load_one(db, primary, viewer),
    }
