"""Circles, the database side (rules are in helpers/circles.py).

Plain SQL (`sqlalchemy.text`) so the same statements run on SQLite in the tests. Nothing here commits: the caller
does, so a failed step leaves nothing half done. The one exception is `renew_due`, which settles every member on
its own (one member's failure must not hold the others up).

**Only an owner's own circles are ever changed**: every function that changes a circle takes the owner's id and
checks the circle is theirs (`owned`), so a route that passes the signed-in member's id cannot touch anyone else's.

**Who is in which circle** is `viewer_circle_ids`: a custom circle's active members, a paid circle's subscribers whose
paid days are still ahead, and the Friends circle when the two accounts follow each other right now.

**Coins move with one conditional UPDATE** (`coin_balance = coin_balance - :price WHERE coin_balance >= :price`):
the check and the charge are one statement, so a balance can never go negative and two payments at once cannot both
spend the same coin. The caller must `flush()` its own pending changes to the user rows first and re-read the
users afterwards (`refresh`), because a stale ORM copy of `coin_balance` would be written back over the new one.
"""
import time

from sqlalchemy import bindparam, text

from ruqqus.helpers import circles

YES, NO = True, False

_SELECT = "SELECT id, user_id, board_id, kind, name, price_coins, created_utc FROM circles"
_KIND_ORDER = {circles.FRIENDS: 0, circles.CUSTOM: 1, circles.PAID: 2}


def _now(now):
    return int(now if now is not None else time.time())


def _ids(values):
    return sorted({int(v) for v in values if v})


# ------------------------------------------------------------------ an account's circles

def circles_of(db, owner_id, kinds=(circles.FRIENDS, circles.CUSTOM, circles.PAID)):
    """The owner's circles of these kinds: Friends first, then their own circles, then paid ones, each by name."""
    rows = db.execute(text(_SELECT + " WHERE user_id = :o"), {"o": owner_id}).fetchall()
    rows = [r for r in rows if r.kind in kinds]
    return sorted(rows, key=lambda r: (_KIND_ORDER.get(r.kind, 9), r.name.casefold(), r.id))


def get(db, circle_id):
    return db.execute(text(_SELECT + " WHERE id = :i"), {"i": circle_id}).fetchone()


def owned(db, owner_id, circle_id, kinds=(circles.FRIENDS, circles.CUSTOM, circles.PAID)):
    """The circle if it is one of the owner's of these kinds, else None (someone else's, a guild's, or none)."""
    row = get(db, circle_id) if circle_id else None
    if row is None or row.user_id != owner_id or row.kind not in kinds:
        return None
    return row


def friends_circle(db, owner_id):
    return db.execute(text(_SELECT + " WHERE user_id = :o AND kind = 'friends'"), {"o": owner_id}).fetchone()


def ensure_friends(db, owner_id, now=None):
    """The id of the owner's Friends circle, making its row the first time it is needed (every account has it)."""
    row = friends_circle(db, owner_id)
    if row:
        return row.id
    db.execute(text("INSERT INTO circles (user_id, price_coins, created_utc, name, kind) VALUES (:o, 0, :t, '', 'friends') "
                    "ON CONFLICT DO NOTHING"), {"o": owner_id, "t": _now(now)})
    return friends_circle(db, owner_id).id


def count_kind(db, owner_id, kind):
    return int(db.execute(text("SELECT COUNT(*) FROM circles WHERE user_id = :o AND kind = :k"), {"o": owner_id, "k": kind}).scalar() or 0)


def _name_taken(db, owner_id, name, except_id=None):
    """Is this name one of the owner's circles already, ignoring case? (Python's casefold: SQLite's lower() is ASCII.)"""
    wanted = name.casefold()
    rows = db.execute(text("SELECT id, name FROM circles WHERE user_id = :o AND kind IN ('custom', 'paid')"), {"o": owner_id}).fetchall()
    return any(r.name.casefold() == wanted and r.id != except_id for r in rows)


