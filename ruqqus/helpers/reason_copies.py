"""The other places a decision's written reason lives, and the clearing of them when an admin redacts the entry (the second review of the
Log, 2026-11-17: C48).

A redaction (`action_log.redact` / `correct`, level 4) hides an entry's reason from everyone below staff **in the Log**. But the reason a
person typed is also stored where the decision's own pages read it: the guild's own mod log (`modactions._note`), the removed or hidden
post's page, the hidden comment's line, the banned account's profile and the banned guild's page, the exile list, the history rows
of a hide, and the summary of an edit (the author's note on what they changed is the edit entry's reason and the history page's `Summary:`).
Left there, a redaction hid a private detail from `/log` and not from the page the item itself is on.

`scrub(db, entry_id)` clears **only the copy of that decision's own reason**, each in a savepoint of its own (one that fails is counted and
named, and never undoes the redaction or another copy), and it never touches what else the row holds:

* a column that holds the reason of its **latest** decision is cleared only while no later decision of that kind exists for the same
  target (`_superseded`, ordered by `(created_utc, id)` like the Log): redacting an older decision never blanks a newer one's line;
* the **site's own** words are not copies (the spam guard's, the ban-evasion and image checks' messages, "CSAM [1]"): only an admin's
  removal or ban writes a person's words into those columns, so those copies are cleared for an admin's entry alone;
* **nothing about the state changes**: a banned account stays banned, a removed post removed, a hidden post hidden, an exile active; the
  pages that print a reason all draw nothing when it is empty (the admin panel's "Reason:" lines are guarded), so a blank reason is a page
  with no reason line, as the Log draws a redacted entry;
* the guild log's note keeps the marker that says an admin did it (`action_log.ADMIN_MARKERS`), because `ModAction.by_admin` reads it to
  draw "the site" instead of the admin's name: blanking it altogether would name the admin in public.

What is **not** a copy and is never touched: the staff notes of the Log, the private copies of the item's words (`log_snapshots`), an
obliteration's record (level 4 already), appeals and reports, the unhide or unexile entries' own reasons, the site-written `ban_reason`
messages, and the reasons of takedowns whose only home is the entry (a wiki revision, a story, a channel message, a Space's board, a chat
message). **The notice the person was sent** carries the reason inside their own notification and is not rewritten: it is a private message
to the one person the decision is about (and it names no one else). A line already sent to Discord can't be recalled.

Plain `text()` SQL on SQLite and Postgres (tests/test_reason_copies.py runs it on SQLite, tests/pg/test_reason_copies.py on the real app).
`action_log.correct` imports this module when it runs (never at the top: this module reads that one's rules), and
`scripts/scrub_redacted_reasons.py` runs `scrub_redacted` once for redactions made before this existed.
"""
import html
import logging

from sqlalchemy import text

from ruqqus.helpers import action_log

_log = logging.getLogger(__name__)

WINDOW = 120            # seconds either side of an entry in which a history row or an alt's entry belongs to the same decision

_ENTRY_COLUMNS = ("id, created_utc, action, actor_id, actor_role, audience, board_id, target_user_id, subject_user_id, "
                  "target_submission_id, target_comment_id, reason, legacy_modaction_id")

_BAN_ACTIONS = ("ban_user", "suspend_user")
_EXILE_ACTIONS = ("exile_user", "exile_alias")
# the one removal an image check writes without a reason on the post (action_log.AUTOMATIC_IMAGE_REASON): it overwrites nothing
_IMAGE_CHECK = action_log.AUTOMATIC_IMAGE_REASON

# an author's own edit: the entry's reason IS the edit summary (routes/posts.py `apply_post_edit`, routes/comments.py `edit_comment` write both from the one
# form field), and the same words are the summary of the history row the item's public history page draws
_EDITS = {"edit_post": "target_submission_id", "edit_comment": "target_comment_id"}

