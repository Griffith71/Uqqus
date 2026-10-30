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
from sqlalchemy import or_, text

from ruqqus.helpers.wordfilter import WordFilter, EXTREME
from ruqqus.helpers import wordfilter_seed

RELOAD_SECONDS = 60

_state = {"filter": None, "loaded": 0.0}

_CAMEL = re.compile(r"(?<=[a-z])(?=[A-Z])")


_ENABLED_ROWS = text("SELECT word, severity, mode, variants, suffixes, language FROM word_filter_entries "
                     "WHERE enabled = :on")


def _split(value):
    return [x.strip() for x in (value or "").split(",") if x.strip()]


def _source(db):
    """(entries, allow phrases, built in) the live filter is made of: the table's enabled rows, or, while the table
    has none, the built-in list (the English starter list and the other languages' words). Entries are engine
    entries with `language`; phrases are {"phrase", "language"}."""
    rows = db.execute(_ENABLED_ROWS, {"on": True}).fetchall()
    if not rows:
        return (list(wordfilter_seed.BUILTIN_ENTRIES),
                [{"phrase": p, "language": None} for p in wordfilter_seed.ALLOW_PHRASES]
                + list(wordfilter_seed.INTL_ALLOW_PHRASES), True)
    entries, phrases = [], []
    for word, severity, mode, variants, suffixes, language in rows:
        word = (word or "").strip()
        if severity == 0 and " " in word:
            phrases.append({"phrase": word, "language": language or None})
        else:
            entries.append({"word": word, "severity": severity, "mode": mode or "word", "variants": _split(variants),
                            "suffixes": None if suffixes is None else _split(suffixes),
                            "language": language or None})
    return entries, phrases, False


def _load(db):
    entries, phrases, _ = _source(db)
    return WordFilter(entries, [p["phrase"] for p in phrases])


def listed_entries(db):
    """What the word filter lists right now, from the same source as the live filter (the table, or the built-in
    list while the table is empty), for a page that shows it. Returns a dict:

        builtin        True while the built-in list is in use (the table is empty)
        entries        [{"word": str, "severity": 0 | 1 | 2, "mode": "word" | "anywhere",
                         "variants": [str], "suffixes": [str] or None (None: the usual English endings),
                         "language": a code from languages.LANGUAGE_NAMES or None}]
                       every ENABLED entry, allowed words (severity 0) included, sorted by language (None first),
                       severity (highest first), then word
        allow_phrases  [{"phrase": str, "language": str or None}]   phrases that are never counted
        source         the other languages' credit: {"source", "url", "license", "license_url", "fetched",
                       "adapted"} (wordfilter_seed.INTL_SOURCE; {} when the file is missing)
    """
    entries, phrases, builtin = _source(db)
    entries = sorted(entries, key=lambda e: (e.get("language") or "", -int(e.get("severity") or 0), e["word"]))
    return {
        "builtin": builtin,
        "entries": [{"word": e["word"], "severity": int(e.get("severity") or 0), "mode": e.get("mode") or "word",
                     "variants": list(e.get("variants") or []),
                     "suffixes": None if e.get("suffixes") is None else list(e["suffixes"]),
                     "language": e.get("language") or None} for e in entries],
        "allow_phrases": sorted(phrases, key=lambda p: (p["language"] or "", p["phrase"])),
        "source": dict(wordfilter_seed.INTL_SOURCE),
    }


# ------------------------------------------------------------------ the built-in list into the table
_WORD_MAX, _LIST_MAX = 64, 512
_INSERT = text("INSERT INTO word_filter_entries (word, severity, mode, variants, suffixes, enabled, note, "
               "created_utc, language) VALUES (:word, :severity, :mode, :variants, :suffixes, :enabled, :note, "
               ":created, :language) ON CONFLICT (word) DO NOTHING")


def _joined(items):
    """Comma separated, as many as fit the column (the last ones are left out, never cut in half)."""
    out = []
    for item in items:
        if len(",".join(out + [item])) > _LIST_MAX:
            break
        out.append(item)
    return ",".join(out)


