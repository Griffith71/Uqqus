"""A member's own delete, and what it leaves in the Log (the Log plan's decisions 3, 4, 12, 20, 22 and 25; slice D3a).

Two ways to take an item off your name, chosen in the delete dialog:
  * **Delete**: the item leaves the site. What it said stays in the public Log as **Anonymous** (title, link and text), under the same word-filter,
    sensitive and Child-level rules as any member text, so what a person wrote and then deleted is still a public record of the site. Like
    Wikipedia, every contribution remains.
  * **Remove my name** (`unlink`): the item stays where it is, with the name taken off it. Irreversible.

**The grace window**: an item deleted within `GRACE_SECONDS` of being made is really erased: no entry, no public text, a private copy for the author
and the admins for 90 days. It is how a typo, a wrong picture or a post made in a rage is taken back. It is capped (`GRACE_ERASES_PER_DAY`) so it can't
be a way to post abuse and leave no trace, and **refused once anyone else has commented** (nobody else's words are orphaned, and the grace window is not a
way to bury an argument). After it every delete is public.

Pure: facts in, an answer out. The database side is `helpers/deletion_store.py`, the anonymising `helpers/anonymise.py`, the Log's tabs and pages
`routes/log_pages.py`.
"""
from ruqqus.helpers.i18n_runtime import N_
from ruqqus.helpers.wordfilter import is_hidden

GRACE_SECONDS = 300                 # the item's own age, counted from when it was made: an edit never restarts it
GRACE_ERASES_PER_DAY = 20           # erases inside the window one account may make a day: past it a delete publishes like a later one

DELETE, UNLINK = "delete", "unlink"

# what a delete does
ERASE = "erase"                     # inside the grace window: gone, no public trace, a private copy
PUBLISH = "publish"                 # after it: gone from the site, its words stay in the Log as Anonymous
LINE = "line"                       # after it, for a child account: the line stays, the words don't (a private copy)

# what a reader is shown of a deleted item's words
TEXT, COVERED, LINE_ONLY = "text", "covered", "line"


def parse_how(raw):
    """`unlink` or the default, `delete`."""
    return UNLINK if raw == UNLINK else DELETE


def in_grace(created_utc, now):
    """Is this item still young enough to be erased? An item made in the future (a clock that moved) counts as young."""
    return (now - int(created_utc or 0)) < GRACE_SECONDS


def plan(*, created_utc, now, erased_today, others_replied, child):
    """What deleting this item does: `ERASE`, `PUBLISH` or `LINE`. `erased_today` counts this account's erases in the last day,
    `others_replied` says someone else has written under the item, `child` that the author's account is locked as a child's."""
    if in_grace(created_utc, now) and erased_today < GRACE_ERASES_PER_DAY and not others_replied:
        return ERASE
    return LINE if child else PUBLISH


def text_published(*, child, removed, hidden_by_guild, board_banned):
    """May the words of this delete be drawn through the Log? Not for a child account's (a line only, with a private copy), for an item a moderator
    had removed or a guild had hidden, nor in a banned guild. Who may read the entry at all is its audience (`entry_floor`, `audience_for`)."""
    return not (child or removed or hidden_by_guild or board_banned)


def entry_floor(*, circle):
    """The narrowest audience an entry needs beyond what its guild gives it: a Circle item (a post for circles, or in a Circle guild) is the staff's
    alone, and `action_log.audience_for` already makes a private guild's the guild's own."""
    from ruqqus.helpers import action_log
    return action_log.STAFF if circle else action_log.PUBLIC


def text_view(copy, *, board_banned, level, shows_sensitive):
    """What a reader gets of a deleted item's words: `TEXT`, `COVERED` (a sensitive item, for a member who chose to see sensitive content: behind the
    usual cover) or `LINE_ONLY`. `copy` is `action_log.deleted_texts`'s row; `level` the reader's word-filter level (`visibility.viewer_level`).
    A visitor and the Child level get a line for anything sensitive, as they get nothing sensitive in a feed."""
    if copy is None or not copy.get("public") or copy.get("scrubbed") or board_banned:
        return LINE_ONLY
    if is_hidden(level, copy.get("word_severity")):
        return LINE_ONLY
    if copy.get("is_sensitive"):
        return COVERED if shows_sensitive else LINE_ONLY
    return TEXT


# What "remove my name" refuses, and why. The standalone action only: a delete anonymises whatever it deletes (`force`).
CIRCLE = N_("A post for a Circle can't have its name removed.")
TIPPED = N_("A post that has been tipped can't have its name removed, because the coins went to you.")
SHARED = N_("A post written with others can't have its name removed here yet.")
SPACE = N_("A Space can't have its name removed.")
ALREADY = N_("This is already anonymous.")
GONE = N_("This has been deleted.")
BOT = N_("An automated post can't have its name removed.")


def unlink_refusal(*, circle, tipped, shared, space, already, deleted, bot=False):
    """Why "Remove my name" is refused for this item, or None. Circle posts and tipped posts need their author's name (the audience and the coins are tied
    to it), a post written with others hides one credit and not the post (slice D3f), a Space is its host's."""
    if deleted:
        return GONE
    if already:
        return ALREADY
    if space:
        return SPACE
    if circle:
        return CIRCLE
    if tipped:
        return TIPPED
    if shared:
        return SHARED
    if bot:
        return BOT
    return None
