"""Appeals against what the Log records (the Log, slice D2; the owner's decision 26 of 2026-11-06: "a basic appeal is part of the launch
work"). Rules and SQL only (plain `text()`, tested on SQLite from the migration: tests/test_log_appeals.py); the routes and the undo
that a reversal runs are `routes/log_appeal_pages.py`.

What can be appealed (`APPEALABLE`): a guild's hide of a post or comment, an admin's removal (also the site's own spam and image
checks, which are removals by the system), a post author's hide of a comment under it, a guild's exile or chat mute (by name or by alias), an
account's suspension or ban, a guild's ban from the site (the owner, 2026-11-14: its founder and full guildmasters, each once) and **an account's quarantine
by an automatic check** (the owner, 2026-11-18: once per quarantine, decided by the site's admins, through `/appeal/quarantine`). **Only the person the decision is about** (`subject_of`: the account that was exiled, suspended or banned, the comment's
author, the person responsible for a post: its forwarder for a guild's copy, else its author) **and only once per entry** (a unique
index), a reason of up to 500 characters, three a day. An appeal is private: its text is read by the people who decide it.

**Who decides** (`board_for`, `may_decide`): a decision a guildmaster made belongs to that guild's guildmasters with the permission the
action asked for (content for a hide, access for an exile), and **not the one who acted while another qualified guildmaster exists**; every
other decision (an admin's, the system's, a post author's hide) goes to the site's admins (level 3), again not the admin who decided it
while another exists. An entry that is an alias's or an anonymous item's shows its deciders no name: the queue draws "Anonymous" for it.

**Outcomes**: `upheld` (the decision stands), `reversed` (the undo runs and writes a new entry **linked to the original**, public when the
original was; the original is never edited) and `dismissed` (not considered: say why in the note). A decision is made once (a conditional
UPDATE), and it needs a note to the person."""
import time

from sqlalchemy import text

from ruqqus.helpers import action_log
from ruqqus.helpers.i18n_runtime import N_

OPEN, UPHELD, REVERSED, DISMISSED = "open", "upheld", "reversed", "dismissed"
OUTCOMES = (UPHELD, REVERSED, DISMISSED)
REASON_MAX = 500
NOTE_MAX = 500
PER_DAY = 3
DAY = 86400

# action -> (the action that undoes it, the guild permission that decides an appeal of it when a guildmaster made it)
APPEALABLE = {
    "hide_post_from_guild": ("unhide_post_from_guild", "content"),
    "hide_comment_from_guild": ("unhide_comment_from_guild", "content"),
    "ban_post": ("unban_post", "content"),
    "ban_comment": ("unban_comment", "content"),
    "author_hide_comment": ("author_unhide_comment", None),
    "exile_user": ("unexile_user", "access"),
    "exile_alias": ("unexile_alias", "access"),
    "chatban_user": ("unchatban_user", "content"),
    "chatban_alias": ("unchatban_alias", "content"),
    "ban_guild": ("unban_guild", None),
    "suspend_user": ("unban_user", None),
    "ban_user": ("unban_user", None),
    # an account an automatic check put on hold (helpers/auto_quarantine.py): the staff's entry about it is a staff-only one, so the person appeals through
    # their own page (/appeal/quarantine), which finds that entry server-side; the site's admins decide, never the system
    "quarantine_user": ("unquarantine_user", None),
}
# a person is the subject of these; everything else is about an item
ABOUT_A_PERSON = {"exile_user", "exile_alias", "chatban_user", "chatban_alias", "suspend_user", "ban_user", "quarantine_user"}
# appealed from a page of their own, never from `/log/<id>/appeal`: the entry is staff-only, so its id is not the person's to know or to name
OWN_PAGE = ("quarantine_user",)
# an action made by alias names no account in the Log: the account is the staff's and the appeal's to know (`subject_user_id`)
BY_ALIAS = ("exile_alias", "chatban_alias")
# the actions that put a suspension or ban back
LIFTS = ("unban_user", "unsuspend_user")


class AppealRefused(ValueError):
    """The appeal can't be made (the message is the one to show; never says why a decision was made)."""


def parse_reason(value, limit=REASON_MAX):
    """Plain text: one line of words, never HTML (it is escaped where it is drawn)."""
    return " ".join(str(value or "").split())[:limit]


def parse_outcome(value):
    return value if value in OUTCOMES else None


