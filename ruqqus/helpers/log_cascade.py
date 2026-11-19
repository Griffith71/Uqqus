"""One Log entry per item an admin's mass decision reached (the second review, 2026-11-17, C79).

An obliteration cascades to the post's forwarded copies, and the nuke of an account removes everything it wrote. Each of those
items used to get nothing of its own (the copies) or a row written one ORM statement at a time (the nuke), so the per-item pages
(`/post/<id>/log`, `/comment/<id>/log`, the account's log, the copy's guild log) showed nothing for a copy that was gone, and a huge
account's nuke ran past the worker's timeout. `record_cascade` writes them in bulk: for each chunk of 500 items, one `INSERT ... SELECT`
into the guild's own mod log (`modactions`, which a guild's log page reads) and one into `log_entries`. Plain `text()` SQL, no
Python loop per row, no ORM, so the star import of `ruqqus.classes` can't replace this module. `take_down_all` is the nuke's own half: it removes
what an account wrote in a few statements and returns exactly the ids it flipped, which are what `record_cascade` then writes.

What an entry says is what `action_log.record` would have said, by the same rules, which `tests/test_log_cascade.py` holds in parity:

* **Audience**: the item's guild's own (public guild: everyone; private or Circle guild: that guild's members and the staff), the staff's alone
  for a post for circles (or a comment under one) and for a row with no guild, and never lower than the cascade's `floor`; a reason that
  says "ban evasion" (`_STAFF_NOTES`) or "csam" (`_LEGAL_NOTES`) raises the whole cascade, as it raises a single entry. An item that is
  anonymous takes `anonymous_floor` instead (a mass removal that stood out from its neighbours in the Log would pair them with the account).
* **No account**: an item's entry never names the account that wrote it (`target_user_id` is left out), which is what `record` does for an
  anonymous item, and no text is copied: the public line is the action, the rule and (for a removal) the reason.
* **Linked**: every entry points at the cascade's main entry (`linked_entry_id`, a column only staff read), as an appeal's reversal or a
  detail does; the original is never edited.
* **Idempotent**: a row is written once per `modactions` id (`legacy_modaction_id` is unique), and `skip_recorded` also leaves out an item
  that already has an entry of the action (a re-run of an obliteration, which cannot undo anything, writes nothing for a copy twice).
* **Best effort, like `record_quietly`**: everything is one savepoint; a failure is logged, counted (`note_failure`: /admin says so) and
  never undoes the removal itself.
"""
import html
import logging
import time

from sqlalchemy import text

from ruqqus.helpers import action_log

_log = logging.getLogger(__name__)

CHUNK = 500                                  # items in one statement
KINDS = {"ban": ("ban_post", "ban_comment"), "obliterate": ("obliterate_post", "obliterate_comment")}
MARKER = "admin action"                      # what an admin's route writes in a mod-log note (`action_log.ADMIN_MARKERS`)
NOTE_MAX = 256                               # `modactions._note`
BAN_REASON_MAX = 128                         # `submissions_aux.ban_reason`


def fit_escaped(value, limit):
    """The HTML-escaped text of `value` that fits `limit` characters once escaped. The raw text is cut, never an entity: a reason of `<` and
    `&` characters is longer escaped, and a cut through `&lt;` would draw as a stray `&lt`. The guild log and a removed post's page draw these
    columns as HTML, so they hold escaped text."""
    kept, used = [], 0
    for char in str(value or ""):
        piece = html.escape(char)
        if used + len(piece) > limit:
            break
        kept.append(piece)
        used += len(piece)
    return "".join(kept)


def note_for(reason):
    """(plain, stored) mod-log note of an admin's removal for this reason. `plain` is what the Log reads its audience markers in; `stored` is the
    escaped note that fits `modactions._note` (an admin's reason can be 512 characters, the column holds 256: the entry keeps the whole reason)."""
    reason = action_log.clean_reason(reason)
    if not reason:
        return MARKER, MARKER
    prefix = f"{MARKER} | "
    return prefix + reason, prefix + fit_escaped(reason, NOTE_MAX - len(prefix))


def _ids(values):
    return sorted({int(v) for v in values or ()})


