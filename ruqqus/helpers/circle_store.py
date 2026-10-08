"""Circles, the database side (rules are in helpers/circles.py).

Plain SQL (`sqlalchemy.text`) so the same statements run on SQLite in the tests. Nothing here commits: the caller
does, so a failed step leaves nothing half done. The one exception is `renew_due`, which settles every member on
its own (one member's failure must not hold the others up).

**Coins move with one conditional UPDATE** (`coin_balance = coin_balance - :price WHERE coin_balance >= :price`):
the check and the charge are one statement, so a balance can never go negative and two payments at once cannot both
spend the same coin. The caller must `flush()` its own pending changes to the user rows first and re-read the
users afterwards (`refresh`), because a stale ORM copy of `coin_balance` would be written back over the new one.
"""
import time

from sqlalchemy import text

from ruqqus.helpers import circles

YES, NO = True, False


def _now(now):
    return int(now if now is not None else time.time())


# ------------------------------------------------------------------ the circle itself

def circle_of(db, user_id):
    """(id, price_coins) of an account's Circle, or None."""
    return db.execute(text("SELECT id, price_coins FROM circles WHERE user_id = :u"), {"u": user_id}).fetchone()


def ensure(db, user_id, now=None):
    """The id of an account's Circle, making it if it does not exist yet."""
    row = circle_of(db, user_id)
    if row:
        return row[0]
    db.execute(text("INSERT INTO circles (user_id, price_coins, created_utc) VALUES (:u, 0, :t) ON CONFLICT DO NOTHING"),
               {"u": user_id, "t": _now(now)})
    return circle_of(db, user_id)[0]


def set_price(db, user_id, price, now=None):
    circle_id = ensure(db, user_id, now)
    db.execute(text("UPDATE circles SET price_coins = :p WHERE id = :c"), {"p": price, "c": circle_id})
    return circle_id


# ------------------------------------------------------------------ who is in it

_MEMBER = text("""
    SELECT m.id, m.tier, m.status, m.renews_utc, m.cancelled, m.price_coins
    FROM circle_members m JOIN circles c ON c.id = m.circle_id
    WHERE c.user_id = :owner AND m.user_id = :viewer
""")


def member_row(db, owner_id, viewer_id):
    return db.execute(_MEMBER, {"owner": owner_id, "viewer": viewer_id}).fetchone()


def tier_for(db, owner_id, viewer_id, now=None):
    """The tier that counts for `viewer` in `owner`'s Circle right now: "friend", "subscriber" or None."""
    row = member_row(db, owner_id, viewer_id)
    if not row:
        return None
    return circles.tier_of(row.tier, row.status, row.renews_utc, _now(now))


def blocked_between(db, a_id, b_id):
    return bool(db.execute(text(
        "SELECT 1 FROM userblocks WHERE (user_id = :a AND target_id = :b) OR (user_id = :b AND target_id = :a) LIMIT 1"),
        {"a": a_id, "b": b_id}).fetchone())


def _account(db, user_id):
    return db.execute(text(
        "SELECT id, username, coin_balance, COALESCE(is_deleted, :no) AS is_deleted, COALESCE(is_banned, 0) AS is_banned, "
        "COALESCE(unban_utc, 0) AS unban_utc FROM users WHERE id = :u"), {"u": user_id, "no": NO}).fetchone()


def _gone(account):
    """Deleted, or banned for good (a temporary ban is not gone)."""
    return account is None or bool(account.is_deleted) or (account.is_banned != 0 and account.unban_utc == 0)


def friends(db, owner_id):
    """[(user_id, username, created_utc)] of the owner's close friends, newest first."""
    return db.execute(text("""
        SELECT m.user_id, u.username, m.created_utc FROM circle_members m
        JOIN circles c ON c.id = m.circle_id JOIN users u ON u.id = m.user_id
        WHERE c.user_id = :o AND m.tier = 'friend' AND m.status = 'active'
        ORDER BY m.created_utc DESC, m.id DESC"""), {"o": owner_id}).fetchall()


def subscribers(db, owner_id, now=None):
    """[(user_id, username, renews_utc, cancelled, price_coins)] of those with access now, soonest renewal first."""
    return db.execute(text("""
        SELECT m.user_id, u.username, m.renews_utc, m.cancelled, m.price_coins FROM circle_members m
        JOIN circles c ON c.id = m.circle_id JOIN users u ON u.id = m.user_id
        WHERE c.user_id = :o AND m.tier = 'subscriber' AND m.status = 'active' AND m.renews_utc > :now
        ORDER BY m.renews_utc, m.id"""), {"o": owner_id, "now": _now(now)}).fetchall()