def create(db, owner_id, kind, name, price=0, now=None):
    """(circle id, None) or (None, message). The kind's limits and Premium are the caller's (circles.refusal_to_make)."""
    if kind not in (circles.CUSTOM, circles.PAID):
        return None, "That kind of circle can't be made."
    if _name_taken(db, owner_id, name):
        return None, "You already have a circle with that name."
    new_id = db.execute(text("INSERT INTO circles (user_id, price_coins, created_utc, name, kind) VALUES (:o, :p, :t, :n, :k) RETURNING id"),
                        {"o": owner_id, "p": price if kind == circles.PAID else 0, "t": _now(now), "n": name, "k": kind}).scalar()
    return new_id, None


def rename(db, owner_id, circle_id, name):
    """None, or the message why not. Friends keeps its own name."""
    if not owned(db, owner_id, circle_id, kinds=(circles.CUSTOM, circles.PAID)):
        return "That circle isn't yours."
    if _name_taken(db, owner_id, name, except_id=circle_id):
        return "You already have a circle with that name."
    db.execute(text("UPDATE circles SET name = :n WHERE id = :i"), {"n": name, "i": circle_id})
    return None


def delete(db, owner_id, circle_id, now=None):
    """None, or the message why not. Its people go with it and posts made for it lose it (they stay for the other
    circles they were for, or for the author alone). A paid circle goes only once nobody has paid days left in it; the
    record of what came in stays. Friends can't be deleted."""
    row = owned(db, owner_id, circle_id, kinds=(circles.CUSTOM, circles.PAID))
    if row is None:
        return "That circle isn't yours."
    if row.kind == circles.PAID and subscribers(db, circle_id, now):
        return ("People have paid for this circle and still have days left. Set its price to 0 so nobody new joins, "
                "and delete it when their paid days are over.")
    db.execute(text("DELETE FROM post_circles WHERE circle_id = :i"), {"i": circle_id})
    db.execute(text("DELETE FROM story_circles WHERE circle_id = :i"), {"i": circle_id})
    db.execute(text("DELETE FROM circle_members WHERE circle_id = :i"), {"i": circle_id})
    db.execute(text("UPDATE circle_payments SET circle_id = NULL WHERE circle_id = :i"), {"i": circle_id})
    db.execute(text("DELETE FROM circles WHERE id = :i"), {"i": circle_id})
    return None


def set_price(db, owner_id, circle_id, price):
    """None, or the message why not. Existing subscribers keep the price they signed up at."""
    if not owned(db, owner_id, circle_id, kinds=(circles.PAID,)):
        return "That circle isn't yours."
    db.execute(text("UPDATE circles SET price_coins = :p WHERE id = :i"), {"p": price, "i": circle_id})
    return None


def resolve(db, owner_id, tokens, now=None):
    """(circle ids, None) or (None, message) for what a form chose: FRIENDS_TOKEN (the Friends circle, made if needed)
    or the id of one of the owner's own circles. Anyone else's circle is refused the same as a made-up one."""
    found = []
    for token in tokens:
        if token == circles.FRIENDS_TOKEN:
            found.append(ensure_friends(db, owner_id, now))
            continue
        row = owned(db, owner_id, int(token))
        if row is None:
            return None, "One of those circles isn't yours any more. Choose again."
        found.append(row.id)
    return sorted(set(found)), None


# ------------------------------------------------------------------ who is in them

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


_MUTUAL = """SELECT f.target_id FROM follows f WHERE f.user_id = :o
             AND EXISTS (SELECT 1 FROM follows b WHERE b.user_id = f.target_id AND b.target_id = :o)"""


def mutual_ids(db, owner_id):
    """Everyone the owner follows who follows them back: the owner's Friends circle."""
    return {r[0] for r in db.execute(text(_MUTUAL), {"o": owner_id}).fetchall()}


def mutual_count(db, owner_id):
    return int(db.execute(text("SELECT COUNT(*) FROM (" + _MUTUAL + ") m"), {"o": owner_id}).scalar() or 0)


