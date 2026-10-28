"""The site's "What's happening" events in the database (plain `text()` SQL, tested on SQLite with the tables built from
migration 2026-12-03_site_events.sql). The rules are helpers/site_events.py; nothing here decides what a reader may see
except by handing rows to those rules.

- **One writer.** Admins (`create`, `update`, `set_state`, `hide`) and sources (`upsert_source`: the committed seed
  today, connectors later) all write through here, each with the word-filter rating of the text stamped on the row
  (`rate`: a function from text to 0-3, see `rating`).
- **A source never undoes an admin.** `upsert_source` updates only the columns a source owns (title, times, status,
  state, details, fetched/seen times). A row an admin edited (`locked_utc`) is only marked seen; `hidden_utc`,
  `priority`, `link_url` and `featured` are never touched by a source; a status only moves forward.
- **An admin edit is compare-and-swap** on `updated_utc` (`update` and `set_state` answer False when someone else
  changed the row first), so two admins, or an admin and a source, cannot silently overwrite each other.
- Dates are bound as ISO text (SQLite and Postgres both take it); times are Unix seconds.
"""
import json
import uuid

from sqlalchemy import text

from ruqqus.helpers import site_events as rules

COLUMNS = ("id, kind, title, title_lang, details, link_url, date_kind, starts_date, ends_date, starts_utc, ends_utc, "
           "expires_utc, show_from_utc, status, status_changed_utc, state, priority, featured, tentative, source, "
           "source_key, last_seen_utc, fetched_utc, locked_utc, poll_post_id, country, subdivision, word_severity, "
           "word_filter_version, is_sensitive, hidden_utc, hidden_by_id, created_by_id, updated_by_id, created_utc, updated_utc")

LIVE_LOOKBACK = 3 * 86400          # a finished row older than this is not even read for the Live section
CALENDAR_CAP = 500                 # rows a calendar span returns at most (the page says so when it is cut)
LIVE_CAP = 200

# The token an admin's edit is compared on is `updated_utc`, in whole seconds: two edits in the same second would both pass
# if the stamp stayed put, so every write moves it by at least one.
_STAMP = "CASE WHEN :now > updated_utc THEN :now ELSE updated_utc + 1 END"

# the order a status can only move along when a source writes (postponed and cancelled may happen at any time)
_FORWARD = {"scheduled": 0, "live": 1, "final": 2, "ended": 3}


def rating(db):
    """(rate, version): the word filter's severity function and the version stamped beside a rating."""
    from ruqqus.helpers.word_filter_store import get_filter
    f = get_filter(db)
    return f.severity, f.version


def _iso(value):
    d = rules.as_date(value)
    return d.isoformat() if d else None


def _row(r):
    return dict(r._mapping) if r is not None else None


def _text_of(fields):
    """Everything a reader sees, rated together."""
    state = fields.get("state") or {}
    bits = [fields.get("title") or "", fields.get("details") or ""]
    bits += [str(v) for v in state.values() if isinstance(v, str)]
    return " ".join(b for b in bits if b)


def _params(fields, now):
    state = fields.get("state") or {}
    return {
        "kind": fields["kind"], "title": fields["title"], "title_lang": fields.get("title_lang") or "",
        "details": fields.get("details") or "", "link_url": fields.get("link_url") or "",
        "date_kind": fields["date_kind"], "starts_date": _iso(fields.get("starts_date")),
        "ends_date": _iso(fields.get("ends_date")), "starts_utc": fields.get("starts_utc"),
        "ends_utc": fields.get("ends_utc"), "expires_utc": fields.get("expires_utc"),
        "show_from_utc": fields.get("show_from_utc"), "status": fields.get("status") or "scheduled",
        "state": json.dumps(state, sort_keys=True, separators=(",", ":")) if isinstance(state, dict) else str(state),
        "priority": int(fields.get("priority") or 0), "featured": bool(fields.get("featured")),
        "tentative": bool(fields.get("tentative")), "poll_post_id": fields.get("poll_post_id"),
        "is_sensitive": bool(fields.get("is_sensitive")), "now": int(now),
    }


# --- reading -----------------------------------------------------------------------------------------------------------

def get(db, event_id):
    return _row(db.execute(text(f"SELECT {COLUMNS} FROM site_events WHERE id = :i"), {"i": int(event_id)}).fetchone())


