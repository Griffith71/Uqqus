"""What the site's own checks do to an account: they QUARANTINE it, they never ban or suspend it (the owner, 2026-11-17 and 18).

The spam guard, a banned or malicious link, the SEO-spam check on a profile, ban-evasion detection and the image checks used to suspend
or ban an account on a guess. They now put it in quarantine, the hold the signup guard already has (`users.quarantined_utc`,
helpers/quarantine.py, `@not_quarantined` on every route that acts on others): the account can read the site and change its own settings,
and nothing else, until **an admin** looks at it. A real person then decides: release it, or ban it through the existing admin ban route
(whose second-admin approval of a reveal of an anonymous poster is unchanged). Admin-made bans, the CSAM route's hand ban, a parent's
cascade onto sub-accounts and the signup guard's own admin ban are not automatic checks and stay what they were.

**This module is the ONLY place an automatic quarantine is set or cleared** (`tests/test_auto_quarantine.py` fails on another write of
`quarantine_kind`). `users.quarantine_kind` is 0 for the signup guard's hold (or no hold, while `quarantined_utc` is 0) and 1 for an automatic
check's; **a kind 1 hold is released only by an admin (`release`, the staff queue) or by a successful appeal, never by verifying an email and never by
time** (`signup_review_store` and `mail.py` never clear a kind 1 hold). A signup-guard hold that then trips an automatic check becomes kind 1.

**What the Log says** (the owner's option B, decision 3):
  * a STAFF-ONLY entry names the account (`quarantine_user`, `target_user_id`), the trigger and the detail (`staff_note`: "<trigger> | <detail>"): it is what
    the staff queue and the appeal point at (`current_entry`). Its audience is the trigger's floor (staff; level 4 for a link about minors);
  * for each quarantine of a MAIN account (not an alt) except ban-evasion detection, ONE public entry (`automatic_quarantine`, role system) that names
    no account, no item and no guild: the rule, a generic reason ("Automatic spam check"...) and the time, **rounded down to the hour** (`public_time`: the removals the same
    request made carry their exact second, so an exact one would pair the two lines and name the post's author). Its `linked_entry_id` is the staff entry (a column
    only staff read); the staff's own entries keep the exact time. Ban-evasion stays staff-only: a public line would teach how alts are caught. An alt's quarantine
    is a staff-only entry ("alt of @name"), with no public line and no notice;
  * lifting one writes a NEW staff entry (`unquarantine_user`, linked to the quarantine's: the original is never edited) and, when the quarantine had a
    public line, a public `automatic_quarantine_lifted`, again naming nobody and again dated to the hour.

**The notice** to the person (helpers/log_notices.py `tell_quarantined`, `tell_quarantine_lifted`) is sent AFTER the caller's commit, best effort: inside a request
it is appended to `g.after_commit` (which `__main__.after_request` spawns once the commit went through); outside one (an image scan's greenlet) the caller
calls `send_pending(db)` after its own commit. Nothing here commits. A notice is checked again when it goes (the account is still held, the entry still exists),
so a transaction that rolled back tells nobody.

**Quiet and idempotent**: a second automatic check on an account that is already quarantined by one (kind 1) writes nothing and tells nobody; admins,
bot accounts, deleted accounts and accounts already banned are never quarantined by the site's own checks. Every state change is ONE conditional UPDATE.
Plain `text()` SQL, tested on SQLite (tests/test_auto_quarantine.py)."""
import functools
import logging
import time
from typing import NamedTuple

from sqlalchemy import text

from ruqqus.helpers import action_log, log_notices

_log = logging.getLogger(__name__)

PAGE = 50
PUBLIC_STEP = 3600           # the public lines are dated to the hour they happened in: see `public_time`

_PENDING = "auto_quarantine_notices"            # Session.info: [(kind, user_id, entry_id, rule_key, reason)] waiting for the caller's commit
_SCHEDULED = "auto_quarantine_scheduled"


class Trigger(NamedTuple):
    key: str
    rule: str               # the posting rules' section the staff entry and the public line cite (action_log.RULES)
    reason: str             # the generic wording of the public line, the staff entry's reason and the notice: never what was matched
    public: bool            # whether a public line is written (ban-evasion detection: never, a line would teach how alts are caught)
    floor: int              # the narrowest audience of the staff entry (a link about minors is for level-4 admins)
    say_rule: bool          # whether the notice to the person names the rule (never for ban evasion)
    keep_detail: bool       # whether `detail` goes into the staff entry (never for an image check: the match is level 4's, in a linked `record_detail` entry)


