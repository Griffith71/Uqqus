"""The Log: what guildmasters and admins decided, in one append-only table (the owner, 2026-10-31).

Modelled on Wikipedia's Special:Log. One row per decision (`log_entries`): a type, an action, who did it (and in what
role), to what, which rule was cited and a written reason. The text of what was hidden or removed is never in the entry:
it is a private snapshot (`log_snapshots`) kept for 90 days. Plain `text()` SQL, tested on SQLite with the tables built
from migration 2026-12-09_action_log.sql (tests/test_action_log.py); no ORM model, so the star import of
`ruqqus.classes` can't replace this module.

**Who may see an entry is decided in ONE place**: `audience_for` when it is written, `visible_clause` when it is read.
  0 PUBLIC  anyone (a visitor included), by the public log (`/log`) and a public guild's own log
  1 GUILD   that guild's members (`Board.can_view`'s: contributors, accepted guildmasters, invited ones) and the staff:
            a private or Circle guild's own entries, so they never reach the public log and the guild is never named
  2 STAFF   admins (level 3): ban evasion, alt bans, a display name cleared, a forced sensitive mark, an age answer
  3 LEGAL   level-4 admins only: illegal content (CSAM), the details of an obliteration
An entry keeps the audience it was written with: a guild that goes public later does not publish what happened while it
was private.

`staff_note` is never selected for anyone but staff (`PUBLIC_COLUMNS`). Admins are drawn "Site admin" to everyone who
isn't staff, in every output, JSON included (`actor_label`).

`ModAction` rows (the guild log's own table, still written by its 78 call sites) are copied here as they are made
(`mirror_modaction`, called from the model's `after_insert`), so every existing writer is covered without being
edited; a route that has a reason or a snapshot to add calls `annotate` right after its `ModAction` is flushed. Anything
a `ModAction` never recorded (an account suspension, a guild ban, a display name cleared) is written with `record`.
"""
import html
import logging
import time

from sqlalchemy import text

from ruqqus.helpers.i18n_runtime import N_

PUBLIC, GUILD, STAFF, LEGAL = 0, 1, 2, 3

CONTENT, SANCTION, GUILD_CHANGE, SUPPRESSION = "content", "sanction", "guild", "suppression"
TYPES = (CONTENT, SANCTION, GUILD_CHANGE, SUPPRESSION)

GUILDMASTER, ADMIN, SYSTEM = "guildmaster", "admin", "system"
SITE_ACCOUNT_ID = 1                 # the site's own account: what an automatic action is done as

SNAPSHOT_DAYS = 90                  # how long the text of what was hidden, removed or deleted is kept privately
LEGAL_HOLD_DAYS = 365               # illegal content: the one-year preservation duty
REASON_MAX = 512
DAY = 86400

# The rule an action cites: the sections of the posting rules (/help/rules), a guild's own rule, or another reason.
RULES = {
    "good_faith": N_("Free expression and good faith"),
    "illegal": N_("Criminal activity and prohibited conduct"),
    "content": N_("Content standards"),
    "disclosure": N_("Transparency and disclosure"),
    "evasion": N_("Evading and manipulating the site"),
    "bots": N_("Bots"),
    "guild_rule": N_("A rule of the guild"),
    "other": N_("Another reason"),
}

_CONTENT = {
    "hide_post_from_guild", "unhide_post_from_guild", "hide_comment_from_guild", "unhide_comment_from_guild",
    "approve_post", "kick_post", "yank_post", "ban_post", "unban_post", "ban_comment", "unban_comment",
    "purge_post", "purge_comment", "obliterate_post", "obliterate_comment", "set_sensitive", "unset_sensitive", "force_sensitive_post",
    "set_sensitive_comment", "unset_sensitive_comment", "channel_message_remove", "remove_wiki_revision",
    "remove_story", "hide_revision",
}
_SANCTION = {
    "exile_user", "unexile_user", "chatban_user", "unchatban_user", "suspend_user", "unsuspend_user", "ban_user",
    "unban_user", "ban_guild", "unban_guild", "clear_display_name", "force_sensitive_user", "age_declaration",
}
_SUPPRESSION = {"suppress_content", "legal_removal"}

