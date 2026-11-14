"""Actions on an alias: a guildmaster exiles or chat-mutes "Anonymous #3" without learning who it is (the Log plan's decision 24; slice D3f).

An anonymous item in a guild is drawn with its author's number in its thread (helpers/anon_aliases.py). A guildmaster who has the permission can act on that
number: the SERVER maps it to the account (the item's author) and applies the ordinary exile or chat mute to it, and the guildmaster is never told whose it is.

What keeps it from being an identity oracle (the plan's list):
* the ban row is an ordinary one for the account, but it is **marked with the thread and the number** it was made by (`alias_post_id`, `alias_n`), so the guild's
  exile and chat-mute lists draw "Anonymous #3" and never the username (a ban made by name is drawn by name, as before), and the lists' JSON names no account;
* **the mod log and the Log draw the alias**: the guild's mod-log row is an `exile_alias` / `chatban_alias` row whose target is the label, not an account; the
  Log's entry has no `target_user_id` (so the `to=` filter and the account's own log can't find it) and carries only the number (`alias_n`, public: it is on the
  item already); the account is kept in `subject_user_id`, **staff only**, for the appeal and the staff's reading;
* **every outcome answers the same**: an alias that can't be acted on (another guildmaster, an admin, an approved contributor, the guildmaster's own, an account
  already exiled, one with no number, an item not in this guild or not anonymous) is "accepted, no effect", exactly as a success is, so success or refusal reveals
  nothing; the page is the only thing that changes;
* **rate limited per guildmaster** (`PER_HOUR`): the visible effect, that the alias can no longer comment, must not become a way to test guesses;
* the exiled account is told as for any exile, with the appeal way (decision 13); the guildmaster is not told who it is.

**What it can't hide** (the plan's decision 24 accepts it; the answers are identical, the side effects are not): a guildmaster who already SUSPECTS an account can test
the guess. (1) Exile the suspect BY NAME, then look at the exile list: if the suspect was exiled by alias, the answer is the same 204 as for any member who has taken
part, but no new named row appears (an account exiled by name answers "already exiled", so the alias row is the one case that is silent). (2) Exile the suspect by
name FIRST, then exile the alias: no new row appears when the alias's author is the suspect, but also when it is any account that can't be acted on (a guildmaster,
an admin, an approved contributor, the guildmaster's own), so one silent answer proves one of several things. Neither tells a guildmaster who it is when they have
no suspect, and each costs a visible exile of the suspect (and since 2026-11-15 an exile by name must first cite a real, named item of that account in this guild,
asked before anything is said about its standing, so a miss is never cheaper than the exile it makes). A name-based action on an account that was exiled by alias never says "already exiled", and un-exiling by
name never finds an alias row.

Plain `text()` SQL where it can be, tested on SQLite (tests/test_alias_actions.py). Nothing here commits.
"""
from sqlalchemy import text

from ruqqus.helpers import anon_aliases
from ruqqus.helpers.i18n_runtime import N_, tr

PER_HOUR = "20/hour"                    # alias actions a guildmaster may make
EXILE, UNEXILE, CHATBAN, UNCHATBAN = "exile_alias", "unexile_alias", "chatban_alias", "unchatban_alias"
ACTIONS = (EXILE, UNEXILE, CHATBAN, UNCHATBAN)
ACCEPTED = N_("Done.")                  # the one answer, whatever happened


def label(number):
    """"Anonymous #3", as the reader reads it."""
    return tr("Anonymous #%(num)s", num=int(number))


def resolve(db, board_id, item):
    """`(user_id, thread post id, number)` for the author of this anonymous item in this guild, or None when there is nothing to act on: the item isn't anonymous,
    wasn't made in this guild, or its author has no number in its thread yet. `item` is a post or a comment."""
    if item is None or not getattr(item, "is_anonymous", False):
        return None
    if getattr(item, "original_board_id", None) != board_id:
        return None
    scope = anon_aliases.scope_of(item)
    author = getattr(item, "author_id", None)
    if scope is None or not author:
        return None
    number = anon_aliases.numbers_in(db, scope).get(author)
    if not number:
        return None
    return int(author), int(scope[1]), int(number)


def alias_ban(db, table, board_id, user_id, active_only=True):
    """The ban row of this account in this guild if it was made by alias, else None (`table` is `bans` or `chatbans`)."""
    extra = " AND is_active" if table == "bans" and active_only else ""
    row = db.execute(text(f"SELECT id, alias_post_id, alias_n FROM {table} WHERE board_id = :b AND user_id = :u AND alias_n IS NOT NULL{extra} LIMIT 1"),
                     {"b": int(board_id), "u": int(user_id)}).first()
    return dict(row._mapping) if row is not None else None


def listed_by_alias(rows):
    """For a list of ban rows (`BanRelationship` or `ChatBan`): [(row, label or None)], a label where the row was made by alias. The page draws the label and never
    the account of such a row."""
    return [(row, label(row.alias_n) if row.alias_n else None) for row in rows]