def live_candidates(db, now):
    """Rows the Live section might show (kinds that go there, not hidden, not ended long ago); the rules choose."""
    kinds = ", ".join(f"'{k}'" for k in rules.LIVE_KINDS)
    since = int(now) - LIVE_LOOKBACK
    since_day = rules.utc_date(since).isoformat()
    rows = db.execute(text(
        f"SELECT {COLUMNS} FROM site_events WHERE kind IN ({kinds}) AND hidden_utc = 0 "
        "AND status <> 'cancelled' AND (expires_utc IS NULL OR expires_utc > :now) "
        "AND ((date_kind = 'instant' AND COALESCE(ends_utc, starts_utc) >= :since) "
        "  OR (date_kind = 'civil' AND COALESCE(ends_date, starts_date) >= :since_day) "
        "  OR (status IN ('final', 'postponed') AND status_changed_utc >= :since) "     # settled a while after it closed
        "  OR expires_utc > :now) "                                                       # an alert or headline still unexpired
        "ORDER BY id DESC LIMIT :cap"), {"now": int(now), "since": since, "since_day": since_day, "cap": LIVE_CAP})
    return [dict(r._mapping) for r in rows]


def calendar_rows(db, first, last):
    """Calendar rows touching the Unix span [first, last): (rows, cut) with `cut` True when the cap trimmed them.
    The SQL reads a little wider than the span (an event with no end lasts up to its kind's default length) and the
    rules (`site_events.touches`) make the exact cut."""
    kinds = ", ".join(f"'{k}'" for k in rules.CALENDAR_KINDS)
    first_day, last_day = rules.utc_date(first).isoformat(), rules.utc_date(last - 1).isoformat()
    rows = db.execute(text(
        f"SELECT {COLUMNS} FROM site_events WHERE kind IN ({kinds}) AND hidden_utc = 0 AND status <> 'cancelled' "
        "AND ((date_kind = 'instant' AND starts_utc < :last AND COALESCE(ends_utc, starts_utc + 10800) >= :first) "
        "  OR (date_kind = 'civil' AND starts_date <= :last_day AND COALESCE(ends_date, starts_date) >= :first_day)) "
        "ORDER BY priority DESC, COALESCE(starts_utc, 0), id LIMIT :cap"),
        {"first": int(first), "last": int(last), "first_day": first_day, "last_day": last_day, "cap": CALENDAR_CAP + 1})
    out = [r for r in (dict(r._mapping) for r in rows) if rules.touches(r, int(first), int(last))]
    return out[:CALENDAR_CAP], len(out) > CALENDAR_CAP


def admin_list(db, now, limit=300):
    """Everything an admin manages, soonest first: current and coming, then recently past, then hidden."""
    rows = db.execute(text(
        f"SELECT {COLUMNS} FROM site_events WHERE source <> 'rules' "
        "AND (hidden_utc > 0 OR COALESCE(ends_utc, starts_utc, 0) >= :since OR COALESCE(ends_date, starts_date) >= :since_day) "
        "ORDER BY hidden_utc > 0, COALESCE(starts_utc, 0), COALESCE(starts_date, '9999-12-31'), id LIMIT :cap"),
        {"since": int(now) - 30 * 86400, "since_day": rules.utc_date(int(now) - 30 * 86400).isoformat(), "cap": limit})
    return [dict(r._mapping) for r in rows]


def log_of(db, event_id, limit=20):
    return [dict(r._mapping) for r in db.execute(text(
        "SELECT admin_id, action, detail, created_utc FROM site_event_log WHERE event_id = :e ORDER BY id DESC LIMIT :n"),
        {"e": int(event_id), "n": limit})]


def write_log(db, event_id, admin_id, action, detail, now):
    db.execute(text("INSERT INTO site_event_log (event_id, admin_id, action, detail, created_utc) "
                    "VALUES (:e, :a, :act, :d, :t)"),
               {"e": int(event_id), "a": admin_id, "act": action[:24], "d": (detail or "")[:400], "t": int(now)})


# --- an admin writing --------------------------------------------------------------------------------------------------