TRIGGERS = {
    # the spam guard's post, comment and comment-edit checks, and a link that would have been an `autoban` bad link
    "spam": Trigger("spam", "evasion", action_log.AUTOMATIC_SPAM_REASON, True, action_log.STAFF, True, True),
    # the SEO-spam check on a bio or a profile (a link in it that earns a ban)
    "seo_spam": Trigger("seo_spam", "evasion", action_log.AUTOMATIC_SPAM_REASON, True, action_log.STAFF, True, True),
    # the banned-domain reasons (helpers/... `Domain.reason`): digitally malicious, illegal, and the one about minors, whose staff entry is level 4
    "link_malicious": Trigger("link_malicious", "illegal", action_log.AUTOMATIC_LINK_REASON, True, action_log.STAFF, True, True),
    "link_illegal": Trigger("link_illegal", "illegal", action_log.AUTOMATIC_LINK_REASON, True, action_log.STAFF, True, True),
    "link_minors": Trigger("link_minors", "illegal", action_log.AUTOMATIC_LINK_REASON, True, action_log.LEGAL, True, True),
    # ban-evasion detection (helpers/wrappers.py): staff only, no public line, the notice names no rule
    "evasion": Trigger("evasion", "evasion", action_log.AUTOMATIC_REASON, False, action_log.STAFF, False, True),
    # an image check (the CDN's list, the site's own banned pictures): what it matched goes in a separate linked entry (`action_log.record_detail`)
    "image": Trigger("image", "illegal", action_log.AUTOMATIC_IMAGE_REASON, True, action_log.STAFF, True, False),
}

# an account the site's own checks never quarantine: an admin, a bot account, a deleted one, one already banned or suspended
# (a suspension that has run out no longer counts: `User.is_suspended`)
_ELIGIBLE = ("NOT COALESCE(is_deleted, false) AND (COALESCE(is_banned, 0) = 0 OR (COALESCE(unban_utc, 0) > 0 AND unban_utc <= :now)) "
             "AND COALESCE(admin_level, 0) = 0 AND NOT COALESCE(is_bot, false)")
_NOT_AUTOMATIC = "NOT (quarantine_kind = 1 AND quarantined_utc > 0)"


def public_time(now):
    """The time a PUBLIC quarantine line carries: the start of the hour `now` falls in. The line names no account and no item, but it used to share its second
    with the removals the same request made ("removed post #abc (Automatic spam check)" beside "an account was put in quarantine (Automatic spam check)"), which let a
    reader pair the two and so name the post's author. An hour is a window several unrelated removals and holds fall into on a busy site and costs the Log nothing it
    needs: the staff's own entries keep the exact time, the order of lines is still (created_utc, id), and a hold and its lifting in one hour keep their order by id."""
    now = int(now)
    return now - now % PUBLIC_STEP



PUBLISH_DELAY = 3600         # a public line is written this long (at least) after the entry it is about: see `publish_due`
PUBLISH_BATCH = 200

# only the entries of a trigger that has a public line are read, in SQL: one that never has one (ban-evasion detection) would stay at the front of every run
_PUBLIC_NOTE = "(" + " OR ".join(f"e.staff_note = '{key}' OR e.staff_note LIKE '{key} |%'" for key, spec in TRIGGERS.items() if spec.public) + ")"
_DUE_HOLDS = ("SELECT e.id, e.created_utc, e.rule_key, e.reason, e.staff_note FROM log_entries e "
              f"WHERE e.action = 'quarantine_user' AND e.linked_entry_id IS NULL AND {_PUBLIC_NOTE} AND e.created_utc <= :cutoff "
              "AND NOT EXISTS (SELECT 1 FROM log_entries p WHERE p.action = 'automatic_quarantine' AND p.linked_entry_id = e.id) "
              "AND NOT EXISTS (SELECT 1 FROM log_entries l WHERE l.action = 'unquarantine_user' AND l.linked_entry_id = e.id) "
              "ORDER BY e.id LIMIT :n")
_DUE_LIFTS = ("SELECT l.id, l.created_utc FROM log_entries l "
              "WHERE l.action = 'unquarantine_user' AND l.created_utc <= :cutoff "
              "AND EXISTS (SELECT 1 FROM log_entries p WHERE p.action = 'automatic_quarantine' AND p.linked_entry_id = l.linked_entry_id) "
              "AND NOT EXISTS (SELECT 1 FROM log_entries x WHERE x.action = 'automatic_quarantine_lifted' AND x.linked_entry_id = l.id) "
              "ORDER BY l.id LIMIT :n")