# What can't be a guild's or the public's business: always these audiences, whatever the guild.
ACCOUNT_ACTIONS = {"suspend_user", "unsuspend_user", "ban_user", "unban_user"}      # public: no guild, post or comment in them
STAFF_ACTIONS = {"clear_display_name", "force_sensitive_user", "force_sensitive_post", "remove_story", "age_declaration"}
LEGAL_ACTIONS = {"suppress_content", "legal_removal"}

# Actions that must say why (Wikipedia's reason). A caller with a reason box passes `require_reason=True`.
REASON_REQUIRED = {
    "hide_post_from_guild", "hide_comment_from_guild", "ban_post", "ban_comment", "suspend_user", "ban_user",
    "ban_guild", "suppress_content", "legal_removal", "exile_user", "hide_revision",
}

# the markers an admin or automatic writer already puts in a ModAction's note (routes/admin_api.py, routes/settings.py,
# helpers/wrappers.py): they mean "done by the site's staff", not a reason a person wrote
ADMIN_MARKERS = ("admin action", "admin_action", "by the site's admins")
_LEGAL_NOTES = ("csam", "banned image")
_STAFF_NOTES = ("ban evasion",)

PUBLIC_COLUMNS = ("id, created_utc, log_type, action, actor_id, actor_role, board_id, target_user_id, "
                  "target_submission_id, target_comment_id, rule_key, reason, audience, redacted_utc")
STAFF_COLUMNS = PUBLIC_COLUMNS + ", staff_note, snapshot_id, notice_sent, redacted_by, legacy_modaction_id"


class ReasonRequired(ValueError):
    """The action needs a written reason (the message is the one to show)."""


def type_of(action):
    if action in _SUPPRESSION:
        return SUPPRESSION
    if action in _SANCTION:
        return SANCTION
    if action in _CONTENT:
        return CONTENT
    return GUILD_CHANGE


def role_of(actor_id, admin_level, note="", role=None):
    """Who acted, as a role: the site's own account is the system, a note an admin route writes marks an admin, an
    account at admin level 3 doing an admin-only action is an admin (the caller says so with `role`), anyone else
    is a guildmaster."""
    if role in (GUILDMASTER, ADMIN, SYSTEM):
        return role
    if actor_id == SITE_ACCOUNT_ID:
        return SYSTEM
    lowered = (note or "").lower()
    if any(mark in lowered for mark in ADMIN_MARKERS):
        return ADMIN
    if lowered.strip() == "siege" and (admin_level or 0) >= 3:
        return ADMIN
    return GUILDMASTER


def audience_for(action, *, board_id=None, private=False, note="", floor=PUBLIC):
    """The audience an entry is written with; `floor` can only make it narrower (a caller that knows more, such as
    "this was a ban on an alt", says STAFF)."""
    lowered = (note or "").lower()
    if action in LEGAL_ACTIONS or any(mark in lowered for mark in _LEGAL_NOTES):
        audience = LEGAL
    elif action in STAFF_ACTIONS or any(mark in lowered for mark in _STAFF_NOTES):
        audience = STAFF
    elif action in ACCOUNT_ACTIONS:
        audience = PUBLIC
    elif not board_id:
        audience = STAFF                # not tied to a guild we know: not the public's to read
    elif private:
        audience = GUILD
    else:
        audience = PUBLIC
    return max(audience, floor)


def clean_reason(value, limit=REASON_MAX):
    """Plain text of a reason: one line of words, never HTML (it is escaped where it is drawn)."""
    return " ".join(str(value or "").split())[:limit]


def parse_rule(value):
    return value if value in RULES else ""


def _board_is_private(db, board_id):
    if not board_id:
        return False
    row = db.execute(text("SELECT is_private, is_circle FROM boards WHERE id = :b"), {"b": board_id}).first()
    return bool(row and (row[0] or row[1]))


def _admin_level(db, user_id):
    if not user_id:
        return 0
    row = db.execute(text("SELECT admin_level FROM users WHERE id = :u"), {"u": user_id}).first()
    return (row[0] or 0) if row else 0


