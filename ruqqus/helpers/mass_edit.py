"""History's mass tools: take your name off, or delete, a hundred of your own posts and comments at once (the Log plan's decisions 11 and 22; slice D3c).

It is the same two actions as the single-item dialog ("Remove my name" and "Delete"), done to the items a member ticked on History > Manage, so
every refusal is the single item's own (a Circle post, a tipped one, one written with others...). What the mass form adds is the cost of a mistake:
a count the member confirms, a cap per request, a rate limit, the **password** (and a two-factor code where they have it), the site only (an API
app can't do it, nor a bot's key, nor a limited QR sign-in), and a list afterwards of what could not be changed and why.

Pure: facts in, an answer out. The page and the loop are `routes/history_mass_pages.py`; the list of what a member deleted is `helpers/mass_edit_store.py`.
"""
import re

from ruqqus.helpers.i18n_runtime import N_

MAX_ITEMS = 100                     # per request: each item is a savepoint and, for a delete, a Log entry
PER_HOUR = "6/hour"                 # requests per account
ANONYMISE, DELETE = "anonymise", "delete"
ACTIONS = (ANONYMISE, DELETE)

POST, COMMENT = "post", "comment"
_KINDS = {"p": POST, "c": COMMENT}
_TOKEN = re.compile(r"^([pc])([0-9a-z]{1,13})$")

NOTHING = N_("Choose at least one post or comment.")
TOO_MANY = N_("You can change up to %(max)s items at a time.")
COUNT = N_("The number of items you confirmed doesn't match what was chosen. Nothing was changed.")
PASSWORD = N_("That password isn't right. Nothing was changed.")
NO_ACTION = N_("Choose what to do with them.")
NOT_FOUND = N_("This isn't yours to change, or it is already gone.")
COLLAB = N_("A post written together is deleted only when its authors agree, in its shared draft.")
FAILED = N_("This couldn't be changed right now.")


def parse_action(raw):
    """`anonymise` or `delete`; None for anything else."""
    return raw if raw in ACTIONS else None


def token(kind, base36id):
    """The form value for an item: `p` or `c` and its base 36 id."""
    return ("p" if kind == POST else "c") + base36id


def parse_items(values, limit=MAX_ITEMS):
    """`(items, problem)`: the ticked items as `(kind, base36 id)` in the order given, each once. Anything that isn't a token is dropped (a hand-made
    request can say what it likes), and none left, or more than `limit`, is a problem (a message id and its values)."""
    seen, items = set(), []
    for raw in values or ():
        match = _TOKEN.match((raw or "").strip().lower())
        if not match:
            continue
        item = (_KINDS[match.group(1)], match.group(2))
        if item not in seen:
            seen.add(item)
            items.append(item)
    if not items:
        return [], (NOTHING, {})
    if len(items) > limit:
        return [], (TOO_MANY, {"max": limit})
    return items, None


def count_matches(raw, items):
    """The member typed (or the page counted) how many they are about to change: the number must be the number chosen."""
    try:
        return int(str(raw).strip()) == len(items)
    except (TypeError, ValueError):
        return False


def summary(results):
    """`(done, refused)` counts from the results `[{"ok": bool, ...}]`."""
    done = sum(1 for r in results if r.get("ok"))
    return done, len(results) - done