def publish_due(db, now=None, delay=PUBLISH_DELAY):
    """Write the PUBLIC lines of holds and of their lifting, from the cycle of `scripts/recomputes.py`, **at least `delay` after the staff entry they are about**; returns
    how many were written. The line names no account, no item and no guild, and is dated to the hour (`public_time`); written at once it would also take the id next to
    the id of the removal the same check made in the same request, and the ids show in the Log's links, so a reader could still pair "removed post #abc (Automatic spam
    check)" with "put an account in quarantine". An hour later other entries sit between them. Which holds get a line: a hold of a main account (not an alt: those link
    to a main entry) by a trigger that is public (`TRIGGERS`, never ban-evasion detection), **that is still on hold: a hold lifted before its line was due never gets
    one, and so no lifting line either**. A lifting gets its line only when its hold has one, `delay` after the lifting. Each line is linked to its staff entry
    (`linked_entry_id`, a column only staff read), so a run again writes nothing twice. One worker runs the cycle. Never commits."""
    now = int(now if now is not None else time.time())
    cutoff, written = now - int(delay), 0
    for row in db.execute(text(_DUE_HOLDS), {"cutoff": cutoff, "n": PUBLISH_BATCH}).fetchall():
        spec = TRIGGERS.get(trigger_of(row.staff_note))
        if spec is None or not spec.public:
            continue
        with db.begin_nested():
            if action_log.record_quietly(db, "automatic_quarantine", action_log.SITE_ACCOUNT_ID, role=action_log.SYSTEM, rule=row.rule_key, reason=row.reason,
                                         linked_entry_id=row.id, created_utc=public_time(row.created_utc)):
                written += 1
    for row in db.execute(text(_DUE_LIFTS), {"cutoff": cutoff, "n": PUBLISH_BATCH}).fetchall():
        with db.begin_nested():
            if action_log.record_quietly(db, "automatic_quarantine_lifted", action_log.SITE_ACCOUNT_ID, role=action_log.SYSTEM, linked_entry_id=row.id,
                                         created_utc=public_time(row.created_utc)):
                written += 1
    return written

def trigger_of(staff_note):
    """The trigger key a staff entry's note starts with ("spam | the third post in a minute" -> "spam"), or ''."""
    head = (staff_note or "").split("|", 1)[0].strip()
    return head if head in TRIGGERS else ""


def detail_of(staff_note):
    """What follows the trigger in a staff entry's note."""
    return (staff_note or "").split("|", 1)[1].strip() if "|" in (staff_note or "") else ""


def _forget_loaded(db, user_ids):
    """The users a session already loaded (the viewer, an alt) read their hold again: the UPDATEs here are SQL, which the ORM can't see."""
    try:
        wanted = {int(i) for i in user_ids}
        for obj in list(db.identity_map.values()):
            if obj.__class__.__name__ == "User" and getattr(obj, "id", None) in wanted:
                db.expire(obj, ["quarantined_utc", "quarantine_kind"])
    except Exception:
        _log.exception("a loaded account's hold could not be read again")


def _cut_off_chat(db, user_id):
    """A held account has no chat (the owner, 2026-11-19): every chat device it holds is signed out of the chat server (`chat_signouts.queue`, in the caller's
    transaction; the Synapse calls run after the commit, in a request, or from `scripts/recomputes.py`'s cycle otherwise, retried for ever), so an open tab or an outside
    app stops at once rather than at its next renewal. An account with no chat queues nothing. The routes refuse it too (`@not_quarantined("chat")`), and
    the token route reads the hold again under its lock (`chat_tokens._refusal`). Best effort: a failure is logged, never lost the hold."""
    try:
        from ruqqus.helpers import chat_signouts
        chat_signouts.queue(db, user_id, "held", "all")
    except Exception:
        _log.exception("a held account's chat could not be signed out")