def snapshot_of(item, board_id=None, legal_hold=False, comment=False):
    """What to keep of a post or comment (a Submission or a Comment, or a stand-in with the same attributes)."""
    return {
        "post_id": None if comment else getattr(item, "id", None),
        "comment_id": getattr(item, "id", None) if comment else None,
        "author_id": getattr(item, "author_id", None),
        "board_id": board_id if board_id is not None else getattr(item, "board_id", None),
        "title": getattr(item, "title", None),
        "url": getattr(item, "url", None),
        "body": getattr(item, "body", None),
        "body_html": getattr(item, "body_html", None),
        "legal_hold": legal_hold,
    }


def _keep(db, snapshot, entry_id, now):
    days = LEGAL_HOLD_DAYS if snapshot.get("legal_hold") else SNAPSHOT_DAYS
    row = db.execute(text(
        "INSERT INTO log_snapshots (entry_id, author_id, board_id, target_submission_id, target_comment_id, legal_hold, title, "
        "url, body, body_html, created_utc, expires_utc) "
        "VALUES (:entry, :author, :board, :post, :comment, :hold, :title, :url, :body, :html, :now, :expires) RETURNING id"),
        {"entry": entry_id, "author": snapshot.get("author_id"), "board": snapshot.get("board_id"),
         "post": snapshot.get("post_id"), "comment": snapshot.get("comment_id"),
         "hold": bool(snapshot.get("legal_hold")), "title": (snapshot.get("title") or None),
         "url": (snapshot.get("url") or "")[:500] or None, "body": (snapshot.get("body") or None),
         "html": (snapshot.get("body_html") or None), "now": now, "expires": now + days * DAY}).first()
    return row[0]


def keep_snapshot(db, snapshot, now=None, entry_id=None):
    """A private copy of an item with no entry of its own (an author deleting their own post: not a decision, so it is
    not in the Log, but the text is kept for 90 days for reports and legal requests)."""
    return _keep(db, snapshot, entry_id, int(now if now is not None else time.time()))


def record(db, action, actor_id, *, board_id=None, target_user_id=None, post_id=None, comment_id=None, rule=None,
           reason="", staff_note="", snapshot=None, role=None, note="", floor=PUBLIC, require_reason=False, now=None,
           legacy_id=None, created_utc=None, notice_sent=False):
    """Write one entry and return its id. Raises ReasonRequired when the action needs a reason and has none (only when
    the caller has a place to ask for one: `require_reason`)."""
    reason = clean_reason(reason)
    if require_reason and action in REASON_REQUIRED and not reason:
        raise ReasonRequired("Give a reason.")
    now = int(created_utc if created_utc is not None else (now if now is not None else time.time()))
    audience = audience_for(action, board_id=board_id, private=_board_is_private(db, board_id), note=note, floor=floor)
    row = db.execute(text(
        "INSERT INTO log_entries (created_utc, log_type, action, actor_id, actor_role, board_id, target_user_id, "
        "target_submission_id, target_comment_id, rule_key, reason, staff_note, audience, notice_sent, legacy_modaction_id) "
        "VALUES (:now, :type, :action, :actor, :role, :board, :tuser, :tpost, :tcomment, :rule, :reason, :staff, :audience, "
        ":notice, :legacy) RETURNING id"),
        {"now": now, "type": type_of(action), "action": action, "actor": actor_id,
         "role": role_of(actor_id, _admin_level(db, actor_id), note, role), "board": board_id or None,
         "tuser": target_user_id or None, "tpost": post_id or None, "tcomment": comment_id or None,
         "rule": parse_rule(rule), "reason": reason, "staff": clean_reason(staff_note), "audience": audience,
         "notice": bool(notice_sent), "legacy": legacy_id}).first()
    entry_id = row[0]
    if snapshot:
        db.execute(text("UPDATE log_entries SET snapshot_id = :s WHERE id = :e"),
                   {"s": _keep(db, snapshot, entry_id, now), "e": entry_id})
    return entry_id


_BARE_MARKERS = {"admin action", "admin_action", "csam detected", "banned image", "ban evasion", "siege"}


