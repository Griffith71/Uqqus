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
from ruqqus.helpers.wordfilter import is_hidden

PUBLIC, GUILD, STAFF, LEGAL = 0, 1, 2, 3

CONTENT, SANCTION, GUILD_CHANGE, SUPPRESSION, DELETION, EDIT = "content", "sanction", "guild", "suppression", "deletion", "edit"
TYPES = (CONTENT, SANCTION, GUILD_CHANGE, SUPPRESSION, DELETION, EDIT)

GUILDMASTER, ADMIN, SYSTEM, AUTHOR = "guildmaster", "admin", "system", "author"
ROLES = (GUILDMASTER, ADMIN, SYSTEM, AUTHOR)
SITE_ACCOUNT_ID = 1                 # the site's own account: what an automatic action is done as
SITE_BOARDS = (1, 2)                # +general and +systemprofile (where every profile post lives): the site's own, not guilds anyone can name

# What a private copy is a copy OF, which decides who may open it (`snapshot_visible`), for how long it is kept and whether it
# is public. `delete` is the copy of an item its author deleted after the grace window (helpers/deletion.py): it never expires, and its
# text is drawn through the Log to whoever the ENTRY's audience allows (`log_snapshots.public`: not for a child account's or a removed item's).
# `evidence` is the documentation of a sanction (helpers/sanction_evidence.py): the content an exile cites, as it was when the decision was made,
# for the person, the deciding guild's guildmasters and the admins; kept a year.
HIDE, AUTHOR_HIDE, GRACE, DELETE, LEGAL_KIND, EVIDENCE = "hide", "author_hide", "grace", "delete", "legal", "evidence"
SNAPSHOT_KINDS = (HIDE, AUTHOR_HIDE, GRACE, DELETE, LEGAL_KIND, EVIDENCE)

SNAPSHOT_DAYS = 90                  # how long the text of what was hidden, removed or deleted is kept privately
LEGAL_HOLD_DAYS = 365               # illegal content: the one-year preservation duty
EVIDENCE_DAYS = 365                 # what a sanction relied on: as long as it can sensibly be appealed
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
    "author_hide_comment", "author_unhide_comment", "remove_broadcast_message", "remove_space_message", "end_space",
    "community_note_add", "community_note_replace", "community_note_remove",
    "hide_post_from_guild", "unhide_post_from_guild", "hide_comment_from_guild", "unhide_comment_from_guild",
    "approve_post", "kick_post", "yank_post", "ban_post", "unban_post", "ban_comment", "unban_comment",
    "purge_post", "purge_comment", "obliterate_post", "obliterate_comment", "set_sensitive", "unset_sensitive", "force_sensitive_post",
    "set_sensitive_comment", "unset_sensitive_comment", "channel_message_remove", "remove_wiki_revision",
    "remove_story", "hide_revision",
}
# staff-only entries about a person: sanctions in kind, never public
_STAFF_SANCTIONS = {"admin_alt_link", "admin_ip_ban", "admin_signup", "automatic_hold", "age_lock"}
# an action on an alias ("Anonymous #3", helpers/alias_actions.py): an exile or a chat mute whose subject is a number in a thread, never an account. Its entry
# carries the number (`alias_n`, public: it is on the item already) and, for the staff and the appeal alone, the account (`subject_user_id`)
ALIAS_ACTIONS = ("exile_alias", "unexile_alias", "chatban_alias", "unchatban_alias")
_SANCTION = {
    "exile_user", "unexile_user", "chatban_user", "unchatban_user", "exile_alias", "unexile_alias", "chatban_alias", "unchatban_alias", "suspend_user", "unsuspend_user", "ban_user",
    "unban_user", "ban_guild", "unban_guild", "clear_display_name", "force_sensitive_user", "age_declaration",
    "child_removed_with_parent",
} | _STAFF_SANCTIONS
_SUPPRESSION = {"suppress_content"}
_DELETION = {"author_delete_post", "author_delete_comment"}      # a member's own delete after the grace window (helpers/deletion.py)
EDIT_ACTIONS = {"edit_post", "edit_comment"}                      # a member's own edit after the grace window: named, unless the item is anonymous

# What can't be a guild's or the public's business: always these audiences, whatever the guild.
ACCOUNT_ACTIONS = {"suspend_user", "unsuspend_user", "ban_user", "unban_user"}      # public: no guild, post or comment in them
# Public by their nature, with or without a guild: a decision about what members read, which the site stands behind in the open
# (the owner's default for the sweep of 2026-11-05, which he can open up or narrow): a community note is the site's own words
# under a post, and an admin's removal of a message in a public channel or a Space's board is a removal like any other
PUBLIC_ACTIONS = {"remove_broadcast_message", "remove_space_message", "end_space", "community_note_add", "community_note_replace",
                  "community_note_remove"}
# What the site's staff do to run it: its defences, the people it holds back, its lists and its bots. Never the public's business
# (an entry about an IP ban, an alt link or a word-list edit would teach how they are caught); staff read them on /admin/log.
# The specifics are in `staff_note`, never in a column the public reads.
RUNNING_ACTIONS = {
    "admin_badge", "admin_alt_link", "admin_ip_ban", "admin_image_ban", "admin_image_purge", "admin_word_filter", "admin_app",
    "admin_trending", "admin_feed_server", "admin_bot", "admin_signup", "admin_throttle", "admin_chat_report", "admin_siege",
    "admin_domain", "redact_entry", "correct_entry", "automatic_hold", "auto_space_takedown", "age_lock", "admin_evidence",
    "remove_chat_message",
}
STAFF_ACTIONS = {"chat_keys_reset", "clear_display_name", "force_sensitive_user", "force_sensitive_post", "child_removed_with_parent",
                 "remove_story", "age_declaration"} | RUNNING_ACTIONS
LEGAL_ACTIONS = {"suppress_content"}