# what each hide's history row is called and where it points (routes/boards.py, routes/comments.py)
_HISTORY = {"hide_post_from_guild": ("guild_hide", "target_submission_id"),
            "hide_comment_from_guild": ("guild_hide", "target_comment_id"),
            "author_hide_comment": ("author_hide", "target_comment_id")}


# --- the pure parts ------------------------------------------------------------------------------------------------------------------

def scrubbed_note(note):
    """What a guild mod-log note becomes once its entry is redacted: None when there is nothing to change (it holds no reason: empty, or
    only a marker the site's code wrote, such as "admin action" or "siege"), otherwise the replacement. An admin's marker is kept
    (`ModAction.by_admin` reads it), so "admin action | the reason" becomes "admin action"; anything else becomes empty."""
    plain = html.unescape(note or "")
    if not action_log.reason_in(plain).strip() or plain.strip().lower() in action_log.AUTOMATIC_NOTES:
        return None                          # nothing a person wrote: empty, a marker, or the one word the site's own checks write ("spam")
    lowered = plain.lower()
    for marker in action_log.ADMIN_MARKERS:
        if marker in lowered:
            return marker
    return ""


def _words(value):
    return " ".join(html.unescape(str(value or "")).split()).casefold()


def same_words(a, b):
    """Whether two stored texts of one reason are the same words: case, spacing and HTML entities set aside, compared by their first 120
    characters (one copy is cut at the column's width, another at the Log's). Empty is never the same as anything."""
    a, b = _words(a), _words(b)
    return bool(a) and bool(b) and a[:120] == b[:120]


def describe(what, cleared, failed):
    """The staff note of the correction: what the admin said, then which copies were cleared and which could not be (so the staff know
    what is left to look at)."""
    note = action_log.clean_reason(what, 300)
    if cleared:
        note += " (also cleared: " + ", ".join(cleared) + ")"
    if failed:
        note += " (could not clear: " + ", ".join(failed) + ")"
    return action_log.clean_reason(note)


# --- reading ---------------------------------------------------------------------------------------------------------------------------

def _entry(db, entry_id):
    row = db.execute(text(f"SELECT {_ENTRY_COLUMNS} FROM log_entries WHERE id = :i"), {"i": int(entry_id)}).first()
    return dict(row._mapping) if row is not None else None


def _superseded(db, e, actions, where_sql, params, *, only_with_reason=False, ignore_reason=None):
    """Whether a LATER decision of this kind exists for the same target: the live column then holds that one's words, not this entry's.
    `where_sql` is a fixed fragment written here (never a person's text) and every value in it is a bound parameter."""
    names = {f"a{n}": action for n, action in enumerate(actions)}
    marks = ", ".join(f":{n}" for n in names)
    sql = (f"SELECT 1 FROM log_entries o WHERE o.action IN ({marks}) AND {where_sql} AND o.id <> :me "
           f"AND (o.created_utc > :at OR (o.created_utc = :at AND o.id > :me))")
    values = {**params, **names, "me": int(e["id"]), "at": int(e["created_utc"] or 0)}
    if only_with_reason:
        sql += " AND COALESCE(o.reason, '') <> ''"
    if ignore_reason is not None:
        sql += " AND COALESCE(o.reason, '') <> :ignored"
        values["ignored"] = ignore_reason
    return db.execute(text(sql + " LIMIT 1"), values).first() is not None


# --- the copies ------------------------------------------------------------------------------------------------------------------------
# Each takes the database and the entry (a dict) and answers how many rows it cleared. It never raises for "nothing to do".

def _guild_log_note(db, e):
    """The guild's own mod log: the note of the entry's ModAction, which the guild's log page and its JSON print. Not for an action on an alias, whose
    note is a label ("Anonymous #3"), never a reason."""
    if not e["legacy_modaction_id"] or e["action"] in action_log.ALIAS_ACTIONS:
        return 0
    row = db.execute(text("SELECT _note FROM modactions WHERE id = :m"), {"m": int(e["legacy_modaction_id"])}).first()
    if row is None:
        return 0
    replacement = scrubbed_note(row[0])
    if replacement is None or replacement == (row[0] or ""):
        return 0
    return db.execute(text("UPDATE modactions SET _note = :n WHERE id = :m"), {"n": replacement, "m": int(e["legacy_modaction_id"])}).rowcount or 0