def _entry(db, entry_id):
    row = db.execute(text("SELECT id, action, actor_id, actor_role, board_id, target_user_id, target_submission_id, "
                          "target_comment_id, audience, created_utc, alias_n, subject_user_id FROM log_entries WHERE id = :i"), {"i": int(entry_id)}).first()
    return dict(row._mapping) if row is not None else None


def entry_of(db, entry_id):
    """The entry as `can_appeal` reads it, or None."""
    return _entry(db, entry_id)


def subject_of(db, entry):
    """The account the decision is about, or None: the person for an exile, a suspension or a ban; for an item, the comment's author, or
    for a post the account responsible for it (its forwarder when it is a guild's copy, else its author)."""
    if entry["action"] in BY_ALIAS:
        return entry.get("subject_user_id")                     # an action by alias names no account in the Log: the person it is about is the staff's and the appeal's to know
    if entry["action"] == "ban_guild":
        return None                                             # a guild has several people who answer for it: `is_subject`
    if entry["action"] in ABOUT_A_PERSON:
        return entry["target_user_id"]
    if entry["target_comment_id"]:
        row = db.execute(text("SELECT author_id FROM comments WHERE id = :i"), {"i": entry["target_comment_id"]}).first()
        return row[0] if row else None
    if entry["target_submission_id"]:
        post = entry["target_submission_id"]
        for sql in ("SELECT forwarded_by_id FROM forwardrels WHERE forward_submission_id = :p",
                    "SELECT promoted_by_id FROM comment_forwardrels WHERE promoted_submission_id = :p"):
            row = db.execute(text(sql), {"p": post}).first()
            if row is not None and row[0]:
                return row[0]
        row = db.execute(text("SELECT author_id FROM submissions WHERE id = :p"), {"p": post}).first()
        return row[0] if row else None
    return None


def is_undone(db, entry):
    """Whether a later entry already put this one right: the matching undo for the same item or person (and guild). The appeal
    page then says it was already lifted instead of taking an appeal for nothing."""
    undo = APPEALABLE[entry["action"]][0]
    actions = LIFTS if undo == "unban_user" else (undo,)
    marks = ", ".join(f":a{n}" for n in range(len(actions)))
    params = {f"a{n}": a for n, a in enumerate(actions)}
    params["id"] = entry["id"]
    where = [f"action IN ({marks})", "id > :id"]
    if entry["action"] in BY_ALIAS:
        # the lifting entry of an action by alias names no account in the Log: it is matched on the account the staff and the appeal know, in this guild
        where.append("subject_user_id = :subject")
        params["subject"] = entry.get("subject_user_id")
    else:
        # an exile that cites an item carries the item beside the person, and the entry that lifts it names the person only: a person's
        # decision is matched on the person, never on what it cited
        about = (("target_user_id", "target_user_id"),) if entry["action"] in ABOUT_A_PERSON else (
            ("target_user_id", "target_user_id"), ("target_submission_id", "target_submission_id"), ("target_comment_id", "target_comment_id"))
        for column, key in about:
            if entry[key]:
                where.append(f"{column} = :{key}")
                params[key] = entry[key]
    if entry["action"] in ("hide_post_from_guild", "hide_comment_from_guild", "exile_user", "exile_alias", "chatban_user", "chatban_alias",
                           "ban_guild") and entry["board_id"]:
        where.append("board_id = :board")
        params["board"] = entry["board_id"]
    return db.execute(text(f"SELECT 1 FROM log_entries WHERE {' AND '.join(where)} LIMIT 1"), params).first() is not None


def board_for(db, entry):
    """The guild whose guildmasters decide an appeal of this entry, or None for the site's admins: only a guildmaster's own decision
    in a guild (not the site's boards, not an admin's, the system's or a post author's)."""
    if entry["actor_role"] != "guildmaster" or not entry["board_id"] or entry["board_id"] in (1, 2):
        return None
    return entry["board_id"]


def permission_for(entry):
    """The guild permission a guildmaster needs to decide an appeal of this entry (`content` or `access`), or None."""
    return APPEALABLE[entry["action"]][1]


def heads_of(db, board_id):
    """The accounts that answer for a guild: its founder and its accepted full guildmasters (not deleted, and never the site's own account, which founds
    its own boards): who is told a guild was banned or reinstated and
    who may appeal the ban, each once."""
    if not board_id:
        return []
    rows = db.execute(text(
        "SELECT u.id FROM users u WHERE u.is_deleted IS NOT TRUE AND u.id <> 1 AND (u.id = (SELECT creator_id FROM boards WHERE id = :b) OR u.id IN "
        "(SELECT m.user_id FROM mods m WHERE m.board_id = :b AND m.accepted IS TRUE AND m.invite_rescinded IS NOT TRUE AND m.perm_full IS TRUE)) "
        "ORDER BY u.id"), {"b": int(board_id)})
    return [r[0] for r in rows]