# Actions that must say why (Wikipedia's reason): **every one is refused with no reason by the route that makes it** (a reason box the person
# must fill: `tests/test_log_d4.py` `ENFORCED` names each route and fails when one stops asking, or when an action is listed here that no route
# enforces). Since 2026-11-14 (the owner) that includes every exile and chat mute, by name or by alias, and every admin takedown. A caller
# with a reason box may also pass `require_reason=True` to `record`.
REASON_REQUIRED = {
    "hide_post_from_guild", "hide_comment_from_guild", "author_hide_comment", "ban_post", "ban_comment", "suspend_user", "ban_user",
    "ban_guild", "suppress_content", "hide_revision",
    "exile_user", "exile_alias", "chatban_user", "chatban_alias",
    "purge_post", "purge_comment", "clear_display_name", "remove_wiki_revision", "remove_broadcast_message", "remove_space_message",
    "end_space", "remove_story", "remove_chat_message",
}

# the markers an admin or automatic writer already puts in a ModAction's note (routes/admin_api.py, routes/settings.py,
# helpers/wrappers.py): they mean "done by the site's staff", not a reason a person wrote
ADMIN_MARKERS = ("admin action", "admin_action", "by the site's admins")
_LEGAL_NOTES = ("csam", "banned image")
_STAFF_NOTES = ("ban evasion",)

PUBLIC_COLUMNS = ("id, created_utc, log_type, action, actor_id, actor_role, board_id, target_user_id, "
                  "target_submission_id, target_comment_id, rule_key, reason, reason_severity, audience, redacted_utc, until_utc, alias_n")
STAFF_COLUMNS = PUBLIC_COLUMNS + ", subject_user_id, staff_note, snapshot_id, notice_sent, redacted_by, legacy_modaction_id, linked_entry_id"


class ReasonRequired(ValueError):
    """The action needs a written reason (the message is the one to show)."""


def type_of(action):
    if action in _SUPPRESSION:
        return SUPPRESSION
    if action in _DELETION:
        return DELETION
    if action in EDIT_ACTIONS:
        return EDIT
    if action in _SANCTION:
        return SANCTION
    if action in _CONTENT:
        return CONTENT
    return GUILD_CHANGE


def role_of(actor_id, admin_level, note="", role=None):
    """Who acted, as a role: the site's own account is the system, a note an admin route writes marks an admin, an
    account at admin level 3 doing an admin-only action is an admin (the caller says so with `role`), anyone else
    is a guildmaster."""
    if role in ROLES:
        return role
    if actor_id == SITE_ACCOUNT_ID:
        return SYSTEM
    lowered = (note or "").lower()
    # the markers are what the site's own admin routes write; a guildmaster's free text (a hide's reason is the same note)
    # can say anything, so a marker only counts for an account that really is an admin (the audit, 2026-11-05: H3)
    if (admin_level or 0) >= 3 and any(mark in lowered for mark in ADMIN_MARKERS):
        return ADMIN
    if lowered.strip() == "siege" and (admin_level or 0) >= 3:
        return ADMIN
    return GUILDMASTER


def audience_for(action, *, board_id=None, private=False, note="", floor=PUBLIC, trusted=False):
    """The audience an entry is written with; `floor` can only make it narrower (a caller that knows more, such as
    "this was a ban on an alt", says STAFF). The words in `note` mark an audience only when `trusted` (the writer is an admin
    or the site, whose code writes those words): a guildmaster's reason is free text, and "suspected ban evasion" in it must not
    take their own entry out of the public Log (the audit, 2026-11-05: H3)."""
    lowered = (note or "").lower() if trusted else ""
    if action in LEGAL_ACTIONS or any(mark in lowered for mark in _LEGAL_NOTES):
        audience = LEGAL
    elif action in STAFF_ACTIONS or any(mark in lowered for mark in _STAFF_NOTES):
        audience = STAFF
    elif action in ACCOUNT_ACTIONS:
        audience = PUBLIC
    elif action in PUBLIC_ACTIONS and not (board_id and private):
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


def _is_anonymous(db, post_id=None, comment_id=None):
    """Whether the post or comment is anonymous (a missing one is not)."""
    if post_id:
        row = db.execute(text("SELECT is_anonymous FROM submissions WHERE id = :i"), {"i": post_id}).first()
        if row and row[0]:
            return True
    if comment_id:
        row = db.execute(text("SELECT is_anonymous FROM comments WHERE id = :i"), {"i": comment_id}).first()
        if row and row[0]:
            return True
    return False


def snapshot_of(item, board_id=None, legal_hold=False, comment=False, kind=None, hider_id=None, public=False, sensitive=False):
    """What to keep of a post or comment (a Submission or a Comment, or a stand-in with the same attributes). `kind` says what
    the copy is of (`SNAPSHOT_KINDS`; a legal hold is `legal`, anything else a guild's `hide` unless the caller says);
    `hider_id` is the account that hid it where that is not a guild (a post's author hiding a comment may open it too). `public` says
    the copy's text may be drawn through the Log (a `delete` copy of an item that was public); its word-filter rating and whether
    it was `sensitive` are kept with it, so a reader's filter hides it as it hid the live item."""
    return {
        "kind": LEGAL_KIND if legal_hold else (kind or HIDE),
        "hider_id": hider_id,
        "post_id": None if comment else getattr(item, "id", None),
        "comment_id": getattr(item, "id", None) if comment else None,
        "author_id": getattr(item, "author_id", None),
        "board_id": board_id if board_id is not None else getattr(item, "board_id", None),
        "title": getattr(item, "title", None),
        "url": getattr(item, "url", None),
        "body": getattr(item, "body", None),
        "body_html": getattr(item, "body_html", None),
        "legal_hold": legal_hold,
        "public": bool(public) and not legal_hold,
        "word_severity": int(getattr(item, "word_severity", 0) or 0),
        "is_sensitive": bool(sensitive or getattr(item, "is_sensitive", False)),
    }


