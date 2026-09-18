from ruqqus.helpers.wrappers import *
from ruqqus.helpers.base36 import *
from ruqqus.helpers.get import *
from ruqqus.classes import *
from ruqqus.__main__ import app


def _mixed_listing(v, post_ids, comment_ids, post_times, comment_times, page):
    """
    Merges an independently-paginated page of post ids and comment ids into
    one feed ordered by vote/view time, for the "all" content filter on the
    Upvoted/Downvoted tabs. Not a perfectly composable cross-type cursor -
    each type is paginated on its own 25-per-page boundary and the two pages
    are merged - but that's a reasonable tradeoff for a personal history
    feed rather than building full cross-type pagination.
    """

    posts = get_posts(post_ids, v=v) if post_ids else []
    comments = get_comments(comment_ids, v=v) if comment_ids else []
    comments = sorted(comments, key=lambda c: comment_ids.index(c.id))

    tagged = (
        [("post", p, post_times[p.id]) for p in posts] +
        [("comment", c, comment_times[c.id]) for c in comments]
    )
    tagged.sort(key=lambda item: item[2], reverse=True)

    next_exists = len(post_ids) == 26 or len(comment_ids) == 26

    return tagged[0:25], next_exists


@app.route("/history", methods=["GET"])
@auth_required
@api("read")
def history_bookmarked(v):
    """The Bookmarked tab of the History page - posts and replies you've
    saved, with the same All/Posts/Comments filter as Upvoted/Downvoted."""

    page = int(request.args.get("page", 1))
    content_type = request.args.get("type", "all")
    if content_type not in ("all", "posts", "comments"):
        content_type = "all"

    post_ids = []
    comment_ids = []
    next_exists = False
    listing = []
    mixed_listing = None

    if content_type in ("all", "posts"):
        post_ids = v.saved_idlist(page=page)

    if content_type in ("all", "comments"):
        comment_ids = v.saved_comment_idlist(page=page)

    if content_type == "posts":
        next_exists = len(post_ids) == 26
        listing = get_posts(post_ids[0:25], v=v)

    elif content_type == "comments":
        next_exists = len(comment_ids) == 26
        listing = get_comments(comment_ids[0:25], v=v)
        listing = sorted(listing, key=lambda c: comment_ids[0:25].index(c.id))

    else:
        # "all" - merge posts and comments by bookmark time
        post_times = {pid: t for pid, t in zip(
            post_ids, _save_times(v, "post", post_ids))}
        comment_times = {cid: t for cid, t in zip(
            comment_ids, _save_times(v, "comment", comment_ids))}

        mixed_listing, next_exists = _mixed_listing(
            v, post_ids[0:26], comment_ids[0:26], post_times, comment_times, page)

    return {"html": lambda: render_template("history.html",
                                            v=v,
                                            active_tab="bookmarked",
                                            content_type=content_type,
                                            listing=listing,
                                            mixed_listing=mixed_listing,
                                            page=page,
                                            next_exists=next_exists),
            "api": lambda: jsonify({"data": [
                x.json for x in (listing if listing else
                                 [item[1] for item in (mixed_listing or [])])
            ]})
            }


def _save_times(v, kind, ids):
    """Looks up bookmark-time timestamps for a set of already-fetched ids,
    used only to sort the merged "all" feed on the Bookmarked tab."""

    if not ids:
        return []

    if kind == "post":
        rows = g.db.query(SaveRelationship.submission_id, SaveRelationship.created_utc).filter(
            SaveRelationship.user_id == v.id,
            SaveRelationship.submission_id.in_(ids)
        ).all()
    else:
        rows = g.db.query(CommentSaveRelationship.comment_id, CommentSaveRelationship.created_utc).filter(
            CommentSaveRelationship.user_id == v.id,
            CommentSaveRelationship.comment_id.in_(ids)
        ).all()

    times = {row[0]: row[1] for row in rows}
    return [times.get(i, 0) for i in ids]


