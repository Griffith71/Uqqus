"""Tips' SQL (ruqqus/helpers/tips_store.py) on SQLite: every coin is shown, but only established accounts that are not
the author and share no address with the author lift a post's ranking."""
import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.orm import Session

from ruqqus.helpers import tips_store

NOW = 2_000_000_000
OLD = NOW - 400 * 86400
NEW = NOW - 5 * 86400

TABLES = """
CREATE TABLE users (id INTEGER PRIMARY KEY, created_utc INTEGER, is_banned INTEGER DEFAULT 0, unban_utc INTEGER DEFAULT 0,
    is_deleted BOOLEAN DEFAULT 0, creation_ip TEXT, is_bot BOOLEAN DEFAULT 0, bot_owner_id INTEGER);
CREATE TABLE login_events (id INTEGER PRIMARY KEY, user_id INTEGER, ip TEXT, created_utc INTEGER);
CREATE TABLE award_relationships (id INTEGER PRIMARY KEY, user_id INTEGER, submission_id INTEGER, comment_id INTEGER,
    coins INTEGER DEFAULT 1 NOT NULL, created_utc INTEGER DEFAULT 0 NOT NULL);
CREATE TABLE submissions (id INTEGER PRIMARY KEY, author_id INTEGER, tip_coins INTEGER DEFAULT 0 NOT NULL,
    tip_boost REAL DEFAULT 0 NOT NULL);
"""

AUTHOR, FAN, FAN2, NEWBIE, BANNED, DELETED, ALT, HOUSEMATE = 1, 2, 3, 4, 5, 6, 7, 8
POST = 100


@pytest.fixture
def db():
    session = Session(create_engine("sqlite://"))
    for statement in TABLES.split(";"):
        if statement.strip():
            session.execute(text(statement))
    users = [(AUTHOR, OLD, 0, "10.0.0.1"), (FAN, OLD, 0, "10.0.0.2"), (FAN2, OLD, 0, "10.0.0.3"),
             (NEWBIE, NEW, 0, "10.0.0.4"), (BANNED, OLD, 9, "10.0.0.5"), (DELETED, OLD, 0, "10.0.0.6"),
             (ALT, OLD, 0, "10.0.0.1"), (HOUSEMATE, OLD, 0, "10.0.0.8")]
    for uid, created, banned, ip in users:
        session.execute(text("INSERT INTO users (id, created_utc, is_banned, is_deleted, creation_ip) VALUES (:i, :c, :b, :d, :ip)"),
                        {"i": uid, "c": created, "b": banned, "d": uid == DELETED, "ip": ip})
    # the housemate logged in from the author's address lately
    session.execute(text("INSERT INTO login_events (user_id, ip, created_utc) VALUES (:u, '10.0.0.1', :t)"),
                    {"u": HOUSEMATE, "t": NOW - 86400})
    session.execute(text("INSERT INTO submissions (id, author_id) VALUES (:p, :a)"), {"p": POST, "a": AUTHOR})
    session.commit()
    yield session
    session.close()


def tip(db, who, coins):
    tips_store.record(db, who, coins, post_id=POST, now=NOW)
    return tips_store.recompute_post(db, POST, AUTHOR, now=NOW)


def stored(db):
    return tuple(db.execute(text("SELECT tip_coins, tip_boost FROM submissions WHERE id = :p"), {"p": POST}).fetchone())


def test_one_persons_tips_are_added_up_then_given_the_doubling_boost(db):
    assert tip(db, FAN, 1) == (1, 10.0)
    assert tip(db, FAN, 1) == (2, 12.0)
    assert tip(db, FAN, 2) == (4, 14.0)
    assert tip(db, FAN2, 1) == (5, 24.0)
    assert stored(db) == (5, 24.0)


def test_every_coin_is_shown_but_these_tippers_lift_nothing(db):
    for who in (AUTHOR, NEWBIE, BANNED, DELETED, ALT, HOUSEMATE):
        tip(db, who, 3)
    assert stored(db) == (18, 0.0)
    assert tip(db, FAN, 1) == (19, 10.0)


def test_a_ban_that_is_over_counts_again(db):
    db.execute(text("UPDATE users SET unban_utc = :t WHERE id = :u"), {"t": NOW - 60, "u": BANNED})
    assert tip(db, BANNED, 1) == (1, 10.0)


def test_an_old_login_from_the_same_address_no_longer_ties_them(db):
    db.execute(text("UPDATE login_events SET created_utc = :t WHERE user_id = :u"), {"t": NOW - 200 * 86400, "u": HOUSEMATE})
    assert tip(db, HOUSEMATE, 1) == (1, 10.0)


def test_tips_on_comments_are_kept_but_never_counted_for_a_post(db):
    tips_store.record(db, FAN, 5, comment_id=55, now=NOW)
    assert tips_store.recompute_post(db, POST, AUTHOR, now=NOW) == (0, 0.0)
    assert db.execute(text("SELECT coins, created_utc FROM award_relationships WHERE comment_id = 55")).fetchone() == (5, NOW)


# --- bots (the owner, 2026-10-20: coins can lift a bot's post) ---------------------------------------------------

BOT, SITE_BOT = 20, 21


def bot_post(db, post_id, bot_id, owner_id):
    db.execute(text("INSERT INTO users (id, created_utc, is_bot, bot_owner_id) VALUES (:i, :c, 1, :o)"),
               {"i": bot_id, "c": OLD, "o": owner_id})
    db.execute(text("INSERT INTO submissions (id, author_id) VALUES (:p, :a)"), {"p": post_id, "a": bot_id})


def tip_bot(db, post_id, bot_id, who, coins):
    tips_store.record(db, who, coins, post_id=post_id, now=NOW)
    return tips_store.recompute_post(db, post_id, bot_id, now=NOW)


def test_a_member_bot_s_owner_stands_for_its_author(db):
    bot_post(db, 200, BOT, AUTHOR)
    # the owner's own tips and their second accounts' come back to them: shown, never a lift
    for who in (AUTHOR, ALT, HOUSEMATE):
        tip_bot(db, 200, BOT, who, 2)
    assert tip_bot(db, 200, BOT, FAN, 1) == (7, 10.0)


def test_a_site_bot_s_tips_lift_its_post_from_anyone_in_good_standing(db):
    bot_post(db, 201, SITE_BOT, None)
    assert tip_bot(db, 201, SITE_BOT, AUTHOR, 1) == (1, 10.0)       # nobody gets these coins back
    assert tip_bot(db, 201, SITE_BOT, NEWBIE, 1) == (2, 10.0)       # the age rule still holds