_VIEWER_CIRCLES = text("""
    SELECT c.id FROM circles c JOIN circle_members m ON m.circle_id = c.id
    WHERE c.user_id = :o AND m.user_id = :v AND m.status = 'active'
      AND ((c.kind = 'custom' AND m.tier = 'member') OR (c.kind = 'paid' AND m.tier = 'subscriber' AND m.renews_utc > :now))
    UNION
    SELECT c.id FROM circles c WHERE c.user_id = :o AND c.kind = 'friends'
      AND EXISTS (SELECT 1 FROM follows f WHERE f.user_id = :o AND f.target_id = :v)
      AND EXISTS (SELECT 1 FROM follows f WHERE f.user_id = :v AND f.target_id = :o)
""")


def viewer_circle_ids(db, owner_id, viewer_id, now=None):
    """The ids of the owner's circles the viewer is in right now (an empty set for the owner themself: they see their
    own posts anyway). A block either way takes them out of all of them."""
    if viewer_id is None or viewer_id == owner_id or blocked_between(db, owner_id, viewer_id):
        return set()
    return {r[0] for r in db.execute(_VIEWER_CIRCLES, {"o": owner_id, "v": viewer_id, "now": _now(now)}).fetchall()}


def members(db, circle_id):
    """[(user_id, username, created_utc)] of a custom circle's people, newest first."""
    return db.execute(text("""
        SELECT m.user_id, u.username, m.created_utc FROM circle_members m JOIN users u ON u.id = m.user_id
        WHERE m.circle_id = :c AND m.tier = 'member' AND m.status = 'active'
        ORDER BY m.created_utc DESC, m.id DESC"""), {"c": circle_id}).fetchall()


_MEMBER_COUNTS = text("""SELECT circle_id, COUNT(*) FROM circle_members WHERE circle_id IN :ids AND status = 'active'
                         AND (tier = 'member' OR (tier = 'subscriber' AND renews_utc > :now)) GROUP BY circle_id"""
                      ).bindparams(bindparam("ids", expanding=True))


def member_counts(db, circle_ids, now=None):
    """{circle id: people in it now} for custom and paid circles (Friends is `mutual_count`)."""
    ids = _ids(circle_ids)
    if not ids:
        return {}
    return {r[0]: int(r[1]) for r in db.execute(_MEMBER_COUNTS, {"ids": ids, "now": _now(now)}).fetchall()}


def add_member(db, owner_id, circle_id, target_id, now=None):
    """Put someone in one of the owner's custom circles. None, or the message why not. They are not told."""
    if not owned(db, owner_id, circle_id, kinds=(circles.CUSTOM,)):
        return "That circle isn't yours."
    already = bool(db.execute(text("SELECT 1 FROM circle_members WHERE circle_id = :c AND user_id = :u AND tier = 'member' AND status = 'active'"),
                              {"c": circle_id, "u": target_id}).fetchone())
    refusal = circles.refusal_to_add_member(
        owner_id=owner_id, target_id=target_id, blocked=blocked_between(db, owner_id, target_id),
        target_gone=_gone(_account(db, target_id)), members=member_counts(db, [circle_id]).get(circle_id, 0), already=already)
    if refusal:
        return refusal
    now = _now(now)
    db.execute(text("""
        INSERT INTO circle_members (circle_id, user_id, tier, status, started_utc, renews_utc, cancelled, price_coins, created_utc)
        VALUES (:c, :u, 'member', 'active', :t, 0, :no, 0, :t)
        ON CONFLICT (circle_id, user_id) DO UPDATE SET tier = 'member', status = 'active', renews_utc = 0, cancelled = :no, price_coins = 0"""),
        {"c": circle_id, "u": target_id, "t": now, "no": NO})
    return None


def remove_member(db, owner_id, circle_id, target_id):
    """Take someone out of one of the owner's custom circles. False when they were not in it."""
    if not owned(db, owner_id, circle_id, kinds=(circles.CUSTOM,)):
        return False
    return db.execute(text("DELETE FROM circle_members WHERE circle_id = :c AND user_id = :u AND tier = 'member'"),
                      {"c": circle_id, "u": target_id}).rowcount > 0


