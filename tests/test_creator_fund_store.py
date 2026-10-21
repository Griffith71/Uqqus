"""The creator fund's SQL (ruqqus/helpers/creator_fund_store.py), run on SQLite: who earns points, the cap one
account can give, the accounts and items that never count, and that a month and a held payout are paid once."""
import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.orm import Session

from ruqqus.helpers import creator_fund as rules
from ruqqus.helpers import creator_fund_store as store

MONTH = "2026-09"
START, END = rules.month_bounds(MONTH)
NOW = END + 11 * 86400                    # 12 October 2026
OLD = START - 400 * 86400
IN_MONTH = START + 5 * 86400

TABLES = """
CREATE TABLE users (id INTEGER PRIMARY KEY, username TEXT, created_utc INTEGER, is_banned INTEGER DEFAULT 0,
    unban_utc INTEGER DEFAULT 0, is_deleted BOOLEAN DEFAULT 0, creator_fund_opt_in_utc INTEGER DEFAULT 0,
    negative_balance_cents INTEGER DEFAULT 0, coin_balance INTEGER DEFAULT 0, is_bot BOOLEAN DEFAULT 0);
CREATE TABLE submissions (id INTEGER PRIMARY KEY, author_id INTEGER, post_public BOOLEAN DEFAULT 1,
    is_banned BOOLEAN DEFAULT 0, deleted_utc INTEGER DEFAULT 0, repost_id INTEGER DEFAULT 0, is_anonymous BOOLEAN DEFAULT 0,
    is_bot BOOLEAN DEFAULT 0);
CREATE TABLE comments (id INTEGER PRIMARY KEY, author_id INTEGER, parent_submission INTEGER, created_utc INTEGER,
    is_banned BOOLEAN DEFAULT 0, deleted_utc INTEGER DEFAULT 0, creation_ip TEXT, app_id INTEGER);
CREATE TABLE votes (id INTEGER PRIMARY KEY, user_id INTEGER, submission_id INTEGER, created_utc INTEGER,
    vote_type INTEGER, creation_ip TEXT, app_id INTEGER);
CREATE TABLE commentvotes (id INTEGER PRIMARY KEY, comment_id INTEGER, vote_type INTEGER, user_id INTEGER,
    created_utc INTEGER, creation_ip TEXT, app_id INTEGER);
CREATE TABLE creator_fund_months (id INTEGER PRIMARY KEY, month CHARACTER(7) NOT NULL UNIQUE, budget_coins INTEGER NOT NULL,
    total_points INTEGER NOT NULL, paid_coins INTEGER NOT NULL, created_utc INTEGER NOT NULL, paid_by_id INTEGER NOT NULL);
CREATE TABLE creator_fund_payouts (id INTEGER PRIMARY KEY, month CHARACTER(7) NOT NULL, user_id INTEGER NOT NULL,
    points INTEGER NOT NULL, coins INTEGER NOT NULL, status VARCHAR(8) NOT NULL CHECK (status IN ('paid', 'held')),
    created_utc INTEGER NOT NULL, paid_utc INTEGER DEFAULT 0 NOT NULL, UNIQUE (month, user_id));
"""

A, B, C, D, NEWBIE = 1, 2, 3, 4, 5                  # members who post
FANS = list(range(10, 22))                          # 12 established accounts
NEW_ACCOUNT, BANNED, DELETED, LIFTED = 30, 31, 32, 33


@pytest.fixture
def db(monkeypatch):
    monkeypatch.setenv("CREATOR_FUND_MIN_POINTS", "10")
    session = Session(create_engine("sqlite://"))
    for statement in TABLES.split(";"):
        if statement.strip():
            session.execute(text(statement))

    def user(uid, created=OLD, opted=True, **over):
        row = {"id": uid, "username": f"user{uid}", "created_utc": created, "creator_fund_opt_in_utc": OLD if opted else 0,
               "is_banned": 0, "unban_utc": 0, "is_deleted": False, "negative_balance_cents": 0, **over}
        session.execute(text("INSERT INTO users (id, username, created_utc, creator_fund_opt_in_utc, is_banned, unban_utc, "
                             "is_deleted, negative_balance_cents) VALUES (:id, :username, :created_utc, "
                             ":creator_fund_opt_in_utc, :is_banned, :unban_utc, :is_deleted, :negative_balance_cents)"), row)

    user(A), user(B), user(C, opted=False), user(D, negative_balance_cents=500), user(NEWBIE, created=END - 5 * 86400)
    for fan in FANS:
        user(fan, opted=False)
    user(NEW_ACCOUNT, created=START + 86400), user(BANNED, is_banned=99), user(DELETED, is_deleted=True)
    user(LIFTED, is_banned=99, unban_utc=START)        # a ban that is over: counts again
    session.commit()
    yield session
    session.close()


