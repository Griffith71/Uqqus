"""The signup guard's records (ruqqus/helpers/signup_review_store.py) on SQLite, with the table from the migration:
recording, the queue of held accounts, clusters, decisions, release, retention."""
from pathlib import Path

from sqlalchemy import create_engine, text
from sqlalchemy.orm import Session

from ruqqus.helpers import signup_review_store as store
from ruqqus.helpers.signup_risk import Verdict

NOW = 1_800_000_000
MIGRATION = Path(__file__).resolve().parents[1] / "scripts/migrations/2026-12-04_signup_guard.sql"


def make():
    db = Session(create_engine("sqlite://"))
    db.execute(text("CREATE TABLE users (id INTEGER PRIMARY KEY, username TEXT, created_utc INTEGER DEFAULT 0, "
                    "is_banned INTEGER DEFAULT 0, is_deleted BOOLEAN DEFAULT 0)"))
    for statement in MIGRATION.read_text().split(";"):
        lines = [line for line in statement.splitlines() if line.strip() and not line.strip().startswith("--")]
        if not lines or lines[0].startswith("ALTER TABLE users"):
            continue
        if "CREATE INDEX" in lines[0] and "users" in lines[0]:
            continue
        db.execute(text("\n".join(lines).replace("SERIAL PRIMARY KEY", "INTEGER PRIMARY KEY")))
    db.execute(text("ALTER TABLE users ADD COLUMN quarantined_utc INTEGER DEFAULT 0 NOT NULL"))
    db.execute(text("ALTER TABLE users ADD COLUMN quarantine_kind INTEGER DEFAULT 0 NOT NULL"))
    return db


def account(db, uid, held=True, network="net1", shape="a9{5}", at=NOW, score=60, reasons=("tor", "fast_form")):
    db.execute(text("INSERT INTO users (id, username, created_utc, quarantined_utc) VALUES (:i, :n, :t, :q)"),
               {"i": uid, "n": f"user{uid}", "t": at, "q": at if held else 0})
    store.record(db, uid, at, Verdict("quarantine" if held else "allow", score, reasons), network, shape, held)


def test_a_held_account_waits_with_its_reasons_and_an_allowed_one_does_not():
    db = make()
    account(db, 1)
    account(db, 2, held=False, score=0, reasons=())
    rows, more = store.waiting(db)
    assert [r["user_id"] for r in rows] == [1] and not more
    assert rows[0]["reasons"] == "tor,fast_form" and rows[0]["username"] == "user1"
    assert store.waiting_count(db) == 1
    assert {r["user_id"]: r["decision"] for r in store.recent(db, NOW - 10)} == {1: "", 2: "allowed"}


def test_recording_twice_keeps_the_first_row():
    db = make()
    account(db, 1)
    store.record(db, 1, NOW + 5, Verdict("allow", 0, ()), "x", "a", False)
    assert store.recent(db, 0)[0]["decision"] == ""


def test_the_queue_pages_newest_first():
    db = make()
    for uid in range(1, store.PER_PAGE + 3):
        account(db, uid, at=NOW + uid)
    first, more = store.waiting(db, 1)
    second, more2 = store.waiting(db, 2)
    assert more and not more2 and len(second) == 2
    assert first[0]["user_id"] == store.PER_PAGE + 2 and second[-1]["user_id"] == 1


def test_a_deleted_account_leaves_the_queue():
    db = make()
    account(db, 1)
    db.execute(text("UPDATE users SET is_deleted = 1 WHERE id = 1"))
    assert store.waiting_count(db) == 0


def test_clusters_count_every_account_but_sweep_only_held_ones():
    db = make()
    account(db, 1, network="a")
    account(db, 2, network="a")
    account(db, 3, network="a", held=False)
    account(db, 4, network="a", at=NOW - 30 * 86400)
    account(db, 5, network="b")
    since = NOW - store.CLUSTER_DAYS * 86400
    assert store.cluster_sizes(db, "network", ["a", "b", ""], since) == {"a": 3, "b": 1}
    assert store.cluster_waiting(db, "network", "a", since) == [1, 2]
    assert store.cluster_sizes(db, "name_shape", ["a9{5}"], since) == {"a9{5}": 4}
    assert store.cluster_waiting(db, "network", "", since) == []


