"""Who sees what under the word filter - the one place that decides.

A viewer browses at a level (User.filter_level):
    0  Off        nothing is hidden
    1  Standard   hides content rated extreme (the default, and what
                  logged-out visitors get)
    2  Child      also hides profanity, and anything marked sensitive

Content carries a stored rating (see helpers/word_filter_store.py). Hidden
means gone: not in feeds, not in threads, and a direct link shows a notice.
A filtered word in a username or guild name hides everything from that user
or guild. Authors always see their own content.

Use the `filter_*` functions on listing queries (so pages stay full) and the
`*_hidden` functions for a single object (direct pages, comment trees).
"""
from sqlalchemy import and_, or_, select

from ruqqus.helpers.wordfilter import is_hidden

OFF = 0
STANDARD = 1
CHILD = 2

NOTICE_TITLE = "Hidden by your word filter"
NOTICE_TEXT = ("This isn't shown at your current word filter level. "
               "You can review your word filter in your content settings.")


def viewer_level(v):
    """The filter level a viewer browses with (logged-out: Standard)."""
    if v is None:
        return STANDARD
    level = getattr(v, "filter_level", None)
    return STANDARD if level is None else int(level)


def _cut(level):
    """Lowest content rating that is hidden at this level."""
    return 3 - min(level, CHILD)


# ----------------------------------------------------------- single objects
def user_hidden(user, v, account_flag=True):
    """A user whose NAME trips the viewer's filter, or a sensitive account for a Child-filter viewer (everything of
    theirs is hidden). `account_flag=False` for an anonymous item: it is never hidden for being that account's."""
    if user is None or (v is not None and user.id == v.id):
        return False
    sensitive = account_flag and bool(getattr(user, "is_sensitive", False))
    return is_hidden(viewer_level(v), getattr(user, "name_severity", 0), sensitive)


def board_hidden(board, v):
    """A guild that is hidden outright: its name trips the filter, or it is a
    sensitive guild and the viewer is on the Child filter."""
    if board is None:
        return False
    return is_hidden(viewer_level(v), getattr(board, "name_severity", 0), bool(board.is_sensitive))


def post_hidden(post, v):
    if v is not None and post.author_id == v.id:
        return False
    level = viewer_level(v)
    if level <= OFF:
        return False
    return (is_hidden(level, post.word_severity, bool(post.is_sensitive))
            or user_hidden(post.author, v, account_flag=not getattr(post, "is_anonymous", False))
            or board_hidden(post.board, v))


def comment_hidden(comment, v):
    if v is not None and comment.author_id == v.id:
        return False
    level = viewer_level(v)
    if level <= OFF:
        return False
    return (is_hidden(level, comment.word_severity, bool(comment.is_sensitive))
            or user_hidden(comment.author, v, account_flag=not getattr(comment, "is_anonymous", False)))


def text_hidden(severity, v, owner_id=None):
    """A profile bio or guild description (only that text is hidden)."""
    if v is not None and owner_id is not None and v.id == owner_id:
        return False
    return is_hidden(viewer_level(v), severity)


# ----------------------------------------------------------------- queries
def _hidden_user_ids(cut):
    from ruqqus.classes.user import User
    return select(User.id).where(User.name_severity >= cut)


def _hidden_board_ids(level):
    from ruqqus.classes.boards import Board
    conds = [Board.name_severity >= _cut(level)]
    if level >= CHILD:
        conds.append(Board.is_sensitive == True)
    return select(Board.id).where(or_(*conds))


def _not_by_sensitive_account(model):
    """For the Child filter: an item not by a sensitive account, or anonymous (never hidden for whose it is)."""
    from ruqqus.helpers.sensitive import sensitive_user_ids
    return or_(model.is_anonymous.is_(True), model.author_id.notin_(sensitive_user_ids()))


