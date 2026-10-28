"""Feedback about the site itself ("Send feedback" in the account menu), and the admins' inbox (/admin/feedback).

A member says what is wrong, what looks off or what they would like, with the page they were on, a few facts about the
screen and, if they want, one screenshot. Admins of level 3 read it, mark it seen, fixed or declined, and may write a
short response, which the sender gets as a notification. Not the same thing as `post_feedback` ("Show fewer posts like
this") or the reports of a post or an account. Rules (pure) and plain text() SQL, tested on SQLite
(tests/test_site_feedback.py):
* a kind (bug, design, idea or other) and a message of 1 to MESSAGE_MAX characters are required;
* at most DAILY_LIMIT a day per member;
* the context is a whitelist: the page's path (no query string, no #fragment), light or dark, the density, the size
  of the window, the page's language and the browser's own text. Never an address, never anything else the page sends;
* a screenshot lives in `site_feedback_images`, apart, so the inbox never reads its bytes (helpers/feedback_image.py
  makes it safe; helpers/retention.py deletes it a month after the item is closed);
* closing an account deletes its feedback and screenshots (helpers/account_cleanup.py).
"""
import json
import re
import time
from urllib.parse import urlsplit

from sqlalchemy import text

from ruqqus.helpers.i18n_runtime import tr

KINDS = ("bug", "design", "idea", "other")
STATUSES = ("open", "seen", "fixed", "declined")
CLOSED = ("fixed", "declined")
MESSAGE_MAX = 2000
RESPONSE_MAX = 1000
PAGE_MAX = 300
BROWSER_MAX = 200
DAILY_LIMIT = 10
THEMES = ("light", "dark")
DENSITIES = ("comfortable", "compact")
_CONTROL = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]")
_LANG = re.compile(r"^[A-Za-z]{2,3}(?:[-_][A-Za-z0-9]{2,8})?$")
_PATH = re.compile(r"^/[^\s\\\x00-\x1f\x7f]*$")


def _clean(raw, limit):
    """Plain text with its line breaks: control characters dropped, runs of blank lines folded, at most `limit`."""
    value = _CONTROL.sub("", str(raw or "")).replace("\r\n", "\n").replace("\r", "\n")
    value = re.sub(r"[ \t]+\n", "\n", value)
    value = re.sub(r"\n{3,}", "\n\n", value).strip()
    return value[:limit]


def parse_kind(raw):
    """(kind, error)."""
    kind = str(raw or "").strip().lower()
    if kind not in KINDS:
        return "", tr("Choose what kind of feedback this is.")
    return kind, None


def parse_message(raw):
    """(message, error): plain text, line breaks kept."""
    message = _clean(raw, MESSAGE_MAX)
    if not message:
        return "", tr("Write what you want to tell us.")
    return message, None


def parse_response(raw):
    """An admin's response to the sender: plain text, may be empty."""
    return _clean(raw, RESPONSE_MAX)


def parse_status(raw):
    """The status an admin chose, or None."""
    status = str(raw or "").strip().lower()
    return status if status in STATUSES else None


def _page_path(raw):
    """The path of the page the member was on, nothing after it: a query string or a #fragment can carry a token. Only
    a path on this site: an address with a host (or "//host", or "/\\host", which browsers read the same way) is not
    one, since the admins' page draws it as a link."""
    raw = str(raw or "")
    if not raw.startswith("/") or raw.startswith(("//", "/\\")):
        return ""
    try:
        path = urlsplit(raw).path
    except ValueError:
        return ""
    return path[:PAGE_MAX] if _PATH.match(path) else ""


def _whole(value, low, high):
    try:
        number = int(value)
    except (TypeError, ValueError):
        return None
    return number if low <= number <= high else None


def parse_context(raw, browser=""):
    """(page, context): the page's path, and the JSON text of the facts kept with the feedback. `raw` is what the page
    sent (a JSON object as text, or already a dict); only the keys named here are read. `browser` is the request's own
    User-Agent."""
    try:
        data = json.loads(raw) if isinstance(raw, (str, bytes)) else (raw or {})
    except ValueError:
        data = {}
    if not isinstance(data, dict):
        data = {}
    page = _page_path(data.get("path"))
    context = {}
    if page:
        context["path"] = page
    if data.get("theme") in THEMES:
        context["theme"] = data["theme"]
    if data.get("density") in DENSITIES:
        context["density"] = data["density"]
    for key in ("width", "height"):
        number = _whole(data.get(key), 1, 20000)
        if number is not None:
            context[key] = number
    lang = str(data.get("lang") or "")
    if _LANG.match(lang):
        context["lang"] = lang
    browser = _CONTROL.sub("", str(browser or "")).strip()[:BROWSER_MAX]
    if browser:
        context["browser"] = browser
    return page, json.dumps(context, sort_keys=True)


