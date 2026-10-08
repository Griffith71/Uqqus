"""Stories, the database side (rules: helpers/stories.py).

Plain SQL (`sqlalchemy.text`) so the same statements run on SQLite in the tests. Nothing here commits: the caller does.

**`watchable` is the one place that decides what a viewer may watch**: not deleted, still live (or, for a Highlight, only
kept), made for an audience the viewer is in (`circle_guard.may_see`: Public, Subscribers, Close Friends, the author, admins),
not from an account the viewer has blocked or that blocked them, and not hidden by the viewer's word filter. Every list,
ring and Highlight is built from it, so a new place that shows a story cannot forget a rule.
"""
import time

from sqlalchemy import bindparam, text

from ruqqus.helpers import circle_guard, circle_store, stories as rules
from ruqqus.helpers.media import rules as media_rules
from ruqqus.helpers.visibility import viewer_level
from ruqqus.helpers.wordfilter import is_hidden

DAY = 24 * 60 * 60


def _now(now):
    return int(now if now is not None else time.time())


_COLUMNS = "id, user_id, kind, body, background, video_ref, audience, word_severity, created_utc, expires_utc, deleted_utc"


# ------------------------------------------------------------------ making and removing

def create(db, user_id, kind, body, background, video_ref, audience, severity, now=None):
    """Make a story and return its id."""
    now = _now(now)
    return db.execute(text(
        "INSERT INTO stories (user_id, kind, body, background, video_ref, audience, word_severity, created_utc, expires_utc) "
        "VALUES (:u, :k, :b, :bg, :v, :a, :s, :t, :e) RETURNING id"),
        {"u": user_id, "k": kind, "b": body, "bg": background, "v": video_ref, "a": audience, "s": severity, "t": now, "e": rules.expires_at(now)}).scalar()


def attach_picture(db, story_id, asset_id, user_id):
    """Make the member's own ready, unattached picture part of the story. False if it is not theirs, not ready or used."""
    changed = db.execute(text(
        "UPDATE media_assets SET story_id = :s WHERE id = :a AND user_id = :u AND status = 'ready' AND kind = 'image' "
        "AND submission_id IS NULL AND comment_id IS NULL AND story_id IS NULL"), {"s": story_id, "a": asset_id, "u": user_id})
    return changed.rowcount == 1


def count_today(db, user_id, now=None):
    return int(db.execute(text("SELECT COUNT(*) FROM stories WHERE user_id = :u AND created_utc >= :t"),
                          {"u": user_id, "t": _now(now) - DAY}).scalar() or 0)


def get(db, story_id):
    return db.execute(text(f"SELECT {_COLUMNS} FROM stories WHERE id = :i"), {"i": story_id}).fetchone()


def delete(db, story_id, user_id):
    """The owner takes a story down (it leaves every Highlight too: a deleted story is never shown)."""
    return db.execute(text("UPDATE stories SET deleted_utc = :t WHERE id = :i AND user_id = :u AND deleted_utc = 0"),
                      {"t": int(time.time()), "i": story_id, "u": user_id}).rowcount == 1


def remove(db, story_id, now=None):
    """An admin takes a story down."""
    return db.execute(text("UPDATE stories SET deleted_utc = :t WHERE id = :i AND deleted_utc = 0"), {"t": _now(now), "i": story_id}).rowcount == 1


def picture_of(db, story_id):
    """The site address of a story's picture (served by routes/media.py, to the right people only), or None."""
    row = db.execute(text("SELECT id, token, ext FROM media_assets WHERE story_id = :s AND status = 'ready' LIMIT 1"), {"s": story_id}).fetchone()
    return media_rules.media_path(row.id, row.token, row.ext) if row else None


# ------------------------------------------------------------------ what a viewer may watch

def _allowed(db, row, viewer, now):
    """May this viewer see this story at all (live or not)? Audience, blocks, word filter."""
    if viewer is not None and viewer.id == row.user_id:
        return True
    if row.deleted_utc:
        return False
    if viewer is not None and circle_store.blocked_between(db, row.user_id, viewer.id):
        return False
    if is_hidden(viewer_level(viewer), row.word_severity):
        return False
    return circle_guard.may_see(db, row.audience, row.user_id, viewer, now)


