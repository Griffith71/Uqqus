"""Documentation for a sanction: the content an exile relies on, kept as it was (the Log, 2026-11-14; the owner: exiles and admin removals need a reason
with supporting references and documentation concerning the content the user posted).

An exile (by name or by alias) must cite at least one post or comment of the exiled account in that guild, and the site keeps a private copy of each
cited item as it was when the decision was made (`log_snapshots.kind = 'evidence'`, a year, `action_log.EVIDENCE_DAYS`), so the account's later
edit or delete cannot rewrite what the guildmasters relied on. The person, the deciding guild's guildmasters and the admins can open a copy
(`action_log.snapshot_visible`); the Log shows what was cited by id, and only for a named, public item (`public_cited`).

**What can be cited** (`usable`): a post or comment made in this guild (`original_board_id`), that is NOT anonymous (an anonymous item would pair an
account with it: an alias exile cites its own anonymous item and the copy is read only through the copy pages, which never name its author), not deleted,
that no admin removed and no post's author hid (a guildmaster is shown only a placeholder for those), and that belongs to the exiled account
(`owner_of`: a guild's copy of a post is its forwarder's). At most `MAX_CITED`.

Plain SQL where it can be, tested on SQLite (tests/test_sanction_evidence.py). Nothing here commits.
"""
from sqlalchemy import text

from ruqqus.helpers import action_log

MAX_CITED = 5
# the cited item has been made anonymous since (a "Remove my name", a deleted account): read now, never remembered, so what the exile cited can't be paired with it
ANONYMOUS_NOW = ("EXISTS (SELECT 1 FROM submissions a WHERE a.id = s.target_submission_id AND COALESCE(a.is_anonymous, false)) "
                 "OR EXISTS (SELECT 1 FROM comments b WHERE b.id = s.target_comment_id AND COALESCE(b.is_anonymous, false))")


def is_comment(item):
    return hasattr(item, "parent_submission")


def usable(item, board_id, user_id, owner_id, allow_anonymous=False):
    """Whether `item` may be cited against `user_id` in this guild. `owner_id` is whose the item is for an exile (the forwarder of a guild's copy)."""
    if item is None or not hasattr(item, "original_board_id"):
        return False
    if getattr(item, "deleted_utc", 0):
        return False
    if getattr(item, "is_banned", False) or getattr(item, "author_hidden", False):
        return False                    # removed by the site's admins, or hidden by the post's author: not for a guildmaster to read, so not to cite and keep a copy of
    if getattr(item, "is_anonymous", False) and not allow_anonymous:
        return False
    return item.original_board_id == board_id and owner_id == user_id


def keep(db, entry_id, board_id, user_id, items, now=None, limit=MAX_CITED):
    """A private `evidence` copy of each cited item, tied to the entry. The copy's author is the exiled account (a guild's copy of a post keeps its
    ORIGINAL author's id, but the person the decision is about is the forwarder). Returns how many were kept."""
    kept = 0
    seen = set()
    for item in items:
        key = (is_comment(item), item.id)
        if key in seen:
            continue
        seen.add(key)
        if kept >= limit:
            break
        snapshot = action_log.snapshot_of(item, board_id, comment=is_comment(item), kind=action_log.EVIDENCE)
        snapshot["author_id"] = user_id
        action_log.keep_snapshot(db, snapshot, now, entry_id)
        kept += 1
    return kept


def cited(db, entry_ids):
    """{entry id: [("post" | "comment", item id)]} for what these entries cited, in the order they were kept."""
    ids = sorted({int(i) for i in entry_ids or () if i})
    if not ids:
        return {}
    names = {f"e{n}": i for n, i in enumerate(ids)}
    marks = ", ".join(f":{n}" for n in names)
    out = {}
    rows = db.execute(text(f"SELECT entry_id, target_submission_id, target_comment_id FROM log_snapshots "
                           f"WHERE entry_id IN ({marks}) AND kind = 'evidence' ORDER BY id"), names)
    for entry_id, post, comment in rows:
        out.setdefault(entry_id, []).append(("comment", comment) if comment else ("post", post))
    return out