def _keep(db, snapshot, entry_id, now):
    kind = LEGAL_KIND if snapshot.get("legal_hold") else snapshot.get("kind")
    days = LEGAL_HOLD_DAYS if snapshot.get("legal_hold") else (EVIDENCE_DAYS if kind == EVIDENCE else SNAPSHOT_DAYS)
    if kind not in SNAPSHOT_KINDS:
        raise ValueError(f"a private copy needs a kind: {kind!r}")     # no default: who may open it depends on it
    expires = 0 if kind == DELETE else now + days * DAY               # a delete copy never expires: only an admin's scrub empties it
    row = db.execute(text(
        "INSERT INTO log_snapshots (entry_id, author_id, board_id, target_submission_id, target_comment_id, legal_hold, kind, "
        "hider_id, title, url, body, body_html, created_utc, expires_utc, public, word_severity, is_sensitive) "
        "VALUES (:entry, :author, :board, :post, :comment, :hold, :kind, :hider, :title, :url, :body, :html, :now, :expires, "
        ":public, :severity, :sensitive) RETURNING id"),
        {"entry": entry_id, "author": snapshot.get("author_id"), "board": snapshot.get("board_id"),
         "kind": kind, "hider": snapshot.get("hider_id") or None,
         "post": snapshot.get("post_id"), "comment": snapshot.get("comment_id"),
         "hold": bool(snapshot.get("legal_hold")), "title": (snapshot.get("title") or None),
         "url": (snapshot.get("url") or "")[:500] or None, "body": (snapshot.get("body") or None),
         "html": (snapshot.get("body_html") or None), "now": now, "expires": expires,
         "public": bool(snapshot.get("public")), "severity": int(snapshot.get("word_severity") or 0),
         "sensitive": bool(snapshot.get("is_sensitive"))}).first()
    return row[0]


def keep_snapshot(db, snapshot, now=None, entry_id=None):
    """A private copy of an item with no entry of its own (an author deleting their own post, or hiding a comment under it: not a
    guild's decision, so it is not in the Log, but the text is kept for 90 days for reports and legal requests). The snapshot
    must say its kind (`snapshot_of(..., kind=GRACE)`): a guildmaster may open only a guild's `hide`."""
    return _keep(db, snapshot, entry_id, int(now if now is not None else time.time()))


def rate(db, value):
    """The word filter's rating of a text a person wrote that the Log draws to other readers (0 clean, 1 profanity, 2 extreme): a reason,
    an edit summary. Drawn only to a reader whose filter level allows it (`visibility.is_hidden`), like any member text (the audit's M8).
    Never raises: the paper trail must not fail for want of a rating, and an unrated text is drawn as one from before the column was."""
    if not value:
        return 0
    try:
        from ruqqus.helpers.word_filter_store import get_filter
        return int(get_filter(db).severity(value) or 0)
    except Exception:
        _log.exception("the Log could not rate a text")
        return 0


def rescan_severities(db, rate_text, batch=500):
    """Re-rate the Log's own words with the live list (`word_filter_store.rescan`, and the first time after the columns were added): an entry's
    reason, and a history row's reason and summary together. `rate_text` is the filter's severity function. Returns how many ratings changed."""
    changed = 0
    for table, columns, column in (("log_entries", ("reason",), "reason_severity"),
                                   ("content_edit_history", ("reason", "summary"), "text_severity")):
        last = 0
        selected = ", ".join(f"COALESCE({c}, '')" for c in columns)
        while True:
            rows = db.execute(text(f"SELECT id, {selected}, {column} FROM {table} WHERE id > :last ORDER BY id LIMIT :n"),
                              {"last": last, "n": int(batch)}).all()
            if not rows:
                break
            for row in rows:
                words = " ".join(w for w in row[1:-1] if w)
                rated = int(rate_text(words) or 0) if words else 0
                if rated != (row[-1] or 0):
                    db.execute(text(f"UPDATE {table} SET {column} = :s WHERE id = :i"), {"s": rated, "i": row[0]})
                    changed += 1
            last = rows[-1][0]
    return changed


def record(db, action, actor_id, *, board_id=None, target_user_id=None, post_id=None, comment_id=None, rule=None,
           reason="", staff_note="", snapshot=None, role=None, note="", floor=PUBLIC, require_reason=False, now=None,
           legacy_id=None, created_utc=None, notice_sent=False, until_utc=0, linked_entry_id=None):
    """Write one entry and return its id. Raises ReasonRequired when the action needs a reason and has none (only when
    the caller has a place to ask for one: `require_reason`). `until_utc` is when a timed suspension ends; `linked_entry_id` the
    entry this one is about (its detail, its lapse, its reversal): the original is never edited."""
    reason = clean_reason(reason)
    if require_reason and action in REASON_REQUIRED and not reason:
        raise ReasonRequired("Give a reason.")
    now = int(created_utc if created_utc is not None else (now if now is not None else time.time()))
    resolved = role_of(actor_id, _admin_level(db, actor_id), note, role)
    audience = audience_for(action, board_id=board_id, private=_board_is_private(db, board_id), note=note, floor=floor,
                            trusted=resolved in (ADMIN, SYSTEM))
    if target_user_id and (post_id or comment_id) and _is_anonymous(db, post_id, comment_id):
        # an entry never pairs an account with an anonymous item (an exile citing it would say who wrote it, and the
        # account's own log and the `to=` filter list it): the exile stays, the pointer to the item does not (H6)
        post_id = comment_id = None
    row = db.execute(text(
        "INSERT INTO log_entries (created_utc, log_type, action, actor_id, actor_role, board_id, target_user_id, "
        "target_submission_id, target_comment_id, rule_key, reason, reason_severity, staff_note, audience, notice_sent, "
        "legacy_modaction_id, until_utc, linked_entry_id) "
        "VALUES (:now, :type, :action, :actor, :role, :board, :tuser, :tpost, :tcomment, :rule, :reason, :severity, :staff, :audience, "
        ":notice, :legacy, :until, :linked) RETURNING id"),
        {"now": now, "type": type_of(action), "action": action, "actor": actor_id,
         "role": resolved, "board": board_id or None,
         "tuser": target_user_id or None, "tpost": post_id or None, "tcomment": comment_id or None,
         "rule": parse_rule(rule), "reason": reason, "severity": rate(db, reason), "staff": clean_reason(staff_note),
         "audience": audience,
         "notice": bool(notice_sent), "legacy": legacy_id, "until": int(until_utc or 0),
         "linked": linked_entry_id or None}).first()
    entry_id = row[0]
    if snapshot:
        db.execute(text("UPDATE log_entries SET snapshot_id = :s WHERE id = :e"),
                   {"s": _keep(db, snapshot, entry_id, now), "e": entry_id})
    return entry_id