def cut_off_held_chats(db, limit=200):
    """Queue the chat sign-out of every held account that still has a live chat device, and return how many were queued (None when the chat tables are missing).
    A hold cuts chat off entirely (the owner, 2026-11-19), and `hold()` does it as it is applied; this is the backstop `scripts/recomputes.py` runs every cycle, for what
    `hold()` can't reach: an account that was already on hold when this shipped, **the signup guard's hold** (it is written in `signup_review_store`, never through
    `hold()`), a hold whose sign-out failed or was lost, and a device made in the moment between. Idempotent: `chat_signouts.queue` revokes the site rows it covers, so the
    next cycle finds nothing. Admins, bot accounts and deleted accounts are never held (`User.is_quarantined`) and are left out. Never commits (the caller does)."""
    try:
        with db.begin_nested():
            ids = [row[0] for row in db.execute(text(
                "SELECT DISTINCT u.id FROM users u JOIN chat_site_devices d ON d.user_id = u.id AND d.revoked_utc = 0 "
                "WHERE u.quarantined_utc > 0 AND u.admin_level = 0 AND NOT COALESCE(u.is_bot, false) AND NOT COALESCE(u.is_deleted, false) "
                "ORDER BY u.id LIMIT :n"), {"n": int(limit)}).all()]
    except Exception:
        _log.exception("the held accounts' chat devices could not be listed")
        return None
    for user_id in ids:
        _cut_off_chat(db, user_id)
    return len(ids)


def _sync(db, obj, now, held):
    """The object the caller passed reads its new state (a plain object in a test just takes the values)."""
    if hasattr(obj, "_sa_instance_state"):
        return
    try:
        obj.quarantined_utc = now if held else 0
        obj.quarantine_kind = 1 if held else 0
    except Exception:
        pass


def _put_on_hold(db, user_id, now):
    """ONE conditional UPDATE: held by an automatic check from `now`, unless the account is exempt or already held by one. True when it changed."""
    return db.execute(text(
        f"UPDATE users SET quarantined_utc = :now, quarantine_kind = 1 WHERE id = :u AND {_ELIGIBLE} AND {_NOT_AUTOMATIC}"),
        {"now": int(now), "u": int(user_id)}).rowcount > 0


# --- the notices: after the caller's commit ----------------------------------------------------------------------------------------------

def _queue(db, kind, user_id, entry_id=None, rule_key="", reason=""):
    items = db.info.setdefault(_PENDING, [])
    items.append((kind, int(user_id), int(entry_id) if entry_id else None, rule_key or "", reason or ""))
    _schedule(db, items)


def _schedule(db, items):
    """Inside a request, deliver once the request's changes are committed (`g.after_commit`, spawned by `__main__.after_request`): once per REQUEST (a worker
    may reuse one session for many requests, so the flag lives on `g`, not on the session). `items` is the session's own list, drained as it is sent."""
    from flask import current_app, g, has_request_context
    if not has_request_context() or g.get(_SCHEDULED):
        return
    setattr(g, _SCHEDULED, True)
    if not hasattr(g, "after_commit"):
        g.after_commit = []
    g.after_commit.append(functools.partial(deliver, current_app._get_current_object(), items))


def _send_all(db, items):
    """Send what is waiting, checking each against the database as it is now. Returns how many went."""
    sent = 0
    while items:
        kind, user_id, entry_id, rule_key, reason = items.pop(0)
        try:
            state = db.execute(text("SELECT quarantine_kind, quarantined_utc FROM users WHERE id = :u"), {"u": user_id}).first()
            held_now = bool(state and state[0] == 1 and state[1] > 0)
            if kind == "held":
                if not held_now or (entry_id and not db.execute(text("SELECT 1 FROM log_entries WHERE id = :e"), {"e": entry_id}).first()):
                    continue                       # rolled back, or put right since: nothing to tell
                sent += bool(log_notices.tell_quarantined(db, user_id, entry_id, rule_key=rule_key, reason=reason))
            elif kind == "lifted":
                if held_now:
                    continue                       # held again since: the new hold has its own notice
                sent += bool(log_notices.tell_lifted(db, user_id, log_notices.QUARANTINE_LIFTED))
        except Exception:
            _log.exception("a quarantine notice was not sent")
            try:
                db.rollback()
            except Exception:
                pass
    return sent


def deliver(app, items):
    """What `g.after_commit` runs in a greenlet with no app or request context: a context and a session of its own, then every waiting notice."""
    from flask import g

    from ruqqus.__main__ import db_session
    if not items:
        return 0
    with app.test_request_context():
        g.db = db_session()
        try:
            return _send_all(g.db, items)
        finally:
            db_session.remove()