def openable(db, entry_ids, viewer_id, level, boards=()):
    """`cited`, only what this reader may open a private copy of (`action_log.snapshot_visible`'s rule written as SQL): the person it was kept about
    (`viewer_id`, None for a visitor or a limited sign-in, which is a stranger to its own anonymous items), a guildmaster of the deciding guild (`boards`)
    and the admins (`level` 3). A copy past its date (scrubbed) is not drawn."""
    ids = sorted({int(i) for i in entry_ids or () if i})
    if not ids or (not viewer_id and level < 3 and not boards):
        return {}
    names = {f"e{n}": i for n, i in enumerate(ids)}
    marks = ", ".join(f":{n}" for n in names)
    who = []
    params = dict(names)
    if level >= 3:
        who.append("1 = 1")
    if viewer_id:
        who.append("s.author_id = :viewer")
        params["viewer"] = int(viewer_id)
    board_marks = []
    for n, board_id in enumerate(sorted({int(b) for b in boards or ()})):
        params[f"b{n}"] = board_id
        board_marks.append(f":b{n}")
    if board_marks:
        # the deciding guild's guildmasters, but not for an item that has been made anonymous since: the line would pair the exiled account with it (an exile BY
        # ALIAS cited an anonymous item to begin with, which is why they hold it)
        who.append(f"(s.board_id IN ({', '.join(board_marks)}) AND (e.action = 'exile_alias' OR NOT ({ANONYMOUS_NOW})))")
    out = {}
    rows = db.execute(text(f"SELECT s.entry_id, s.target_submission_id, s.target_comment_id FROM log_snapshots s JOIN log_entries e ON e.id = s.entry_id "
                           f"WHERE s.entry_id IN ({marks}) AND s.kind = 'evidence' AND s.scrubbed_utc = 0 AND ({' OR '.join(who)}) ORDER BY s.id"), params)
    for entry_id, post, comment in rows:
        out.setdefault(entry_id, []).append(("comment", comment) if comment else ("post", post))
    return out


def public_cited(db, entry_ids):
    """`cited`, leaving out what the public Log may not draw: an anonymous item (it would pair an account with it), a deleted one, an item of a
    private or Circle guild, a post that was never public, and a comment under one. Only what is drawn to a visitor: the ids of named, public content."""
    found = cited(db, entry_ids)
    posts = {i for items in found.values() for kind, i in items if kind == "post"}
    comments = {i for items in found.values() for kind, i in items if kind == "comment"}
    ok_posts, ok_comments = set(), set()
    if posts:
        names = {f"p{n}": i for n, i in enumerate(sorted(posts))}
        marks = ", ".join(f":{n}" for n in names)
        ok_posts = {r[0] for r in db.execute(text(
            f"SELECT s.id FROM submissions s LEFT JOIN boards b ON b.id = s.board_id "
            f"WHERE s.id IN ({marks}) AND NOT COALESCE(s.is_anonymous, false) AND COALESCE(s.deleted_utc, 0) = 0 "
            f"AND COALESCE(s.post_public, false) AND NOT COALESCE(b.is_private, false) AND NOT COALESCE(b.is_circle, false)"), names)}
    if comments:
        names = {f"c{n}": i for n, i in enumerate(sorted(comments))}
        marks = ", ".join(f":{n}" for n in names)
        ok_comments = {r[0] for r in db.execute(text(
            f"SELECT c.id FROM comments c JOIN submissions s ON s.id = c.parent_submission LEFT JOIN boards b ON b.id = s.board_id "
            f"WHERE c.id IN ({marks}) AND NOT COALESCE(c.is_anonymous, false) AND COALESCE(c.deleted_utc, 0) = 0 "
            f"AND COALESCE(s.post_public, false) AND NOT COALESCE(b.is_private, false) AND NOT COALESCE(b.is_circle, false)"), names)}
    return {entry: [(k, i) for k, i in items if (k == "post" and i in ok_posts) or (k == "comment" and i in ok_comments)]
            for entry, items in found.items()}