def test_only_known_columns_are_clustered():
    db = make()
    for column in ("username", "network; DROP TABLE users"):
        try:
            store.cluster_sizes(db, column, ["x"], 0)
        except ValueError:
            continue
        raise AssertionError(column)


def test_approving_releases_and_banning_keeps_the_hold():
    db = make()
    for uid in (1, 2, 3):
        account(db, uid)
    assert store.decide(db, [1, 2, 99], "approved", 7, NOW + 60) == [1, 2]
    assert store.decide(db, [1], "banned", 7, NOW + 61) == [], "already decided"
    assert store.decide(db, [3], "banned", 7, NOW + 62) == [3]
    held = dict(db.execute(text("SELECT id, quarantined_utc FROM users")).all())
    assert held[1] == 0 and held[2] == 0 and held[3] == NOW
    decided = {r["user_id"]: r["decision"] for r in store.recent(db, 0)}
    assert decided == {1: "approved", 2: "approved", 3: "banned"}
    assert store.waiting_count(db) == 0


def test_release_works_without_a_review_row():
    db = make()
    db.execute(text("INSERT INTO users (id, username, quarantined_utc) VALUES (9, 'old', 5)"))
    assert store.release(db, 9, "verified", NOW)
    assert not store.release(db, 9, "verified", NOW), "not held any more"
    account(db, 1)
    assert store.release(db, 1, "verified", NOW)
    assert store.recent(db, 0)[0]["decision"] == "verified"


def test_retention_keeps_held_rows_and_drops_old_decided_ones():
    db = make()
    old = NOW - (store.KEEP_DAYS + 5) * 86400
    account(db, 1, at=old)                                  # still held: kept
    account(db, 2, at=old, held=False)                      # allowed long ago: goes
    account(db, 3, at=old)
    store.decide(db, [3], "approved", 1, NOW - 86400)       # decided yesterday: kept
    assert store.prune(db, NOW) == 1
    assert sorted(r[0] for r in db.execute(text("SELECT user_id FROM signup_reviews"))) == [1, 3]


def test_forget_removes_the_row():
    db = make()
    account(db, 1)
    store.forget(db, 1)
    assert store.recent(db, 0) == []


def test_a_hold_an_automatic_check_placed_is_not_this_stores_to_read_or_clear():
    """quarantine_kind 1 (helpers/auto_quarantine.py): out of the queue, out of every cluster, and untouched by approving, verifying or a bulk decision."""
    db = make()
    account(db, 1)
    account(db, 2)
    account(db, 3, network="net2")
    db.execute(text("UPDATE users SET quarantine_kind = 1 WHERE id = 2"))
    assert sorted(r["user_id"] for r in store.waiting(db)[0]) == [1, 3]
    assert store.waiting_count(db) == 2 and store.held_among(db, [1, 2, 3]) == [1, 3]
    assert store.cluster_waiting(db, "name_shape", "a9{5}", 0) == [1, 3]
    assert store.release(db, 2, "verified", NOW) is False and store.release(db, 2, "approved", NOW, admin_id=9) is False
    assert store.decide(db, [2], "approved", 9, NOW) == []
    assert dict(db.execute(text("SELECT id, quarantined_utc > 0 FROM users")).all()) == {1: 1, 2: 1, 3: 1}
    assert db.execute(text("SELECT decision FROM signup_reviews WHERE user_id = 2")).scalar() == ""      # its review row is not closed as if it had been let go
    assert store.decide(db, [1, 2, 3], "approved", 9, NOW) == [1, 3]
    assert dict(db.execute(text("SELECT id, quarantined_utc > 0 FROM users")).all()) == {1: 0, 2: 1, 3: 0}