_BARE_MARKERS = {"admin action", "admin_action", "csam detected", "banned image", "ban evasion", "siege"}
AUTOMATIC_NOTES = {"spam": ("evasion", "Automatic spam check")}      # a ModAction note the site's own code writes, as the Log says it


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
    # an action on an alias keeps its label in the guild log's note: it is no reason (the Log draws the number from `alias_n`)
    plain = "" if kind in ALIAS_ACTIONS else html.unescape(note or "")
    rule, reason = None, reason_in(plain)
    if user_id == SITE_ACCOUNT_ID and plain.strip().lower() in AUTOMATIC_NOTES:
        # the site's own removals write a bare word ("spam"): the Log says it in the generic words it uses everywhere
        rule, reason = AUTOMATIC_NOTES[plain.strip().lower()]
    return record(db, kind, user_id, board_id=board_id, target_user_id=target_user_id, post_id=post_id,
                  comment_id=comment_id, rule=rule, reason=reason, note=plain, created_utc=created_utc or int(time.time()),
                  legacy_id=modaction_id)


_ENTRY_OF = "SELECT id, action, board_id, audience, created_utc FROM log_entries WHERE legacy_modaction_id = :i"


def mirror_from_table(db, modaction_id):
    """Copy one `modactions` row into the Log from the table itself: for the row whose copy failed when it was made (the
    after-insert hook is best effort, so a failed copy left the row with no entry, no rule, no reason and no preserved copy). Returns
    the entry id, or None when there is no such row."""
    row = db.execute(text("SELECT user_id, board_id, kind, target_user_id, target_submission_id, target_comment_id, _note, "
                          "created_utc FROM modactions WHERE id = :i"), {"i": int(modaction_id)}).first()
    if row is None:
        return None
    user_id, board_id, kind, target_user, post_id, comment_id, note, created = row
    return mirror_modaction(db, modaction_id, user_id, board_id, kind, target_user or None, post_id or None, comment_id or None,
                            note, created)


def annotate(db, modaction_id, *, rule=None, reason=None, staff_note=None, snapshot=None, floor=None, linked_entry_id=None, alias_n=None,
             subject_user_id=None):
    """Add what a route knows to the entry its ModAction became: the cited rule, a written reason, a private note, the
    item as it was. When the copy of the ModAction failed when it was made, it is made now (`mirror_from_table`), so the rule, the
    reason and the preserved copy are never the price of one failed write. `linked_entry_id` says which entry this one is about (an appeal's
    reversal). Returns the entry id, or None when there is no such row (the failure is then counted: `note_failure`)."""
    row = db.execute(text(_ENTRY_OF), {"i": modaction_id}).first()
    if row is None:
        try:
            with db.begin_nested():
                made = mirror_from_table(db, modaction_id)
        except Exception as error:
            _log.exception("the Log could not copy mod action %s", modaction_id)
            note_failure(db, "modaction", error)
            return None
        if made is None:
            return None
        row = db.execute(text(_ENTRY_OF), {"i": modaction_id}).first()
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
        sets.append("reason_severity = :severity")
        values["severity"] = rate(db, values["reason"])
    if staff_note is not None:
        sets.append("staff_note = :staff")
        values["staff"] = clean_reason(staff_note)
    if floor is not None and floor > audience:
        sets.append("audience = :audience")
        values["audience"] = floor
    if linked_entry_id:
        sets.append("linked_entry_id = :linked")
        values["linked"] = int(linked_entry_id)
    if alias_n:
        sets.append("alias_n = :alias_n")
        values["alias_n"] = int(alias_n)
    if subject_user_id:
        sets.append("subject_user_id = :subject")
        values["subject"] = int(subject_user_id)
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
    """Whether this account may open that preserved copy (None: there is none), by `snapshot_visible`'s rule: its item's
    author, admins (a legal hold only level 4), whoever hid it, and the guildmasters of the guild only for a guild's hide."""
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
    if role == AUTHOR:
        return "author"
    return "guildmaster" if role == GUILDMASTER else "admin"


# the public Log names no guild that is private or a Circle guild NOW, even for an entry written while it was open
_OPEN_GUILDS = ("(board_id IS NULL OR NOT EXISTS (SELECT 1 FROM boards b WHERE b.id = log_entries.board_id "
                "AND (b.is_private OR b.is_circle)))")


# an entry that names an account and also an item that is anonymous now: listing it under that account (or showing it on the
# item's page) would say who wrote the item. Written when the entry is made (`record` drops the item), and read here for an
# item that was made anonymous after it.
_NOT_PAIRED_WITH_ANONYMOUS = (
    "NOT (target_user_id IS NOT NULL AND ("
    "(target_submission_id IS NOT NULL AND EXISTS (SELECT 1 FROM submissions sp WHERE sp.id = log_entries.target_submission_id "
    "AND COALESCE(sp.is_anonymous, false))) OR "
    "(target_comment_id IS NOT NULL AND EXISTS (SELECT 1 FROM comments cp WHERE cp.id = log_entries.target_comment_id "
    "AND COALESCE(cp.is_anonymous, false)))))")


# the entry's item is anonymous NOW, whenever it was made so (a read-time check, as `about_scope` makes it for items): an edit's editor is named on an item
# that isn't, and is Anonymous on one that is (the audit's M10 and M11)
_ITEM_ANONYMOUS_NOW = (
    "(EXISTS (SELECT 1 FROM submissions sa WHERE sa.id = log_entries.target_submission_id AND COALESCE(sa.is_anonymous, false)) OR "
    "EXISTS (SELECT 1 FROM comments ca WHERE ca.id = log_entries.target_comment_id AND COALESCE(ca.is_anonymous, false)))")
# the editor's CREDIT on the post is anonymous (a co-author of a post written with others, slice D3f, decision 21): an edit by them is not named either
_ACTOR_CREDIT_ANONYMOUS = (
    "EXISTS (SELECT 1 FROM post_coauthors pc JOIN submissions sc ON sc.id = log_entries.target_submission_id "
    "WHERE pc.post_id = COALESCE(NULLIF(sc.repost_id, 0), sc.id) AND pc.user_id = log_entries.actor_id AND pc.is_anonymous)")