def reason_in(note):
    """The reason in a legacy note: the markers the site's code writes ("admin action", "CSAM detected") say who or what
    did it, not why, so a note that is only a marker has no reason, and "admin action | Spamming" has "Spamming"."""
    lowered = (note or "").strip().lower()
    if lowered in _BARE_MARKERS:
        return ""
    for marker in ("admin action", "admin_action"):
        if lowered.startswith(marker + " |"):
            return note.strip()[len(marker) + 2:].strip()
    return note or ""


def mirror_modaction(db, modaction_id, user_id, board_id, kind, target_user_id, post_id, comment_id, note, created_utc):
    """Copy one guild mod-log row into the Log (the model's `after_insert`; scripts/backfill_action_log.py for the old
    ones). The legacy note was stored HTML-escaped: it is stored here as plain text. Idempotent on the legacy id."""
    if db.execute(text("SELECT 1 FROM log_entries WHERE legacy_modaction_id = :i"), {"i": modaction_id}).first():
        return None
    plain = html.unescape(note or "")
    return record(db, kind, user_id, board_id=board_id, target_user_id=target_user_id, post_id=post_id,
                  comment_id=comment_id, reason=reason_in(plain), note=plain, created_utc=created_utc or int(time.time()),
                  legacy_id=modaction_id)


def annotate(db, modaction_id, *, rule=None, reason=None, staff_note=None, snapshot=None, floor=None):
    """Add what a route knows to the entry its ModAction became: the cited rule, a written reason, a private note, the
    item as it was. Returns the entry id, or None when there is no such entry (the mirror failed, so say so)."""
    row = db.execute(text("SELECT id, action, board_id, audience, created_utc FROM log_entries WHERE legacy_modaction_id = :i"),
                     {"i": modaction_id}).first()
    if row is None:
        return None
    entry_id, _, _, audience, created = row
    sets, values = [], {"e": entry_id}
    if rule is not None:
        sets.append("rule_key = :rule")
        values["rule"] = parse_rule(rule)
    if reason is not None:
        sets.append("reason = :reason")
        values["reason"] = clean_reason(reason)
    if staff_note is not None:
        sets.append("staff_note = :staff")
        values["staff"] = clean_reason(staff_note)
    if floor is not None and floor > audience:
        sets.append("audience = :audience")
        values["audience"] = floor
    if sets:
        db.execute(text(f"UPDATE log_entries SET {', '.join(sets)} WHERE id = :e"), values)
    if snapshot:
        db.execute(text("UPDATE log_entries SET snapshot_id = :s WHERE id = :e"),
                   {"s": _keep(db, snapshot, entry_id, int(created or time.time())), "e": entry_id})
    return entry_id


# --- reading ------------------------------------------------------------------------------------------------------------

def staff_level(admin_level):
    """0 for everyone, 3 for an admin who may read staff entries, 4 for one who may also read legal ones."""
    level = admin_level or 0
    return 4 if level >= 4 else 3 if level >= 3 else 0


def led_boards(viewer):
    """The ids of the guilds this account leads (an accepted guildmaster, invitation not rescinded): who may open the preserved
    copy of what was hidden in one. Read from the account's own loaded list, no query."""
    if viewer is None:
        return set()
    return {m.board_id for m in viewer.moderates if m.accepted and not m.invite_rescinded}


def may_open_copy(snapshot, viewer):
    """Whether this account may open that preserved copy (None: there is none): its item's author, admins (a legal hold only
    level 4) and the guildmasters of the guild it was hidden in."""
    if snapshot is None or viewer is None:
        return False
    return snapshot_visible(snapshot, viewer_id=viewer.id, level=staff_level(viewer.admin_level),
                            guild_mod=snapshot["board_id"] in led_boards(viewer))


def member_board_ids(db, user_id):
    """The guilds whose own entries this account may read: `Board.can_view`'s memberships (active contributor, accepted
    guildmaster or an invitation to be one)."""
    if not user_id:
        return []
    rows = db.execute(text(
        "SELECT board_id FROM contributors WHERE user_id = :u AND is_active "
        "UNION SELECT board_id FROM mods WHERE user_id = :u AND NOT COALESCE(invite_rescinded, false)"), {"u": user_id}).all()
    return sorted({r[0] for r in rows if r[0]})