def _removed_post_reason(db, e):
    """The reason an admin gave for removing a post, drawn on its removed page and in its JSON (rendered markdown, so by place and not by text). Only an
    admin's removal writes a person's words there: the spam guard, ban evasion and CSAM write the site's own messages."""
    if e["action"] != "ban_post" or e["actor_role"] != action_log.ADMIN or not e["target_submission_id"]:
        return 0
    if _superseded(db, e, ("ban_post",), "o.target_submission_id = :t", {"t": e["target_submission_id"]}, ignore_reason=_IMAGE_CHECK):
        return 0
    return db.execute(text("UPDATE submissions_aux SET ban_reason = '' WHERE id = :t AND COALESCE(ban_reason, '') <> ''"),
                      {"t": int(e["target_submission_id"])}).rowcount or 0


def _hidden_post_reason(db, e):
    if e["action"] != "hide_post_from_guild" or not e["target_submission_id"]:
        return 0
    if _superseded(db, e, ("hide_post_from_guild",), "o.target_submission_id = :t", {"t": e["target_submission_id"]}):
        return 0
    return db.execute(text("UPDATE submissions_aux SET hidden_reason = '' WHERE id = :t AND COALESCE(hidden_reason, '') <> ''"),
                      {"t": int(e["target_submission_id"])}).rowcount or 0


def _hidden_comment_reason(db, e):
    if e["action"] != "hide_comment_from_guild" or not e["target_comment_id"]:
        return 0
    if _superseded(db, e, ("hide_comment_from_guild",), "o.target_comment_id = :t", {"t": e["target_comment_id"]}):
        return 0
    return db.execute(text("UPDATE comments_aux SET hidden_reason = '' WHERE id = :t AND COALESCE(hidden_reason, '') <> ''"),
                      {"t": int(e["target_comment_id"])}).rowcount or 0


def _author_hidden_comment_reason(db, e):
    if e["action"] != "author_hide_comment" or not e["target_comment_id"]:
        return 0
    if _superseded(db, e, ("author_hide_comment",), "o.target_comment_id = :t", {"t": e["target_comment_id"]}):
        return 0
    return db.execute(text("UPDATE comments_aux SET author_hidden_reason = '' WHERE id = :t AND COALESCE(author_hidden_reason, '') <> ''"),
                      {"t": int(e["target_comment_id"])}).rowcount or 0


def _clear_ban_reason(db, entry, user_id):
    """`users.ban_reason`, which holds the reason of the account's latest ban or suspension that gave one (`User.ban` writes it only when there is one)."""
    if _superseded(db, entry, _BAN_ACTIONS, "o.target_user_id = :u", {"u": int(user_id)}, only_with_reason=True):
        return 0
    return db.execute(text("UPDATE users SET ban_reason = '' WHERE id = :u AND COALESCE(ban_reason, '') <> ''"), {"u": int(user_id)}).rowcount or 0


def _account_ban_reason(db, e):
    """An admin's ban or suspension: the reason on the account's own and banned profile. The ban itself, its end and its roles are untouched."""
    if e["action"] not in _BAN_ACTIONS or e["actor_role"] != action_log.ADMIN or not e["target_user_id"]:
        return 0
    return _clear_ban_reason(db, e, e["target_user_id"])


