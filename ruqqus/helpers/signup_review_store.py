"""The signup guard's records (`signup_reviews`, `users.quarantined_utc`; migration 2026-12-04_signup_guard.sql): one
row per account made, the admins' queue of held accounts at /admin/signups, clusters (accounts sharing a network or a
name shape) and the decisions. Plain `text()` SQL, tested on SQLite (tests/test_signup_review_store.py). The caller
commits.

Decisions: '' while held; `approved` (an admin), `verified` (the account verified an email that isn't a throwaway),
`banned` (an admin); `allowed` for a sign-up that was never held. Approving or verifying clears `quarantined_utc`;
banning leaves it set, so a reinstated account is still held until someone approves it.
"""
from sqlalchemy import bindparam, text

CLUSTER_DAYS = 7
KEEP_DAYS = 90
DECISIONS = ("approved", "verified", "banned")
PER_PAGE = 50


def record(db, user_id, now, verdict, network, shape, held):
    db.execute(text(
        "INSERT INTO signup_reviews (user_id, created_utc, score, reasons, network, name_shape, decision) "
        "VALUES (:u, :t, :s, :r, :n, :shape, :d) ON CONFLICT (user_id) DO NOTHING"),
        {"u": user_id, "t": int(now), "s": max(0, min(32767, int(verdict.score))), "r": ",".join(verdict.reasons)[:300],
         "n": (network or "")[:16], "shape": (shape or "")[:40], "d": "" if held else "allowed"})


_WAITING = (
    "FROM signup_reviews s JOIN users u ON u.id = s.user_id "
    "WHERE s.decision = '' AND u.quarantined_utc > 0 AND u.is_deleted = false")


def waiting(db, page=1):
    rows = db.execute(text(
        "SELECT s.user_id, u.username, u.created_utc, s.score, s.reasons, s.network, s.name_shape, u.is_banned "
        + _WAITING + " ORDER BY s.created_utc DESC, s.user_id DESC LIMIT :n OFFSET :o"),
        {"n": PER_PAGE + 1, "o": PER_PAGE * (max(1, page) - 1)}).mappings().all()
    return [dict(row) for row in rows[:PER_PAGE]], len(rows) > PER_PAGE


def waiting_count(db):
    return db.execute(text("SELECT count(*) " + _WAITING)).scalar() or 0


def recent(db, since, limit=100):
    """Every account made since `since`, held or not, newest first: what the guard thought of each."""
    rows = db.execute(text(
        "SELECT s.user_id, u.username, s.created_utc, s.score, s.reasons, s.network, s.name_shape, s.decision, "
        "u.is_banned FROM signup_reviews s JOIN users u ON u.id = s.user_id "
        "WHERE s.created_utc >= :since ORDER BY s.created_utc DESC, s.user_id DESC LIMIT :n"),
        {"since": int(since), "n": limit}).mappings().all()
    return [dict(row) for row in rows]


def cluster_sizes(db, column, values, since):
    """value -> how many accounts made since `since` share it (`column` is "network" or "name_shape")."""
    if column not in ("network", "name_shape"):
        raise ValueError(column)
    values = sorted({v for v in values if v})
    if not values:
        return {}
    statement = text(
        f"SELECT {column} AS v, count(*) AS n FROM signup_reviews "
        f"WHERE {column} IN :values AND created_utc >= :since GROUP BY {column}"
    ).bindparams(bindparam("values", expanding=True))
    return {row.v: row.n for row in db.execute(statement, {"values": values, "since": int(since)})}


def cluster_waiting(db, column, value, since):
    """The held accounts made since `since` that share `value`: what a bulk action on a cluster acts on (accounts
    that were allowed are never swept up with it)."""
    if column not in ("network", "name_shape") or not value:
        return []
    return [row[0] for row in db.execute(text(
        f"SELECT s.user_id {_WAITING} AND s.{column} = :v AND s.created_utc >= :since ORDER BY s.user_id"),
        {"v": value, "since": int(since)})]


def held_among(db, user_ids):
    ids = sorted({int(i) for i in user_ids})
    if not ids:
        return []
    statement = text("SELECT s.user_id " + _WAITING + " AND s.user_id IN :ids ORDER BY s.user_id").bindparams(
        bindparam("ids", expanding=True))
    return [row[0] for row in db.execute(statement, {"ids": ids})]


def decide(db, user_ids, decision, admin_id, now):
    """Close the held accounts among `user_ids` with `decision`; returns the ids that were held."""
    if decision not in DECISIONS:
        raise ValueError(decision)
    ids = held_among(db, user_ids)
    if not ids:
        return []
    db.execute(text(
        "UPDATE signup_reviews SET decision = :d, decided_utc = :t, decided_by = :a "
        "WHERE decision = '' AND user_id IN :ids").bindparams(bindparam("ids", expanding=True)),
        {"d": decision, "t": int(now), "a": int(admin_id or 0), "ids": ids})
    if decision != "banned":
        db.execute(text("UPDATE users SET quarantined_utc = 0 WHERE id IN :ids").bindparams(
            bindparam("ids", expanding=True)), {"ids": ids})
    return ids


def release(db, user_id, decision, now, admin_id=0):
    """Let one account go (approved or verified), whether or not it has a review row (an account held by an admin by
    hand, or from before the table, has none)."""
    if decision not in ("approved", "verified"):
        raise ValueError(decision)
    db.execute(text(
        "UPDATE signup_reviews SET decision = :d, decided_utc = :t, decided_by = :a "
        "WHERE user_id = :u AND decision = ''"), {"d": decision, "t": int(now), "a": int(admin_id or 0), "u": user_id})
    return db.execute(text("UPDATE users SET quarantined_utc = 0 WHERE id = :u AND quarantined_utc > 0"),
                      {"u": user_id}).rowcount > 0


def forget(db, user_id):
    db.execute(text("DELETE FROM signup_reviews WHERE user_id = :u"), {"u": user_id})


def prune(db, now):
    """Rows decided (or allowed) more than KEEP_DAYS ago. A held account's row stays while it is held."""
    cutoff = int(now) - KEEP_DAYS * 86400
    return db.execute(text(
        "DELETE FROM signup_reviews WHERE decision <> '' AND "
        "CASE WHEN decided_utc > 0 THEN decided_utc ELSE created_utc END < :c"), {"c": cutoff}).rowcount