def send_pending(db):
    """For a caller outside a request (an image scan's greenlet): send the notices `hold` and `release` queued on `db`, AFTER the caller committed it.
    Best effort; returns how many went. Inside a request this is not needed (and does no harm: a notice goes once)."""
    from flask import g, has_app_context
    items = db.info.get(_PENDING)
    if not items:
        return 0
    if has_app_context() and getattr(g, "db", None) is db:
        return _send_all(db, items)
    from ruqqus.__main__ import app
    with app.test_request_context():
        g.db = db
        return _send_all(db, items)


# --- putting an account on hold -------------------------------------------------------------------------------------------------------

def hold(db, user, trigger, *, detail="", alts=(), notify=True):
    """Quarantine `user` (an account object with `id` and `username`) because the automatic check `trigger` (a key of TRIGGERS) fired.

    Returns the STAFF entry's id when the account was put on hold, and **None when nothing changed**: the account is an admin, a bot, deleted, banned, or
    already held by an automatic check (then nothing at all is written and nobody is told, its alts included). If the account was put on hold but its entry
    could not be written (logged, counted for /admin), the hold stands and the answer is 0 (falsy, but not None).

    One conditional UPDATE, then the staff entry (audience: the trigger's floor), the public line (unless the trigger writes none), the notice, and each of `alts`
    (accounts or ids; only those eligible and not already held by an automatic check) the same way with a staff-only entry "alt of @name", no public line and no notice.
    **Never commits**: the caller's transaction. The notice (`notify`) is sent after that commit: see the module's docstring and `send_pending`. `detail` goes in
    the staff entry's note (never for an image check: pass nothing and call `action_log.record_detail` with the returned id)."""
    spec = TRIGGERS[trigger]
    now = int(time.time())
    if not _put_on_hold(db, user.id, now):
        return None
    _sync(db, user, now, True)
    _forget_loaded(db, [user.id])
    _cut_off_chat(db, user.id)
    note = spec.key + (" | " + action_log.clean_reason(detail) if detail and spec.keep_detail else "")
    entry = action_log.record_quietly(db, "quarantine_user", action_log.SITE_ACCOUNT_ID, role=action_log.SYSTEM, target_user_id=user.id,
                                      rule=spec.rule, reason=spec.reason, staff_note=note, floor=spec.floor, created_utc=now)
    # the public line (`automatic_quarantine`) is NOT written here: `publish_due` writes it from the five-minute cycle, an hour or more later (see there)
    for alt in alts or ():
        alt_id = getattr(alt, "id", alt)
        if not alt_id or int(alt_id) == int(user.id) or not _put_on_hold(db, alt_id, now):
            continue
        _sync(db, alt, now, True)
        _forget_loaded(db, [alt_id])
        _cut_off_chat(db, alt_id)
        action_log.record_quietly(db, "quarantine_user", action_log.SITE_ACCOUNT_ID, role=action_log.SYSTEM, target_user_id=int(alt_id), rule=spec.rule,
                                  reason=spec.reason, staff_note=f"{spec.key} | alt of @{getattr(user, 'username', '')}", floor=max(spec.floor, action_log.STAFF),
                                  linked_entry_id=entry, created_utc=now)
    if notify:
        _queue(db, "held", user.id, entry, spec.rule if spec.say_rule else "", spec.reason)
    return entry or 0


# --- the staff's reads ------------------------------------------------------------------------------------------------------------------

_CURRENT = ("SELECT e.id, e.created_utc, e.rule_key, e.reason, e.staff_note, e.audience, e.linked_entry_id FROM log_entries e "
            "JOIN users u ON u.id = e.target_user_id WHERE e.action = 'quarantine_user' AND e.target_user_id = :u "
            "AND u.quarantine_kind = 1 AND u.quarantined_utc > 0 "
            "AND NOT EXISTS (SELECT 1 FROM log_entries l WHERE l.linked_entry_id = e.id AND l.action = 'unquarantine_user') "
            "ORDER BY e.id DESC LIMIT 1")


def _shaped(row):
    entry = dict(row)
    entry["trigger"] = trigger_of(entry["staff_note"])
    entry["detail"] = detail_of(entry["staff_note"])
    note = entry["detail"]
    entry["alt_of"] = note.split("alt of @", 1)[1].strip() if "alt of @" in note else ""
    return entry


