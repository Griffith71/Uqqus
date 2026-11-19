"""How the staff and the person look at an account an automatic check put on hold (helpers/auto_quarantine.py, which sets and clears the hold): the staff queue at
`/admin/quarantine`, the person's own appeal page at `/appeal/quarantine`, and what an admin's release does to an appeal still waiting. The routes are
`routes/quarantine_pages.py`; the rules of appeals are helpers/log_appeals.py.

**What the person is shown** (`shown_to_person`): the generic wording the automatic check gave (never what it matched, never the trigger's own name), the time, and the
rule it cites **unless the check is ban-evasion detection or the account is an alt held beside another**: those name no rule in the notice either (a rule named to an
alt, or for evasion, would teach how alts are caught). **What the staff are shown** (`check_line`): the trigger, its detail and, for an alt, the main account.

**A release is final for its appeals** (`close_appeals`): an admin who lifts a hold from the queue closes any appeal of it still waiting as reversed ("Lifted by an
admin."), so the queue and the person's page agree; the person is told the hold was lifted, once, by the release itself.

**So is a permanent ban** (`close_appeals_for_ban`): an admin who bans an account the site holds decided it, and a later reversal of the hold's appeal would "release"
the hold of an account that stays banned, so the appeal waiting for the hold is closed as dismissed by that admin ("Closed when an admin suspended this account...").
The person is told once, by the ban's own notice. A suspension for a time closes nothing: the hold outlives it, and so does the appeal of it. The queue never lists a
banned account (`auto_quarantine._WAITING`); the hold itself is left as it is, so an unban brings the account back to the queue.

**A release lifts the hold the admin was shown, never another** (`parse_entry`, `is_stale`, `lock_hold`): the queue's form posts the id of the staff entry it drew, and the
route compares it with the account's current one under a row lock (Postgres), so an admin who opens a row, waits while another admin lifts the hold and a new check holds
the account again, is told it changed instead of lifting a hold they never read (and settling an appeal of it as reversed).

**A legal-level hold is not in a level-3 admin's queue or count at all** (`queue`, `queue_count`; the owner's rule for the appeals queue, second review C12): a masked row
that names the account would tell level 3 which account tripped the check about minors, which is what the legal audience keeps from them. The route still answers a level-3
release of one as it answers a hold that isn't there. Plain `text()` SQL, tested on SQLite (tests/test_quarantine_queue.py)."""
import logging
import time

from sqlalchemy import text

from ruqqus.helpers import action_log, auto_quarantine, log_appeals

_log = logging.getLogger(__name__)

LIFTED_BY_ADMIN = "Lifted by an admin."
CLOSED_BY_BAN = "Closed when an admin suspended this account: the suspension has its own appeal."

# what the staff call each automatic check (English: the admin pages are)
TRIGGER_LABELS = {
    "spam": "Spam guard (similar posts or comments, a bad link)",
    "seo_spam": "SEO-spam check on a bio or profile",
    "link_malicious": "Banned link: digitally malicious",
    "link_illegal": "Banned link: illegal",
    "link_minors": "Banned link: about minors (legal: level 4)",
    "evasion": "Ban-evasion detection",
    "image": "Image check",
}


def parse_note(staff_note):
    """The staff entry's note ("spam | six similar posts in a minute", or "spam | alt of @name") as `check_line` reads it: {trigger, detail, alt_of}."""
    note = staff_note or ""
    detail = auto_quarantine.detail_of(note)
    return {"trigger": auto_quarantine.trigger_of(note), "detail": detail,
            "alt_of": detail.split("alt of @", 1)[1].strip() if "alt of @" in detail else ""}


def check_line(entry):
    """The staff's one line about what put the account on hold: "Spam guard (...): six similar posts in a minute", with "an alt of @name" for an alt held beside another.
    `entry` is what `auto_quarantine.current_entry` or one row of `waiting` gives (a dict with `trigger`, `detail`, `alt_of`)."""
    label = TRIGGER_LABELS.get(entry.get("trigger") or "", "An automatic check")
    detail = entry.get("detail") or ""
    alt = entry.get("alt_of") or ""
    if alt:
        return f"{label}: an alt of @{alt}"
    return f"{label}: {detail}" if detail else label