def _chunks(ids):
    for start in range(0, len(ids), CHUNK):
        yield ids[start:start + CHUNK]


def _marks(prefix, ids):
    names = {f"{prefix}{n}": i for n, i in enumerate(ids)}
    return ", ".join(f":{n}" for n in names), names


# What audience an item's entry gets, in SQL: the same rule as `action_log.audience_for` (no guild: staff; a private or Circle guild: that
# guild's; else public) with `record`'s own addition (a post for circles is the staff's), then the cascade's floor. The inner SELECT works the
# item's own audience (`own`) and the lowest it may be given (`lowest`: the floor, or the anonymous floor for an anonymous item).
_POST_SOURCE = ("FROM modactions m JOIN submissions s ON s.id = m.target_submission_id LEFT JOIN boards b ON b.id = m.board_id "
                "WHERE m.id IN ({marks}) AND NOT EXISTS (SELECT 1 FROM log_entries e WHERE e.legacy_modaction_id = m.id)")
_COMMENT_SOURCE = ("FROM modactions m JOIN comments c ON c.id = m.target_comment_id JOIN submissions p ON p.id = c.parent_submission "
                   "LEFT JOIN boards b ON b.id = m.board_id "
                   "WHERE m.id IN ({marks}) AND NOT EXISTS (SELECT 1 FROM log_entries e WHERE e.legacy_modaction_id = m.id)")
_OWN = ("CASE WHEN COALESCE({parent}.audience, 0) = 4 THEN 2 WHEN COALESCE(m.board_id, 0) = 0 THEN 2 "
        "WHEN COALESCE(b.is_private, false) OR COALESCE(b.is_circle, false) THEN 1 ELSE 0 END")
_LOWEST = "CASE WHEN COALESCE({item}.is_anonymous, false) THEN :anon_lowest ELSE :lowest END"

_ENTRY = ("INSERT INTO log_entries (created_utc, log_type, action, actor_id, actor_role, board_id, {target}, rule_key, reason, reason_severity, "
          "audience, legacy_modaction_id, linked_entry_id) "
          "SELECT :now, :type, :kind, :actor, 'admin', NULLIF(t.board_id, 0), t.item_id, :rule, :reason, :severity, "
          "CASE WHEN t.own < t.lowest THEN t.lowest ELSE t.own END, t.mid, CAST(:linked AS BIGINT) "
          "FROM (SELECT m.id AS mid, m.board_id AS board_id, {item}.id AS item_id, {own} AS own, {lowest} AS lowest {source}) t")

_MODACTION = ("INSERT INTO modactions (user_id, board_id, kind, target_user_id, target_submission_id, target_comment_id, _note, created_utc) "
              "SELECT :actor, {board}, :kind, 0, {post}, {comment}, :note, :now FROM {source} WHERE {item}.id IN ({marks}){skip} "
              "ORDER BY {item}.id RETURNING id")


def _post_sql(skip):
    modaction = _MODACTION.format(board="s.board_id", post="s.id", comment="0", source="submissions s", item="s", marks="{marks}",
                                  skip=(" AND NOT EXISTS (SELECT 1 FROM log_entries x WHERE x.action = :kind AND x.target_submission_id = s.id)"
                                        if skip else ""))
    entry = _ENTRY.format(target="target_submission_id", item="s", own=_OWN.format(parent="s"), lowest=_LOWEST.format(item="s"),
                          source=_POST_SOURCE)
    return modaction, entry


def _comment_sql(skip):
    modaction = _MODACTION.format(board="p.board_id", post="0", comment="c.id",
                                  source="comments c JOIN submissions p ON p.id = c.parent_submission", item="c", marks="{marks}",
                                  skip=(" AND NOT EXISTS (SELECT 1 FROM log_entries x WHERE x.action = :kind AND x.target_comment_id = c.id)"
                                        if skip else ""))
    entry = _ENTRY.format(target="target_comment_id", item="c", own=_OWN.format(parent="p"), lowest=_LOWEST.format(item="c"),
                          source=_COMMENT_SOURCE)
    return modaction, entry