def filter_posts(query, v, level=None):
    """Drop posts the viewer's filter hides from a query over Submission."""
    from ruqqus.classes.submission import Submission

    level = viewer_level(v) if level is None else level
    if level <= OFF:
        return query
    cut = _cut(level)
    conds = [
        Submission.word_severity < cut,
        Submission.author_id.notin_(_hidden_user_ids(cut)),
        Submission.board_id.notin_(_hidden_board_ids(level)),
    ]
    if level >= CHILD:
        conds.append(Submission.is_sensitive.isnot(True))
        conds.append(_not_by_sensitive_account(Submission))
    visible = and_(*conds)
    if v is not None:
        visible = or_(Submission.author_id == v.id, visible)
    return query.filter(visible)


def filter_comments(query, v, level=None):
    """Drop comments the viewer's filter hides from a query over Comment
    (including comments under a hidden post or in a hidden guild)."""
    from ruqqus.classes.comment import Comment
    from ruqqus.classes.submission import Submission

    level = viewer_level(v) if level is None else level
    if level <= OFF:
        return query
    cut = _cut(level)
    hidden_posts = [Submission.word_severity >= cut,
                    Submission.author_id.in_(_hidden_user_ids(cut)),
                    Submission.board_id.in_(_hidden_board_ids(level))]
    conds = [
        Comment.word_severity < cut,
        Comment.author_id.notin_(_hidden_user_ids(cut)),
    ]
    if level >= CHILD:
        conds.append(Comment.is_sensitive.isnot(True))
        conds.append(_not_by_sensitive_account(Comment))
        hidden_posts.append(Submission.is_sensitive == True)
        hidden_posts.append(~_not_by_sensitive_account(Submission))
    # system messages have no parent post; NOT IN would drop them (NULL)
    conds.append(or_(Comment.parent_submission.is_(None),
                     Comment.parent_submission.notin_(select(Submission.id).where(or_(*hidden_posts)))))
    visible = and_(*conds)
    if v is not None:
        visible = or_(Comment.author_id == v.id, visible)
    return query.filter(visible)


def filter_boards(query, v, level=None):
    """Drop guilds hidden outright from a query over Board."""
    from ruqqus.classes.boards import Board

    level = viewer_level(v) if level is None else level
    if level <= OFF:
        return query
    query = query.filter(Board.name_severity < _cut(level))
    if level >= CHILD:
        query = query.filter(Board.is_sensitive.isnot(True))
    return query


def filter_users(query, v, level=None):
    """Drop users with a filtered name from a query over User."""
    from ruqqus.classes.user import User

    level = viewer_level(v) if level is None else level
    if level <= OFF:
        return query
    visible = User.name_severity < _cut(level)
    if level >= CHILD:
        visible = and_(visible, User.is_sensitive.isnot(True))
    if v is not None:
        visible = or_(User.id == v.id, visible)
    return query.filter(visible)


def hidden_notice(v):
    """What a direct link to hidden content returns (for routes wrapped in @api)."""
    from flask import jsonify, render_template

    return {
        "html": lambda: (render_template("message.html", v=v, title=NOTICE_TITLE, error=NOTICE_TEXT), 403),
        "api": lambda: (jsonify({"error": NOTICE_TITLE}), 403),
    }


def comment_thread_hidden(comment, v):
    """A comment is hidden with everything under it, so a comment is also
    hidden when any comment above it is."""
    seen = 0
    while comment is not None and seen < 20:
        if comment_hidden(comment, v):
            return True
        comment = comment.parent_comment if comment.parent_comment_id else None
        seen += 1
    return False


def history_text_shown(item, v):
    """Whether an item's edit history may show the text it had (/post/<id>/history, /comment/<id>/history).
    Edits, author deletes and guild removals stay public. Text the site's admins removed is shown only to
    admins (level 3+, as on the item's page); purged text to nobody, since purging means erasing it."""
    if (item.purged_utc or 0) > 0:
        return False
    if item.is_banned:
        return bool(v and v.admin_level >= 3)
    return True