def shown_to_person(entry):
    """What the page that asks for a review says the automatic check did, to the account it held: {rule_key, reason, created_utc}. The rule is left out for ban-evasion
    detection and for an alt (see the module's docstring), and then the reason is the plainest generic wording, never the trigger's."""
    trigger = entry.get("trigger") or ""
    spec = auto_quarantine.TRIGGERS.get(trigger)
    plain = not spec or not spec.say_rule or bool(entry.get("alt_of"))
    return {"rule_key": "" if plain else (entry.get("rule_key") or ""),
            "reason": action_log.AUTOMATIC_REASON if plain else (entry.get("reason") or action_log.AUTOMATIC_REASON),
            "created_utc": entry.get("created_utc") or 0}


def release_refusal(audience, level):
    """None when an admin of `level` may lift this hold, else a short reason. A hold whose entry is legal-level (a link about minors) is a level-4 admin's: the
    queue leaves it out for level 3, and the route answers a level-3 release of it as it answers a hold that isn't there."""
    if (audience or 0) >= action_log.LEGAL and (level or 0) < 4:
        return "That hold is a level 4 admin's to decide."
    return None


# --- the staff queue, as an admin of a given level may read it ------------------------------------------------------------------------------------

# the id of the staff entry of an account's current hold (the same rule as `auto_quarantine.current_entry` and `waiting`); `u` is the queue's users row
_CURRENT_ENTRY_ID = ("(SELECT MAX(x.id) FROM log_entries x WHERE x.target_user_id = u.id AND x.action = 'quarantine_user' AND NOT EXISTS "
                     "(SELECT 1 FROM log_entries l WHERE l.linked_entry_id = x.id AND l.action = 'unquarantine_user'))")
_ABOVE_LEVEL = f" AND NOT EXISTS (SELECT 1 FROM log_entries z WHERE z.id = {_CURRENT_ENTRY_ID} AND z.audience > :readable)"
LEVEL_4 = 4


def reader_level(level):
    """The staff level a queue is drawn for: never below 3 (the queue is the admins'), so an unknown or missing level reads as the narrowest admin, who sees no legal hold."""
    try:
        return max(3, int(level or 0))
    except (TypeError, ValueError):
        return 3


def _audience_clause(level):
    """(SQL, params) that leave out the holds whose entry this level may not read: none for level 4, the legal-level ones below it."""
    if reader_level(level) >= LEVEL_4:
        return "", {}
    return _ABOVE_LEVEL, {"readable": action_log.STAFF}


def queue(db, page=1, level=3, now=None):
    """The staff queue for an admin of `level`: accounts held by an automatic check, newest first, `auto_quarantine.PAGE` a page: ([row...], more). A row has user_id,
    username, created_utc, since, entry_id, rule_key, reason, trigger, detail, alt_of (the main account's name for an alt), audience, legal (the hold's entry is
    legal-level: only a level-4 reader is ever given one) and appeal ('' when none, else the latest appeal's status). **A hold whose entry is above the reader's level is
    not in it at all**, so neither the account's name nor its appeal says anything of it to level 3. An account banned now has been decided and is not listed."""
    now = int(now if now is not None else time.time())
    clause, params = _audience_clause(level)
    page = max(1, int(page))
    found = db.execute(text(
        "SELECT u.id AS user_id, u.username, u.created_utc, u.quarantined_utc AS since " + auto_quarantine._WAITING + clause +
        " ORDER BY u.quarantined_utc DESC, u.id DESC LIMIT :n OFFSET :o"),
        {"n": auto_quarantine.PAGE + 1, "o": auto_quarantine.PAGE * (page - 1), "now": now, **params}).mappings().all()
    out = []
    for base in found[:auto_quarantine.PAGE]:
        item = {**dict(base), "entry_id": None, "rule_key": "", "reason": "", "trigger": "", "detail": "", "alt_of": "", "audience": 0, "legal": False, "appeal": ""}
        entry = auto_quarantine.current_entry(db, item["user_id"])
        if entry is not None:
            item.update(entry_id=entry["id"], rule_key=entry["rule_key"], reason=entry["reason"], audience=entry["audience"], trigger=entry["trigger"],
                        detail=entry["detail"], alt_of=entry["alt_of"], legal=entry["audience"] >= action_log.LEGAL)
            appeal = db.execute(text("SELECT status FROM log_appeals WHERE entry_id = :e ORDER BY id DESC LIMIT 1"), {"e": entry["id"]}).first()
            item["appeal"] = appeal[0] if appeal else ""
        out.append(item)
    return out, len(found) > auto_quarantine.PAGE


