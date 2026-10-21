"""Tips: the rules (no I/O). The database side is helpers/tips_store.py; the routes are `gift_post_pid` and
`gift_comment_pid` in routes/paypal.py.

A tip moves coins from the tipper to the author and is shown on the post as the total coins tipped (never who
tipped: tips are anonymous). It also lifts the post in the ranked sorts: a post's `tip_boost` is added to its
`upvotes - downvotes` in the Postgres functions `rank_hot` and `rank_best` (so Hot, Best, For You and curations), but
**not** to its score or the Top sort, so the number shown on a post stays an honest vote count.

The boost is counted per person: each person whose tips count adds `tipper_boost(their coins on the post)`, worth
PER_TIPPER upvotes plus PER_DOUBLING for each doubling of their coins, at most PER_TIPPER_MAX. Tipping again moves a
person up the doubling scale; it never adds a second person's worth.

Whose tips count for the boost (the coins move either way): not the author's own, only accounts at least
ACCOUNT_AGE_DAYS old that are not banned or deleted, and never an account sharing an address with the author
(helpers/guild_limits.addresses_of), because a tip from one's own second account would cost nothing: the coins come
straight back.

**Bots take tips** (the owner, 2026-10-20), so coins can lift a bot's post. Whoever the coins go to is
`recipient_id`: a member bot's coins go to the member who runs it, and a site bot's to nobody (they are spent and only
lift the post). For a member bot the owner stands in for the author in the boost rules (`answerable_id`): the owner's
own tips and their second accounts' would cost them nothing, since the coins come back.
"""
import math

PER_TIPPER = 10
PER_DOUBLING = 2
PER_TIPPER_MAX = 20
MAX_TIP = 1000
ACCOUNT_AGE_DAYS = 30
DAY = 86400


def parse_coins(raw):
    """A tip's coins: a whole number from 1 to MAX_TIP, or None."""
    text = ("1" if raw is None else str(raw)).strip()
    if not (text.isascii() and text.isdigit()):
        return None
    coins = int(text)
    return coins if 1 <= coins <= MAX_TIP else None


def tipper_boost(coins):
    """Upvotes' worth one person's coins on a post add to its ranking."""
    if coins < 1:
        return 0
    return min(PER_TIPPER_MAX, PER_TIPPER + PER_DOUBLING * int(math.floor(math.log2(coins))))


def post_boost(coins_by_tipper):
    """A post's tip_boost: {tipper id: their coins on it}, only tippers whose tips count."""
    return float(sum(tipper_boost(c) for c in coins_by_tipper.values()))


def established_before(now):
    """A tipper counts if their account was made before this moment."""
    return int(now) - ACCOUNT_AGE_DAYS * DAY


def recipient_id(author_id, is_bot=False, bot_owner_id=None):
    """Who gets a post's tip coins: its author; for a member bot, the member who runs it; for a site bot (no owner),
    nobody: None, the coins are spent and only lift the post."""
    if not is_bot:
        return author_id
    return bot_owner_id or None


def answerable_id(author_id, is_bot=False, bot_owner_id=None):
    """Whose own tips (and whose second accounts' tips) never count for a post's boost: whoever the coins come back
    to. Nobody gets a site bot's coins back, so that is only the bot itself (which can't tip)."""
    return recipient_id(author_id, is_bot, bot_owner_id) or author_id


def label(coins):
    return f"{coins} coin{'s' if coins != 1 else ''} tipped"
