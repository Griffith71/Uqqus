"""Block and mute an account, and move a block list in and out as a file.

`block_account` is what the Block button does, in one place, so the import cannot differ from it: not yourself, not the
site's own account, not a deleted one, the chat consequences (`chat_permissions.on_block_created`), the end of any Circle
between the two (`circle_store.end_between`), and the follows both ways. The routes forget cached feeds afterwards (once,
for an import). `mute_account` is the quiet one-way counterpart (helpers/muting.py).

The file is a CSV of `kind,username` (`block` or `mute`); a plain list of names, one per line, is read as blocks. An
import takes at most `MAX_NAMES` names and reports **counts only**: a name that does not exist and one that can't be
blocked (yourself, the site's account, a deleted account, a name that could not be one) read the same, so an import
cannot be used to find out which accounts exist or are deleted beyond what a profile address already says.
"""
import csv
import io
import re
import time

from sqlalchemy import and_, or_

MAX_NAMES = 200
MAX_CHARS = 30000
NAME = re.compile(r"^[A-Za-z0-9_]{3,25}$")
KINDS = ("block", "mute")

REFUSALS = {
    "missing": ("That user doesn't exist.", 404),
    "self": ("You can't block yourself.", 409),
    "already": ("You have already blocked @{name}.", 409),
    "protected": ("You can't block @{name}.", 409),
    "deleted": ("That account has been deactivated", 410),
}
SITE_ACCOUNT = 1
PROBLEMS = {"size": "That file is too large to import.", "count": f"Import at most {MAX_NAMES} names at a time.",
            "empty": "There were no names in that file."}


def refusal(blocker, target):
    """Why `blocker` can't block `target`, as a key of REFUSALS, or None."""
    if target is None:
        return "missing"
    if target.id == blocker.id:
        return "self"
    if blocker.has_block(target):
        return "already"
    if target.id == SITE_ACCOUNT:
        return "protected"
    if target.is_deleted:
        return "deleted"
    return None


def block_account(db, blocker, target):
    """Blocks `target` for `blocker`: "blocked", or the refusal key. Commits; the caller forgets cached feeds."""
    from ruqqus.classes import Follow, UserBlock
    from ruqqus.helpers import circle_store
    from ruqqus.helpers.chat_permissions import on_block_created

    why = refusal(blocker, target)
    if why:
        return why

    db.add(UserBlock(user_id=blocker.id, target_id=target.id, created_utc=int(time.time())))
    db.commit()

    on_block_created(blocker, target)

    # a block takes each of you out of the other's Circle (friends go, subscriptions end; no refund)
    circle_store.end_between(db, blocker.id, target.id)

    # and ends the follows both ways (a blocked account could not follow you again either, routes/users.py)
    db.query(Follow).filter(or_(and_(Follow.user_id == blocker.id, Follow.target_id == target.id),
                                and_(Follow.user_id == target.id, Follow.target_id == blocker.id))
                            ).delete(synchronize_session=False)
    db.flush()
    for account in (blocker, target):
        account.stored_subscriber_count = account.follower_count
        db.add(account)
    db.commit()
    return "blocked"


def mute_account(db, muter, target):
    """Mutes `target`: "muted", "already", or a refusal key ("missing", "self", "protected", "deleted")."""
    from ruqqus.classes import UserMute

    if target is None:
        return "missing"
    if target.id == muter.id:
        return "self"
    if target.id == SITE_ACCOUNT:
        return "protected"
    if target.is_deleted:
        return "deleted"
    if db.query(UserMute).filter_by(user_id=muter.id, target_id=target.id).first():
        return "already"
    db.add(UserMute(user_id=muter.id, target_id=target.id, created_utc=int(time.time())))
    db.commit()
    return "muted"


# ---- the file ----

def render(blocked, muted):
    """The CSV of an account's blocked and muted usernames."""
    out = io.StringIO()
    writer = csv.writer(out, lineterminator="\n")
    writer.writerow(["kind", "username"])
    for name in sorted(blocked, key=str.lower):
        writer.writerow(["block", name])
    for name in sorted(muted, key=str.lower):
        writer.writerow(["mute", name])
    return out.getvalue()


def parse(raw):
    """(rows, problem): rows is [(kind, name)] without duplicates, in the order given, or problem is a key of PROBLEMS.

    Reads `kind,username` lines, the header, and plain names (one per line, or separated by spaces or commas), with or
    without a leading @. A name that could not be a username is kept as a row with name None so it counts as not found.
    """
    raw = str(raw or "")
    if len(raw) > MAX_CHARS:
        return [], "size"
    rows, seen = [], set()
    for line in raw.replace("\r", "\n").split("\n"):
        line = line.strip().lstrip("﻿")
        if not line or line.startswith("#"):
            continue
        tokens = [t.strip() for t in re.split(r"[,\s]+", line) if t.strip()]
        kind = "block"
        if [t.lower() for t in tokens] == ["kind", "username"]:
            continue                                                 # the header
        if len(tokens) >= 2 and tokens[0].lower() in KINDS:          # "mute" alone is an account called mute
            kind, tokens = tokens[0].lower(), tokens[1:]
        for token in tokens:
            name = token.lstrip("@")
            valid = bool(NAME.match(name))
            key = (kind, name.lower() if valid else token)
            if key in seen:
                continue
            seen.add(key)
            rows.append((kind, name if valid else None))
    if not rows:
        return [], "empty"
    if len(rows) > MAX_NAMES:
        return [], "count"
    return rows, None


def tally(outcomes):
    """Counts of what an import did: {"blocked", "muted", "already", "not_found"}. Nothing else is reported."""
    counts = {"blocked": 0, "muted": 0, "already": 0, "not_found": 0}
    for outcome in outcomes:
        if outcome in ("blocked", "muted", "already"):
            counts[outcome] += 1
        else:
            counts["not_found"] += 1
    return counts


def run_import(db, owner, rows, find):
    """Applies parsed rows for `owner`; `find(name)` gives an account or None. Returns (counts, anything_muted)."""
    outcomes = []
    for kind, name in rows:
        target = find(name) if name else None
        if target is None:
            outcomes.append("missing")
        elif kind == "mute":
            outcomes.append(mute_account(db, owner, target))
        else:
            outcomes.append(block_account(db, owner, target))
    counts = tally(outcomes)
    return counts, counts["muted"] > 0


def summary(counts):
    """The sentence an import ends with."""
    parts = []
    if counts["blocked"]:
        parts.append(f"{counts['blocked']} blocked")
    if counts["muted"]:
        parts.append(f"{counts['muted']} muted")
    if counts["already"]:
        parts.append(f"{counts['already']} already on your list")
    if counts["not_found"]:
        parts.append(f"{counts['not_found']} not found")
    return ("Imported: " + ", ".join(parts) + ".") if parts else "Nothing was imported."


def counts_from_query(args):
    """The counts back out of the page's address (`done=b.m.a.n`: four numbers), or None. Numbers only, so the address
    can never carry text onto the page."""
    raw = str(args or "")
    if not re.fullmatch(r"\d{1,4}\.\d{1,4}\.\d{1,4}\.\d{1,4}", raw):
        return None
    blocked, muted, already, not_found = (int(x) for x in raw.split("."))
    return {"blocked": blocked, "muted": muted, "already": already, "not_found": not_found}


def counts_to_query(counts):
    return f"{counts['blocked']}.{counts['muted']}.{counts['already']}.{counts['not_found']}"