@app.route("/history/viewed", methods=["GET"])
@auth_required
@api("read")
def history_viewed(v):
    """The History tab of the History page - posts you've viewed."""

    page = int(request.args.get("page", 1))

    ids = v.history_idlist(page=page)
    next_exists = len(ids) == 26
    ids = ids[0:25]

    listing = get_posts(ids, v=v)

    return {"html": lambda: render_template("history.html",
                                            v=v,
                                            active_tab="viewed",
                                            listing=listing,
                                            mixed_listing=None,
                                            content_type=None,
                                            page=page,
                                            next_exists=next_exists),
            "api": lambda: jsonify({"data": [x.json for x in listing]})
            }


def _voted_tab(v, direction, tab_name):

    page = int(request.args.get("page", 1))
    content_type = request.args.get("type", "all")
    if content_type not in ("all", "posts", "comments"):
        content_type = "all"

    post_ids = []
    comment_ids = []
    next_exists = False
    listing = []
    mixed_listing = None

    if content_type in ("all", "posts"):
        if direction == 1:
            post_ids = v.upvoted_idlist(page=page)
        else:
            post_ids = v.downvoted_idlist(page=page)

    if content_type in ("all", "comments"):
        if direction == 1:
            comment_ids = v.upvoted_comment_idlist(page=page)
        else:
            comment_ids = v.downvoted_comment_idlist(page=page)

    if content_type == "posts":
        next_exists = len(post_ids) == 26
        listing = get_posts(post_ids[0:25], v=v)

    elif content_type == "comments":
        next_exists = len(comment_ids) == 26
        listing = get_comments(comment_ids[0:25], v=v)
        listing = sorted(listing, key=lambda c: comment_ids[0:25].index(c.id))

    else:
        # "all" - merge posts and comments by vote time
        vote_type = 1 if direction == 1 else -1
        post_times = {pid: t for pid, t in zip(
            post_ids, _vote_times(v, "post", vote_type, post_ids))}
        comment_times = {cid: t for cid, t in zip(
            comment_ids, _vote_times(v, "comment", vote_type, comment_ids))}

        mixed_listing, next_exists = _mixed_listing(
            v, post_ids[0:26], comment_ids[0:26], post_times, comment_times, page)

    return {"html": lambda: render_template("history.html",
                                            v=v,
                                            active_tab=tab_name,
                                            content_type=content_type,
                                            listing=listing,
                                            mixed_listing=mixed_listing,
                                            page=page,
                                            next_exists=next_exists),
            "api": lambda: jsonify({"data": [
                x.json for x in (listing if listing else
                                 [item[1] for item in (mixed_listing or [])])
            ]})
            }


def _vote_times(v, kind, vote_type, ids):
    """Looks up vote-cast timestamps for a set of already-fetched ids, used
    only to sort the merged "all" feed on the Upvoted/Downvoted tabs."""

    if not ids:
        return []

    if kind == "post":
        rows = g.db.query(Vote.submission_id, Vote.created_utc).filter(
            Vote.user_id == v.id,
            Vote.vote_type == vote_type,
            Vote.submission_id.in_(ids)
        ).all()
    else:
        rows = g.db.query(CommentVote.comment_id, CommentVote.created_utc).filter(
            CommentVote.user_id == v.id,
            CommentVote.vote_type == vote_type,
            CommentVote.comment_id.in_(ids)
        ).all()

    times = {row[0]: row[1] for row in rows}
    return [times.get(i, 0) for i in ids]


@app.route("/history/upvoted", methods=["GET"])
@auth_required
@api("read")
def history_upvoted(v):
    """The Upvoted tab of the History page."""
    return _voted_tab(v, 1, "upvoted")


@app.route("/history/downvoted", methods=["GET"])
@auth_required
@api("read")
def history_downvoted(v):
    """The Downvoted tab of the History page."""
    return _voted_tab(v, -1, "downvoted")