def visible_clause(level, member_boards=(), alias=""):
    """(SQL, params) for the entries a reader may see: `level` from staff_level, `member_boards` from member_board_ids.
    `alias` is a table alias to put before the columns when the clause is used in a join or a subquery ("e")."""
    p = f"{alias}." if alias else ""
    if level >= 4:
        return f"{p}audience <= 3", {}
    if level >= 3:
        return f"{p}audience <= 2", {}
    boards = [int(b) for b in member_boards or ()]
    if not boards:
        return f"{p}audience = 0", {}
    names = {f"vb{i}": b for i, b in enumerate(boards)}
    marks = ", ".join(f":{n}" for n in names)
    return f"({p}audience = 0 OR ({p}audience = 1 AND {p}board_id IN ({marks})))", names


def actor_label(entry, level):
    """Who the reader is told did it: a guildmaster by name, an admin as 'Site admin' (staff see which), the site's own
    account as the system. `entry` has actor_role and actor_id; the name is the caller's to look up for a guildmaster."""
    role = entry["actor_role"]
    if role == ADMIN and level < 3:
        return "admin"
    if role == SYSTEM:
        return "system"
    return "guildmaster" if role == GUILDMASTER else "admin"


# the public Log names no guild that is private or a Circle guild NOW, even for an entry written while it was open
_OPEN_GUILDS = ("(board_id IS NULL OR NOT EXISTS (SELECT 1 FROM boards b WHERE b.id = log_entries.board_id "
                "AND (b.is_private OR b.is_circle)))")


def fetch(db, *, level=0, member_boards=(), log_type=None, board_id=None, actor_id=None, target_user_id=None,
          rule=None, action=None, before_id=None, limit=50, open_guilds_only=False, scope=None, offset=0):
    """A page of entries, newest first, for a reader with this `level`: (rows, has_more). The staff-only columns come
    only for staff; an admin entry's actor_id is left out for everyone else. `open_guilds_only` is the public page's: it
    leaves out entries of a guild that has since gone private. `scope` is `about_scope`'s (SQL, params): only the entries about
    one item or account. `offset` is for a page that merges the entries with another list."""
    where, params = visible_clause(level, member_boards)
    clauses = [where]
    if open_guilds_only:
        clauses.append(_OPEN_GUILDS)
    if scope:
        clauses.append(scope[0])
        params.update(scope[1])
    for column, value, name in (("log_type", log_type, "type"), ("board_id", board_id, "board"), ("action", action, "act"),
                                ("target_user_id", target_user_id, "tuser"), ("actor_id", actor_id, "actor"),
                                ("rule_key", rule, "rule")):
        if value not in (None, ""):
            clauses.append(f"{column} = :{name}")
            params[name] = value
    if actor_id not in (None, "") and level < 3:
        # an admin is "Site admin" below staff: filtering by an admin's id would list exactly their entries
        clauses.append("actor_role <> 'admin'")
    if before_id:
        clauses.append("id < :before")
        params["before"] = int(before_id)
    params["limit"] = int(limit) + 1
    params["offset"] = max(0, int(offset))
    columns = STAFF_COLUMNS if level >= 3 else PUBLIC_COLUMNS
    rows = [dict(r._mapping) for r in db.execute(
        text(f"SELECT {columns} FROM log_entries WHERE {' AND '.join(clauses)} ORDER BY id DESC LIMIT :limit OFFSET :offset"),
        params)]
    more = len(rows) > limit
    rows = rows[:limit]
    if level < 3:
        for row in rows:
            if row["actor_role"] == ADMIN:
                row["actor_id"] = None
            if row["redacted_utc"]:
                row["reason"] = ""
    return rows, more


def _numbered(prefix, ids):
    """(`:p0, :p1`, {p0: id...}) for a list of ids, numbers only."""
    names = {f"{prefix}{n}": int(i) for n, i in enumerate(ids)}
    return ", ".join(f":{n}" for n in names), names