def custom_circles_with(db, owner_id, target_id):
    """The ids of the owner's custom circles this account is in (the owner's "Add to circles" sheet; nobody else asks)."""
    return {r[0] for r in db.execute(text("""
        SELECT c.id FROM circles c JOIN circle_members m ON m.circle_id = c.id
        WHERE c.user_id = :o AND c.kind = 'custom' AND m.user_id = :u AND m.tier = 'member' AND m.status = 'active'"""),
        {"o": owner_id, "u": target_id}).fetchall()}


def eligible_ids(db, owner_id, circle_ids, now=None):
    """Account ids in any of these circles of the owner's right now (for notifying them and nobody else). Blocked
    accounts are left out (a block ends follows and memberships, so this is a second line)."""
    rows = [r for r in (get(db, i) for i in _ids(circle_ids)) if r is not None and r.user_id == owner_id]
    now = _now(now)
    found = set()
    for row in rows:
        if row.kind == circles.FRIENDS:
            found |= mutual_ids(db, owner_id)
        elif row.kind == circles.CUSTOM:
            found |= {m.user_id for m in members(db, row.id)}
        elif row.kind == circles.PAID:
            found |= {s.user_id for s in subscribers(db, row.id, now)}
    if not found:
        return found
    blocked = {r[0] if r[1] == owner_id else r[1] for r in db.execute(text(
        "SELECT user_id, target_id FROM userblocks WHERE user_id = :o OR target_id = :o"), {"o": owner_id}).fetchall()}
    return found - blocked - {owner_id}


def reaches_nobody(db, owner_id, circle_ids, now=None):
    """Would something made for these circles be seen by nobody but its author right now?"""
    return not eligible_ids(db, owner_id, circle_ids, now)


# ------------------------------------------------------------------ what a post or a story is for

_POST_CIRCLES = text("SELECT post_id, circle_id FROM post_circles WHERE post_id IN :ids").bindparams(bindparam("ids", expanding=True))
_STORY_CIRCLES = text("SELECT story_id, circle_id FROM story_circles WHERE story_id IN :ids").bindparams(bindparam("ids", expanding=True))


def _grouped(db, statement, ids):
    ids = _ids(ids)
    found = {i: set() for i in ids}
    if ids:
        for item_id, circle_id in db.execute(statement, {"ids": ids}).fetchall():
            found.setdefault(item_id, set()).add(circle_id)
    return found


def post_circle_ids(db, post_ids):
    """{post id: the ids of the circles it is for} (an empty set for a post made for none, or for Public)."""
    return _grouped(db, _POST_CIRCLES, post_ids)


def story_circle_ids(db, story_ids):
    return _grouped(db, _STORY_CIRCLES, story_ids)


def set_post_circles(db, post_id, circle_ids):
    db.execute(text("DELETE FROM post_circles WHERE post_id = :p"), {"p": post_id})
    for circle_id in _ids(circle_ids):
        db.execute(text("INSERT INTO post_circles (post_id, circle_id) VALUES (:p, :c)"), {"p": post_id, "c": circle_id})


def set_story_circles(db, story_id, circle_ids):
    db.execute(text("DELETE FROM story_circles WHERE story_id = :s"), {"s": story_id})
    for circle_id in _ids(circle_ids):
        db.execute(text("INSERT INTO story_circles (story_id, circle_id) VALUES (:s, :c)"), {"s": story_id, "c": circle_id})


def names_of(db, circle_ids):
    """[(id, kind, name)] of these circles, Friends first then by name (Friends has no stored name)."""
    rows = [r for r in (get(db, i) for i in _ids(circle_ids)) if r is not None]
    rows.sort(key=lambda r: (_KIND_ORDER.get(r.kind, 9), r.name.casefold(), r.id))
    return [(r.id, r.kind, circles.FRIENDS_NAME if r.kind == circles.FRIENDS else r.name) for r in rows]


# ------------------------------------------------------------------ paid circles

def paid_circles(db, owner_id):
    return circles_of(db, owner_id, kinds=(circles.PAID,))


