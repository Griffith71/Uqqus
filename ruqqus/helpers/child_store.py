"""Parent and child accounts, the database side (rules: helpers/child_accounts.py). Plain `text()` SQL on Postgres and SQLite (tested on SQLite, tables as in
schema.sql); no ORM model, so no star import can replace it. The tables are `account_links` (a child and its parent: one row per child), `child_controls`
(what the parent switched, and the child's open requests) and `child_family` (every account a parent made, kept when a link ends: what the limits count and
what makes a family not alts). **No statement is built from text**: a control is named by `clean_control` and picks one of the fixed statements below.
Nothing here commits: the caller does."""
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError

from ruqqus.helpers import child_accounts as rules

LOCK_KEY = 7002            # pg_advisory_xact_lock(LOCK_KEY, parent id): making a child account, and closing the parent's, take one at a time per parent


def lock_parent(db, parent_id, required=True):
    """Take the parent's lock until the transaction ends (Postgres): a child made while its parent is being closed would outlive it. `required=False` is
    for the ban cascade, which may run where there is no such lock (SQLite in tests): a failure is then ignored instead of raised."""
    try:
        with db.begin_nested():                      # a lock taken in a savepoint is held until the whole transaction ends
            db.execute(text("SELECT pg_advisory_xact_lock(:k, :p)"), {"k": LOCK_KEY, "p": parent_id})
    except DBAPIError:
        if required:
            raise


_SET = {name: text(f"UPDATE child_controls SET {name} = :on, updated_utc = :now WHERE child_id = :c") for name in rules.CONTROLS}   # names are the fixed tuple


def link(db, child_id, parent_id, now):
    """Make `parent_id` the parent of `child_id`, with the controls at their defaults. False when the child already has a parent."""
    made = db.execute(text("INSERT INTO account_links (child_id, parent_id, created_utc) VALUES (:c, :p, :t) ON CONFLICT (child_id) DO NOTHING"),
                      {"c": child_id, "p": parent_id, "t": now}).rowcount
    if not made:
        return False
    db.execute(text("INSERT INTO child_controls (child_id, updated_utc) VALUES (:c, :t) ON CONFLICT (child_id) DO NOTHING"), {"c": child_id, "t": now})
    db.execute(text("INSERT INTO child_family (parent_id, child_id, made_utc) VALUES (:p, :c, :t)"), {"p": parent_id, "c": child_id, "t": now})
    return True


def parent_of(db, child_id):
    row = db.execute(text("SELECT parent_id FROM account_links WHERE child_id = :c"), {"c": child_id}).fetchone()
    return row[0] if row else None


def linked(db, a_id, b_id):
    """Are these two accounts a parent and its child (either way round), now?"""
    return bool(db.execute(text("SELECT 1 FROM account_links WHERE (child_id = :a AND parent_id = :b) OR (child_id = :b AND parent_id = :a) LIMIT 1"),
                           {"a": a_id, "b": b_id}).fetchone())


def related(db, a_id, b_id):
    """Are these two accounts one family: a parent and a child it made (even after the link ended), or two children of one parent? A family signs in
    on one browser on purpose, so it is never an alt (`check_for_alts`): one's ban would otherwise reach the rest."""
    return bool(db.execute(text("""
        SELECT 1 FROM child_family x WHERE (x.child_id = :a AND x.parent_id = :b) OR (x.child_id = :b AND x.parent_id = :a)
        OR EXISTS (SELECT 1 FROM child_family y WHERE y.child_id = :a AND y.parent_id = x.parent_id AND x.child_id = :b) LIMIT 1"""),
                           {"a": a_id, "b": b_id}).fetchone()) or linked(db, a_id, b_id)


def _tolerant(db, work, default):
    """Run a lookup that the sign-in and the ban paths make on every call, and answer `default` when the database hasn't reached the migration that made
    the tables yet (a deploy that applies it after the code): the rest of the site must go on working."""
    try:
        with db.begin_nested():
            return work()
    except DBAPIError:
        return default


def related_safely(db, a_id, b_id):
    return _tolerant(db, lambda: related(db, a_id, b_id), False)


def child_ids_safely(db, parent_id):
    return _tolerant(db, lambda: child_ids(db, parent_id), [])