def about_scope(*, post_ids=(), comment_ids=(), user_id=None, include_anonymous=False):
    """(SQL, params) that narrows the entries to the ones ABOUT these posts, these comments or this account: what was done to
    the items (a hide, a removal, a hidden version), what was done to the account (an exile, a suspension), and what was done to
    anything the account wrote, a guild's copy of a post or a post that was never on their profile included. **An anonymous item
    is left out** unless `include_anonymous` (the admins'): listing it under its author would say who wrote it."""
    parts, params = [], {}
    if post_ids:
        marks, names = _numbered("sp", post_ids)
        parts.append(f"target_submission_id IN ({marks})")
        params.update(names)
    if comment_ids:
        marks, names = _numbered("sc", comment_ids)
        parts.append(f"target_comment_id IN ({marks})")
        params.update(names)
    if user_id:
        anonymous_post = "" if include_anonymous else " AND NOT COALESCE(s.is_anonymous, false)"
        anonymous_comment = "" if include_anonymous else " AND NOT COALESCE(c.is_anonymous, false)"
        parts.append("target_user_id = :about")
        parts.append(f"target_submission_id IN (SELECT s.id FROM submissions s WHERE s.author_id = :about{anonymous_post})")
        parts.append(f"target_comment_id IN (SELECT c.id FROM comments c WHERE c.author_id = :about{anonymous_comment})")
        params["about"] = int(user_id)
    return ("(" + " OR ".join(parts) + ")", params) if parts else ("1 = 0", {})


def author_deletes(db, *, post_ids=(), comment_ids=(), actor_id=None, include_anonymous=False, limit=26):
    """The times authors deleted their own posts and comments, newest first (a date, who and which item: **never the words**,
    which are a private copy). A self-delete is no decision, so it has no entry in the Log; this reads the history row that
    records it. Only the author's own page and the admins may be shown these: the caller decides, this never filters by reader."""
    parts, params = [], {"limit": int(limit)}
    if post_ids:
        marks, names = _numbered("dp", post_ids)
        parts.append(f"h.target_submission_id IN ({marks})")
        params.update(names)
    if comment_ids:
        marks, names = _numbered("dc", comment_ids)
        parts.append(f"h.target_comment_id IN ({marks})")
        params.update(names)
    if actor_id:
        parts.append("h.actor_id = :actor")
        params["actor"] = int(actor_id)
    if not parts:
        return []
    anonymous = "" if include_anonymous else (
        " AND NOT EXISTS (SELECT 1 FROM submissions s WHERE s.id = h.target_submission_id AND COALESCE(s.is_anonymous, false))"
        " AND NOT EXISTS (SELECT 1 FROM comments c WHERE c.id = h.target_comment_id AND COALESCE(c.is_anonymous, false))")
    rows = db.execute(text(
        f"SELECT h.id, h.created_utc, h.actor_id, h.target_submission_id, h.target_comment_id FROM content_edit_history h "
        f"WHERE h.action = 'delete' AND ({' OR '.join(parts)}){anonymous} ORDER BY h.created_utc DESC, h.id DESC LIMIT :limit"),
        params)
    return [dict(r._mapping) for r in rows]


def family_ids(db, post_id):
    """A post's own id and its forwarded copies', which carry the same author (a repost by someone else is another person's post
    and is not part of this one's log)."""
    row = db.execute(text("SELECT COALESCE(NULLIF(repost_id, 0), id), author_id FROM submissions WHERE id = :p"), {"p": int(post_id)}).first()
    if row is None:
        return [int(post_id)]
    primary, author = row
    rows = db.execute(text("SELECT id FROM submissions WHERE (id = :primary OR repost_id = :primary) AND author_id = :author"),
                      {"primary": primary, "author": author})
    return sorted({int(r[0]) for r in rows} | {int(post_id)})


def copies_for(db, entry_ids):
    """{entry id: {author_id, board_id, legal_hold, scrubbed_utc}} for the entries on a page that kept a private copy of what
    they hid or removed. Asked only about entries the reader may already see; `snapshot_visible` says who may open one."""
    ids = sorted({int(i) for i in entry_ids or () if i})
    if not ids:
        return {}
    names = {f"c{n}": i for n, i in enumerate(ids)}
    marks = ", ".join(f":{n}" for n in names)
    rows = db.execute(text(
        f"SELECT e.id, s.author_id, s.board_id, s.legal_hold, s.scrubbed_utc FROM log_entries e "
        f"JOIN log_snapshots s ON s.id = e.snapshot_id WHERE e.id IN ({marks})"), names)
    return {r[0]: {"author_id": r[1], "board_id": r[2], "legal_hold": bool(r[3]), "scrubbed_utc": r[4]} for r in rows}