def current_entry(db, user_id):
    """The staff entry of this account's CURRENT automatic quarantine (a dict: id, created_utc, rule_key, reason, staff_note, audience, linked_entry_id, and the
    parsed `trigger`, `detail` and `alt_of`), or None: the account isn't held by an automatic check, or nothing was written for it. What the appeal page finds,
    server-side, for the signed-in account itself: an id from the browser is never trusted."""
    row = db.execute(text(_CURRENT), {"u": int(user_id)}).mappings().first()
    return _shaped(row) if row else None


def lifted_by(db, entry_id):
    """The id of the `unquarantine_user` entry that lifted the quarantine whose staff entry is `entry_id`, or None while it stands: how an appeal asks whether the decision is
    already put right (a moot appeal is not reversed twice)."""
    return db.execute(text("SELECT MIN(id) FROM log_entries WHERE action = 'unquarantine_user' AND linked_entry_id = :e"), {"e": int(entry_id)}).scalar() or None


# the accounts that wait for an admin: the one definition of "held by an automatic check and not decided", read by the staff queue and its count
# (`quarantine_review.queue`, `queue_count`, which also leave out what the reader's level may not see)
_WAITING = ("FROM users u WHERE u.quarantine_kind = 1 AND u.quarantined_utc > 0 AND NOT COALESCE(u.is_deleted, false) "
            "AND (COALESCE(u.is_banned, 0) = 0 OR (COALESCE(u.unban_utc, 0) > 0 AND u.unban_utc <= :now))")


# --- letting one go ------------------------------------------------------------------------------------------------------------------------

def release(db, user_id, *, admin_id=None, appeal_entry_id=None, note="", notify=True):
    """Lift an automatic quarantine: ONE conditional UPDATE (held by an automatic check: kind 1 and `quarantined_utc` > 0), then the NEW staff entry
    `unquarantine_user` (actor: the admin, or the site when `admin_id` is None), linked to the quarantine's entry (`appeal_entry_id` when the caller gives the entry
    an appeal was about, else the account's current one) and no more public than it; the public `automatic_quarantine_lifted` line (naming nobody) only if the
    quarantine had a public line; and the notice that it was lifted.

    **Returns False when nothing was lifted** (not held by an automatic check: a signup-guard hold is `signup_review_store.release`'s, and an email or an admin
    there can never lift THIS kind), else the new staff entry's id, or True if the hold was lifted but its entry could not be written. **A caller that is a successful
    appeal passes `appeal_entry_id`: the appeal's own outcome notice then tells the person, so no second 'lifted' notice is queued** (as every reversal in the Log).
    A quarantine from ban-evasion detection also resets the account's `ban_evade` count, or the next request would hold it again. **Never commits.**"""
    user_id = int(user_id)
    current = current_entry(db, user_id)
    if not db.execute(text("UPDATE users SET quarantined_utc = 0, quarantine_kind = 0 WHERE id = :u AND quarantine_kind = 1 AND quarantined_utc > 0"),
                      {"u": user_id}).rowcount:
        return False
    _forget_loaded(db, [user_id])
    now = int(time.time())
    try:
        with db.begin_nested():             # an undecided sign-up review of the account (it was held by the signup guard first) is closed too
            db.execute(text("UPDATE signup_reviews SET decision = 'approved', decided_utc = :now, decided_by = :a WHERE user_id = :u AND decision = ''"),
                       {"now": now, "a": int(admin_id or 0), "u": user_id})
    except Exception:
        pass
    if current is not None and current["trigger"] == "evasion":
        try:
            with db.begin_nested():
                db.execute(text("UPDATE users SET ban_evade = 0 WHERE id = :u"), {"u": user_id})
        except Exception:
            _log.exception("ban_evade could not be reset")
    linked = int(appeal_entry_id) if appeal_entry_id else (current["id"] if current else None)
    clean = action_log.clean_reason(note)
    entry = action_log.record_quietly(
        db, "unquarantine_user", admin_id or action_log.SITE_ACCOUNT_ID, role=action_log.ADMIN if admin_id else action_log.SYSTEM,
        target_user_id=user_id, reason=clean, staff_note="lifted after an appeal" if appeal_entry_id else "lifted by an admin",
        floor=max(action_log.STAFF, current["audience"] if current else 0), linked_entry_id=linked, created_utc=now)
    # the public `automatic_quarantine_lifted` line, when the hold has a public line, is written by `publish_due` too
    if notify and not appeal_entry_id:
        _queue(db, "lifted", user_id, entry)
    return entry or True