_EDIT_MARKS = "('edit_post', 'edit_comment')"
# below staff, filtering by "who" must not list an admin's entries (they are "Site admin"), a post author's hide of a comment (drawn "the post's author"), nor
# any author-role entry whose item is anonymous (an own delete, an edit of an anonymous item): it would list exactly one person's anonymous activity
_ACTOR_MAY_BE_FILTERED = (f"(actor_role <> 'admin' AND (actor_role <> 'author' OR "
                          f"(action IN {_EDIT_MARKS} AND NOT {_ITEM_ANONYMOUS_NOW} AND NOT {_ACTOR_CREDIT_ANONYMOUS})))")


def _anonymous_edit_items(db, rows):
    """{("post"|"comment", id)} among the edit entries of these rows whose item is anonymous now: their editor is not named."""
    posts = sorted({r["target_submission_id"] for r in rows if r["actor_role"] == AUTHOR and r["action"] in EDIT_ACTIONS and r["target_submission_id"]})
    comments = sorted({r["target_comment_id"] for r in rows if r["actor_role"] == AUTHOR and r["action"] in EDIT_ACTIONS and r["target_comment_id"]})
    found = set()
    for kind, table, ids in (("post", "submissions", posts), ("comment", "comments", comments)):
        if ids:
            names = {f"a{n}": int(i) for n, i in enumerate(ids)}
            marks = ", ".join(f":{n}" for n in names)
            found |= {(kind, r[0]) for r in db.execute(text(f"SELECT id FROM {table} WHERE id IN ({marks}) AND COALESCE(is_anonymous, false)"), names)}
    return found


def _anonymous_credit_edits(db, rows):
    """{entry id} among these rows' edit entries whose editor's CREDIT on the post is anonymous: an edit of a post written with others is drawn as its editor's alias
    (the Edits tab), never by name."""
    ids = sorted({r["id"] for r in rows if r["actor_role"] == AUTHOR and r["action"] in EDIT_ACTIONS and r["actor_id"] and r["target_submission_id"]})
    if not ids:
        return set()
    names = {f"c{n}": int(i) for n, i in enumerate(ids)}
    marks = ", ".join(f":{n}" for n in names)
    return {r[0] for r in db.execute(text(
        f"SELECT log_entries.id FROM log_entries WHERE log_entries.id IN ({marks}) AND {_ACTOR_CREDIT_ANONYMOUS}"), names)}


def fetch(db, *, level=0, member_boards=(), log_type=None, board_id=None, actor_id=None, target_user_id=None,
          rule=None, action=None, before_id=None, limit=50, open_guilds_only=None, scope=None, offset=0):
    """A page of entries, newest first, for a reader with this `level`: (rows, has_more). The staff-only columns come
    only for staff; an admin entry's actor_id is left out for everyone else. `open_guilds_only` leaves out entries of a guild
    that has since gone private or become a Circle guild: **on unless the reader is staff or the caller names the guilds the
    reader belongs to** (`member_boards`, a guild's own view) when left at None, because the public page never names such a guild
    and a caller that forgot to say so used to publish one; pass False to opt out. `scope` is
    `about_scope`'s (SQL, params): only the entries about one item or account. `offset` is for a page that merges the entries
    with another list."""
    where, params = visible_clause(level, member_boards)
    clauses = [where]
    if open_guilds_only is None:
        open_guilds_only = level < 3 and not member_boards
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
        # an admin is "Site admin" below staff, and a post's author hiding a comment is "the post's author" (their name is not drawn:
        # the post may be anonymous): filtering by either's id would list exactly their entries. An edit's editor is named only while its item is not anonymous
        clauses.append(_ACTOR_MAY_BE_FILTERED)
    if target_user_id not in (None, "") and level < 3:
        # an entry that names an account AND an item that is anonymous now would say who wrote it (H6)
        clauses.append(_NOT_PAIRED_WITH_ANONYMOUS)
    if before_id:
        # the order is (created_utc, id), not id: a backfill run after live entries exist gives old entries higher ids (the audit's M6).
        # The cursor stays an entry id, so the links are what they were
        clauses.append("(created_utc, id) < (SELECT b.created_utc, b.id FROM log_entries b WHERE b.id = :before)")
        params["before"] = int(before_id)
    params["limit"] = int(limit) + 1
    params["offset"] = max(0, int(offset))
    columns = STAFF_COLUMNS if level >= 3 else PUBLIC_COLUMNS
    rows = [dict(r._mapping) for r in db.execute(
        text(f"SELECT {columns} FROM log_entries WHERE {' AND '.join(clauses)} ORDER BY created_utc DESC, id DESC LIMIT :limit OFFSET :offset"),
        params)]
    more = len(rows) > limit
    rows = rows[:limit]
    if level < 3:
        anonymous = _anonymous_edit_items(db, rows)
        credit_hidden = _anonymous_credit_edits(db, rows)
        for row in rows:
            if row["actor_role"] == ADMIN:
                row["actor_id"] = None
            elif row["actor_role"] == AUTHOR:
                # an own delete and a post author's hide of a comment never name the author; an edit names its editor unless the item is anonymous now
                item = ("post", row["target_submission_id"]) if row["target_submission_id"] else ("comment", row["target_comment_id"])
                if row["action"] not in EDIT_ACTIONS or item in anonymous or row["id"] in credit_hidden:
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
    unpaired = "" if include_anonymous else f" AND {_NOT_PAIRED_WITH_ANONYMOUS}"
    if post_ids:
        marks, names = _numbered("sp", post_ids)
        parts.append(f"(target_submission_id IN ({marks}){unpaired})")
        params.update(names)
    if comment_ids:
        marks, names = _numbered("sc", comment_ids)
        parts.append(f"(target_comment_id IN ({marks}){unpaired})")
        params.update(names)
    if user_id:
        anonymous_post = "" if include_anonymous else " AND NOT COALESCE(s.is_anonymous, false)"
        anonymous_comment = "" if include_anonymous else " AND NOT COALESCE(c.is_anonymous, false)"
        # a UNION of three index-driven reads (the account, its posts, its comments), not an OR across columns that no index can serve (the
        # audit's M7): log_entries_target_user_idx, then submissions / comments by author and log_entries_post_idx / log_entries_comment_idx
        parts.append(
            "id IN (SELECT id FROM log_entries WHERE target_user_id = :about" + unpaired
            + " UNION SELECT e.id FROM log_entries e JOIN submissions s ON s.id = e.target_submission_id WHERE s.author_id = :about"
            + anonymous_post
            + " UNION SELECT e.id FROM log_entries e JOIN comments c ON c.id = e.target_comment_id WHERE c.author_id = :about"
            + anonymous_comment + ")")
        params["about"] = int(user_id)
    return ("(" + " OR ".join(parts) + ")", params) if parts else ("1 = 0", {})


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
    """{entry id: {author_id, board_id, legal_hold, kind, hider_id, scrubbed_utc}} for the entries on a page that kept a private
    copy of what they hid or removed. Asked only about entries the reader may already see; `snapshot_visible` says who may
    open one."""
    ids = sorted({int(i) for i in entry_ids or () if i})
    if not ids:
        return {}
    names = {f"c{n}": i for n, i in enumerate(ids)}
    marks = ", ".join(f":{n}" for n in names)
    rows = db.execute(text(
        f"SELECT e.id, s.author_id, s.board_id, s.legal_hold, s.scrubbed_utc, s.kind, s.hider_id FROM log_entries e "
        f"JOIN log_snapshots s ON s.id = e.snapshot_id WHERE e.id IN ({marks})"), names)
    return {r[0]: {"author_id": r[1], "board_id": r[2], "legal_hold": bool(r[3]), "scrubbed_utc": r[4], "kind": r[5],
                   "hider_id": r[6]} for r in rows}