def _alts_ban_reasons(db, e):
    """The same decision on the account's alts: the admin's ban of an account also bans its alts with the same words, each as a staff-only entry of its
    own, and each alt's banned profile prints them. Found by the same admin, the same words and the same minute, in staff-only entries, and only from
    the public decision: redacting an alt's own entry (already staff's alone) would otherwise blank the main account's line the Log still shows."""
    if (e["action"] not in _BAN_ACTIONS or e["actor_role"] != action_log.ADMIN or not e["target_user_id"] or not e["actor_id"]
            or (e["audience"] or 0) >= action_log.STAFF):
        return 0
    siblings = db.execute(text(
        "SELECT o.id, o.created_utc, o.target_user_id FROM log_entries o WHERE o.action IN ('ban_user', 'suspend_user') AND o.actor_role = 'admin' "
        "AND o.actor_id = :actor AND o.audience >= :staff AND o.id <> :me AND o.target_user_id IS NOT NULL AND o.target_user_id <> :tu "
        "AND o.reason = :reason AND o.created_utc BETWEEN :lo AND :hi"),
        {"actor": int(e["actor_id"]), "staff": action_log.STAFF, "me": int(e["id"]), "tu": int(e["target_user_id"]), "reason": e["reason"],
         "lo": int(e["created_utc"] or 0) - WINDOW, "hi": int(e["created_utc"] or 0) + WINDOW}).all()
    return sum(_clear_ban_reason(db, {"id": s[0], "created_utc": s[1]}, s[2]) for s in siblings)


def _guild_ban_reason(db, e):
    """An admin's ban of a guild: the reason on the banned guild's page and its JSON. The guild stays banned."""
    if e["action"] != "ban_guild" or not e["board_id"]:
        return 0
    if _superseded(db, e, ("ban_guild",), "o.board_id = :b", {"b": e["board_id"]}):
        return 0
    return db.execute(text("UPDATE boards SET ban_reason = '' WHERE id = :b AND COALESCE(ban_reason, '') <> ''"), {"b": int(e["board_id"])}).rowcount or 0


def _exile_list_reason(db, e):
    """The exile list's Reason column (`bans.mod_note`), which the guild's guildmasters read. An exile by alias names the account only as the entry's
    subject. The exile stays active."""
    if e["action"] not in _EXILE_ACTIONS or not e["board_id"]:
        return 0
    user_id = e["target_user_id"] or e["subject_user_id"]
    if not user_id:
        return 0
    if _superseded(db, e, _EXILE_ACTIONS, "(o.target_user_id = :u OR o.subject_user_id = :u) AND o.board_id = :b", {"u": int(user_id), "b": int(e["board_id"])}):
        return 0
    return db.execute(text("UPDATE bans SET mod_note = '' WHERE user_id = :u AND board_id = :b AND COALESCE(mod_note, '') <> ''"),
                      {"u": int(user_id), "b": int(e["board_id"])}).rowcount or 0


def _history_rows(db, e):
    """The history rows of a hide, which the item's public history page draws with the reason. A history row has no entry id, so it is found by what it
    says: the same kind of decision on the same item, in the same minute, with the same words (two hides with different words are told apart)."""
    kind = _HISTORY.get(e["action"])
    if kind is None:
        return 0
    action, column = kind
    target = e[column]
    if not target:
        return 0
    candidates = db.execute(text(
        f"SELECT id, reason FROM content_edit_history WHERE action = :a AND {column} = :t AND COALESCE(reason, '') <> '' "
        f"AND created_utc BETWEEN :lo AND :hi"),
        {"a": action, "t": int(target), "lo": int(e["created_utc"] or 0) - WINDOW, "hi": int(e["created_utc"] or 0) + WINDOW}).all()
    ids = [row[0] for row in candidates if same_words(row[1], e["reason"])]
    if not ids:
        return 0
    names = {f"i{n}": i for n, i in enumerate(ids)}
    marks = ", ".join(f":{n}" for n in names)
    return db.execute(text(f"UPDATE content_edit_history SET reason = NULL, text_severity = 0 WHERE id IN ({marks})"), names).rowcount or 0