def take_down_all(db, user_id, reason):
    """Remove everything an account wrote that is still up, in a few statements (an admin's nuke): every live post and comment is flagged removed
    by ONE `UPDATE ... RETURNING id`, so only the rows this call flipped come back and a second admin's simultaneous nuke (which waits on the row
    locks and then finds them removed) or a re-run takes down, and records, nothing twice; what a guildmaster had already removed keeps its own
    decision. The removed post's page draws `ban_reason` as HTML, so the reason is stored escaped and cut to the column (`BAN_REASON_MAX`: a
    longer reason used to be a DataError that rolled the whole nuke back). Returns (post ids, comment ids), oldest first."""
    user_id = int(user_id)
    posts = [int(r[0]) for r in db.execute(text(
        "UPDATE submissions SET is_banned = true WHERE author_id = :u AND NOT COALESCE(is_banned, false) RETURNING id"), {"u": user_id})]
    posts.sort()
    shown = fit_escaped(reason, BAN_REASON_MAX)
    for chunk in _chunks(posts):
        marks, names = _marks("p", chunk)
        db.execute(text(f"UPDATE submissions_aux SET ban_reason = :r WHERE id IN ({marks})"), {**names, "r": shown})
    comments = [int(r[0]) for r in db.execute(text(
        "UPDATE comments SET is_banned = true WHERE author_id = :u AND NOT COALESCE(is_banned, false) RETURNING id"), {"u": user_id})]
    return posts, sorted(comments)


def record_cascade(db, kind, actor_id, *, post_ids=(), comment_ids=(), rule="", reason="", linked_entry_id=None, floor=action_log.PUBLIC,
                   anonymous_floor=action_log.PUBLIC, skip_recorded=False, now=None):
    """Write an entry (and the guild's own mod-log row) for each of these posts and comments, as an admin's `kind` of decision:
    `"ban"` (a removal: `ban_post` / `ban_comment`, with the `reason`) or `"obliterate"` (`obliterate_post` / `obliterate_comment`, whose public
    line is the rule alone: pass no reason). `linked_entry_id` is the cascade's main entry. Returns how many entries were written (0 when nothing
    was, and 0 after a failure: it is counted, logged and leaves everything else standing). The ids are coerced to integers here and are the only
    values that reach the SQL text, as numbered parameters."""
    if kind not in KINDS:
        raise ValueError(f"unknown cascade kind {kind!r}")
    posts, comments = _ids(post_ids), _ids(comment_ids)
    if not posts and not comments:
        return 0
    post_kind, comment_kind = KINDS[kind]
    reason = action_log.clean_reason(reason)
    rule = action_log.parse_rule(rule)
    plain, note = note_for(reason)
    # the audience markers an admin's route writes in a note raise the whole cascade, as they raise one entry (`audience_for`, trusted: an admin wrote it)
    lowest = max(int(floor), action_log.audience_for(post_kind, board_id=1, private=False, note=plain, trusted=True))
    anonymous_lowest = max(lowest, int(anonymous_floor))
    now = int(now if now is not None else time.time())
    severity = action_log.rate(db, reason)
    written = 0
    try:
        with db.begin_nested():
            for ids, action, build in ((posts, post_kind, _post_sql), (comments, comment_kind, _comment_sql)):
                if not ids:
                    continue
                modaction_sql, entry_sql = build(skip_recorded)
                for chunk in _chunks(ids):
                    marks, names = _marks("p", chunk)
                    made = db.execute(text(modaction_sql.format(marks=marks)),
                                      {**names, "actor": int(actor_id), "kind": action, "note": note, "now": now})
                    modaction_ids = [row[0] for row in made]
                    if not modaction_ids:
                        continue
                    marks, mids = _marks("m", modaction_ids)
                    result = db.execute(text(entry_sql.format(marks=marks)), {
                        **mids, "now": now, "type": action_log.type_of(action), "kind": action, "actor": int(actor_id), "rule": rule,
                        "reason": reason, "severity": severity, "lowest": lowest, "anon_lowest": anonymous_lowest,
                        "linked": int(linked_entry_id) if linked_entry_id else None})
                    written += max(0, int(result.rowcount or 0))
    except Exception as error:
        _log.exception("the Log could not record the %s cascade", kind)
        action_log.note_failure(db, f"{kind}_cascade", error)
        return 0
    return written