def is_subject(db, entry, user_id):
    """Whether this account is who the decision is about (`subject_of`; for a guild's ban, one of the people who answer for the guild)."""
    if not user_id:
        return False
    if entry["action"] == "ban_guild":
        return int(user_id) in heads_of(db, entry["board_id"])
    return subject_of(db, entry) == user_id


ALREADY_APPEALED = N_("You have already appealed this decision.")


def can_appeal(db, entry, user_id, now=None):
    """None when this account may appeal this entry now, else the message to give. Never says who decided or why."""
    if entry is None or entry["action"] not in APPEALABLE:
        return N_("There is nothing here to appeal.")
    if not is_subject(db, entry, user_id):
        return N_("There is nothing here to appeal.")                    # the same answer for an entry that isn't theirs
    if is_undone(db, entry):
        return N_("This decision has already been lifted.")
    if db.execute(text("SELECT 1 FROM log_appeals WHERE entry_id = :e AND user_id = :u"), {"e": entry["id"], "u": user_id}).first():
        return ALREADY_APPEALED
    now = int(now if now is not None else time.time())
    made = db.execute(text("SELECT COUNT(*) FROM log_appeals WHERE user_id = :u AND created_utc > :since"),
                      {"u": user_id, "since": now - DAY}).scalar() or 0
    if made >= PER_DAY:
        return N_("You can make three appeals a day. Try again tomorrow.")
    return None


APPEAL_LOCK = 7012                  # pg_advisory_xact_lock(APPEAL_LOCK, account id): one appeal being filed at a time per account


def file_appeal(db, entry_id, user_id, reason, now=None):
    """Make the appeal and return its id. Raises AppealRefused (the message to show) when it can't be made. One at a time per account
    (Postgres): the checks (once per entry, three a day) were read before the INSERT, so two requests at once both passed and the second
    hit the unique index and the member got a 500 (the second review, C08)."""
    if db.get_bind().dialect.name == "postgresql":
        db.execute(text("SELECT pg_advisory_xact_lock(:ns, :u)"), {"ns": APPEAL_LOCK, "u": int(user_id)})
    entry = _entry(db, entry_id)
    problem = can_appeal(db, entry, user_id, now)
    if problem:
        raise AppealRefused(problem)
    reason = parse_reason(reason)
    if not reason:
        raise AppealRefused(N_("Say why you are appealing."))
    now = int(now if now is not None else time.time())
    row = db.execute(text("INSERT INTO log_appeals (entry_id, user_id, board_id, reason, created_utc) "
                          "VALUES (:e, :u, :b, :r, :now) RETURNING id"),
                     {"e": entry["id"], "u": user_id, "b": board_for(db, entry), "r": reason, "now": now}).first()
    return row[0]


def mine(db, user_id, limit=50):
    """The appeals this account made, newest first, with how each was decided."""
    return [dict(r._mapping) for r in db.execute(text(
        "SELECT a.id, a.entry_id, a.created_utc, a.reason, a.status, a.decided_utc, a.decision_note, e.action "
        "FROM log_appeals a JOIN log_entries e ON e.id = a.entry_id WHERE a.user_id = :u ORDER BY a.id DESC LIMIT :n"),
        {"u": user_id, "n": int(limit)})]


def open_queue(db, board_id=None, limit=100):
    """The open appeals a queue decides, oldest first: a guild's (`board_id`) or the site's (None). Each row has the entry's action,
    its item (by id) and who it is about, so the page can say what is appealed without naming anyone it may not."""
    where, params = ("a.board_id = :b", {"b": int(board_id)}) if board_id else ("a.board_id IS NULL", {})
    params["n"] = int(limit)
    return [dict(r._mapping) for r in db.execute(text(
        f"SELECT a.id, a.entry_id, a.user_id, a.board_id, a.created_utc, a.reason, e.action, e.actor_id, e.actor_role, "
        f"e.target_submission_id, e.target_comment_id, e.target_user_id, e.board_id AS entry_board, e.reason AS entry_reason, e.rule_key, "
        f"e.audience AS entry_audience "
        f"FROM log_appeals a JOIN log_entries e ON e.id = a.entry_id WHERE a.status = 'open' AND {where} "
        f"ORDER BY a.id LIMIT :n"), params)]


