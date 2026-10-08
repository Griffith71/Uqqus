"""The site-wide deletion log (helpers/deletion_log.py has the rules and the promises).

`GET /log/deleted` lists the posts and comments their authors deleted, newest first, with the text each had
when it was deleted. Public: visitors and members alike. Every list goes through the word filter
(`filter_posts` / `filter_comments`), leaves out what was not public or was removed by someone else, and
treats anonymous items as the rest of the site does."""
from flask import abort, g, render_template, request
from sqlalchemy import exists
from sqlalchemy.orm import aliased, contains_eager, joinedload, lazyload

from ruqqus.classes import Board, Comment, ContentEditHistory, ObliterationRecord, Submission
from ruqqus.helpers import anonymity
from ruqqus.helpers import deletion_log as rules
from ruqqus.helpers.get import get_guild, get_user
from ruqqus.helpers.visibility import board_hidden, filter_boards, filter_comments, filter_posts, user_hidden
from ruqqus.helpers.wrappers import auth_desired
from ruqqus.__main__ import app

H = ContentEditHistory


def _guild_filter(name, v):
    """The guild a list is narrowed to. An unknown, banned, private or filtered guild is a 404 (the same answer
    for all four, so the log never confirms a private guild)."""
    if not name:
        return None
    guild = get_guild(name, v=v, graceful=True)
    if guild is None or guild.is_banned or guild.is_private or board_hidden(guild, v):
        abort(404)
    return guild


def _user_filter(name, v):
    if not name:
        return None
    user = get_user(name, v=v, graceful=True)
    if user is None or user.is_deleted or user_hidden(user, v):
        abort(404)
    return user


def _post_entries(v, page, guild, user):
    forward = aliased(Submission)
    query = (g.db.query(H)
             .options(lazyload("*"), contains_eager(H.target_post), joinedload(H.actor))
             .join(Submission, Submission.id == H.target_submission_id)
             .join(Board, Board.id == Submission.board_id)
             .filter(H.action == "delete",
                     Submission.deleted_utc > 0,
                     Submission.purged_utc == 0,
                     Submission.is_banned.is_(False),
                     Submission.hidden_by_guild.is_(False),
                     Submission.post_public.is_(True),
                     Board.is_private.is_(False),
                     Board.is_banned.is_(False),
                     H.previous_title.isnot(None),
                     ~exists().where(ObliterationRecord.target_submission_id == H.target_submission_id)))
    query = filter_posts(query, v)
    if user is not None:
        query = query.filter(H.actor_id == user.id, anonymity.hide_anonymous(Submission, v))
    if guild is not None:
        query = query.filter(
            (H.board_id == guild.id) | exists().where(forward.repost_id == Submission.id, forward.board_id == guild.id))
    return query.order_by(H.id.desc()).offset(rules.PER_PAGE * (page - 1)).limit(rules.PER_PAGE + 1).all()


def _comment_entries(v, page, guild, user):
    parent = aliased(Submission)
    parent_board = aliased(Board)
    query = (g.db.query(H)
             .options(lazyload("*"), contains_eager(H.target_comment), joinedload(H.actor))
             .join(Comment, Comment.id == H.target_comment_id)
             .join(parent, parent.id == Comment.parent_submission)
             .join(parent_board, parent_board.id == parent.board_id)
             .filter(H.action == "delete",
                     Comment.deleted_utc > 0,
                     Comment.purged_utc == 0,
                     Comment.is_banned.is_(False),
                     parent.is_banned.is_(False),
                     parent.hidden_by_guild.is_(False),
                     parent.post_public.is_(True),
                     parent_board.is_private.is_(False),
                     parent_board.is_banned.is_(False),
                     H.previous_body_html.isnot(None),
                     ~exists().where(ObliterationRecord.target_comment_id == H.target_comment_id)))
    query = filter_comments(query, v)
    if user is not None:
        query = query.filter(H.actor_id == user.id, anonymity.hide_anonymous(Comment, v))
    if guild is not None:
        query = query.filter(H.board_id == guild.id)
    return query.order_by(H.id.desc()).offset(rules.PER_PAGE * (page - 1)).limit(rules.PER_PAGE + 1).all()


def _forwarded_to(v, post_ids):
    """The public guilds each of these posts was forwarded to (by post id), the viewer's word filter applied."""
    if not post_ids:
        return {}
    rows = filter_boards(
        g.db.query(Submission.repost_id, Board)
        .options(lazyload("*"))
        .join(Board, Board.id == Submission.board_id)
        .filter(Submission.repost_id.in_(post_ids), Board.is_private.is_(False), Board.is_banned.is_(False)),
        v).order_by(Board.name).all()
    found = {}
    for post_id, board in rows:
        names = found.setdefault(post_id, [])
        if board.name not in [b.name for b in names]:
            names.append(board)
    return found


@app.route("/log/deleted", methods=["GET"])
@auth_desired
def deletion_log(v):
    tab = rules.parse_tab(request.args.get("type"))
    page = rules.parse_page(request.args.get("page"))
    guild = _guild_filter(request.args.get("guild"), v)
    user = _user_filter(request.args.get("user"), v)

    if tab == rules.COMMENTS:
        entries = _comment_entries(v, page, guild, user)
    else:
        entries = _post_entries(v, page, guild, user)
    next_exists = len(entries) > rules.PER_PAGE
    entries = entries[:rules.PER_PAGE]

    forwarded = _forwarded_to(v, [e.target_submission_id for e in entries]) if tab == rules.POSTS else {}

    return render_template("deletion_log.html",
                           v=v,
                           tab=tab,
                           page=page,
                           entries=entries,
                           next_exists=next_exists,
                           guild=guild,
                           user_filter=user,
                           forwarded=forwarded,
                           lived=rules.lived,
                           media_free=rules.without_media,
                           guilds_shown=rules.GUILDS_SHOWN)