def create(db, fields, admin_id, now, rate, version=""):
    """Add an event by hand. Returns its id. `fields` come from rules.clean_event."""
    p = _params(fields, now)
    p.update({"severity": rate(_text_of(fields)), "version": version, "admin": admin_id,
              "key": f"admin:{uuid.uuid4().hex}"})
    result = db.execute(text(
        "INSERT INTO site_events (kind, title, title_lang, details, link_url, date_kind, starts_date, ends_date, "
        "starts_utc, ends_utc, expires_utc, show_from_utc, status, status_changed_utc, state, priority, featured, "
        "tentative, source, source_key, poll_post_id, word_severity, word_filter_version, is_sensitive, "
        "created_by_id, updated_by_id, created_utc, updated_utc) VALUES (:kind, :title, :title_lang, :details, "
        ":link_url, :date_kind, :starts_date, :ends_date, :starts_utc, :ends_utc, :expires_utc, :show_from_utc, "
        ":status, :now, :state, :priority, :featured, :tentative, 'admin', :key, :poll_post_id, :severity, :version, "
        ":is_sensitive, :admin, :admin, :now, :now) RETURNING id"), p)
    event_id = result.scalar()
    write_log(db, event_id, admin_id, "create", f"{fields['kind']}: {fields['title']}", now)
    return event_id


def update(db, event_id, fields, admin_id, now, expected_updated, rate, version=""):
    """Change an event by hand; False when someone changed it since `expected_updated` (nothing is written)."""
    before = get(db, event_id)
    if before is None:
        return False
    p = _params(fields, now)
    p.update({"id": int(event_id), "expected": int(expected_updated), "admin": admin_id,
              "severity": rate(_text_of(fields)), "version": version,
              "changed": int(now) if fields.get("status", before["status"]) != before["status"] else before["status_changed_utc"]})
    done = db.execute(text(
        "UPDATE site_events SET kind = :kind, title = :title, title_lang = :title_lang, details = :details, "
        "link_url = :link_url, date_kind = :date_kind, starts_date = :starts_date, ends_date = :ends_date, "
        "starts_utc = :starts_utc, ends_utc = :ends_utc, expires_utc = :expires_utc, show_from_utc = :show_from_utc, "
        "status = :status, status_changed_utc = :changed, state = :state, priority = :priority, featured = :featured, "
        "tentative = :tentative, poll_post_id = :poll_post_id, word_severity = :severity, word_filter_version = :version, "
        "is_sensitive = :is_sensitive, locked_utc = CASE WHEN source = 'admin' THEN locked_utc ELSE :now END, "
        "updated_by_id = :admin, updated_utc = " + _STAMP + " WHERE id = :id AND updated_utc = :expected"), p).rowcount
    if done:
        write_log(db, event_id, admin_id, "update", f"{fields['kind']}: {fields['title']}", now)
    return bool(done)


def set_state(db, event_id, status, state, admin_id, now, expected_updated, rate, version=""):
    """The quick edit for a live match or a resolved prediction: its status and its state object only."""
    if status not in rules.STATUSES:
        raise rules.Refused("Choose a status.")
    before = get(db, event_id)
    if before is None:
        return False
    merged = rules.state_of(before)
    for key, value in (state or {}).items():            # None: the field was sent empty, so the key goes
        if value is None:
            merged.pop(key, None)
        else:
            merged[key] = value
    fields = dict(before, state=merged)
    done = db.execute(text(
        "UPDATE site_events SET status = :status, status_changed_utc = CASE WHEN status <> :status THEN :now ELSE status_changed_utc END, "
        "state = :state, word_severity = :severity, word_filter_version = :version, "
        "locked_utc = CASE WHEN source = 'admin' THEN locked_utc ELSE :now END, updated_by_id = :admin, updated_utc = " + _STAMP + " "
        "WHERE id = :id AND updated_utc = :expected"),
        {"status": status, "now": int(now), "state": json.dumps(merged, sort_keys=True, separators=(",", ":")),
         "severity": rate(_text_of(fields)), "version": version, "admin": admin_id, "id": int(event_id),
         "expected": int(expected_updated)}).rowcount
    if done:
        write_log(db, event_id, admin_id, "state", f"{status} {json.dumps(state or {}, sort_keys=True)[:300]}", now)
    return bool(done)


def hide(db, event_id, admin_id, now):
    done = db.execute(text("UPDATE site_events SET hidden_utc = :now, hidden_by_id = :a, updated_by_id = :a, updated_utc = " + _STAMP + " "
                           "WHERE id = :i AND hidden_utc = 0"), {"now": int(now), "a": admin_id, "i": int(event_id)}).rowcount
    if done:
        write_log(db, event_id, admin_id, "hide", "", now)
    return bool(done)


