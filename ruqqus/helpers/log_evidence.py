"""The ban reveal: what an admin cites when they ban or suspend an account, and who it names (the Log plan's decisions 10, 17, 18 and 27; slice D3d).

**A site-wide ban is different from a guild action: it de-anonymises what is relevant, in the Log.** The banning admin may tick the items that justify the
ban (a post or a comment of the account, anonymous or made anonymous included; ticking nothing reveals nothing). Only those items are attributed to the
account in the Log, so anyone can check whether the account was unfairly targeted and who it was: the ban's line lists them with the author named, and
the Log's lines about a cited item (its own log, its entry in the Deleted tab) draw the real author instead of Anonymous. **The item itself stays
anonymous everywhere else on the site**, and nothing here changes `is_anonymous`.

**A second admin must approve each reveal** (decision 17): the banning admin proposes the evidence, a **different** admin of level 3 or more confirms,
and only then does it show. The ban itself takes effect at once; until approved the Log shows the ban alone, and a reveal nobody confirms or that is
declined never shows. **Sole-admin case** (decision 27): when no other admin exists (level 3 or more, not banned or deleted, checked when approving) a
level-4 owner may confirm their own proposal, and the row records it as a sole-admin approval; a level-3 admin who is the only admin has no one to
confirm and the reveal stays unpublished. **Reversing the ban withdraws the reveal** (the lines draw Anonymous again).

Only the items of the banned account can be cited; never an item with a legal-level entry (it has no public line to attach to), never one erased inside
the grace window (it has no public record), and an item that is not public (a Circle's, a private guild's) is counted, never listed by id. The rows live in
`log_evidence`, which the pointer-stripping of "Remove my name" and of an account's deletion never touches: **citing an item and then deleting the account
or taking your name off it does not undo the reveal**. A deleted account can still be banned with evidence, and an approved reveal names it by its username.

Plain `text()` SQL, tested on SQLite (tests/test_log_evidence.py). Nothing here commits.
"""
from sqlalchemy import text

from ruqqus.helpers import mass_edit
from ruqqus.helpers.i18n_runtime import N_

MAX_ITEMS = 50                          # items cited by one ban: a reveal is for what justifies it, not for an account's whole history
PER_PAGE = 25                           # the picker's page

PENDING, APPROVED, DECLINED, WITHDRAWN = "pending", "approved", "declined", "withdrawn"

TOO_MANY = N_("A ban can cite up to %(max)s items.")
NOT_THEIRS = N_("This isn't one of the account's own posts or comments.")
NOT_PUBLIC = N_("This is in a private place, and a private item can't be cited in the public Log.")
LEGAL = N_("This has a legal-level record, which can't be cited.")
ERASED = N_("This was erased within five minutes of being made, so it has no public record to attach to.")
SAME_ADMIN = N_("A different admin has to confirm a reveal.")
NOT_ADMIN = N_("Only an admin can confirm a reveal.")
NOTHING_TO_DECIDE = N_("There is nothing waiting for this ban.")


def parse_items(values):
    """`(items, problem)`: the ticked evidence as `(kind, base36 id)`, each once; none ticked is no evidence and no problem. More than `MAX_ITEMS` is."""
    if not [v for v in values or () if (v or "").strip()]:
        return [], None
    items, problem = mass_edit.parse_items(values, limit=MAX_ITEMS)
    if problem and problem[0] is mass_edit.TOO_MANY:
        return [], (TOO_MANY, {"max": MAX_ITEMS})
    return items, None if items else (mass_edit.NOTHING, {})


def approval(*, proposer_id, admin_id, admin_level, another_admin_exists):
    """`(allowed, sole_admin, why)`: may this admin confirm the proposal? A different admin of level 3 or more, always. The proposer may confirm their own
    only as the level-4 owner when no other admin exists, and the row records that (decision 27)."""
    if (admin_level or 0) < 3:
        return False, False, NOT_ADMIN
    if int(admin_id) != int(proposer_id):
        return True, False, None
    if (admin_level or 0) >= 4 and not another_admin_exists:
        return True, True, None
    return False, False, SAME_ADMIN


