"""Circles, the database side (ruqqus/helpers/circle_store.py): the real SQL on in-memory SQLite (tables as in
schema.sql). The promises under test: coins move exactly and never below zero, a subscriber keeps what they paid for,
a block ends a Circle both ways, and a friend is never taken for a subscriber."""
import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.orm import Session

from ruqqus.helpers import circle_store as store
from ruqqus.helpers import circles as c

NOW = 2_000_000_000
DAY = 86400
OWNER, FAN, POOR, FRIEND, BANNED, DELETED, STRANGER, OTHER = 1, 2, 3, 4, 5, 6, 7, 8


@pytest.fixture
def db():
    engine = create_engine("sqlite://")
    with engine.begin() as conn:
        conn.execute(text("CREATE TABLE users (id integer PRIMARY KEY, username text, coin_balance integer DEFAULT 0, "
                          "is_deleted boolean DEFAULT 0, is_banned integer DEFAULT 0, unban_utc integer DEFAULT 0)"))
        conn.execute(text("CREATE TABLE userblocks (id integer PRIMARY KEY AUTOINCREMENT, user_id integer, target_id integer)"))
        conn.execute(text("CREATE TABLE circles (id integer PRIMARY KEY AUTOINCREMENT, user_id integer, board_id integer, "
                          "price_coins integer NOT NULL DEFAULT 0, created_utc integer NOT NULL DEFAULT 0)"))
        conn.execute(text("CREATE UNIQUE INDEX circles_user_key ON circles (user_id) WHERE user_id IS NOT NULL"))
        conn.execute(text("CREATE TABLE circle_members (id integer PRIMARY KEY AUTOINCREMENT, circle_id integer NOT NULL, "
                          "user_id integer NOT NULL, tier text NOT NULL, status text NOT NULL DEFAULT 'active', "
                          "started_utc integer NOT NULL DEFAULT 0, renews_utc integer NOT NULL DEFAULT 0, "
                          "cancelled boolean NOT NULL DEFAULT 0, price_coins integer NOT NULL DEFAULT 0, "
                          "created_utc integer NOT NULL DEFAULT 0, UNIQUE (circle_id, user_id))"))
        conn.execute(text("CREATE TABLE boards (id integer PRIMARY KEY, name text, creator_id integer, is_circle boolean DEFAULT 0, is_banned boolean DEFAULT 0)"))
        conn.execute(text("CREATE TABLE circle_payments (id integer PRIMARY KEY AUTOINCREMENT, circle_id integer NOT NULL, "
                          "payer_id integer NOT NULL, owner_id integer NOT NULL, coins integer NOT NULL, kind text NOT NULL, "
                          "created_utc integer NOT NULL DEFAULT 0)"))
        for uid, name, coins in ((OWNER, "owner", 0), (FAN, "fan", 100), (POOR, "poor", 5), (FRIEND, "friend", 0), (BANNED, "banned", 50),
                                 (DELETED, "deleted", 50), (STRANGER, "stranger", 50), (OTHER, "other", 50)):
            conn.execute(text("INSERT INTO users (id, username, coin_balance) VALUES (:i, :n, :c)"), {"i": uid, "n": name, "c": coins})
        conn.execute(text("UPDATE users SET is_banned = 1 WHERE id = :i"), {"i": BANNED})
        conn.execute(text("UPDATE users SET is_deleted = 1 WHERE id = :i"), {"i": DELETED})
    session = Session(engine)
    yield session
    session.close()


def coins(db, uid):
    return db.execute(text("SELECT coin_balance FROM users WHERE id = :i"), {"i": uid}).scalar()


def opened(db, price=10):
    store.set_price(db, OWNER, price, NOW)
    return store.circle_of(db, OWNER).id


def block(db, a, b):
    db.execute(text("INSERT INTO userblocks (user_id, target_id) VALUES (:a, :b)"), {"a": a, "b": b})


def payments(db):
    return db.execute(text("SELECT payer_id, owner_id, coins, kind FROM circle_payments ORDER BY id")).fetchall()


