"""Buying coins and paying off what an account owes: the rules (bug hunt 2026-10, H4). Pure, no Flask.

`users.negative_balance_cents` is what an account owes (a refund or chargeback PayPal took back), never below 0: every
check reads it as "owes something" when it is not 0, so a value below 0 locked the account for good. That is what a
purchase of 0 coins did: it was priced at 49 cents and then paid off 49 cents of a debt that wasn't there."""

MIN_COINS = 1
MAX_COINS = 10_000
BAD_COUNT = "Choose how many coins to buy."
FREE = "That promo code makes this order free, which can't be paid for. Choose another package."
NOTHING_OWED = "Your account doesn't owe anything."


def parse_coin_count(value):
    """The number of coins asked for (1 to MAX_COINS), or None: only plain ASCII digits count."""
    value = (value or "").strip() if isinstance(value, str) else ""
    if not value.isascii() or not value.isdigit() or len(value) > 6:
        return None
    count = int(value)
    return count if MIN_COINS <= count <= MAX_COINS else None


def owed_after_payment(owed, paid):
    """What is still owed once `paid` cents of a debt-payment order arrived: never below 0."""
    return max(0, (owed or 0) - max(0, paid or 0))