def entry_with_copy(db, entry_id, level, member_boards=()):
    """(entry, snapshot) for one entry the reader may see (both None when they may not: it does not exist for them). The
    snapshot is the full row, text included, or None when the entry kept none; the caller applies `snapshot_visible`."""
    where, params = visible_clause(level, member_boards)
    params["entry"] = int(entry_id)
    row = db.execute(text(f"SELECT {PUBLIC_COLUMNS}, snapshot_id FROM log_entries WHERE id = :entry AND {where}"), params).first()
    if row is None:
        return None, None
    entry = dict(row._mapping)
    snapshot_id = entry.pop("snapshot_id")
    if not snapshot_id:
        return entry, None
    snap = db.execute(text(
        "SELECT id, author_id, board_id, target_submission_id, target_comment_id, legal_hold, title, url, body, body_html, "
        "created_utc, expires_utc, scrubbed_utc FROM log_snapshots WHERE id = :s"), {"s": snapshot_id}).first()
    return entry, (dict(snap._mapping) if snap else None)


def snapshot_visible(snapshot, *, viewer_id, level, guild_mod=False):
    """Whether a reader may open a snapshot: its author, admins (level 3; legal holds only level 4) and, for a guild
    hide, the guildmasters of that guild (`guild_mod`). A scrubbed one has no text for anyone."""
    if snapshot["scrubbed_utc"]:
        return False
    if snapshot["legal_hold"]:
        return level >= 4
    return bool(level >= 3 or (viewer_id and snapshot["author_id"] == viewer_id) or guild_mod)


def not_hidden_from(level, member_boards=(), column="modactions.id"):
    """(SQL, params): a condition on a legacy `modactions` row that leaves out the ones whose Log entry this reader may not
    read. A guild's own log is still drawn from `modactions`; the entry decides who sees a row (an entry written while the
    guild was private stays the guild's after it goes public). A row with no entry yet (the copy failed, or the backfill has
    not run) is shown as it always was, so the log never loses a row."""
    where, params = visible_clause(level, member_boards, alias="e")
    return f"NOT EXISTS (SELECT 1 FROM log_entries e WHERE e.legacy_modaction_id = {column} AND NOT {where})", params


def notes_for(db, modaction_ids):
    """{legacy modaction id: {rule_key, reason, redacted_utc}} for a page of a guild's log: the rule an action cited and
    the reason written for it, which the old note column did not carry (an admin's removal, a rule menu)."""
    ids = sorted({int(i) for i in modaction_ids or () if i})
    if not ids:
        return {}
    names = {f"m{n}": i for n, i in enumerate(ids)}
    marks = ", ".join(f":{n}" for n in names)
    rows = db.execute(text(f"SELECT legacy_modaction_id, rule_key, reason, redacted_utc FROM log_entries "
                           f"WHERE legacy_modaction_id IN ({marks})"), names)
    return {r[0]: {"rule_key": r[1], "reason": "" if r[3] else r[2], "redacted_utc": r[3]} for r in rows}


def snapshot_for(db, post_id=None, comment_id=None):
    """The newest private copy of an item that still has its text, or None (what an author who deleted it, a guild that
    hid it or an admin that removed it can still open). The caller decides who may read it (`snapshot_visible`)."""
    column, value = ("target_submission_id", post_id) if post_id else ("target_comment_id", comment_id)
    if not value:
        return None
    row = db.execute(text(
        f"SELECT id, entry_id, author_id, board_id, legal_hold, title, url, body, body_html, created_utc, expires_utc, scrubbed_utc "
        f"FROM log_snapshots WHERE {column} = :v AND scrubbed_utc = 0 ORDER BY id DESC LIMIT 1"), {"v": int(value)}).first()
    return dict(row._mapping) if row else None


# --- keeping and scrubbing ----------------------------------------------------------------------------------------------