def watchable(db, owner_id, viewer, now=None):
    """The owner's live stories this viewer may watch, oldest first."""
    now = _now(now)
    rows = db.execute(text(f"SELECT {_COLUMNS} FROM stories WHERE user_id = :o AND deleted_utc = 0 AND expires_utc > :n ORDER BY created_utc, id"),
                      {"o": owner_id, "n": now}).fetchall()
    return [row for row in rows if _allowed(db, row, viewer, now)]


_LIVE_BY_OWNERS = text(f"SELECT {_COLUMNS} FROM stories WHERE user_id IN :owners AND deleted_utc = 0 AND expires_utc > :n ORDER BY created_utc, id"
                       ).bindparams(bindparam("owners", expanding=True))
_SEEN = text("SELECT story_id FROM story_views WHERE viewer_id = :v AND story_id IN :ids").bindparams(bindparam("ids", expanding=True))


def seen_ids(db, viewer_id, story_ids):
    ids = list(story_ids)
    if not ids or viewer_id is None:
        return set()
    return {r[0] for r in db.execute(_SEEN, {"v": viewer_id, "ids": ids}).fetchall()}


def rings(db, owner_ids, viewer, now=None):
    """{owner id: "unseen" | "seen"} for those of these accounts that have a story the viewer may watch: one query for the
    stories and one for what the viewer has seen, however many accounts."""
    now = _now(now)
    owners = sorted({int(o) for o in owner_ids if o})
    if not owners or viewer is None:
        return {}
    rows = [row for row in db.execute(_LIVE_BY_OWNERS, {"owners": owners, "n": now}).fetchall() if _allowed(db, row, viewer, now)]
    seen = seen_ids(db, viewer.id, [row.id for row in rows])
    by_owner = {}
    for row in rows:
        by_owner.setdefault(row.user_id, []).append(row.id)
    return {owner: rules.ring(ids, seen) for owner, ids in by_owner.items()}


def mark_seen(db, story_id, viewer_id, now=None):
    db.execute(text("INSERT INTO story_views (story_id, viewer_id, created_utc) VALUES (:s, :v, :t) ON CONFLICT DO NOTHING"),
               {"s": story_id, "v": viewer_id, "t": _now(now)})


_COUNTS = text("SELECT story_id, COUNT(*) FROM story_views WHERE story_id IN :ids GROUP BY story_id").bindparams(bindparam("ids", expanding=True))


def view_counts(db, story_ids):
    """{story id: how many accounts watched}: only ever a number."""
    ids = list(story_ids)
    if not ids:
        return {}
    return {r[0]: r[1] for r in db.execute(_COUNTS, {"ids": ids}).fetchall()}


def archive(db, user_id, limit=rules.ARCHIVE_LIMIT):
    """The member's own stories, live or past, newest first (to look back on and to put in a Highlight)."""
    return db.execute(text(f"SELECT {_COLUMNS} FROM stories WHERE user_id = :u AND deleted_utc = 0 ORDER BY created_utc DESC, id DESC LIMIT :l"),
                      {"u": user_id, "l": limit}).fetchall()


# ------------------------------------------------------------------ highlights

def highlights(db, owner_id, viewer, now=None):
    """[(id, title, [stories the viewer may see])] for the owner, in order; one with nothing the viewer may see is left out.
    A Highlight keeps a story past its 24 hours, so liveness is not asked here."""
    now = _now(now)
    out = []
    for h in db.execute(text("SELECT id, title FROM highlights WHERE user_id = :o ORDER BY position, id"), {"o": owner_id}).fetchall():
        rows = db.execute(text(f"SELECT {', '.join('s.' + c.strip() for c in _COLUMNS.split(','))} FROM highlight_stories hs JOIN stories s ON s.id = hs.story_id "
                               "WHERE hs.highlight_id = :h AND s.deleted_utc = 0 ORDER BY hs.position, s.created_utc, s.id"), {"h": h.id}).fetchall()
        shown = [row for row in rows if _allowed(db, row, viewer, now)]
        if shown:
            out.append((h.id, h.title, shown))
    return out


def highlight_row(db, highlight_id):
    return db.execute(text("SELECT id, user_id, title FROM highlights WHERE id = :h"), {"h": highlight_id}).fetchone()