# --- the Circle ---------------------------------------------------------------------------------------

def test_a_circle_is_made_once_and_its_price_changes_in_place(db):
    first = store.ensure(db, OWNER, NOW)
    assert store.ensure(db, OWNER, NOW) == first
    store.set_price(db, OWNER, 25, NOW)
    assert tuple(store.circle_of(db, OWNER)) == (first, 25)
    assert db.execute(text("SELECT COUNT(*) FROM circles")).scalar() == 1


# --- close friends ------------------------------------------------------------------------------------

def test_a_close_friend_is_added_and_listed_newest_first(db):
    assert store.add_friend(db, OWNER, FRIEND, NOW) is None
    assert store.add_friend(db, OWNER, STRANGER, NOW + 5) is None
    assert [f.username for f in store.friends(db, OWNER)] == ["stranger", "friend"]
    assert store.tier_for(db, OWNER, FRIEND, NOW) == c.FRIEND
    assert store.tier_for(db, OWNER, OTHER, NOW) is None


def test_adding_the_same_friend_twice_or_yourself_or_a_gone_account_is_refused(db):
    assert store.add_friend(db, OWNER, FRIEND, NOW) is None
    assert "already" in store.add_friend(db, OWNER, FRIEND, NOW)
    assert "own Circle" in store.add_friend(db, OWNER, OWNER, NOW)
    assert "isn't available" in store.add_friend(db, OWNER, BANNED, NOW)
    assert "isn't available" in store.add_friend(db, OWNER, DELETED, NOW)
    assert "isn't available" in store.add_friend(db, OWNER, 999, NOW)
    assert len(store.friends(db, OWNER)) == 1


def test_a_block_either_way_stops_a_friend_being_added(db):
    block(db, OWNER, OTHER)
    block(db, STRANGER, OWNER)
    assert store.add_friend(db, OWNER, OTHER, NOW) == "You can't add that account."
    assert store.add_friend(db, OWNER, STRANGER, NOW) == "You can't add that account."


def test_a_temporary_ban_is_not_a_gone_account(db):
    db.execute(text("UPDATE users SET is_banned = 1, unban_utc = :u WHERE id = :i"), {"u": NOW + DAY, "i": STRANGER})
    assert store.add_friend(db, OWNER, STRANGER, NOW) is None


def test_there_is_a_limit_on_close_friends(db, monkeypatch):
    monkeypatch.setattr(c, "FRIEND_LIMIT", 2)
    assert store.add_friend(db, OWNER, FRIEND, NOW) is None
    assert store.add_friend(db, OWNER, STRANGER, NOW) is None
    assert "2 Close Friends at most" in store.add_friend(db, OWNER, OTHER, NOW)


def test_removing_a_friend_removes_only_friends_never_a_paying_subscriber(db):
    opened(db)
    store.add_friend(db, OWNER, FRIEND, NOW)
    store.subscribe(db, OWNER, FAN, NOW)
    assert store.remove_friend(db, OWNER, FRIEND) is True
    assert store.remove_friend(db, OWNER, FRIEND) is False
    assert store.remove_friend(db, OWNER, FAN) is False
    assert store.tier_for(db, OWNER, FAN, NOW) == c.SUBSCRIBER
    assert store.tier_for(db, OWNER, FRIEND, NOW) is None


def test_adding_a_subscriber_as_a_friend_keeps_their_access_and_stops_the_payments(db):
    opened(db)
    store.subscribe(db, OWNER, FAN, NOW)
    assert store.add_friend(db, OWNER, FAN, NOW + DAY) is None
    row = store.member_row(db, OWNER, FAN)
    assert (row.tier, row.renews_utc, row.price_coins, bool(row.cancelled)) == ("friend", 0, 0, False)
    assert store.tier_for(db, OWNER, FAN, NOW + 400 * DAY) == c.FRIEND
    assert store.renew_due(db, NOW + 90 * DAY) == []