def subscriber_row(db, circle_id, viewer_id):
    return db.execute(text("SELECT id, tier, status, renews_utc, cancelled, price_coins FROM circle_members WHERE circle_id = :c AND user_id = :u"),
                      {"c": circle_id, "u": viewer_id}).fetchone()


def subscribers(db, circle_id, now=None):
    """[(user_id, username, renews_utc, cancelled, price_coins)] of a paid circle's people with access now, soonest renewal first."""
    return db.execute(text("""
        SELECT m.user_id, u.username, m.renews_utc, m.cancelled, m.price_coins FROM circle_members m JOIN users u ON u.id = m.user_id
        WHERE m.circle_id = :c AND m.tier = 'subscriber' AND m.status = 'active' AND m.renews_utc > :now
        ORDER BY m.renews_utc, m.id"""), {"c": circle_id, "now": _now(now)}).fetchall()


def memberships(db, user_id, now=None):
    """The paid circles this account is in, with access now:
    [(circle_id, owner_id, owner_username, name, renews_utc, cancelled, price_coins)]. Custom circles are never listed:
    nobody is told they are in one."""
    return db.execute(text("""
        SELECT c.id AS circle_id, c.user_id AS owner_id, u.username, c.name, m.renews_utc, m.cancelled, m.price_coins
        FROM circle_members m JOIN circles c ON c.id = m.circle_id JOIN users u ON u.id = c.user_id
        WHERE m.user_id = :v AND c.kind = 'paid' AND m.tier = 'subscriber' AND m.status = 'active' AND m.renews_utc > :now
        ORDER BY u.username, c.name"""), {"v": user_id, "now": _now(now)}).fetchall()


def earnings(db, owner_id, since=0):
    """Coins that came into the owner's paid circles since a date."""
    return int(db.execute(text("SELECT COALESCE(SUM(coins), 0) FROM circle_payments WHERE owner_id = :o AND created_utc >= :s"),
                          {"o": owner_id, "s": since}).scalar() or 0)


def earnings_of(db, circle_id, since=0):
    return int(db.execute(text("SELECT COALESCE(SUM(coins), 0) FROM circle_payments WHERE circle_id = :c AND created_utc >= :s"),
                          {"c": circle_id, "s": since}).scalar() or 0)


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


def subscribe(db, circle_id, payer_id, now=None):
    """Subscribe `payer_id` to a paid circle. Returns (outcome, text): outcome is "subscribed", "resumed" (a cancelled
    subscriber who still had access: nothing is charged) or "refused" with the reason."""
    now = _now(now)
    circle = get(db, circle_id)
    if circle is None or circle.kind != circles.PAID:
        return "refused", "That circle isn't taking subscribers."
    row = subscriber_row(db, circle.id, payer_id)
    tier = circles.tier_of(row.tier, row.status, row.renews_utc, now) if row else None
    cancelled = bool(row and row.cancelled)
    refusal = circles.refusal_to_subscribe(
        owner_id=circle.user_id, viewer_id=payer_id, price=circle.price_coins, blocked=blocked_between(db, circle.user_id, payer_id),
        owner_gone=_gone(_account(db, circle.user_id)), tier=tier, cancelled=cancelled)
    if refusal:
        return "refused", refusal
    if tier == circles.SUBSCRIBER and cancelled:
        db.execute(text("UPDATE circle_members SET cancelled = :no WHERE id = :i"), {"no": NO, "i": row.id})
        return "resumed", "Your subscription will renew again."
    price = circle.price_coins
    if not _pay(db, payer_id, circle.user_id, circle.id, price, "subscribe", now):
        return "refused", f"You need {price} coins to subscribe and you don't have enough."
    db.execute(text("""
        INSERT INTO circle_members (circle_id, user_id, tier, status, started_utc, renews_utc, cancelled, price_coins, created_utc)
        VALUES (:c, :u, 'subscriber', 'active', :t, :r, :no, :p, :t)
        ON CONFLICT (circle_id, user_id) DO UPDATE SET tier = 'subscriber', status = 'active', started_utc = :t,
            renews_utc = :r, cancelled = :no, price_coins = :p"""),
        {"c": circle.id, "u": payer_id, "t": now, "r": circles.next_renewal(now, 0), "no": NO, "p": price})
    return "subscribed", f"You're subscribed. It renews in 30 days for {price} coins unless you cancel."