def memberships(db, user_id, now=None):
    """The Circles this account is in: [(owner_id, owner_username, tier, renews_utc, cancelled, price_coins)]."""
    rows = db.execute(text("""
        SELECT c.user_id, u.username, m.tier, m.status, m.renews_utc, m.cancelled, m.price_coins FROM circle_members m
        JOIN circles c ON c.id = m.circle_id JOIN users u ON u.id = c.user_id
        WHERE m.user_id = :v AND c.user_id IS NOT NULL ORDER BY u.username"""), {"v": user_id}).fetchall()
    now = _now(now)
    return [(r.user_id, r.username, circles.tier_of(r.tier, r.status, r.renews_utc, now), r.renews_utc, r.cancelled, r.price_coins)
            for r in rows if circles.tier_of(r.tier, r.status, r.renews_utc, now)]


def earnings(db, owner_id, since=0):
    """Coins that came into the owner's Circle since a date."""
    return int(db.execute(text("SELECT COALESCE(SUM(coins), 0) FROM circle_payments WHERE owner_id = :o AND created_utc >= :s"),
                          {"o": owner_id, "s": since}).scalar() or 0)


# ------------------------------------------------------------------ close friends

def add_friend(db, owner_id, target_id, now=None):
    """Add a close friend. Returns None, or the message why not. A subscriber who is added becomes a friend: they
    keep access and their payments stop (the days already paid for are not refunded)."""
    now = _now(now)
    account = _account(db, target_id)
    row = member_row(db, owner_id, target_id)
    already = bool(row and row.tier == circles.FRIEND and row.status == circles.ACTIVE)
    count = len(friends(db, owner_id))
    refusal = circles.refusal_to_add_friend(
        owner_id=owner_id, target_id=target_id, blocked=blocked_between(db, owner_id, target_id),
        target_gone=_gone(account), friends=count, already=already)
    if refusal:
        return refusal
    circle_id = ensure(db, owner_id, now)
    db.execute(text("""
        INSERT INTO circle_members (circle_id, user_id, tier, status, started_utc, renews_utc, cancelled, price_coins, created_utc)
        VALUES (:c, :u, 'friend', 'active', :t, 0, :no, 0, :t)
        ON CONFLICT (circle_id, user_id) DO UPDATE SET tier = 'friend', status = 'active', renews_utc = 0,
            cancelled = :no, price_coins = 0"""), {"c": circle_id, "u": target_id, "t": now, "no": NO})
    return None


def remove_friend(db, owner_id, target_id):
    """Take a close friend out. Only a friend row goes: a subscriber is never removed this way (they paid)."""
    result = db.execute(text("""
        DELETE FROM circle_members WHERE tier = 'friend' AND user_id = :u
        AND circle_id IN (SELECT id FROM circles WHERE user_id = :o)"""), {"u": target_id, "o": owner_id})
    return result.rowcount > 0


# ------------------------------------------------------------------ subscribing

def _pay(db, payer_id, owner_id, circle_id, coins, kind, now):
    """Move `coins` from payer to owner with one conditional UPDATE. False if the payer cannot cover it."""
    charged = db.execute(text("UPDATE users SET coin_balance = coin_balance - :c WHERE id = :p AND coin_balance >= :c"),
                         {"c": coins, "p": payer_id})
    if charged.rowcount != 1:
        return False
    db.execute(text("UPDATE users SET coin_balance = coin_balance + :c WHERE id = :o"), {"c": coins, "o": owner_id})
    db.execute(text("INSERT INTO circle_payments (circle_id, payer_id, owner_id, coins, kind, created_utc) "
                    "VALUES (:ci, :p, :o, :c, :k, :t)"), {"ci": circle_id, "p": payer_id, "o": owner_id, "c": coins, "k": kind, "t": now})
    return True