# --- subscribing --------------------------------------------------------------------------------------

def test_subscribing_moves_exactly_the_price_and_records_it(db):
    opened(db, 10)
    outcome, message = store.subscribe(db, OWNER, FAN, NOW)
    assert outcome == "subscribed" and "10 coins" in message
    assert (coins(db, FAN), coins(db, OWNER)) == (90, 10)
    row = store.member_row(db, OWNER, FAN)
    assert (row.tier, row.status, row.renews_utc, row.price_coins, bool(row.cancelled)) == ("subscriber", "active", NOW + c.PERIOD, 10, False)
    assert payments(db) == [(FAN, OWNER, 10, "subscribe")]
    assert store.tier_for(db, OWNER, FAN, NOW) == c.SUBSCRIBER


def test_coins_are_conserved_across_the_whole_site(db):
    opened(db, 17)
    total = db.execute(text("SELECT SUM(coin_balance) FROM users")).scalar()
    store.subscribe(db, OWNER, FAN, NOW)
    assert db.execute(text("SELECT SUM(coin_balance) FROM users")).scalar() == total


def test_someone_who_cannot_pay_is_refused_and_nothing_changes(db):
    opened(db, 10)
    outcome, message = store.subscribe(db, OWNER, POOR, NOW)
    assert outcome == "refused" and "10 coins" in message and "don't have enough" in message
    assert (coins(db, POOR), coins(db, OWNER)) == (5, 0)
    assert store.member_row(db, OWNER, POOR) is None and payments(db) == []


def test_a_balance_exactly_the_price_is_enough_and_ends_at_zero_never_below(db):
    opened(db, 5)
    assert store.subscribe(db, OWNER, POOR, NOW)[0] == "subscribed"
    assert coins(db, POOR) == 0
    assert store.subscribe(db, OWNER, POOR, NOW + 2 * c.PERIOD)[0] == "refused"        # ended below: a new payment is needed
    assert coins(db, POOR) == 0


@pytest.mark.parametrize("who, word", [(OWNER, "own Circle"), (BANNED, None), (DELETED, None)])
def test_who_cannot_subscribe(db, who, word):
    opened(db)
    if who == OWNER:
        outcome, message = store.subscribe(db, OWNER, OWNER, NOW)
        assert outcome == "refused" and word in message
        return
    # a banned or deleted OWNER's Circle is closed to everyone
    db.execute(text("UPDATE circles SET user_id = :u"), {"u": who})
    outcome, message = store.subscribe(db, who, FAN, NOW)
    assert outcome == "refused" and "isn't available" in message and coins(db, FAN) == 100


def test_a_circle_with_no_price_or_none_at_all_takes_no_subscribers(db):
    assert store.subscribe(db, OWNER, FAN, NOW)[0] == "refused"          # no Circle yet
    opened(db, 0)
    outcome, message = store.subscribe(db, OWNER, FAN, NOW)
    assert outcome == "refused" and "isn't taking subscribers" in message and coins(db, FAN) == 100


def test_a_block_either_way_refuses_a_subscription_and_charges_nothing(db):
    opened(db)
    block(db, FAN, OWNER)
    assert store.subscribe(db, OWNER, FAN, NOW) == ("refused", "You can't subscribe to this Circle.")
    assert coins(db, FAN) == 100
    block(db, OWNER, OTHER)
    assert store.subscribe(db, OWNER, OTHER, NOW)[0] == "refused" and coins(db, OTHER) == 50


def test_a_close_friend_is_told_they_already_see_everything_and_is_not_charged(db):
    opened(db)
    store.add_friend(db, OWNER, FRIEND, NOW)
    db.execute(text("UPDATE users SET coin_balance = 50 WHERE id = :i"), {"i": FRIEND})
    outcome, message = store.subscribe(db, OWNER, FRIEND, NOW)
    assert outcome == "refused" and "Close Friends" in message and coins(db, FRIEND) == 50