def _marks(prefix, ids):
    names = {f"{prefix}{n}": int(i) for n, i in enumerate(ids)}
    return ", ".join(f":{n}" for n in names), names


def another_admin_exists(db, excluding_id):
    """Is there an admin (level 3 or more, not banned, not deleted) other than this one?"""
    return db.execute(text("SELECT 1 FROM users WHERE admin_level >= 3 AND id <> :a AND NOT COALESCE(is_deleted, false) AND COALESCE(is_banned, 0) = 0 "
                           "LIMIT 1"), {"a": int(excluding_id)}).first() is not None


# --- what can be cited -------------------------------------------------------------------------------------------------------------------

_POST_PUBLIC = ("s.post_public AND COALESCE(s.audience, 0) = 0 AND NOT COALESCE(b.is_private, false) AND NOT COALESCE(b.is_circle, false)")


def _problems(db, target_id, posts, comments):
    """{("post"|"comment", id): why not} for the items that can't be cited; an item that can has no entry."""
    found = {}
    target = int(target_id)
    for kind, ids in (("post", posts), ("comment", comments)):
        ids = sorted({int(i) for i in ids})
        if not ids:
            continue
        marks, names = _marks("i", ids)
        if kind == "post":
            sql = (f"SELECT s.id, s.author_id, ({_POST_PUBLIC}) FROM submissions s LEFT JOIN boards b ON b.id = s.board_id WHERE s.id IN ({marks})")
        else:
            sql = (f"SELECT c.id, c.author_id, (s.post_public AND COALESCE(s.audience, 0) = 0 AND NOT COALESCE(b.is_private, false) "
                   f"AND NOT COALESCE(b.is_circle, false)) FROM comments c JOIN submissions s ON s.id = c.parent_submission "
                   f"LEFT JOIN boards b ON b.id = s.board_id WHERE c.id IN ({marks})")
        rows = {r[0]: (r[1], bool(r[2])) for r in db.execute(text(sql), names)}
        column = "target_submission_id" if kind == "post" else "target_comment_id"
        legal = {r[0] for r in db.execute(text(f"SELECT DISTINCT {column} FROM log_entries WHERE audience >= 3 AND {column} IN ({marks})"), names)}
        erased = {r[0] for r in db.execute(text(f"SELECT DISTINCT {column} FROM log_snapshots WHERE kind = 'grace' AND entry_id IS NULL "
                                                f"AND {column} IN ({marks})"), names)}
        for item in ids:
            owner, public = rows.get(item, (None, False))
            if owner is None or int(owner) != target:
                found[(kind, item)] = NOT_THEIRS
            elif item in legal:
                found[(kind, item)] = LEGAL
            elif item in erased:
                found[(kind, item)] = ERASED
            elif not public:
                found[(kind, item)] = NOT_PUBLIC
    return found


def refusals(db, target_id, items):
    """{`(kind, id)`: why not} for every ticked item that can't be cited (`items` are `(kind, id)` with the id as a number)."""
    posts = [i for kind, i in items if kind == "post"]
    comments = [i for kind, i in items if kind == "comment"]
    return _problems(db, target_id, posts, comments)