def appeal_of(db, appeal_id):
    row = db.execute(text(
        "SELECT a.id, a.entry_id, a.user_id, a.board_id, a.created_utc, a.reason, a.status, a.decided_by, a.reversal_entry_id, "
        "e.action, e.actor_id, e.actor_role, e.target_submission_id, e.target_comment_id, e.target_user_id, e.board_id AS entry_board, "
        "e.audience AS entry_audience FROM log_appeals a JOIN log_entries e ON e.id = a.entry_id WHERE a.id = :i"), {"i": int(appeal_id)}).first()
    return dict(row._mapping) if row is not None else None


def _live(now):
    # not suspended: a suspension that has run out no longer counts (`unban_utc` is when it ends, 0 for a ban that never does)
    return f"(u.is_banned = 0 OR (u.unban_utc > 0 AND u.unban_utc <= {int(now)}))"


def _also_not(also, prefix="x"):
    """(SQL, params) that leave these accounts out of "who else could decide": the one who appealed is never a decider of their own appeal."""
    ids = [int(i) for i in also or () if i]
    names = {f"{prefix}{n}": i for n, i in enumerate(ids)}
    return "".join(f" AND u.id <> :{n}" for n in names), names


def other_admins(db, user_id, now=None, level=3, also=()):
    """How many other admins (`level` or more: a guild's ban is a level-4 decision) could decide: not the one asked about, nor anyone in `also` (the person
    who appealed), not deleted, not suspended."""
    now = int(now if now is not None else time.time())
    extra, names = _also_not(also)
    return db.execute(text(f"SELECT COUNT(*) FROM users u WHERE u.admin_level >= :lvl AND u.id <> :u AND u.is_deleted IS NOT TRUE "
                           f"AND {_live(now)}{extra}"), {"u": user_id, "lvl": int(level), **names}).scalar() or 0


def other_guildmasters(db, board_id, permission, user_id, now=None, also=()):
    """How many other accepted guildmasters of the guild hold `permission` (or full): who else could decide (not the person who appealed)."""
    now = int(now if now is not None else time.time())
    allowed = "m.perm_full" + (f" OR m.perm_{permission}" if permission in ("content", "access") else "")
    extra, names = _also_not(also)
    return db.execute(text(
        f"SELECT COUNT(*) FROM mods m JOIN users u ON u.id = m.user_id WHERE m.board_id = :b AND m.accepted IS TRUE "
        f"AND m.invite_rescinded IS NOT TRUE AND ({allowed}) AND u.id <> :u AND u.is_deleted IS NOT TRUE AND {_live(now)}{extra} "
        f"AND COALESCE(u.quarantined_utc, 0) = 0"),            # a held account (helpers/quarantine.py) decides nothing: it is not "another who could"
        {"b": int(board_id), "u": user_id, **names}).scalar() or 0


def conflicted(db, appeal, user_id, now=None):
    """Whether this account is the one who made the decision and someone else could decide instead (then it is not theirs to decide)."""
    if appeal["actor_id"] != user_id:
        return False
    also = (appeal.get("user_id"),)          # the person who appealed can't decide it, so they are not "someone else who could"
    if appeal["board_id"]:
        entry = {"action": appeal["action"]}
        return other_guildmasters(db, appeal["board_id"], permission_for(entry), user_id, now, also=also) > 0
    # a guild's ban is a level-4 decision (the site queue lets only level 4 decide it), so a level-3 admin is not "another who could" (the second review, C11)
    return other_admins(db, user_id, now, level=4 if appeal["action"] == "ban_guild" else 3, also=also) > 0


ESCALATE_DAYS = 14
ALONE, LATE = "alone", "late"


def escalation(db, appeal, now=None):
    """Why an open appeal against a GUILD's decision is the site's admins' to decide too (the owner, 2026-11-19: "escalate to admins"), or None. `ALONE`: no other
    guildmaster who could decide exists (the one who decided is the only qualified guildmaster, or none is left, or the others are held or gone), so the guild's
    queue is no longer a place it can be answered and **only the admins decide it**. `LATE`: the guild has left it open `ESCALATE_DAYS`, so the admins may decide it as
    well (whoever decides first settles it: `decide` is one conditional UPDATE). A site-queue appeal (no guild) is never escalated: it is the admins' already."""
    if not appeal.get("board_id") or appeal.get("action") not in APPEALABLE:
        return None
    now = int(now if now is not None else time.time())
    also = (appeal.get("user_id"),)
    if other_guildmasters(db, appeal["board_id"], permission_for({"action": appeal["action"]}), appeal.get("actor_id") or 0, now, also=also) == 0:
        return ALONE
    if now - int(appeal.get("created_utc") or now) >= ESCALATE_DAYS * DAY:
        return LATE
    return None