def test_subscribing_twice_charges_once(db):
    opened(db)
    store.subscribe(db, OWNER, FAN, NOW)
    assert store.subscribe(db, OWNER, FAN, NOW + DAY) == ("refused", "You are already subscribed.")
    assert coins(db, FAN) == 90 and len(payments(db)) == 1


# --- cancelling and the paid-to date ------------------------------------------------------------------

def test_a_cancelled_subscriber_keeps_access_until_the_date_and_is_not_charged_again(db):
    opened(db)
    store.subscribe(db, OWNER, FAN, NOW)
    assert store.cancel(db, OWNER, FAN) is True
    assert store.tier_for(db, OWNER, FAN, NOW + 29 * DAY) == c.SUBSCRIBER
    assert store.tier_for(db, OWNER, FAN, NOW + c.PERIOD) is None
    assert store.renew_due(db, NOW + c.PERIOD) == [("ended", FAN, OWNER, 10, None)]
    assert coins(db, FAN) == 90 and len(payments(db)) == 1


def test_cancelling_when_there_is_nothing_to_cancel_does_nothing(db):
    opened(db)
    assert store.cancel(db, OWNER, FAN) is False
    store.add_friend(db, OWNER, FRIEND, NOW)
    assert store.cancel(db, OWNER, FRIEND) is False


def test_subscribing_again_after_cancelling_resumes_without_a_charge(db):
    opened(db)
    store.subscribe(db, OWNER, FAN, NOW)
    store.cancel(db, OWNER, FAN)
    assert store.subscribe(db, OWNER, FAN, NOW + 5 * DAY)[0] == "resumed"
    assert coins(db, FAN) == 90 and len(payments(db)) == 1
    assert not store.member_row(db, OWNER, FAN).cancelled
    assert store.renew_due(db, NOW + c.PERIOD)[0][0] == "renewed"


def test_access_ends_at_the_date_even_if_the_renewal_job_never_ran(db):
    opened(db)
    store.subscribe(db, OWNER, FAN, NOW)
    assert store.tier_for(db, OWNER, FAN, NOW + c.PERIOD - 1) == c.SUBSCRIBER
    assert store.tier_for(db, OWNER, FAN, NOW + c.PERIOD) is None
    assert store.member_row(db, OWNER, FAN).status == "active"


def test_subscribing_again_after_the_subscription_ended_charges_again(db):
    opened(db)
    store.subscribe(db, OWNER, FAN, NOW)
    store.cancel(db, OWNER, FAN)
    store.renew_due(db, NOW + c.PERIOD)
    assert store.subscribe(db, OWNER, FAN, NOW + 40 * DAY)[0] == "subscribed"
    assert coins(db, FAN) == 80
    assert store.member_row(db, OWNER, FAN).renews_utc == NOW + 40 * DAY + c.PERIOD


# --- renewals -----------------------------------------------------------------------------------------

def test_a_due_subscriber_is_renewed_from_their_coins(db):
    opened(db, 10)
    store.subscribe(db, OWNER, FAN, NOW)
    assert store.renew_due(db, NOW + c.PERIOD - 1) == []
    assert store.renew_due(db, NOW + c.PERIOD) == [("renewed", FAN, OWNER, 10, None)]
    assert (coins(db, FAN), coins(db, OWNER)) == (80, 20)
    assert store.member_row(db, OWNER, FAN).renews_utc == NOW + 2 * c.PERIOD
    assert [p[3] for p in payments(db)] == ["subscribe", "renew"]


def test_a_subscriber_renews_at_the_price_they_signed_up_for(db):
    opened(db, 10)
    store.subscribe(db, OWNER, FAN, NOW)
    store.set_price(db, OWNER, 90, NOW + DAY)                         # the owner raises the price
    store.renew_due(db, NOW + c.PERIOD)
    assert coins(db, FAN) == 80                                       # still 10
    assert store.member_row(db, OWNER, FAN).price_coins == 10