def cancel(db, circle_id, payer_id):
    """Stop a subscription to a paid circle from renewing. They keep access until the date they paid to."""
    return db.execute(text("""
        UPDATE circle_members SET cancelled = :yes WHERE circle_id = :c AND tier = 'subscriber' AND status = 'active' AND user_id = :u
        AND circle_id IN (SELECT id FROM circles WHERE kind = 'paid')"""), {"yes": YES, "c": circle_id, "u": payer_id}).rowcount > 0


def end_between(db, a_id, b_id):
    """A block between two accounts takes each out of the other's circles: they leave each other's custom circles and
    their subscriptions end with no refund (the block also ends the follows, so Friends follows). Returns how many
    rows changed."""
    changed = 0
    for owner, member in ((a_id, b_id), (b_id, a_id)):
        changed += db.execute(text("""
            DELETE FROM circle_members WHERE tier = 'member' AND user_id = :m
            AND circle_id IN (SELECT id FROM circles WHERE user_id = :o)"""), {"m": member, "o": owner}).rowcount
        changed += db.execute(text("""
            UPDATE circle_members SET status = 'ended', cancelled = :yes WHERE tier = 'subscriber' AND status = 'active'
            AND user_id = :m AND circle_id IN (SELECT id FROM circles WHERE user_id = :o)"""), {"yes": YES, "m": member, "o": owner}).rowcount
    return changed


# ------------------------------------------------------------------ renewals

_DUE = text("""
    SELECT m.id, m.user_id, m.circle_id, m.cancelled, m.price_coins, m.renews_utc,
           COALESCE(c.user_id, b.creator_id) AS owner_id, c.board_id, c.name
    FROM circle_members m JOIN circles c ON c.id = m.circle_id LEFT JOIN boards b ON b.id = c.board_id
    WHERE m.tier = 'subscriber' AND m.status = 'active' AND m.renews_utc <= :now
    ORDER BY m.renews_utc, m.id LIMIT :limit""")


def renew_due(db, now=None, limit=500):
    """Settle every subscriber whose paid days have run out: renew from their coins, or end the subscription.
    Commits after each one. Returns [(event, payer_id, owner_id, coins, board_id, circle name)] with event renewed,
    lapsed or ended, for the caller to tell people about. A Circle guild's subscription goes to the guild's founder
    and, when it ends, takes the member's access with it. `board_id` is the guild, or None for a paid circle."""
    now = _now(now)
    events = []
    for row in db.execute(_DUE, {"now": now, "limit": limit}).fetchall():
        account = _account(db, row.user_id)
        balance = account.coin_balance if account else 0
        decision = circles.renewal(now, row.renews_utc, row.cancelled, balance, row.price_coins)
        if decision == circles.RENEW and (blocked_between(db, row.owner_id, row.user_id)
                                          or (row.board_id and exiled_from(db, row.board_id, row.user_id))):
            decision = circles.END
        if decision == circles.RENEW:
            if _pay(db, row.user_id, row.owner_id, row.circle_id, row.price_coins, "renew", now):
                db.execute(text("UPDATE circle_members SET renews_utc = :r WHERE id = :i"),
                           {"r": circles.next_renewal(now, row.renews_utc), "i": row.id})
                events.append(("renewed", row.user_id, row.owner_id, row.price_coins, row.board_id, row.name))
            else:
                decision = circles.LAPSE
        if decision in (circles.LAPSE, circles.END):
            db.execute(text("UPDATE circle_members SET status = 'ended' WHERE id = :i"), {"i": row.id})
            if row.board_id:
                leave_guild(db, row.board_id, row.user_id)
            events.append(("lapsed" if decision == circles.LAPSE else "ended", row.user_id, row.owner_id, row.price_coins, row.board_id, row.name))
        db.commit()
    return events


# ------------------------------------------------------------------ Circle guilds