_post_ids = iter(range(100, 10000))
_vote_ids = iter(range(1, 100000))


def post(db, author, **over):
    pid = next(_post_ids)
    row = {"id": pid, "author_id": author, "post_public": True, "is_banned": False, "deleted_utc": 0, "repost_id": 0,
           "is_anonymous": False, **over}
    db.execute(text("INSERT INTO submissions (id, author_id, post_public, is_banned, deleted_utc, repost_id, is_anonymous) "
                    "VALUES (:id, :author_id, :post_public, :is_banned, :deleted_utc, :repost_id, :is_anonymous)"), row)
    return pid


def upvote(db, who, pid, when=IN_MONTH, kind=1, app=None, ip=None):
    db.execute(text("INSERT INTO votes (id, user_id, submission_id, created_utc, vote_type, creation_ip, app_id) "
                    "VALUES (:i, :u, :p, :t, :k, :ip, :app)"),
               {"i": next(_vote_ids), "u": who, "p": pid, "t": when, "k": kind, "ip": ip or f"net{who}", "app": app})


def comment(db, who, pid, when=IN_MONTH, **over):
    cid = next(_post_ids)
    row = {"id": cid, "a": who, "p": pid, "t": when, "b": False, "d": 0, "ip": f"net{who}", "app": None, **over}
    db.execute(text("INSERT INTO comments (id, author_id, parent_submission, created_utc, is_banned, deleted_utc, "
                    "creation_ip, app_id) VALUES (:id, :a, :p, :t, :b, :d, :ip, :app)"), row)
    return cid


def upvote_comment(db, who, cid, when=IN_MONTH):
    db.execute(text("INSERT INTO commentvotes (id, comment_id, vote_type, user_id, created_utc, creation_ip) "
                    "VALUES (:i, :c, 1, :u, :t, :ip)"), {"i": next(_vote_ids), "c": cid, "u": who, "t": when, "ip": f"net{who}"})


def scores(db, eligible_only=True):
    return {a: p for a, p, _, _ in store.points(db, MONTH, now=NOW, eligible_only=eligible_only)}


def test_one_point_per_account_and_item(db):
    pid = post(db, A)
    upvote(db, FANS[0], pid)
    comment(db, FANS[0], pid)
    comment(db, FANS[0], pid)                      # upvoting and commenting on one post is one point
    upvote(db, FANS[1], pid)
    assert scores(db) == {A: 2}


def test_comment_upvotes_and_comments_on_posts_count(db):
    pid = post(db, B)
    cid = comment(db, A, pid)                      # A's comment on B's post: a point for B (A is established)
    upvote_comment(db, FANS[0], cid)               # an upvote on A's comment: a point for A
    assert scores(db) == {A: 1, B: 1}


def test_one_account_gives_one_member_at_most_twenty_points(db):
    for _ in range(25):
        upvote(db, FANS[0], post(db, A))
    upvote(db, FANS[1], post(db, A))
    rows = store.points(db, MONTH, now=NOW)
    assert rows == [(A, rules.POINTS_PER_ACCOUNT + 1, 2, 2)]


def test_accounts_that_never_count(db):
    pid = post(db, A)
    for who in (A, NEW_ACCOUNT, BANNED, DELETED):  # the member themself, a new, a banned and a deleted account
        upvote(db, who, pid)
    upvote(db, FANS[0], pid, app=7)                # through an API app
    upvote(db, FANS[1], pid, kind=-1)              # a downvote
    comment(db, FANS[2], pid, app=7)
    assert scores(db) == {}
    upvote(db, LIFTED, pid)                        # a ban that is over
    assert scores(db) == {A: 1}


def test_items_and_times_that_never_count(db):
    for over in ({"post_public": False}, {"is_banned": True}, {"deleted_utc": 5}):
        upvote(db, FANS[0], post(db, A, **over))   # Circle or private-guild, removed, deleted
    upvote(db, FANS[1], post(db, A), when=START - 1)
    upvote(db, FANS[2], post(db, A), when=END)
    pid = post(db, A)
    comment(db, FANS[3], pid, b=True)              # a removed comment
    removed = comment(db, A, post(db, B), b=True)
    upvote_comment(db, FANS[4], removed)
    assert scores(db) == {}