def subscribe(db, owner_id, payer_id, now=None):
    """Subscribe `payer_id` to `owner_id`'s Circle. Returns (outcome, text): outcome is "subscribed", "resumed" (a
    cancelled subscriber who still had access: nothing is charged) or "refused" with the reason."""
    now = _now(now)
    circle = circle_of(db, owner_id)
    price = circle.price_coins if circle else 0
    row = member_row(db, owner_id, payer_id)
    tier = circles.tier_of(row.tier, row.status, row.renews_utc, now) if row else None
    cancelled = bool(row and row.cancelled)
    refusal = circles.refusal_to_subscribe(
        owner_id=owner_id, viewer_id=payer_id, price=price, blocked=blocked_between(db, owner_id, payer_id),
        owner_gone=_gone(_account(db, owner_id)), tier=tier, cancelled=cancelled)
    if refusal:
        return "refused", refusal
    if tier == circles.SUBSCRIBER and cancelled:
        db.execute(text("UPDATE circle_members SET cancelled = :no WHERE id = :i"), {"no": NO, "i": row.id})
        return "resumed", "Your subscription will renew again."
    if not _pay(db, payer_id, owner_id, circle.id, price, "subscribe", now):
        return "refused", f"You need {price} coins to subscribe and you don't have enough."
    db.execute(text("""
        INSERT INTO circle_members (circle_id, user_id, tier, status, started_utc, renews_utc, cancelled, price_coins, created_utc)
        VALUES (:c, :u, 'subscriber', 'active', :t, :r, :no, :p, :t)
        ON CONFLICT (circle_id, user_id) DO UPDATE SET tier = 'subscriber', status = 'active', started_utc = :t,
            renews_utc = :r, cancelled = :no, price_coins = :p"""),
        {"c": circle.id, "u": payer_id, "t": now, "r": circles.next_renewal(now, 0), "no": NO, "p": price})
    return "subscribed", f"You're in their Circle. It renews in 30 days for {price} coins unless you cancel."


def cancel(db, owner_id, payer_id):
    """Stop a subscription from renewing. They keep access until the date they paid to."""
    result = db.execute(text("""
        UPDATE circle_members SET cancelled = :yes WHERE tier = 'subscriber' AND status = 'active' AND user_id = :u
        AND circle_id IN (SELECT id FROM circles WHERE user_id = :o)"""), {"yes": YES, "u": payer_id, "o": owner_id})
    return result.rowcount > 0


def end_between(db, a_id, b_id):
    """A block between two accounts takes each out of the other's Circle (friend rows go, subscriptions end with no
    refund). Returns how many rows changed."""
    changed = 0
    for owner, member in ((a_id, b_id), (b_id, a_id)):
        changed += db.execute(text("""
            DELETE FROM circle_members WHERE tier = 'friend' AND user_id = :m
            AND circle_id IN (SELECT id FROM circles WHERE user_id = :o)"""), {"m": member, "o": owner}).rowcount
        changed += db.execute(text("""
            UPDATE circle_members SET status = 'ended', cancelled = :yes WHERE tier = 'subscriber' AND status = 'active'
            AND user_id = :m AND circle_id IN (SELECT id FROM circles WHERE user_id = :o)"""), {"yes": YES, "m": member, "o": owner}).rowcount
    return changed


# ------------------------------------------------------------------ renewals

_DUE = text("""
    SELECT m.id, m.user_id, m.circle_id, m.cancelled, m.price_coins, m.renews_utc, c.user_id AS owner_id
    FROM circle_members m JOIN circles c ON c.id = m.circle_id
    WHERE m.tier = 'subscriber' AND m.status = 'active' AND m.renews_utc <= :now AND c.user_id IS NOT NULL
    ORDER BY m.renews_utc, m.id LIMIT :limit""")


def renew_due(db, now=None, limit=500):
    """Settle every subscriber whose paid days have run out: renew from their coins, or end the subscription.
    Commits after each one. Returns [(event, payer_id, owner_id, coins)] with event renewed, lapsed or ended, for
    the caller to tell people about."""
    now = _now(now)
    events = []
    for row in db.execute(_DUE, {"now": now, "limit": limit}).fetchall():
        account = _account(db, row.user_id)
        balance = account.coin_balance if account else 0
        decision = circles.renewal(now, row.renews_utc, row.cancelled, balance, row.price_coins)
        if decision == circles.RENEW and blocked_between(db, row.owner_id, row.user_id):
            decision = circles.END
        if decision == circles.RENEW:
            if _pay(db, row.user_id, row.owner_id, row.circle_id, row.price_coins, "renew", now):
                db.execute(text("UPDATE circle_members SET renews_utc = :r WHERE id = :i"),
                           {"r": circles.next_renewal(now, row.renews_utc), "i": row.id})
                events.append(("renewed", row.user_id, row.owner_id, row.price_coins))
            else:
                decision = circles.LAPSE
        if decision in (circles.LAPSE, circles.END):
            db.execute(text("UPDATE circle_members SET status = 'ended' WHERE id = :i"), {"i": row.id})
            events.append(("lapsed" if decision == circles.LAPSE else "ended", row.user_id, row.owner_id, row.price_coins))
        db.commit()
    return events