def _builtin_rows(which):
    """Rows for the table: "intl" the other languages' words and phrases, "all" the English starter list too."""
    rows = []
    if which == "all":
        for e in wordfilter_seed.ENTRIES:
            rows.append({"word": e["word"], "severity": e["severity"], "mode": e["mode"],
                         "variants": _joined(e["variants"]),
                         "suffixes": None if e["suffixes"] is None else _joined(e["suffixes"]),
                         "note": "starter list", "language": None})
        for phrase in wordfilter_seed.ALLOW_PHRASES:
            rows.append({"word": phrase, "severity": 0, "mode": "word", "variants": "", "suffixes": "",
                         "note": "starter list: allowed phrase", "language": None})
    for e in wordfilter_seed.INTL_ENTRIES:
        rows.append({"word": e["word"], "severity": e["severity"], "mode": e["mode"], "variants": _joined(e["variants"]),
                     "suffixes": _joined(e["suffixes"]), "note": "built-in list (LDNOOBW)",
                     "language": e["language"]})
    for p in wordfilter_seed.INTL_ALLOW_PHRASES:
        rows.append({"word": p["phrase"], "severity": 0, "mode": "word", "variants": "", "suffixes": "",
                     "note": "built-in list (LDNOOBW): allowed phrase", "language": p["language"]})
    return [r for r in rows if len(r["word"]) <= _WORD_MAX and len(r["language"] or "") <= 16]


def _entry_key(word, severity):
    """What a row of the table is, as the filter reads it: the form it is matched in, so the same word spelled another
    way ("maricón" and the admin form's "maricon", Persian "کیر" and Arabic "كير") is one word. An allowed word with
    letters beyond a-z keeps them (helpers/wordfilter.py: "kút" is allowed as written, "kut" is listed), and a phrase
    is its words."""
    word = (word or "").strip()
    if " " in word:
        return ("phrase", " ".join(WordFilter.clean_word(part) for part in word.split()))
    clean = WordFilter.clean_word(word) or word
    if not severity:
        exact = WordFilter.allowed_form(word)
        if exact and exact != clean:
            return ("exact", exact)
    return ("word", clean)


def add_builtin(db, which="intl", dry_run=False, now=None):
    """Put the built-in words into the table: the ones for other languages ("intl"), or the whole built-in list
    ("all": the English starter list too). A word already in the table, in any state and however it is spelled
    (_entry_key: "maricón" is the "maricon" an admin added and turned off), is never changed or added again (and
    ON CONFLICT (word) DO NOTHING), so it is safe to run again. Returns the words added (or, with dry_run, that
    would be); the caller commits and asks for a re-scan."""
    existing = {_entry_key(word, severity)
                for word, severity in db.execute(text("SELECT word, severity FROM word_filter_entries")).fetchall()}
    added = []
    for row in _builtin_rows(which):
        key = _entry_key(row["word"], row["severity"])
        if key in existing:
            continue
        existing.add(key)
        if not dry_run:
            result = db.execute(_INSERT, dict(row, enabled=True, created=int(now or time.time())))
            if not result.rowcount:
                continue
        added.append(row["word"])
    return added


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
    from ruqqus.helpers.profile_details import rated_text
    f = get_filter(db)
    # the display name has spaces (it is plain text, not a handle), so it is rated as text; it hides the account the
    # same way a rude username does
    user.name_severity = max(name_severity(user.username, db), f.severity(getattr(user, "display_name", "") or ""))
    # the job and location on the profile header are rated with the bio, and hidden with it (User.details_for)
    extra = rated_text(getattr(user, "profile_job", "") or "", getattr(user, "profile_location", "") or "")
    user.bio_severity = max(f.severity_html(user.bio_html or ""), f.severity(extra) if extra else 0)


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

    # guild channels' names, topics and rules (helpers/guild_channels.py)
    from ruqqus.helpers import guild_channel_store
    guild_channel_store.rescan_severities(db, f.severity)
    guild_channel_store.rescan_category_severities(db, f.severity)
    from ruqqus.helpers import guild_role_store
    guild_role_store.rescan_severities(db, f.severity)
    # the "What's happening" events (helpers/site_events.py): a title, details and a score line a source or an admin wrote
    from ruqqus.helpers import site_event_store
    site_event_store.rescan_severities(db, f.severity, f.version)
    db.commit()

    return changed