def items_of(db, target_id, page=1):
    """`(rows, next_exists, private_count)`: the account's own posts and comments for the ban form's picker, newest first, each `{"kind", "id", "title",
    "anonymous", "deleted", "created", "citable", "why"}`. An item that can't be cited says why; the ones in private places are only counted (never listed
    by id): the picker is for staff, and a private item has nothing public to attach to."""
    page = max(int(page or 1), 1)
    target = int(target_id)
    posts = db.execute(text(
        "SELECT s.id, a.title, s.is_anonymous, s.deleted_utc, s.created_utc FROM submissions s LEFT JOIN submissions_aux a ON a.id = s.id "
        "WHERE s.author_id = :t AND COALESCE(s.repost_id, 0) = 0 ORDER BY s.created_utc DESC, s.id DESC LIMIT :n OFFSET :o"),
        {"t": target, "n": PER_PAGE + 1, "o": PER_PAGE * (page - 1)}).fetchall()
    comments = db.execute(text(
        "SELECT c.id, a.body, c.is_anonymous, c.deleted_utc, c.created_utc FROM comments c LEFT JOIN comments_aux a ON a.id = c.id "
        "WHERE c.author_id = :t ORDER BY c.created_utc DESC, c.id DESC LIMIT :n OFFSET :o"),
        {"t": target, "n": PER_PAGE + 1, "o": PER_PAGE * (page - 1)}).fetchall()
    more = len(posts) > PER_PAGE or len(comments) > PER_PAGE
    posts, comments = posts[:PER_PAGE], comments[:PER_PAGE]
    problems = _problems(db, target, [r[0] for r in posts], [r[0] for r in comments])
    rows, private = [], 0
    for kind, found in (("post", posts), ("comment", comments)):
        for item_id, words, anonymous, deleted, created in found:
            why = problems.get((kind, item_id))
            if why is NOT_PUBLIC:
                private += 1
                continue
            rows.append({"kind": kind, "id": item_id, "title": " ".join((words or "").split())[:140], "anonymous": bool(anonymous),
                         "deleted": bool(deleted), "created": created, "citable": why is None, "why": why})
    rows.sort(key=lambda r: r["created"], reverse=True)
    return rows, more, private


def anonymous_among(db, items):
    """The cited `(kind, id)` that are anonymous now: what the banned account is told the ban relies on and an approval could name it for."""
    out = []
    for kind, table in (("post", "submissions"), ("comment", "comments")):
        ids = sorted({int(i) for k, i in items if k == kind})
        if not ids:
            continue
        marks, names = _marks("i", ids)
        found = {r[0] for r in db.execute(text(f"SELECT id FROM {table} WHERE id IN ({marks}) AND COALESCE(is_anonymous, false)"), names)}
        out += [(kind, i) for i in ids if i in found]
    return out


# --- proposing, deciding, withdrawing -----------------------------------------------------------------------------------------------------

def propose(db, entry_id, target_id, proposer_id, items, now):
    """Records the cited items for a ban's entry as `pending` (a second admin decides). `items` are `(kind, id)` already checked by `refusals`. Returns
    how many were recorded (an item cited twice for one entry is one row)."""
    done = 0
    for kind, item in items:
        column = "post_id" if kind == "post" else "comment_id"
        result = db.execute(text(
            f"INSERT INTO log_evidence (entry_id, target_user_id, {column}, proposed_by, proposed_utc, status) VALUES (:e, :t, :i, :p, :now, 'pending') "
            f"ON CONFLICT DO NOTHING"), {"e": int(entry_id), "t": int(target_id), "i": int(item), "p": int(proposer_id), "now": int(now)})
        done += result.rowcount or 0
    return done


def waiting(db):
    """The proposals waiting for a second admin, one per ban entry: `[{"entry_id", "target_id", "target_name", "proposed_by", "by_name", "proposed_utc",
    "items": [(kind, id)]}]`, oldest first."""
    rows = db.execute(text(
        "SELECT e.id, e.entry_id, e.target_user_id, t.username, e.proposed_by, p.username, e.proposed_utc, e.post_id, e.comment_id FROM log_evidence e "
        "LEFT JOIN users t ON t.id = e.target_user_id LEFT JOIN users p ON p.id = e.proposed_by WHERE e.status = 'pending' "
        "ORDER BY e.proposed_utc, e.id")).fetchall()
    grouped = {}
    for _id, entry, target, target_name, proposer, proposer_name, at, post, comment in rows:
        row = grouped.setdefault(entry, {"entry_id": entry, "target_id": target, "target_name": target_name or "", "proposed_by": proposer,
                                         "by_name": proposer_name or "", "proposed_utc": at, "items": []})
        row["items"].append(("post", post) if post else ("comment", comment))
    return list(grouped.values())