def queue_count(db, level=3, now=None):
    """How many accounts wait for an admin of `level` to decide: the number on /admin and on the queue's own page. It counts what `queue` lists, so a legal-level hold is
    in level 4's number and not in level 3's."""
    clause, params = _audience_clause(level)
    return db.execute(text("SELECT count(*) " + auto_quarantine._WAITING + clause),
                      {"now": int(now if now is not None else time.time()), **params}).scalar() or 0


# --- a release is of the hold the admin was shown -------------------------------------------------------------------------------------------------

def parse_entry(raw):
    """The staff entry id the queue's form posted (ASCII digits only), or None when it sent none or something else: a form that names no hold lifts none that has one."""
    value = (raw or "").strip() if isinstance(raw, str) else ""
    return int(value) if value.isascii() and value.isdigit() and len(value) <= 12 else None


def is_stale(shown, current):
    """True when the hold the admin was shown (`shown`: the entry id the form posted, 0 for a row that had no entry) is not the account's current one (`current`:
    `auto_quarantine.current_entry`, or None). A form that named nothing is stale: it is never taken for a hold that has an entry."""
    return shown is None or shown != (int(current["id"]) if current else 0)


def lock_hold(db, user_id):
    """Lock the account's row (Postgres: `FOR UPDATE`, so a check that holds the account again waits for the release to commit instead of landing between the entry read
    and the lift) and say whether an automatic check holds it now."""
    suffix = " FOR UPDATE" if db.get_bind().dialect.name == "postgresql" else ""
    row = db.execute(text("SELECT quarantine_kind, quarantined_utc FROM users WHERE id = :u" + suffix), {"u": int(user_id)}).first()
    return bool(row and row[0] == 1 and (row[1] or 0) > 0)


def _settle_waiting(db, entry_id, outcome, admin_id, note, reversal_entry_id=None):
    """Settle every appeal still waiting for this quarantine entry with `outcome`, by `admin_id`, once each. Never tells the person. Returns how many were closed."""
    ids = [r[0] for r in db.execute(text("SELECT id FROM log_appeals WHERE entry_id = :e AND status = 'open' ORDER BY id"), {"e": int(entry_id)})]
    closed = 0
    for appeal_id in ids:
        closed += bool(log_appeals.decide(db, appeal_id, outcome, admin_id, note, reversal_entry_id=reversal_entry_id or None))
    return closed


def close_appeals(db, entry_id, admin_id, reversal_entry_id=None):
    """Settle every appeal still waiting for this quarantine entry as reversed, by `admin_id`, once each: the hold it asked to have lifted is lifted. Never tells the
    person (the release's own notice does). Returns how many were closed."""
    return _settle_waiting(db, entry_id, log_appeals.REVERSED, admin_id, LIFTED_BY_ADMIN, reversal_entry_id)


def close_appeals_for_ban(db, entry_id, admin_id):
    """Settle every appeal still waiting for this quarantine entry as dismissed by `admin_id`, who has banned the account for good: the ban is the decision, and the
    hold is not being lifted, so the appeal must not be reversible into a release of a banned account. Never tells the person (the ban's own notice does). The hold
    itself is not touched. Returns how many were closed."""
    return _settle_waiting(db, entry_id, log_appeals.DISMISSED, admin_id, CLOSED_BY_BAN)


def close_hold_appeal_for_ban(db, user_id, admin_id):
    """An account an automatic check holds is banned for good: close the review its appeal waits for (`close_appeals_for_ban`), or a later reversal of that appeal would
    release the hold of an account that stays banned. **The one place every permanent ban asks it** (`User.ban`, which the admin's ban, an alt's, the signup queue's and
    the site's own go through, and the CSAM route's hand ban, which doesn't). Never tells the person (the ban's own notice does) and leaves the hold as it is. Best
    effort in a savepoint: a database without the tables still bans. Returns how many appeals were closed."""
    try:
        with db.begin_nested():
            held = auto_quarantine.current_entry(db, user_id)
            return close_appeals_for_ban(db, held["id"], admin_id) if held is not None else 0
    except Exception:
        _log.exception("a banned account's hold appeal could not be closed")
        return 0


def appeal_of(db, entry_id, user_id):
    """The appeal this account made of this quarantine entry ({status, decided_utc, decision_note}), or None."""
    row = db.execute(text("SELECT status, decided_utc, decision_note FROM log_appeals WHERE entry_id = :e AND user_id = :u"),
                     {"e": int(entry_id), "u": int(user_id)}).first()
    return dict(row._mapping) if row else None