def money_locked(db, user_id):
    """Is this account under the child lock with money not switched on: a standalone locked account, or a sub-account whose parent hasn't allowed money? The
    stored state only, for the jobs that move coins with no request in hand (the renewals); a database without the columns says no."""
    return _tolerant(db, lambda: bool(db.execute(text("""
        SELECT 1 FROM users u WHERE u.id = :u AND (u.age_declared = 2 OR u.age_lock IS NOT NULL)
        AND NOT (u.age_lock = 2 AND EXISTS (SELECT 1 FROM child_controls c WHERE c.child_id = u.id AND c.money))"""), {"u": user_id}).first()), False)


def child_ids(db, parent_id):
    return [r[0] for r in db.execute(text("SELECT child_id FROM account_links WHERE parent_id = :p ORDER BY child_id"), {"p": parent_id})]


def count_children(db, parent_id):
    return db.execute(text("SELECT COUNT(*) FROM account_links WHERE parent_id = :p"), {"p": parent_id}).scalar() or 0


def count_children_safely(db, parent_id):
    return _tolerant(db, lambda: count_children(db, parent_id), 0)


def owing_children(db, parent_id):
    """How many of the parent's live children owe money (a refund or chargeback): an account that owes isn't deleted, and so neither is its parent's."""
    return db.execute(text("SELECT COUNT(*) FROM account_links l JOIN users u ON u.id = l.child_id WHERE l.parent_id = :p AND NOT u.is_deleted "
                           "AND COALESCE(u.negative_balance_cents, 0) > 0"), {"p": parent_id}).scalar() or 0


def made_since(db, parent_id, since):
    """How many accounts this parent has made since then, whatever became of them (the ledger, not the live links)."""
    return db.execute(text("SELECT COUNT(*) FROM child_family WHERE parent_id = :p AND made_utc >= :s"), {"p": parent_id, "s": since}).scalar() or 0


def children_of(db, parent_id, now):
    """[{id, username, created_utc, controls, ask_private_utc, ask_release_utc, suspended, ...}] of the parent's live children, oldest first. `suspended` is
    whether a suspension is in force at `now`, not whether one ever was."""
    rows = db.execute(text("""
        SELECT u.id, u.username, l.created_utc, c.chat, c.spaces, c.money, c.anonymous_guilds, c.posting, c.commenting, c.ask_private_utc, c.ask_release_utc,
               u.is_banned, u.unban_utc, u.private_posting
        FROM account_links l JOIN users u ON u.id = l.child_id LEFT JOIN child_controls c ON c.child_id = l.child_id
        WHERE l.parent_id = :p AND NOT u.is_deleted ORDER BY l.child_id"""), {"p": parent_id})
    out = []
    for r in rows:
        controls = rules.switches({"chat": r[3], "spaces": r[4], "money": r[5], "anonymous_guilds": r[6], "posting": r[7], "commenting": r[8]}
                                  if r[3] is not None else None)
        banned, until = bool(r[11]), r[12] or 0
        out.append({"id": r[0], "username": r[1], "created_utc": r[2], "controls": controls, "ask_private_utc": r[9] or 0,
                    "ask_release_utc": r[10] or 0, "suspended": banned and (until == 0 or until > now), "unban_utc": until, "private_posting": bool(r[13])})
    return out


def child_of(db, parent_id, username, now):
    """One of the parent's children by name (case-insensitive: usernames are), or None. Never someone else's."""
    for child in children_of(db, parent_id, now):
        if child["username"].lower() == (username or "").lower():
            return child
    return None


def controls_of(db, child_id):
    """The child's switches as {name: bool}, or None when it has no row (it isn't a sub-account)."""
    row = db.execute(text("SELECT chat, spaces, money, anonymous_guilds, posting, commenting FROM child_controls WHERE child_id = :c"), {"c": child_id}).fetchone()
    if row is None:
        return None
    return {"chat": bool(row[0]), "spaces": bool(row[1]), "money": bool(row[2]), "anonymous_guilds": bool(row[3]), "posting": bool(row[4]),
            "commenting": bool(row[5])}


def set_control(db, child_id, name, on, now):
    """Switch one control. False for a name that isn't one, or a child with no controls."""
    statement = _SET.get(rules.clean_control(name))
    if statement is None:
        return False
    return db.execute(statement, {"on": bool(on), "now": now, "c": child_id}).rowcount > 0


_ASK = {"private": text("UPDATE child_controls SET ask_private_utc = :t WHERE child_id = :c AND ask_private_utc = 0"),
        "release": text("UPDATE child_controls SET ask_release_utc = :t, ask_release_country = :cc WHERE child_id = :c AND ask_release_utc = 0")}
