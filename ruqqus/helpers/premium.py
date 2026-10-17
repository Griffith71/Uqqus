"""Premium time bought with coins (audit $-04, $-05).

A coin buys a week of Premium **only when the member asks**: the Redeem button on /settings/premium, or the
auto-renewal they switched on (`users.premium_auto_renew`), which the hourly circles job runs (`renew_due`). Reading
`User.has_premium` never spends anything any more (it used to spend a coin whenever a page asked, sidebar included).
"Premium only" means a week that is paid for and running (`User.has_premium_no_renew`), never "has a coin".
The coin moves with `helpers/coins.spend` (one conditional statement). Nothing here commits.
"""
from sqlalchemy import text

from ruqqus.helpers import coins

WEEK = 7 * 86400


def redeem_week(db, user, now):
    """Spends one of `user`'s coins on a week of Premium, added after any time left. True when it did."""
    db.flush()
    if not coins.spend(db, user.id, 1):
        return False
    db.refresh(user)
    user.premium_expires_utc = max(user.premium_expires_utc or 0, now) + WEEK
    if not user.premium_first_purchased_utc:
        user.premium_first_purchased_utc = now
    db.add(user)
    return True


def due_ids(db, now, limit=500):
    """Accounts that asked to renew automatically and whose week has run out (or runs out within the hour)."""
    return [r[0] for r in db.execute(text("""SELECT id FROM users WHERE premium_auto_renew = :yes AND coin_balance >= 1
                                               AND premium_expires_utc > 0 AND premium_expires_utc <= :soon
                                               AND COALESCE(negative_balance_cents, 0) = 0 AND is_deleted = :no
                                             ORDER BY premium_expires_utc LIMIT :n"""),
                                     {"yes": True, "no": False, "soon": now + 3600, "n": limit})]