def context_of(stored):
    """The saved context as a dict (empty when it can't be read)."""
    try:
        data = json.loads(stored or "{}")
    except ValueError:
        return {}
    return data if isinstance(data, dict) else {}


def _now(now):
    return int(time.time()) if now is None else int(now)


def filed_today(db, user_id, now=None):
    return db.execute(text("SELECT COUNT(*) FROM site_feedback WHERE user_id = :u AND created_utc > :since"),
                      {"u": user_id, "since": _now(now) - 86400}).scalar() or 0


def add(db, user_id, kind, message, page, context, now=None):
    """File it. The new item's id."""
    return db.execute(text("INSERT INTO site_feedback (user_id, kind, message, page, context, status, response, "
                           "created_utc, resolved_utc) VALUES (:u, :k, :m, :p, :c, 'open', '', :t, 0) RETURNING id"),
                      {"u": user_id, "k": kind, "m": message, "p": page, "c": context, "t": _now(now)}).scalar()


def attach_image(db, feedback_id, mime, data, now=None):
    db.execute(text("INSERT INTO site_feedback_images (feedback_id, mime, data, created_utc) "
                    "VALUES (:f, :m, :d, :t)"), {"f": feedback_id, "m": mime, "d": data, "t": _now(now)})


def image(db, feedback_id):
    """(mime, bytes) of an item's screenshot, or None."""
    row = db.execute(text("SELECT mime, data FROM site_feedback_images WHERE feedback_id = :f"),
                     {"f": feedback_id}).fetchone()
    return (row.mime, bytes(row.data)) if row else None


def _item(row, has_image):
    return {"id": row.id, "user_id": row.user_id, "username": row.username, "kind": row.kind, "message": row.message,
            "page": row.page, "context": context_of(row.context), "status": row.status, "response": row.response,
            "created_utc": row.created_utc, "resolved_utc": row.resolved_utc, "has_image": bool(has_image)}


_LIST = """
    SELECT f.id, f.user_id, u.username, f.kind, f.message, f.page, f.context, f.status, f.response, f.created_utc,
           f.resolved_utc, (SELECT COUNT(*) FROM site_feedback_images i WHERE i.feedback_id = f.id) AS has_image
    FROM site_feedback f LEFT JOIN users u ON u.id = f.user_id"""


def items(db, status=None, kind=None, limit=100):
    """[item] newest first, optionally of one status and/or one kind. `status` may be "waiting" (open or seen)."""
    where, params = [], {"l": limit}
    if status == "waiting":
        where.append("f.status IN ('open', 'seen')")
    elif status in STATUSES:
        where.append("f.status = :s")
        params["s"] = status
    if kind in KINDS:
        where.append("f.kind = :k")
        params["k"] = kind
    sql = _LIST + (" WHERE " + " AND ".join(where) if where else "") + " ORDER BY f.id DESC LIMIT :l"
    return [_item(row, row.has_image) for row in db.execute(text(sql), params).fetchall()]


def get(db, feedback_id):
    row = db.execute(text(_LIST + " WHERE f.id = :i"), {"i": feedback_id}).fetchone()
    return _item(row, row.has_image) if row else None


def waiting_count(db):
    """How many items still need a look (the count beside Admin > Feedback)."""
    return db.execute(text("SELECT COUNT(*) FROM site_feedback WHERE status IN ('open', 'seen')")).scalar() or 0


def set_status(db, feedback_id, status, response="", now=None):
    """Mark an item. False when there is no such item. A closed item records when; one reopened forgets it. The
    response is kept only when one is given, so marking an item seen never wipes an earlier response."""
    resolved = _now(now) if status in CLOSED else 0
    sql = "UPDATE site_feedback SET status = :s, resolved_utc = :r" + (", response = :x" if response else "") + \
          " WHERE id = :i"
    params = {"s": status, "r": resolved, "i": feedback_id}
    if response:
        params["x"] = response
    return db.execute(text(sql), params).rowcount == 1