def escalated_queue(db, now=None, limit=200):
    """The open guild appeals the admins decide too (`escalation`), oldest first, in the shape `open_queue` gives them, each with `escalated` set to why."""
    now = int(now if now is not None else time.time())
    out, after = [], 0
    while len(out) < limit:
        # in batches by id, so the filter is applied to EVERY open guild appeal and not only the oldest few hundred (a newer one that the guild can no longer answer must show)
        rows = [dict(r._mapping) for r in db.execute(text(
            "SELECT a.id, a.entry_id, a.user_id, a.board_id, a.created_utc, a.reason, e.action, e.actor_id, e.actor_role, "
            "e.target_submission_id, e.target_comment_id, e.target_user_id, e.board_id AS entry_board, e.reason AS entry_reason, e.rule_key, "
            "e.audience AS entry_audience FROM log_appeals a JOIN log_entries e ON e.id = a.entry_id "
            "WHERE a.status = 'open' AND a.board_id IS NOT NULL AND a.id > :after ORDER BY a.id LIMIT 200"), {"after": after})]
        if not rows:
            break
        for row in rows:
            why = escalation(db, row, now)
            if why and len(out) < limit:
                out.append({**row, "escalated": why})
        after = rows[-1]["id"]
    return out


def decide(db, appeal_id, outcome, decider_id, note, now=None, reversal_entry_id=None):
    """Settle an open appeal, once: True when this call settled it (a second decision of the same appeal changes nothing)."""
    if outcome not in OUTCOMES:
        raise ValueError(outcome)
    return db.execute(text(
        "UPDATE log_appeals SET status = :s, decided_by = :d, decided_utc = :now, decision_note = :n, reversal_entry_id = :r "
        "WHERE id = :i AND status = 'open'"),
        {"s": outcome, "d": decider_id, "now": int(now if now is not None else time.time()),
         "n": parse_reason(note, NOTE_MAX), "r": reversal_entry_id, "i": int(appeal_id)}).rowcount == 1


def site_level(level):
    """The staff level the site's queue is read at: never below 3 (the queue is the admins'), so a missing or unknown level reads as the narrowest admin."""
    try:
        return max(3, int(level or 0))
    except (TypeError, ValueError):
        return 3


def may_decide_site(action, entry_audience, level):
    """Whether an admin of `level` may read and decide a site-queue appeal of an entry with this `action` and `audience`: **the one rule of that queue**, which the page
    (`routes/log_appeal_pages.py` `_may_decide_site`) and the count beside it (`waiting_count`) both follow. An entry only level-4 admins may read (illegal content, a
    link about minors) is not shown to, and cannot be reversed by, a level-3 admin: its row would carry its reason, and the appeal's very existence says who it is about
    (the second review, C12). A guild's ban is a level-4 decision too (only a level-4 admin bans or reinstates a guild); every other appeal is any admin's."""
    if (entry_audience or 0) >= action_log.LEGAL:
        return site_level(level) >= 4
    return action != "ban_guild" or site_level(level) >= 4


def waiting_count(db, board_id=None, level=3, viewer_id=None):
    """How many appeals wait for a decision: a guild's (`board_id`), or the site's as an admin of `level` may read the queue (`may_decide_site`, in SQL: one legal-level
    appeal must not show as "1 waiting" on /admin while the queue itself leaves it out) and without the viewer's own (`viewer_id`: nobody decides their own appeal, so it is
    not on their queue either). With no level the narrowest admin's number is the answer, never the widest."""
    if board_id:
        return db.execute(text("SELECT COUNT(*) FROM log_appeals WHERE status = 'open' AND board_id = :b"), {"b": int(board_id)}).scalar() or 0
    where, params = ["a.status = 'open'", "a.board_id IS NULL"], {}
    if site_level(level) < 4:
        where.append("e.audience < :legal AND e.action <> 'ban_guild'")
        params["legal"] = action_log.LEGAL
    if viewer_id:
        where.append("a.user_id <> :viewer")
        params["viewer"] = int(viewer_id)
    own = db.execute(text("SELECT COUNT(*) FROM log_appeals a JOIN log_entries e ON e.id = a.entry_id WHERE " + " AND ".join(where)), params).scalar() or 0
    escalated = [a for a in escalated_queue(db) if may_decide_site(a["action"], a.get("entry_audience"), level) and a["user_id"] != viewer_id]
    return own + len(escalated)