def unhide(db, event_id, admin_id, now):
    done = db.execute(text("UPDATE site_events SET hidden_utc = 0, hidden_by_id = NULL, updated_by_id = :a, updated_utc = " + _STAMP + " "
                           "WHERE id = :i AND hidden_utc > 0"), {"now": int(now), "a": admin_id, "i": int(event_id)}).rowcount
    if done:
        write_log(db, event_id, admin_id, "unhide", "", now)
    return bool(done)


# --- a source writing (the committed seed now, connectors later) ----------------------------------------------------------

def upsert_source(db, source, source_key, fields, now, rate, version=""):
    """Insert or refresh a row a SOURCE owns (`source` is never 'admin'). Returns 'inserted', 'updated' or 'kept'.

    Only source-owned columns are written on a conflict: title, details, times (and when to start showing it), status
    (forward only, postponed and cancelled any time), state, tentative, fetched and seen times. Never `hidden_utc`, `priority`, `link_url`,
    `featured`, `is_sensitive` or a row an admin edited (`locked_utc`), which is only marked seen."""
    if source in ("admin", ""):
        raise ValueError("a source writes under its own name")
    p = _params(fields, now)
    p.update({"source": source, "key": source_key, "severity": rate(_text_of(fields)), "version": version})
    inserted = db.execute(text(
        "INSERT INTO site_events (kind, title, title_lang, details, link_url, date_kind, starts_date, ends_date, "
        "starts_utc, ends_utc, expires_utc, show_from_utc, status, status_changed_utc, state, priority, featured, "
        "tentative, source, source_key, last_seen_utc, fetched_utc, poll_post_id, word_severity, "
        "word_filter_version, is_sensitive, created_utc, updated_utc) VALUES (:kind, :title, :title_lang, :details, "
        ":link_url, :date_kind, :starts_date, :ends_date, :starts_utc, :ends_utc, :expires_utc, :show_from_utc, "
        ":status, :now, :state, :priority, :featured, :tentative, :source, :key, :now, :now, :poll_post_id, "
        ":severity, :version, :is_sensitive, :now, :now) ON CONFLICT (source, source_key) DO NOTHING"), p).rowcount
    if inserted:
        return "inserted"
    existing = db.execute(text("SELECT id, status, locked_utc FROM site_events WHERE source = :s AND source_key = :k"),
                          {"s": source, "k": source_key}).fetchone()
    event_id, stored_status, locked = existing
    if locked:
        db.execute(text("UPDATE site_events SET last_seen_utc = :now WHERE id = :i"), {"now": int(now), "i": event_id})
        return "kept"
    new_status = p["status"]
    if new_status not in ("postponed", "cancelled") and stored_status not in ("postponed", "cancelled") \
            and _FORWARD.get(new_status, 0) < _FORWARD.get(stored_status, 0):
        new_status = stored_status
    p.update({"id": event_id, "status": new_status, "changed_flag": 1 if new_status != stored_status else 0})
    db.execute(text(
        "UPDATE site_events SET title = :title, details = :details, title_lang = :title_lang, date_kind = :date_kind, "
        "starts_date = :starts_date, ends_date = :ends_date, starts_utc = :starts_utc, ends_utc = :ends_utc, "
        "expires_utc = :expires_utc, show_from_utc = :show_from_utc, poll_post_id = :poll_post_id, status = :status, "
        "status_changed_utc = CASE WHEN :changed_flag = 1 THEN :now ELSE status_changed_utc END, state = :state, "
        "tentative = :tentative, word_severity = :severity, word_filter_version = :version, last_seen_utc = :now, "
        "fetched_utc = :now, updated_utc = :now WHERE id = :id"), p)
    return "updated"


# --- upkeep (old events go by helpers/retention.py) -------------------------------------------------------------------------

def rescan_severities(db, rate, version=""):
    """Rate every row again (the word list changed): word_filter_store.rescan calls this."""
    rows = db.execute(text(f"SELECT {COLUMNS} FROM site_events")).fetchall()
    changed = 0
    for r in rows:
        row = dict(r._mapping)
        row["state"] = rules.state_of(row)
        severity = rate(_text_of(row))
        if severity != (row["word_severity"] or 0):
            changed += 1
        db.execute(text("UPDATE site_events SET word_severity = :s, word_filter_version = :v WHERE id = :i"),
                   {"s": severity, "v": version, "i": row["id"]})
    return changed