def decide(db, entry_id, admin_id, admin_level, approve, now):
    """Approve or decline what is waiting for this entry. Returns `(count, why)`: how many rows changed, and the refusal when nobody may decide. One
    conditional UPDATE per outcome, so two admins deciding at once decide once."""
    row = db.execute(text("SELECT proposed_by FROM log_evidence WHERE entry_id = :e AND status = 'pending' LIMIT 1"), {"e": int(entry_id)}).first()
    if row is None:
        return 0, NOTHING_TO_DECIDE
    sole = False
    if approve:
        ok, sole, why = approval(proposer_id=row[0], admin_id=admin_id, admin_level=admin_level,
                                 another_admin_exists=another_admin_exists(db, admin_id))
        if not ok:
            return 0, why
    elif (admin_level or 0) < 3:
        return 0, NOT_ADMIN
    result = db.execute(text(
        "UPDATE log_evidence SET status = :s, decided_by = :a, decided_utc = :now, sole_admin = :sole WHERE entry_id = :e AND status = 'pending'"),
        {"s": APPROVED if approve else DECLINED, "a": int(admin_id), "now": int(now), "sole": bool(sole), "e": int(entry_id)})
    return result.rowcount or 0, None


def withdraw(db, target_id, now):
    """Reversing a ban withdraws its reveal: every pending or approved row of this account is `withdrawn`, so the Log draws Anonymous again."""
    return db.execute(text("UPDATE log_evidence SET status = 'withdrawn', withdrawn_utc = :now WHERE target_user_id = :t AND status IN ('pending', 'approved')"),
                      {"now": int(now), "t": int(target_id)}).rowcount or 0


# --- what the Log draws -------------------------------------------------------------------------------------------------------------------

def revealed(db, post_ids=(), comment_ids=()):
    """{("post"|"comment", id): username} for the items a ban cited and a second admin approved, and that haven't been withdrawn: the Log's lines about
    them name the author. A deleted account's username shows too (decision 18)."""
    found = {}
    for kind, column, ids in (("post", "post_id", post_ids), ("comment", "comment_id", comment_ids)):
        ids = sorted({int(i) for i in ids or () if i})
        if not ids:
            continue
        marks, names = _marks("i", ids)
        for item, username in db.execute(text(
                f"SELECT e.{column}, u.username FROM log_evidence e JOIN users u ON u.id = e.target_user_id WHERE e.status = 'approved' "
                f"AND e.{column} IN ({marks})"), names):
            found[(kind, item)] = username
    return found


def listed(db, entry_ids):
    """{entry id: [(kind, id)]} for the ban entries with approved evidence: the ban's line lists these with the author named."""
    ids = sorted({int(i) for i in entry_ids or () if i})
    if not ids:
        return {}
    marks, names = _marks("e", ids)
    found = {}
    for entry, post, comment in db.execute(text(
            f"SELECT entry_id, post_id, comment_id FROM log_evidence WHERE status = 'approved' AND entry_id IN ({marks}) ORDER BY id"), names):
        found.setdefault(entry, []).append(("post", post) if post else ("comment", comment))
    return found


def author_of_entry(db, entry_ids):
    """{entry id: username} of the account an approved reveal names, for the ban's own line (a deleted account is drawn by its username then)."""
    ids = sorted({int(i) for i in entry_ids or () if i})
    if not ids:
        return {}
    marks, names = _marks("e", ids)
    return {r[0]: r[1] for r in db.execute(text(
        f"SELECT DISTINCT e.entry_id, u.username FROM log_evidence e JOIN users u ON u.id = e.target_user_id WHERE e.status = 'approved' AND e.entry_id IN ({marks})"),
        names)}