def test_a_subscriber_who_cannot_pay_lapses_and_is_not_charged(db):
    opened(db, 5)
    store.subscribe(db, OWNER, POOR, NOW)                              # 5 -> 0
    assert store.renew_due(db, NOW + c.PERIOD) == [("lapsed", POOR, OWNER, 5, None)]
    assert coins(db, POOR) == 0 and store.tier_for(db, OWNER, POOR, NOW + c.PERIOD) is None
    assert store.member_row(db, OWNER, POOR).status == "ended"


def test_a_block_ends_a_renewal_instead_of_charging(db):
    opened(db)
    store.subscribe(db, OWNER, FAN, NOW)
    block(db, OWNER, FAN)
    assert store.renew_due(db, NOW + c.PERIOD) == [("ended", FAN, OWNER, 10, None)]
    assert coins(db, FAN) == 90


def test_a_late_renewal_runs_from_now(db):
    opened(db)
    store.subscribe(db, OWNER, FAN, NOW)
    late = NOW + 10 * c.PERIOD
    store.renew_due(db, late)
    assert store.member_row(db, OWNER, FAN).renews_utc == late + c.PERIOD


def test_renewals_are_settled_one_by_one_and_friends_are_never_touched(db):
    opened(db, 5)
    store.subscribe(db, OWNER, POOR, NOW)
    store.subscribe(db, OWNER, FAN, NOW)
    store.add_friend(db, OWNER, FRIEND, NOW)
    events = store.renew_due(db, NOW + c.PERIOD)
    assert sorted(e[0] for e in events) == ["lapsed", "renewed"]
    assert store.tier_for(db, OWNER, FRIEND, NOW + 10 * c.PERIOD) == c.FRIEND


def test_the_renewal_job_stops_at_its_limit(db):
    opened(db, 1)
    for uid in (FAN, STRANGER, OTHER):
        store.subscribe(db, OWNER, uid, NOW)
    assert len(store.renew_due(db, NOW + c.PERIOD, limit=2)) == 2
    assert len(store.renew_due(db, NOW + c.PERIOD, limit=2)) == 1


# --- blocks -------------------------------------------------------------------------------------------

def test_a_block_takes_each_account_out_of_the_others_circle_with_no_refund(db):
    opened(db)
    store.add_friend(db, OWNER, FRIEND, NOW)
    store.subscribe(db, OWNER, FAN, NOW)
    # and the other direction: the fan has a Circle of their own with the owner as a friend
    store.add_friend(db, FAN, OWNER, NOW)
    assert store.end_between(db, OWNER, FAN) == 2                      # the owner's subscriber row and the fan's friend row
    assert store.tier_for(db, OWNER, FAN, NOW) is None and store.tier_for(db, FAN, OWNER, NOW) is None
    assert store.tier_for(db, OWNER, FRIEND, NOW) == c.FRIEND          # nobody else is touched
    assert coins(db, FAN) == 90 and coins(db, OWNER) == 10             # the coins stay where they went


# --- the lists ----------------------------------------------------------------------------------------

def test_subscribers_and_memberships_list_only_those_with_access_now(db):
    opened(db)
    store.subscribe(db, OWNER, FAN, NOW)
    store.subscribe(db, OWNER, STRANGER, NOW - 29 * DAY)
    assert [s.username for s in store.subscribers(db, OWNER, NOW)] == ["stranger", "fan"]
    assert [s.username for s in store.subscribers(db, OWNER, NOW + 2 * DAY)] == ["fan"]
    assert [m[:3] for m in store.memberships(db, FAN, NOW)] == [(OWNER, "owner", c.SUBSCRIBER)]
    assert store.memberships(db, FAN, NOW + c.PERIOD) == []


def test_earnings_add_up_what_came_in_since_a_date(db):
    opened(db, 10)
    store.subscribe(db, OWNER, FAN, NOW)
    store.subscribe(db, OWNER, STRANGER, NOW + 5 * DAY)
    assert store.earnings(db, OWNER) == 20
    assert store.earnings(db, OWNER, since=NOW + DAY) == 10
    assert store.earnings(db, FAN) == 0