_TAKE = {"private": text("UPDATE child_controls SET ask_private_utc = 0 WHERE child_id = :c AND ask_private_utc > 0"),
         "release": text("UPDATE child_controls SET ask_release_utc = 0, ask_release_country = NULL WHERE child_id = :c AND ask_release_utc > 0")}
_OPEN = {"private": text("SELECT ask_private_utc FROM child_controls WHERE child_id = :c"),
         "release": text("SELECT ask_release_utc FROM child_controls WHERE child_id = :c")}


def ask(db, child_id, kind, now, country=None):
    """The child asks their parent (`private`: to turn Private posting off, `release`: to be released as an adult; `country` is where a release was asked from).
    True only for the request that opened it: asking again while one is open changes nothing and says False, so the parent is told once."""
    if kind not in _ASK:
        return False
    return db.execute(_ASK[kind], {"t": now, "c": child_id, "cc": (country or None)}).rowcount > 0


def asked(db, child_id, kind):
    """When the child's open request was made, or 0."""
    row = db.execute(_OPEN[kind], {"c": child_id}).fetchone() if kind in _OPEN else None
    return (row[0] or 0) if row else 0


def asked_country(db, child_id):
    """Where the child asked to be released from (a two-letter country, or None)."""
    row = db.execute(text("SELECT ask_release_country FROM child_controls WHERE child_id = :c"), {"c": child_id}).fetchone()
    return row[0] if row else None


def take_ask(db, child_id, kind):
    """Answer a request: True for the one answer that closed it, False when it was already answered (two answers at once do their work once)."""
    return kind in _TAKE and db.execute(_TAKE[kind], {"c": child_id}).rowcount > 0


def clear_ask(db, child_id, kind):
    return take_ask(db, child_id, kind)


def set_suspension(db, child_id, until_utc, prior_until_utc=0):
    """Remember (0, 0: forget) the suspension the site gave this child because its parent was suspended: `until_utc` is the parent's end, `prior_until_utc` the
    end of a shorter ban the child already had for its own conduct, which comes back when the parent's is lifted. It lives on the ledger, not on the controls:
    the ledger row outlives the link, so a link that ends while the parent is suspended still has its suspension found and lifted."""
    return db.execute(text("UPDATE child_family SET suspended_until_utc = :u, prior_until_utc = :p WHERE child_id = :c"),
                      {"u": until_utc, "p": prior_until_utc, "c": child_id}).rowcount > 0


def suspension(db, child_id):
    """(until, prior) of the suspension the site gave this child with its parent's, or (0, 0)."""
    row = db.execute(text("SELECT suspended_until_utc, prior_until_utc FROM child_family WHERE child_id = :c"), {"c": child_id}).fetchone()
    return ((row[0] or 0), (row[1] or 0)) if row else (0, 0)


def suspended_children(db, parent_id):
    """[(child_id, until, prior)] of the parent's accounts (linked now or not) that carry a suspension the site gave them with the parent's."""
    return [(r[0], r[1] or 0, r[2] or 0) for r in db.execute(
        text("SELECT child_id, suspended_until_utc, prior_until_utc FROM child_family WHERE parent_id = :p AND child_id IS NOT NULL AND suspended_until_utc > 0 "
             "ORDER BY child_id"), {"p": parent_id})]


def suspended_children_safely(db, parent_id):
    return _tolerant(db, lambda: suspended_children(db, parent_id), [])


def release(db, child_id):
    """End the link: the controls and the parent's access go. Returns the parent's id, or None when it had none."""
    parent = parent_of(db, child_id)
    db.execute(text("DELETE FROM child_controls WHERE child_id = :c"), {"c": child_id})
    db.execute(text("DELETE FROM account_links WHERE child_id = :c"), {"c": child_id})
    return parent


def forget_account(db, user_id):
    """Closing an account: its own link and controls (as a child) and the links of any children it still had (as a parent). The ledger keeps counting what a
    parent made: a closing child only loses its id from its row, and a closing parent's rows go."""
    db.execute(text("DELETE FROM child_controls WHERE child_id = :u OR child_id IN (SELECT child_id FROM account_links WHERE parent_id = :u)"), {"u": user_id})
    db.execute(text("DELETE FROM account_links WHERE child_id = :u OR parent_id = :u"), {"u": user_id})
    db.execute(text("UPDATE child_family SET child_id = NULL WHERE child_id = :u"), {"u": user_id})
    db.execute(text("DELETE FROM child_family WHERE parent_id = :u"), {"u": user_id})