def highlight_stories(db, highlight_id, viewer, now=None):
    """The stories of one Highlight the viewer may watch, or None if there is no such Highlight."""
    now = _now(now)
    h = highlight_row(db, highlight_id)
    if h is None:
        return None
    rows = db.execute(text(f"SELECT {', '.join('s.' + c.strip() for c in _COLUMNS.split(','))} FROM highlight_stories hs JOIN stories s ON s.id = hs.story_id "
                           "WHERE hs.highlight_id = :h AND s.deleted_utc = 0 ORDER BY hs.position, s.created_utc, s.id"), {"h": highlight_id}).fetchall()
    return h, [row for row in rows if _allowed(db, row, viewer, now)]


def create_highlight(db, user_id, title, story_ids, now=None):
    """Make a Highlight of the member's own stories. Returns (id, None) or (None, message)."""
    now = _now(now)
    if int(db.execute(text("SELECT COUNT(*) FROM highlights WHERE user_id = :u"), {"u": user_id}).scalar() or 0) >= rules.HIGHLIGHTS_MAX:
        return None, f"You can have {rules.HIGHLIGHTS_MAX} highlights at most."
    ids = rules.clean_ids(story_ids)
    if not ids:
        return None, "Choose at least one story."
    mine = {r[0] for r in db.execute(text("SELECT id FROM stories WHERE user_id = :u AND deleted_utc = 0 AND id IN :ids").bindparams(bindparam("ids", expanding=True)),
                                     {"u": user_id, "ids": ids}).fetchall()}
    chosen = [i for i in ids if i in mine]
    if not chosen:
        return None, "Choose at least one story."
    position = int(db.execute(text("SELECT COALESCE(MAX(position), -1) + 1 FROM highlights WHERE user_id = :u"), {"u": user_id}).scalar() or 0)
    hid = db.execute(text("INSERT INTO highlights (user_id, title, position, created_utc) VALUES (:u, :t, :p, :n) RETURNING id"),
                     {"u": user_id, "t": title, "p": position, "n": now}).scalar()
    for n, story_id in enumerate(chosen):
        db.execute(text("INSERT INTO highlight_stories (highlight_id, story_id, position) VALUES (:h, :s, :p)"), {"h": hid, "s": story_id, "p": n})
    return hid, None


def rename_highlight(db, user_id, highlight_id, title):
    return db.execute(text("UPDATE highlights SET title = :t WHERE id = :h AND user_id = :u"), {"t": title, "h": highlight_id, "u": user_id}).rowcount == 1


def delete_highlight(db, user_id, highlight_id):
    """Delete the Highlight; its stories stay in the member's archive."""
    db.execute(text("DELETE FROM highlight_stories WHERE highlight_id IN (SELECT id FROM highlights WHERE id = :h AND user_id = :u)"), {"h": highlight_id, "u": user_id})
    return db.execute(text("DELETE FROM highlights WHERE id = :h AND user_id = :u"), {"h": highlight_id, "u": user_id}).rowcount == 1


# ------------------------------------------------------------------ reports

def report(db, story_id, reporter_id, reason, now=None):
    """File a report (once per person per story). False if they had already."""
    return db.execute(text("INSERT INTO story_reports (story_id, reporter_id, reason, created_utc) VALUES (:s, :r, :x, :t) ON CONFLICT DO NOTHING"),
                      {"s": story_id, "r": reporter_id, "x": reason, "t": _now(now)}).rowcount == 1


def open_reports(db, limit=50):
    """[(story_id, owner_id, owner name, kind, body, how many reports, the newest reason)] most reported first."""
    return db.execute(text("""
        SELECT s.id AS story_id, s.user_id, u.username, s.kind, s.body, COUNT(r.id) AS reports,
               (SELECT x.reason FROM story_reports x WHERE x.story_id = s.id AND x.resolved_utc = 0 ORDER BY x.id DESC LIMIT 1) AS reason
        FROM story_reports r JOIN stories s ON s.id = r.story_id JOIN users u ON u.id = s.user_id
        WHERE r.resolved_utc = 0 AND s.deleted_utc = 0 GROUP BY s.id, s.user_id, u.username, s.kind, s.body
        ORDER BY COUNT(r.id) DESC, s.id DESC LIMIT :l"""), {"l": limit}).fetchall()


def resolve_reports(db, story_id, now=None):
    db.execute(text("UPDATE story_reports SET resolved_utc = :t WHERE story_id = :s AND resolved_utc = 0"), {"t": _now(now), "s": story_id})