def deleted_texts(db, entry_ids):
    """{entry id: the text a deleted item's `delete` copy may show} for a page of `deletion` entries: only a copy marked public, and
    only while an admin has not scrubbed it (a scrub empties the text and sets `scrubbed_utc`). The caller applies the reader's word
    filter, the sensitive rule and the guild's state (`helpers/deletion.py text_view`); the entry's audience has already decided who
    reads the line at all."""
    ids = sorted({int(i) for i in entry_ids or () if i})
    if not ids:
        return {}
    names = {f"t{n}": i for n, i in enumerate(ids)}
    marks = ", ".join(f":{n}" for n in names)
    rows = db.execute(text(
        f"SELECT entry_id, title, url, body_html, word_severity, is_sensitive, public, scrubbed_utc, board_id, target_submission_id, "
        f"target_comment_id FROM log_snapshots WHERE kind = 'delete' AND entry_id IN ({marks})"), names)
    return {r[0]: {"title": r[1], "url": r[2], "body_html": r[3], "word_severity": r[4], "is_sensitive": bool(r[5]),
                   "public": bool(r[6]), "scrubbed": bool(r[7]), "board_id": r[8], "post_id": r[9], "comment_id": r[10]} for r in rows}


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
        "SELECT id, author_id, board_id, target_submission_id, target_comment_id, legal_hold, kind, hider_id, title, url, body, "
        "body_html, created_utc, expires_utc, scrubbed_utc FROM log_snapshots WHERE id = :s"), {"s": snapshot_id}).first()
    return entry, (dict(snap._mapping) if snap else None)


def snapshot_visible(snapshot, *, viewer_id, level, guild_mod=False):
    """Whether a reader may open a snapshot: its author, admins (level 3; legal holds only level 4), the account that hid it
    (a post's author, for a comment under it) and, **for a guild's hide or a sanction's evidence only**, the guildmasters of that guild (`guild_mod`).
    A comment its author deleted, or a Circle post's copy, carries the guild's board id too, and the guildmasters have no
    business reading it (the audit, 2026-11-05: H2). The same for a sanction's `evidence`: the deciding guild's guildmasters made the decision
    on it. A scrubbed one has no text for anyone."""
    if snapshot["scrubbed_utc"]:
        return False
    kind = snapshot.get("kind")
    if snapshot["legal_hold"] or kind == LEGAL_KIND:
        return level >= 4
    if level >= 3:
        return True
    if viewer_id and (snapshot["author_id"] == viewer_id or snapshot.get("hider_id") == viewer_id):
        return True
    return bool(guild_mod and kind in (HIDE, EVIDENCE))


def not_hidden_from(level, member_boards=(), column="modactions.id"):
    """(SQL, params): a condition on a legacy `modactions` row that leaves out the ones whose Log entry this reader may not
    read. A guild's own log is still drawn from `modactions`; the entry decides who sees a row (an entry written while the
    guild was private stays the guild's after it goes public). A row with no entry yet (the copy failed, or the backfill has
    not run) is shown as it always was, so the log never loses a row."""
    where, params = visible_clause(level, member_boards, alias="e")
    return f"NOT EXISTS (SELECT 1 FROM log_entries e WHERE e.legacy_modaction_id = {column} AND NOT {where})", params


def notes_for(db, modaction_ids, word_level=None):
    """{legacy modaction id: {rule_key, reason, redacted_utc}} for a page of a guild's log: the rule an action cited and
    the reason written for it, which the old note column did not carry (an admin's removal, a rule menu). With a `word_level` (the
    reader's filter level) a reason their filter hides is left out, as any member text is."""
    ids = sorted({int(i) for i in modaction_ids or () if i})
    if not ids:
        return {}
    names = {f"m{n}": i for n, i in enumerate(ids)}
    marks = ", ".join(f":{n}" for n in names)
    rows = db.execute(text(f"SELECT legacy_modaction_id, rule_key, reason, redacted_utc, reason_severity FROM log_entries "
                           f"WHERE legacy_modaction_id IN ({marks})"), names)
    return {r[0]: {"rule_key": r[1], "reason": "" if r[3] or (word_level is not None and is_hidden(word_level, r[4])) else r[2],
                   "redacted_utc": r[3]} for r in rows}


def snapshot_for(db, post_id=None, comment_id=None, viewer=None):
    """The newest private copy of an item that still has its text, or None (what an author who deleted it, a guild that
    hid it or an admin that removed it can still open). With a `viewer`, the newest one THEY may open (an item can have a
    guild's hide and then its author's delete, and a guildmaster can read only the first); without, the newest of all, and the
    caller decides who may read it (`snapshot_visible`)."""
    column, value = ("target_submission_id", post_id) if post_id else ("target_comment_id", comment_id)
    if not value:
        return None
    rows = db.execute(text(
        f"SELECT id, entry_id, author_id, board_id, legal_hold, kind, hider_id, title, url, body, body_html, created_utc, "
        f"expires_utc, scrubbed_utc FROM log_snapshots WHERE {column} = :v AND scrubbed_utc = 0 ORDER BY id DESC LIMIT 10"),
        {"v": int(value)})
    for row in rows:
        snapshot = dict(row._mapping)
        if viewer is None or may_open_copy(snapshot, viewer):
            return snapshot
    return None