def scrub_due(db, now=None):
    """Empty the text of every snapshot past its date (the row and the entry stay): returns how many."""
    now = int(now if now is not None else time.time())
    result = db.execute(text(
        "UPDATE log_snapshots SET title = NULL, url = NULL, body = NULL, body_html = NULL, scrubbed_utc = :now "
        "WHERE scrubbed_utc = 0 AND expires_utc > 0 AND expires_utc <= :now"), {"now": now})
    return result.rowcount or 0


def scrub_for(db, post_ids=(), comment_ids=(), now=None):
    """Empty the text of every snapshot of these posts and comments NOW (an admin's purge or obliteration means the text
    must be gone, so a private copy here may not outlive it). Returns how many."""
    now = int(now if now is not None else time.time())
    total = 0
    for column, ids in (("target_submission_id", post_ids), ("target_comment_id", comment_ids)):
        ids = [int(i) for i in ids or ()]
        if not ids:
            continue
        names = {f"i{n}": i for n, i in enumerate(ids)}
        marks = ", ".join(f":{n}" for n in names)
        total += db.execute(text(
            f"UPDATE log_snapshots SET title = NULL, url = NULL, body = NULL, body_html = NULL, scrubbed_utc = :now "
            f"WHERE scrubbed_utc = 0 AND {column} IN ({marks})"), {"now": now, **names}).rowcount or 0
    return total


def redact(db, entry_id, admin_id, now=None):
    """Hide an entry's reason from everyone but staff (Wikipedia's revision deletion: the entry stays). Level 4 only,
    which the caller checks."""
    db.execute(text("UPDATE log_entries SET redacted_utc = :now, redacted_by = :by WHERE id = :e AND redacted_utc = 0"),
               {"now": int(now if now is not None else time.time()), "by": admin_id, "e": entry_id})


# --- account sanctions: one write point for every ban and unban -----------------------------------------------------------------

# What the site's own code says when it suspends someone (the msgids it passes to `User.ban`): the rule it cites and who may
# read the entry. Ban-evasion detection is staff-only, so it never teaches anyone how it is spotted; the one about minors is
# for level-4 admins only.
AUTOMATIC_REASONS = {
    "Sexualizing minors": ("illegal", LEGAL),
    "Digitally malicious content": ("illegal", PUBLIC),
    "Digitally malicious content is not allowed.": ("illegal", PUBLIC),
    "Engaging in illegal activity": ("illegal", PUBLIC),
    "Spamming.": ("evasion", PUBLIC),
    "spam": ("evasion", PUBLIC),
    "seo spam": ("evasion", PUBLIC),
    "Evading a site-wide ban": ("evasion", STAFF),
}
_log = logging.getLogger(__name__)


def record_quietly(db, *args, **kwargs):
    """`record` inside a savepoint: a failure to write the Log is logged loudly and never undoes the action itself.
    Returns the entry id or None."""
    try:
        with db.begin_nested():
            return record(db, *args, **kwargs)
    except ReasonRequired:
        raise
    except Exception:
        _log.exception("the Log could not record %s", args[:2])
        return None


def record_ban(db, user_id, actor_id, *, days=0, reason="", rule=None, staff_note="", floor=PUBLIC, automatic=False,
               notice_sent=False):
    """A suspension (`days` > 0) or a permanent ban. An automatic one (the site's own rules, `automatic`) is the system,
    and takes its rule and audience from AUTOMATIC_REASONS; an admin's is the admin's, with the rule they chose."""
    if automatic:
        known_rule, known_floor = AUTOMATIC_REASONS.get(reason or "", ("", PUBLIC))
        rule, floor = rule or known_rule, max(floor, known_floor)
    return record_quietly(db, "suspend_user" if days else "ban_user", actor_id, target_user_id=user_id, rule=rule,
                          reason=reason or "", staff_note=staff_note or "", role=SYSTEM if automatic else ADMIN,
                          floor=floor, notice_sent=notice_sent)


def record_unban(db, user_id, actor_id, *, reason="", automatic=False, notice_sent=False):
    return record_quietly(db, "unban_user", actor_id, target_user_id=user_id, reason=reason or "",
                          role=SYSTEM if automatic else ADMIN, notice_sent=notice_sent)