def circle_of_board(db, board_id):
    """(id, price_coins) of a Circle guild's circle, or None."""
    return db.execute(text("SELECT id, price_coins FROM circles WHERE board_id = :b"), {"b": board_id}).fetchone()


def set_board_price(db, board_id, price, now=None):
    """Set what joining a Circle guild costs every 30 days (0: by invitation only). Makes the guild's circle if needed."""
    if not circle_of_board(db, board_id):
        db.execute(text("INSERT INTO circles (board_id, price_coins, created_utc, name, kind) VALUES (:b, 0, :t, '', 'guild') "
                        "ON CONFLICT DO NOTHING"), {"b": board_id, "t": _now(now)})
    circle_id = circle_of_board(db, board_id).id
    db.execute(text("UPDATE circles SET price_coins = :p WHERE id = :c"), {"p": price, "c": circle_id})
    return circle_id


def guild_row(db, board_id):
    """(creator_id, is_circle, is_banned, name) of a guild, or None."""
    return db.execute(text("SELECT creator_id, COALESCE(is_circle, :no) AS is_circle, COALESCE(is_banned, :no) AS is_banned, name "
                           "FROM boards WHERE id = :b"), {"b": board_id, "no": NO}).fetchone()


def exiled_from(db, board_id, user_id):
    return bool(db.execute(text("SELECT 1 FROM bans WHERE board_id = :b AND user_id = :u AND is_active = :yes LIMIT 1"),
                           {"b": board_id, "u": user_id, "yes": YES}).fetchone())


def is_guild_member(db, board_id, user_id):
    """An approved contributor or a guildmaster of the guild (whoever got there: invited free or paid)."""
    return bool(db.execute(text("""
        SELECT 1 WHERE EXISTS (SELECT 1 FROM contributors WHERE user_id = :u AND board_id = :b AND is_active = :yes)
        OR EXISTS (SELECT 1 FROM mods WHERE user_id = :u AND board_id = :b AND accepted = :yes AND COALESCE(invite_rescinded, :no) = :no)"""),
        {"u": user_id, "b": board_id, "yes": YES, "no": NO}).fetchone())


def join_guild(db, board_id, user_id, approving_id, now=None):
    """Make an account an approved contributor of the guild (reactivating a row it had before)."""
    changed = db.execute(text("UPDATE contributors SET is_active = :yes WHERE user_id = :u AND board_id = :b"),
                         {"yes": YES, "u": user_id, "b": board_id}).rowcount
    if not changed:
        db.execute(text("INSERT INTO contributors (user_id, board_id, created_utc, is_active, approving_mod_id) "
                        "VALUES (:u, :b, :t, :yes, :a)"), {"u": user_id, "b": board_id, "t": _now(now), "yes": YES, "a": approving_id})


def leave_guild(db, board_id, user_id):
    """Take an account's access to the guild away (its contributor row goes inactive; a guildmaster stays one)."""
    db.execute(text("UPDATE contributors SET is_active = :no WHERE user_id = :u AND board_id = :b"), {"no": NO, "u": user_id, "b": board_id})


def end_guild_member(db, board_id, user_id):
    """A guildmaster removed a member: their paid subscription (if any) ends with no refund, so they are not charged again."""
    return db.execute(text("""
        UPDATE circle_members SET status = 'ended', cancelled = :yes WHERE tier = 'subscriber' AND status = 'active' AND user_id = :u
        AND circle_id IN (SELECT id FROM circles WHERE board_id = :b)"""), {"yes": YES, "u": user_id, "b": board_id}).rowcount