# --- keeping and scrubbing ----------------------------------------------------------------------------------------------

# the one statement that empties a private copy on its date: `helpers/retention.py` runs it every cycle, `scrub_due` runs it for a test
SCRUB_DUE = ("UPDATE log_snapshots SET title = NULL, url = NULL, body = NULL, body_html = NULL, scrubbed_utc = :now "
             "WHERE scrubbed_utc = 0 AND expires_utc > 0 AND expires_utc <= :now")


def scrub_due(db, now=None):
    """Empty the text of every snapshot past its date (the row and the entry stay): returns how many."""
    now = int(now if now is not None else time.time())
    return db.execute(text(SCRUB_DUE), {"now": now}).rowcount or 0


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
    # what an image check says of itself: no category, since a match can be a false positive (the owner, decision 1)
    "Automatic image check": ("illegal", PUBLIC),
    # a child account suspended with its parent (helpers/child_accounts.SUSPENDED_REASON): a public line would say it has a parent
    "This account is suspended.": ("", STAFF),
}
_log = logging.getLogger(__name__)


def note_failure(db, action, error):
    """Count an entry the Log could not write (the action itself stood): a row in `log_failures`, in a savepoint of its own so it
    can't undo anything, which /admin and /admin/log show. A database too sick to take the row is only logged."""
    try:
        with db.begin_nested():
            db.execute(text("INSERT INTO log_failures (created_utc, action, detail) VALUES (:now, :action, :detail)"),
                       {"now": int(time.time()), "action": str(action or "")[:32], "detail": str(error)[:300]})
    except Exception:
        _log.exception("the Log could not even note that %s failed", action)


FAILURE_DAYS = 7                # how far back /admin counts failures: a row older than that is no longer a worry


def failure_count(db, now=None, days=FAILURE_DAYS):
    """How many entries the Log failed to write in the last `days` (0 when the table is missing: a deploy before the migration)."""
    try:
        with db.begin_nested():
            return int(db.execute(text("SELECT COUNT(*) FROM log_failures WHERE created_utc >= :since"),
                                  {"since": int(now if now is not None else time.time()) - days * DAY}).scalar() or 0)
    except Exception:
        return 0


def unmirrored_count(db, now=None, grace=120, days=FAILURE_DAYS):
    """How many guild mod-log rows of the last `days` have no entry in the Log after `grace` seconds (what `reconcile` mends)."""
    now = int(now if now is not None else time.time())
    try:
        with db.begin_nested():
            return int(db.execute(text(
                "SELECT COUNT(*) FROM modactions m WHERE m.created_utc >= :since AND m.created_utc < :cutoff AND NOT EXISTS "
                "(SELECT 1 FROM log_entries e WHERE e.legacy_modaction_id = m.id)"),
                {"since": now - days * DAY, "cutoff": now - grace}).scalar() or 0)
    except Exception:
        return 0


def reconcile(db, now=None, limit=200, grace=120, days=FAILURE_DAYS):
    """Mend the paper trail: copy every guild mod-log row of the last `days` that has no entry (its copy failed when it was made),
    oldest first, each in a savepoint of its own so one bad row can't stop the rest. The `grace` seconds leave a row being made
    right now to its own after-insert hook. Returns (copied, failed); a failure is counted (`note_failure`)."""
    now = int(now if now is not None else time.time())
    ids = [r[0] for r in db.execute(text(
        "SELECT m.id FROM modactions m WHERE m.created_utc >= :since AND m.created_utc < :cutoff AND NOT EXISTS "
        "(SELECT 1 FROM log_entries e WHERE e.legacy_modaction_id = m.id) ORDER BY m.id LIMIT :limit"),
        {"since": now - days * DAY, "cutoff": now - grace, "limit": int(limit)})]
    copied = failed = 0
    for modaction_id in ids:
        try:
            with db.begin_nested():
                if mirror_from_table(db, modaction_id) is not None:
                    copied += 1
        except Exception as error:
            failed += 1
            _log.exception("the Log could not copy mod action %s", modaction_id)
            note_failure(db, "modaction", error)
    return copied, failed


def record_quietly(db, *args, **kwargs):
    """`record` inside a savepoint: a failure to write the Log is logged loudly, counted (`note_failure`: /admin says so) and never
    undoes the action itself. Returns the entry id or None."""
    try:
        with db.begin_nested():
            return record(db, *args, **kwargs)
    except ReasonRequired:
        raise
    except Exception as error:
        _log.exception("the Log could not record %s", args[:2])
        note_failure(db, args[0] if args else "", error)
        return None


# --- what the site's own code decides, and what the staff do to run it ---------------------------------------------------------

AUTOMATIC_REASON = "Automatic check"                  # the one generic wording of a public entry the site wrote itself
AUTOMATIC_IMAGE_REASON = "Automatic image check"      # an image check never says what it matched: it can be wrong (the owner, decision 1)
AUTOMATIC_SPAM_REASON = "Automatic spam check"


def record_automatic(db, action, *, target_user_id=None, post_id=None, comment_id=None, board_id=None, rule="evasion",
                     reason=AUTOMATIC_REASON, staff_note="", snapshot=None, floor=PUBLIC, notice_sent=False, until_utc=0):
    """An entry for something the site decided by itself (a removal by the spam guard, an image check, a ban its rules made): done by
    the system (the site's own account), public unless `floor` or the action says otherwise, in generic words that name no
    category (an image-check match can be a false positive, and an entry names the account). The detail goes in a separate
    level-4 entry (`record_detail`). Returns the entry id or None."""
    return record_quietly(db, action, SITE_ACCOUNT_ID, role=SYSTEM, target_user_id=target_user_id, post_id=post_id,
                          comment_id=comment_id, board_id=board_id, rule=rule, reason=reason, staff_note=staff_note,
                          snapshot=snapshot, floor=floor, notice_sent=notice_sent, until_utc=until_utc)