def test_an_automated_post_earns_nothing_but_the_members_own_words_under_it_do(db):
    """A member's own account can post from feeds (helpers/bots.py SELF): what a feed posted isn't their work."""
    automated = post(db, A)
    db.execute(text("UPDATE submissions SET is_bot = 1 WHERE id = :p"), {"p": automated})
    upvote(db, FANS[0], automated)                 # an upvote on it: nothing
    comment(db, FANS[1], automated)                # a comment on it: nothing for A
    own = comment(db, A, automated)                # A's own comment under it ...
    upvote_comment(db, FANS[2], own)               # ... is A's work: a point
    upvote(db, FANS[3], post(db, A))               # and A's own post earns as always
    assert scores(db) == {A: 2}


def test_a_forwarded_copy_earns_for_the_original_author_and_anonymous_posts_for_their_author(db):
    original = post(db, A)
    copy = post(db, A, repost_id=original)         # a forward copy keeps the original author
    upvote(db, FANS[0], copy)
    upvote(db, FANS[1], post(db, A, is_anonymous=True))
    assert scores(db) == {A: 2}


def test_who_may_share_the_budget(db):
    for member in (A, C, D, NEWBIE):               # C has not opted in, D owes money, NEWBIE is too new
        upvote(db, FANS[0], post(db, member))
    assert scores(db) == {A: 1}
    assert scores(db, eligible_only=False) == {A: 1, C: 1, D: 1, NEWBIE: 1}
    assert store.points(db, MONTH, now=NOW, member=C, eligible_only=False) == [(C, 1, 1, 1)]


def fill(db):
    """A: 30 points from 12 accounts; B: 12 points."""
    for fan in FANS:
        for _ in range(2 if fan != FANS[0] else 8):
            upvote(db, fan, post(db, A))
    for fan in FANS:
        upvote(db, fan, post(db, B))


def test_preview_splits_the_budget_with_the_cap(db):
    fill(db)
    lines = {r["user_id"]: r for r in store.preview(db, MONTH, 100, now=NOW)}
    assert lines[A]["points"] == 30 and lines[B]["points"] == 12 and lines[A]["engagers"] == 12
    assert lines[A]["coins"] == 25 and lines[B]["coins"] == 25          # 71 and 28 before the 25% cap
    assert lines[A]["username"] == "user1"


def test_a_month_is_paid_once(db):
    fill(db)
    db.execute(text("UPDATE users SET coin_balance = 3 WHERE id = :a"), {"a": A})
    result = store.pay(db, MONTH, 1000, held_ids=[B], admin_id=99, now=NOW)
    assert sorted(result) == [(A, 250, "paid"), (B, 250, "held")]
    balance = dict(db.execute(text("SELECT id, coin_balance FROM users WHERE id IN (1, 2)")).fetchall())
    assert balance == {A: 253, B: 0}
    with pytest.raises(store.AlreadyPaid):
        store.pay(db, MONTH, 1000, held_ids=[], admin_id=99, now=NOW)
    assert db.execute(text("SELECT coin_balance FROM users WHERE id = 1")).scalar() == 253
    assert store.is_paid(db, MONTH) and store.paid_months(db)[0][:3] == (MONTH, 1000, 250)


def test_a_held_payout_is_released_once(db):
    fill(db)
    store.pay(db, MONTH, 1000, held_ids=[B], admin_id=99, now=NOW)
    held = [row for row in store.payouts_of_month(db, MONTH) if row[5] == "held"][0]
    assert store.release(db, held[0], now=NOW) == (B, 250, MONTH)
    assert store.release(db, held[0], now=NOW) is None
    assert db.execute(text("SELECT coin_balance FROM users WHERE id = 2")).scalar() == 250
    assert store.paid_months(db)[0][2] == 500
    assert store.payouts_of_member(db, B) == [(MONTH, 12, 250, "paid")]


def test_a_month_that_is_not_over_cannot_be_paid(db):
    fill(db)
    with pytest.raises(ValueError):
        store.pay(db, MONTH, 1000, held_ids=[], admin_id=99, now=END - 1)
    assert not store.is_paid(db, MONTH)


def test_opting_in_and_out(db):
    store.set_opt_in(db, C, True, now=NOW)
    assert db.execute(text("SELECT creator_fund_opt_in_utc FROM users WHERE id = 3")).scalar() == NOW
    store.set_opt_in(db, C, False)
    assert db.execute(text("SELECT creator_fund_opt_in_utc FROM users WHERE id = 3")).scalar() == 0
