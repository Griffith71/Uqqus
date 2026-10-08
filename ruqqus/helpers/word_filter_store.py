"""The word filter as the app uses it: load the list from the database, and
stamp posts, comments, users and guilds with how offensive their text is.

Classification happens when something is written (and again in a re-scan
after the list changes), so feeds can filter in the database and pages stay
full. The rating is stored as:

    submissions / comments   word_severity, word_filter_version
    users                    name_severity (username), bio_severity
    boards                   name_severity (guild name), description_severity
"""
import html
import re
import time

from flask import g
from sqlalchemy import or_

from ruqqus.helpers.wordfilter import WordFilter, EXTREME
from ruqqus.helpers import wordfilter_seed

RELOAD_SECONDS = 60

_state = {"filter": None, "loaded": 0.0}

_CAMEL = re.compile(r"(?<=[a-z])(?=[A-Z])")


def _load(db):
    from ruqqus.classes.word_filter import WordFilterEntry

    rows = db.query(WordFilterEntry).filter_by(enabled=True).all()
    if not rows:
        # nothing configured yet: start from the built-in list
        return WordFilter(wordfilter_seed.ENTRIES, wordfilter_seed.ALLOW_PHRASES)

    entries, phrases = [], []
    for row in rows:
        if row.severity == 0 and " " in (row.word or "").strip():
            phrases.append(row.word)
        else:
            entries.append(row.as_engine_entry)
    return WordFilter(entries, phrases)


def get_filter(db=None, force=False):
    """The compiled list, reloaded from the database at most once a minute."""
    now = time.time()
    if force or _state["filter"] is None or now - _state["loaded"] > RELOAD_SECONDS:
        _state["filter"] = _load(db or g.db)
        _state["loaded"] = now
    return _state["filter"]


def post_severity(title, body_html, db=None, extra=""):
    """`extra` is more plain text that belongs to the post: the options of its poll."""
    f = get_filter(db)
    # titles are stored HTML-escaped
    return max(f.severity(html.unescape(title or "")), f.severity_html(body_html),
               f.severity(extra) if extra else 0), f.version


def apply_post_severity(post, title, body_html, db=None, extra=""):
    severity, version = post_severity(title, body_html, db, extra)
    post.word_severity = severity
    post.word_filter_version = version
    post.is_offensive = severity >= EXTREME   # kept for API clients
    return severity


def apply_comment_severity(comment, body_html, db=None):
    f = get_filter(db)
    severity = f.severity_html(body_html)
    comment.word_severity = severity
    comment.word_filter_version = f.version
    comment.is_offensive = severity >= EXTREME
    return severity


def name_severity(name, db=None):
    # names have no spaces: read "Big_BadWord" and "BigBadWord" as words
    spaced = _CAMEL.sub(" ", name or "").replace("_", " ").replace("-", " ")
    f = get_filter(db)
    return max(f.severity(name or ""), f.severity(spaced))


def apply_user_severity(user, db=None):
    user.name_severity = name_severity(user.username, db)
    user.bio_severity = get_filter(db).severity_html(user.bio_html or "")


def apply_board_severity(board, db=None):
    board.name_severity = name_severity(board.name, db)
    board.description_severity = get_filter(db).severity_html(board.description_html or "")


def rescan(db, batch=500, log=None):
    """Re-rate everything whose rating was made with an older list.
    Returns how many rows changed rating."""
    from ruqqus.classes import Submission, Comment, User, Board

    f = get_filter(db, force=True)
    changed = 0

    for model in (Submission, Comment):
        while True:
            rows = db.query(model).filter(
                or_(model.word_filter_version.is_(None), model.word_filter_version != f.version)
            ).limit(batch).all()
            if not rows:
                break
            extras = {}
            if model is Submission:
                from ruqqus.helpers import poll_store
                extras = poll_store.option_texts(db, [row.repost_id or row.id for row in rows])
            for row in rows:
                before = row.word_severity or 0
                if model is Submission:
                    apply_post_severity(row, row.title, row.body_html, db, extra=extras.get(row.repost_id or row.id, ""))
                else:
                    apply_comment_severity(row, row.body_html, db)
                changed += before != row.word_severity
                db.add(row)
            db.commit()
            if log:
                log(f"{model.__name__}: {len(rows)} checked")

    for model, apply in ((User, apply_user_severity), (Board, apply_board_severity)):
        offset = 0
        while True:
            rows = db.query(model).order_by(model.id.asc()).offset(offset).limit(batch).all()
            if not rows:
                break
            for row in rows:
                before = (row.name_severity or 0, getattr(row, "bio_severity", None) or getattr(row, "description_severity", None) or 0)
                apply(row, db)
                after = (row.name_severity or 0, getattr(row, "bio_severity", None) or getattr(row, "description_severity", None) or 0)
                changed += before != after
                db.add(row)
            db.commit()
            offset += batch
            if log:
                log(f"{model.__name__}: {len(rows)} checked")

    return changed