def record_detail(db, entry_id, detail, *, target_user_id=None, post_id=None, comment_id=None, board_id=None, legal=True):
    """The detail an automatic public entry never shows (a hash match, the CDN's 451): its own entry, linked to the public one, for
    level-4 admins (`legal`) or for staff, never the public. Returns the entry id or None."""
    if not entry_id:
        return None
    return record_quietly(db, "suppress_content" if legal else "admin_image_ban", SITE_ACCOUNT_ID, role=SYSTEM,
                          target_user_id=target_user_id, post_id=post_id, comment_id=comment_id, board_id=board_id,
                          staff_note=detail, floor=LEGAL if legal else STAFF, linked_entry_id=entry_id)


def link(db, entry_id, to_entry_id):
    """Say that an entry is about another (an appeal's reversal is about the decision it put right): the original is never touched."""
    if entry_id and to_entry_id:
        db.execute(text("UPDATE log_entries SET linked_entry_id = :to WHERE id = :e"), {"to": int(to_entry_id), "e": int(entry_id)})


def record_image_check_removal(db, *, post_id=None, comment_id=None, board_id=None, detail="", legal=False):
    """The entry for a post or comment the site's image check took down: public in generic words (rule: criminal activity), with
    what it matched in a separate entry for level-4 admins (`legal`: the CDN's child-abuse list answered 451) or for staff (a picture
    on the site's own banned list). Returns the public entry's id, or None. The removed item keeps its own text, as after an admin's
    removal, so no private copy is made."""
    public = record_automatic(db, "ban_post" if post_id else "ban_comment", post_id=post_id, comment_id=comment_id,
                              board_id=board_id, rule="illegal", reason=AUTOMATIC_IMAGE_REASON)
    record_detail(db, public, detail, post_id=post_id, comment_id=comment_id, board_id=board_id, legal=legal)
    return public


def record_staff(db, action, admin, what, *, target_user_id=None, post_id=None, comment_id=None, board_id=None, reason=""):
    """A staff-only entry about how the site is run (`RUNNING_ACTIONS`): `what` says exactly what was done, for the staff page
    only. `admin` is the admin who did it, or None for the site's own code (a signup held, a bot paused)."""
    if action not in STAFF_ACTIONS:
        raise ValueError(f"{action!r} is not a staff action")
    return record_quietly(db, action, admin.id if admin is not None else SITE_ACCOUNT_ID,
                          role=ADMIN if admin is not None else SYSTEM, target_user_id=target_user_id, post_id=post_id,
                          comment_id=comment_id, board_id=board_id, staff_note=what, reason=reason, floor=STAFF)


def lapse_due(db, now=None, limit=200):
    """Write the entry for every timed suspension that has run out: a system `unsuspend_user`, linked to the suspension and as
    private as it was (a staff-only suspension of an alt lapses in staff-only words too). One per suspension, so a second run writes
    nothing; none when a later sanction on the same account stands in its place (an unban, a new suspension or a ban) or the
    account is suspended still (a longer end was set meanwhile, as when a parent's suspension is extended). Returns how many."""
    now = int(now if now is not None else time.time())
    rows = db.execute(text(
        "SELECT e.id, e.target_user_id, e.audience, e.until_utc FROM log_entries e WHERE e.action = 'suspend_user' "
        "AND e.until_utc > 0 AND e.until_utc <= :now AND e.target_user_id IS NOT NULL "
        "AND NOT EXISTS (SELECT 1 FROM log_entries l WHERE l.linked_entry_id = e.id AND l.action = 'unsuspend_user') "
        "AND NOT EXISTS (SELECT 1 FROM log_entries o WHERE o.target_user_id = e.target_user_id AND o.id > e.id "
        "AND o.action IN ('unban_user', 'suspend_user', 'ban_user', 'unsuspend_user')) "
        "ORDER BY e.id LIMIT :limit"), {"now": now, "limit": int(limit)}).all()
    written = 0
    for entry_id, user_id, audience, until in rows:
        state = db.execute(text("SELECT is_banned, unban_utc FROM users WHERE id = :u"), {"u": user_id}).first()
        if state is not None and state[0] and (not state[1] or state[1] > now):
            continue                                  # suspended still: a later end was set, or it was made permanent
        made = record_quietly(db, "unsuspend_user", SITE_ACCOUNT_ID, role=SYSTEM, target_user_id=user_id,
                              reason="The suspension ended.", floor=audience, linked_entry_id=entry_id, created_utc=until)
        if made:
            written += 1
    return written


def correct(db, entry_id, admin, what):
    """Hide an entry's reason from everyone but staff (`redact`) and say so in a new, linked staff entry: the entry stays and is
    never edited, and the correction has a name and a date (the owner's rule of 2026-11-05: a correction is a new entry)."""
    redact(db, entry_id, admin.id)
    return record_quietly(db, "redact_entry", admin.id, role=ADMIN, staff_note=what, floor=STAFF, linked_entry_id=entry_id)


def record_ban(db, user_id, actor_id, *, days=0, reason="", rule=None, staff_note="", floor=PUBLIC, automatic=False,
               notice_sent=False, until_utc=None):
    """A suspension (`days` > 0) or a permanent ban. An automatic one (the site's own rules, `automatic`) is the system,
    and takes its rule and audience from AUTOMATIC_REASONS; an admin's is the admin's, with the rule they chose. A suspension
    keeps its end (`until_utc`: the account's own `unban_utc` when the caller has it) so the Log can say when it lapsed."""
    if automatic:
        known_rule, known_floor = AUTOMATIC_REASONS.get(reason or "", ("", PUBLIC))
        rule, floor = rule or known_rule, max(floor, known_floor)
    end = int(until_utc if until_utc else (time.time() + days * DAY)) if days else 0
    return record_quietly(db, "suspend_user" if days else "ban_user", actor_id, target_user_id=user_id, rule=rule,
                          reason=reason or "", staff_note=staff_note or "", role=SYSTEM if automatic else ADMIN,
                          floor=floor, notice_sent=notice_sent, until_utc=end)


def record_unban(db, user_id, actor_id, *, reason="", automatic=False, notice_sent=False, floor=PUBLIC):
    return record_quietly(db, "unban_user", actor_id, target_user_id=user_id, reason=reason or "",
                          role=SYSTEM if automatic else ADMIN, notice_sent=notice_sent, floor=floor)