def _edit_summaries(db, e):
    """The edit summary an author gave, which the item's public history page prints under the version edited away (`Summary:`). The entry of an edit
    (`edit_post`, `edit_comment`) carries the same note as its reason, so redacting the entry would hide it from the Log and leave it on the history page.
    A history row has no entry id, so it is found by what it says: an edit of the same item by the same account, in the same minute, with the same words.
    A post's edit is one row for the post and one for each live forwarded copy, all of one `edit_group` (hiding a version hides them all), so the copies'
    rows go with it. Only the summary is cleared: the version itself, who edited and when stay as they were."""
    column = _EDITS.get(e["action"])
    target = e[column] if column else None
    if not target:
        return 0
    sql = (f"SELECT id, summary, edit_group FROM content_edit_history WHERE action = 'edit' AND {column} = :t AND COALESCE(summary, '') <> '' "
           f"AND created_utc BETWEEN :lo AND :hi")
    params = {"t": int(target), "lo": int(e["created_utc"] or 0) - WINDOW, "hi": int(e["created_utc"] or 0) + WINDOW}
    if e["actor_id"]:
        sql += " AND actor_id = :actor"
        params["actor"] = int(e["actor_id"])
    found = [row for row in db.execute(text(sql), params).all() if same_words(row[1], e["reason"])]
    if not found:
        return 0
    ids = {f"i{n}": row[0] for n, row in enumerate(found)}
    groups = {f"g{n}": group for n, group in enumerate(sorted({row[2] for row in found if row[2] is not None}))}
    where = f"id IN ({', '.join(f':{n}' for n in ids)})"
    if groups:
        where += f" OR edit_group IN ({', '.join(f':{n}' for n in groups)})"
    return db.execute(text(f"UPDATE content_edit_history SET summary = NULL, text_severity = 0 WHERE ({where}) AND COALESCE(summary, '') <> ''"),
                      {**ids, **groups}).rowcount or 0


# (label, function, whether the label carries the count): in the order they are cleared, and the order the staff note lists them
COPIES = (
    ("the guild log's note", _guild_log_note, False),
    ("the removed post's reason", _removed_post_reason, False),
    ("the hidden post's reason", _hidden_post_reason, False),
    ("the hidden comment's reason", _hidden_comment_reason, False),
    ("the reason a post's author gave for hiding a comment", _author_hidden_comment_reason, False),
    ("the account's ban reason", _account_ban_reason, False),
    ("the alts' ban reasons", _alts_ban_reasons, False),
    ("the guild's ban reason", _guild_ban_reason, False),
    ("the exile list's reason", _exile_list_reason, False),
    ("history rows", _history_rows, True),
    ("edit summaries", _edit_summaries, True),
)


def scrub(db, entry_id):
    """Clear every live copy of this entry's reason. Returns (cleared, failed): the labels of what was cleared (a count in the history rows') and of
    what could not be, each failure counted for /admin (`action_log.note_failure`). Never raises, and an entry with no reason clears nothing:
    there is nothing recorded to hide. Safe to run twice (the second finds nothing)."""
    e = _entry(db, entry_id)
    if e is None or not (e["reason"] or "").strip():
        return [], []
    cleared, failed = [], []
    for label, copy, counted in COPIES:
        try:
            with db.begin_nested():
                changed = int(copy(db, e) or 0)
        except Exception as error:
            _log.exception("the reason of entry %s could not be cleared from %s", entry_id, label)
            action_log.note_failure(db, "reason_copy", error)
            failed.append(label)
            continue
        if changed:
            cleared.append(f"{label} ({changed})" if counted else label)
    return cleared, failed


def scrub_redacted(db, after=0, limit=500):
    """Run `scrub` again for entries that were redacted before the copies were cleared with them (`scripts/scrub_redacted_reasons.py`): the next
    `limit` redacted entries after id `after`, oldest first. Writes no entry (the redaction already has its own). Returns (entries read, the last
    id read, copies cleared)."""
    ids = [r[0] for r in db.execute(text("SELECT id FROM log_entries WHERE redacted_utc > 0 AND id > :after ORDER BY id LIMIT :limit"),
                                    {"after": int(after), "limit": int(limit)})]
    total = 0
    for entry_id in ids:
        cleared, _ = scrub(db, entry_id)
        total += len(cleared)
    return len(ids), (ids[-1] if ids else int(after)), total