def subscribe_guild(db, board_id, payer_id, now=None):
    """Join a Circle guild for its price: the coins go to the guild's founder and the payer becomes an approved
    contributor. Returns (outcome, text): "subscribed", "resumed" or "refused" with the reason."""
    now = _now(now)
    guild = guild_row(db, board_id)
    circle = circle_of_board(db, board_id)
    price = circle.price_coins if circle else 0
    if guild is None or not guild.is_circle or guild.is_banned:
        return "refused", "That guild isn't a Circle guild."
    founder = guild.creator_id
    row = db.execute(text("SELECT m.id, m.tier, m.status, m.renews_utc, m.cancelled FROM circle_members m JOIN circles c ON c.id = m.circle_id "
                          "WHERE c.board_id = :b AND m.user_id = :u"), {"b": board_id, "u": payer_id}).fetchone()
    tier = circles.tier_of(row.tier, row.status, row.renews_utc, now) if row else None
    cancelled = bool(row and row.cancelled)
    if tier == circles.SUBSCRIBER and cancelled:
        db.execute(text("UPDATE circle_members SET cancelled = :no WHERE id = :i"), {"no": NO, "i": row.id})
        return "resumed", "Your membership will renew again."
    if tier == circles.SUBSCRIBER:
        return "refused", "You are already a member."
    if is_guild_member(db, board_id, payer_id):
        return "refused", "You are already a member of this guild."
    if exiled_from(db, board_id, payer_id) or _gone(_account(db, founder)) or blocked_between(db, founder, payer_id):
        return "refused", "You can't join this Circle guild."
    if price <= 0:
        return "refused", "This Circle guild is by invitation only."
    if not _pay(db, payer_id, founder, circle.id, price, "subscribe", now):
        return "refused", f"You need {price} coins to join and you don't have enough."
    db.execute(text("""
        INSERT INTO circle_members (circle_id, user_id, tier, status, started_utc, renews_utc, cancelled, price_coins, created_utc)
        VALUES (:c, :u, 'subscriber', 'active', :t, :r, :no, :p, :t)
        ON CONFLICT (circle_id, user_id) DO UPDATE SET tier = 'subscriber', status = 'active', started_utc = :t,
            renews_utc = :r, cancelled = :no, price_coins = :p"""),
        {"c": circle.id, "u": payer_id, "t": now, "r": circles.next_renewal(now, 0), "no": NO, "p": price})
    join_guild(db, board_id, payer_id, founder, now)
    return "subscribed", f"You're in. It renews in 30 days for {price} coins unless you cancel."


def cancel_guild(db, board_id, payer_id):
    """Stop a guild membership from renewing. They keep access until the date they paid to."""
    return db.execute(text("""
        UPDATE circle_members SET cancelled = :yes WHERE tier = 'subscriber' AND status = 'active' AND user_id = :u
        AND circle_id IN (SELECT id FROM circles WHERE board_id = :b)"""), {"yes": YES, "u": payer_id, "b": board_id}).rowcount > 0


def guild_subscribers(db, board_id, now=None):
    """[(user_id, username, renews_utc, cancelled, price_coins)] of paying members with access now."""
    return db.execute(text("""
        SELECT m.user_id, u.username, m.renews_utc, m.cancelled, m.price_coins FROM circle_members m
        JOIN circles c ON c.id = m.circle_id JOIN users u ON u.id = m.user_id
        WHERE c.board_id = :b AND m.tier = 'subscriber' AND m.status = 'active' AND m.renews_utc > :now
        ORDER BY m.renews_utc, m.id"""), {"b": board_id, "now": _now(now)}).fetchall()


def guild_memberships(db, user_id, now=None):
    """The Circle guilds this account pays to be in: [(board_id, name, renews_utc, cancelled, price_coins)]."""
    return db.execute(text("""
        SELECT c.board_id, b.name, m.renews_utc, m.cancelled, m.price_coins FROM circle_members m
        JOIN circles c ON c.id = m.circle_id JOIN boards b ON b.id = c.board_id
        WHERE m.user_id = :u AND c.board_id IS NOT NULL AND m.tier = 'subscriber' AND m.status = 'active' AND m.renews_utc > :now
        ORDER BY b.name"""), {"u": user_id, "now": _now(now)}).fetchall()


def guild_earnings(db, board_id, since=0):
    """Coins that came into a Circle guild since a date (they went to its founder)."""
    return int(db.execute(text("""
        SELECT COALESCE(SUM(p.coins), 0) FROM circle_payments p JOIN circles c ON c.id = p.circle_id
        WHERE c.board_id = :b AND p.created_utc >= :s"""), {"b": board_id, "s": since}).scalar() or 0)
