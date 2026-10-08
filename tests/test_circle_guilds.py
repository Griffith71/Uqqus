"""Circle guilds, the database side (ruqqus/helpers/circle_store.py): real SQL on in-memory SQLite. The promises under
test: paying for a guild moves exactly the price to its founder and makes the payer a member, a membership ends on its
date or when the coins run out or when the member is exiled or removed, and a guild nobody can pay for stays by invitation."""
import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.orm import Session

from ruqqus.helpers import circle_store as store
from ruqqus.helpers import circles as c

NOW = 2_000_000_000
DAY = 86400
FOUNDER, FAN, POOR, INVITED, EXILED, BLOCKED, STRANGER = 1, 2, 3, 4, 5, 6, 7
GUILD, PLAIN, BANNED_GUILD = 10, 11, 12


@pytest.fixture
def db():
    engine = create_engine("sqlite://")
    with engine.begin() as conn:
        conn.execute(text("CREATE TABLE users (id integer PRIMARY KEY, username text, coin_balance integer DEFAULT 0, "
                          "is_deleted boolean DEFAULT 0, is_banned integer DEFAULT 0, unban_utc integer DEFAULT 0)"))
        conn.execute(text("CREATE TABLE userblocks (id integer PRIMARY KEY AUTOINCREMENT, user_id integer, target_id integer)"))
        conn.execute(text("CREATE TABLE boards (id integer PRIMARY KEY, name text, creator_id integer, is_circle boolean DEFAULT 0, is_banned boolean DEFAULT 0)"))
        conn.execute(text("CREATE TABLE circles (id integer PRIMARY KEY AUTOINCREMENT, user_id integer, board_id integer, "
                          "price_coins integer NOT NULL DEFAULT 0, created_utc integer NOT NULL DEFAULT 0)"))
        conn.execute(text("CREATE UNIQUE INDEX circles_board_key ON circles (board_id) WHERE board_id IS NOT NULL"))
        conn.execute(text("CREATE TABLE circle_members (id integer PRIMARY KEY AUTOINCREMENT, circle_id integer NOT NULL, "
                          "user_id integer NOT NULL, tier text NOT NULL, status text NOT NULL DEFAULT 'active', "
                          "started_utc integer NOT NULL DEFAULT 0, renews_utc integer NOT NULL DEFAULT 0, "
                          "cancelled boolean NOT NULL DEFAULT 0, price_coins integer NOT NULL DEFAULT 0, "
                          "created_utc integer NOT NULL DEFAULT 0, UNIQUE (circle_id, user_id))"))
        conn.execute(text("CREATE TABLE circle_payments (id integer PRIMARY KEY AUTOINCREMENT, circle_id integer NOT NULL, "
                          "payer_id integer NOT NULL, owner_id integer NOT NULL, coins integer NOT NULL, kind text NOT NULL, "
                          "created_utc integer NOT NULL DEFAULT 0)"))
        conn.execute(text("CREATE TABLE contributors (id integer PRIMARY KEY AUTOINCREMENT, user_id integer, board_id integer, "
                          "created_utc integer DEFAULT 0, is_active boolean DEFAULT 1, approving_mod_id integer)"))
        conn.execute(text("CREATE TABLE mods (id integer PRIMARY KEY AUTOINCREMENT, user_id integer, board_id integer, accepted boolean DEFAULT 0, invite_rescinded boolean DEFAULT 0)"))
        conn.execute(text("CREATE TABLE bans (id integer PRIMARY KEY AUTOINCREMENT, user_id integer, board_id integer, is_active boolean DEFAULT 0)"))
        for uid, name, coins in ((FOUNDER, "founder", 0), (FAN, "fan", 100), (POOR, "poor", 5), (INVITED, "invited", 50), (EXILED, "exiled", 50),
                                 (BLOCKED, "blocked", 50), (STRANGER, "stranger", 50)):
            conn.execute(text("INSERT INTO users (id, username, coin_balance) VALUES (:i, :n, :c)"), {"i": uid, "n": name, "c": coins})
        conn.execute(text("INSERT INTO boards (id, name, creator_id, is_circle) VALUES (:g, 'circleclub', :f, 1)"), {"g": GUILD, "f": FOUNDER})
        conn.execute(text("INSERT INTO boards (id, name, creator_id, is_circle) VALUES (:g, 'plainclub', :f, 0)"), {"g": PLAIN, "f": FOUNDER})
        conn.execute(text("INSERT INTO boards (id, name, creator_id, is_circle, is_banned) VALUES (:g, 'bannedclub', :f, 1, 1)"), {"g": BANNED_GUILD, "f": FOUNDER})
        conn.execute(text("INSERT INTO mods (user_id, board_id, accepted) VALUES (:f, :g, 1)"), {"f": FOUNDER, "g": GUILD})
        conn.execute(text("INSERT INTO contributors (user_id, board_id, is_active) VALUES (:u, :g, 1)"), {"u": INVITED, "g": GUILD})
        conn.execute(text("INSERT INTO bans (user_id, board_id, is_active) VALUES (:u, :g, 1)"), {"u": EXILED, "g": GUILD})
        conn.execute(text("INSERT INTO userblocks (user_id, target_id) VALUES (:f, :b)"), {"f": FOUNDER, "b": BLOCKED})
    session = Session(engine)
    yield session
    session.close()


def coins(db, uid):
    return db.execute(text("SELECT coin_balance FROM users WHERE id = :i"), {"i": uid}).scalar()


def contributor(db, uid, board=GUILD):
    row = db.execute(text("SELECT is_active FROM contributors WHERE user_id = :u AND board_id = :b"), {"u": uid, "b": board}).fetchone()
    return None if row is None else bool(row[0])


def priced(db, price=10, board=GUILD):
    return store.set_board_price(db, board, price, NOW)


# --- the guild's price ------------------------------------------------------------------------------------

def test_a_guilds_circle_is_made_once_and_its_price_changes_in_place(db):
    first = store.set_board_price(db, GUILD, 25, NOW)
    assert store.set_board_price(db, GUILD, 12, NOW) == first
    assert tuple(store.circle_of_board(db, GUILD)) == (first, 12)
    assert db.execute(text("SELECT COUNT(*) FROM circles WHERE board_id = :b"), {"b": GUILD}).scalar() == 1


# --- joining ----------------------------------------------------------------------------------------------

def test_joining_pays_the_founder_exactly_and_makes_the_payer_a_member(db):
    priced(db, 10)
    outcome, message = store.subscribe_guild(db, GUILD, FAN, NOW)
    assert outcome == "subscribed" and "10 coins" in message
    assert (coins(db, FAN), coins(db, FOUNDER)) == (90, 10)
    assert contributor(db, FAN) is True and store.is_guild_member(db, GUILD, FAN)
    row = db.execute(text("SELECT m.tier, m.status, m.renews_utc, m.price_coins FROM circle_members m JOIN circles c ON c.id = m.circle_id "
                          "WHERE c.board_id = :b AND m.user_id = :u"), {"b": GUILD, "u": FAN}).fetchone()
    assert tuple(row) == ("subscriber", "active", NOW + c.PERIOD, 10)
    assert db.execute(text("SELECT payer_id, owner_id, coins, kind FROM circle_payments")).fetchall() == [(FAN, FOUNDER, 10, "subscribe")]


def test_the_founder_is_paid_not_the_person_who_set_the_price(db):
    priced(db, 10)
    store.subscribe_guild(db, GUILD, FAN, NOW)
    assert db.execute(text("SELECT owner_id FROM circle_payments")).scalar() == FOUNDER


@pytest.mark.parametrize("board, why", [(PLAIN, "isn't a Circle guild"), (BANNED_GUILD, "isn't a Circle guild"), (999, "isn't a Circle guild")])
def test_only_a_live_circle_guild_can_be_joined(db, board, why):
    if board != 999:
        priced(db, 10, board=board)
    outcome, message = store.subscribe_guild(db, board, FAN, NOW)
    assert outcome == "refused" and why in message and coins(db, FAN) == 100


def test_a_guild_with_no_price_is_by_invitation_only(db):
    outcome, message = store.subscribe_guild(db, GUILD, FAN, NOW)
    assert (outcome, message) == ("refused", "This Circle guild is by invitation only.")
    priced(db, 0)
    assert store.subscribe_guild(db, GUILD, FAN, NOW)[0] == "refused" and coins(db, FAN) == 100 and contributor(db, FAN) is None


def test_someone_who_cannot_pay_joins_nothing_and_keeps_their_coins(db):
    priced(db, 10)
    outcome, message = store.subscribe_guild(db, GUILD, POOR, NOW)
    assert outcome == "refused" and "don't have enough" in message
    assert (coins(db, POOR), coins(db, FOUNDER)) == (5, 0) and contributor(db, POOR) is None


@pytest.mark.parametrize("who, why", [(INVITED, "already a member"), (EXILED, "can't join"), (BLOCKED, "can't join")])
def test_who_cannot_pay_to_join(db, who, why):
    priced(db, 10)
    outcome, message = store.subscribe_guild(db, GUILD, who, NOW)
    assert outcome == "refused" and why in message and coins(db, who) == 50 and coins(db, FOUNDER) == 0


def test_joining_twice_charges_once(db):
    priced(db, 10)
    store.subscribe_guild(db, GUILD, FAN, NOW)
    assert store.subscribe_guild(db, GUILD, FAN, NOW + DAY) == ("refused", "You are already a member.")
    assert coins(db, FAN) == 90


def test_a_guildmaster_is_already_a_member(db):
    priced(db, 10)
    assert "already a member" in store.subscribe_guild(db, GUILD, FOUNDER, NOW)[1]


# --- cancelling and the date ------------------------------------------------------------------------------

def test_cancelling_keeps_access_to_the_date_and_resuming_charges_nothing(db):
    priced(db, 10)
    store.subscribe_guild(db, GUILD, FAN, NOW)
    assert store.cancel_guild(db, GUILD, FAN) is True and store.cancel_guild(db, GUILD, STRANGER) is False
    assert store.subscribe_guild(db, GUILD, FAN, NOW + DAY)[0] == "resumed"
    assert coins(db, FAN) == 90 and len(db.execute(text("SELECT id FROM circle_payments")).fetchall()) == 1


# --- renewing and leaving ---------------------------------------------------------------------------------

def test_a_renewal_pays_the_founder_and_keeps_the_member(db):
    priced(db, 10)
    store.subscribe_guild(db, GUILD, FAN, NOW)
    events = store.renew_due(db, NOW + c.PERIOD)
    assert events == [("renewed", FAN, FOUNDER, 10, GUILD)]
    assert (coins(db, FAN), coins(db, FOUNDER)) == (80, 20) and contributor(db, FAN) is True


def test_a_member_who_cannot_pay_loses_access_and_is_reported_with_the_guild(db):
    priced(db, 5)
    store.subscribe_guild(db, GUILD, POOR, NOW)
    assert store.renew_due(db, NOW + c.PERIOD) == [("lapsed", POOR, FOUNDER, 5, GUILD)]
    assert contributor(db, POOR) is False and not store.is_guild_member(db, GUILD, POOR) and coins(db, POOR) == 0


def test_a_cancelled_member_leaves_on_the_date(db):
    priced(db, 10)
    store.subscribe_guild(db, GUILD, FAN, NOW)
    store.cancel_guild(db, GUILD, FAN)
    assert store.renew_due(db, NOW + c.PERIOD) == [("ended", FAN, FOUNDER, 10, GUILD)]
    assert contributor(db, FAN) is False and coins(db, FAN) == 90


def test_a_member_exiled_in_the_meantime_is_not_charged_again(db):
    priced(db, 10)
    store.subscribe_guild(db, GUILD, FAN, NOW)
    db.execute(text("INSERT INTO bans (user_id, board_id, is_active) VALUES (:u, :g, 1)"), {"u": FAN, "g": GUILD})
    assert store.renew_due(db, NOW + c.PERIOD) == [("ended", FAN, FOUNDER, 10, GUILD)]
    assert coins(db, FAN) == 90 and contributor(db, FAN) is False


def test_a_member_the_guildmasters_remove_is_not_charged_again(db):
    priced(db, 10)
    store.subscribe_guild(db, GUILD, FAN, NOW)
    assert store.end_guild_member(db, GUILD, FAN) == 1 and store.end_guild_member(db, GUILD, FAN) == 0
    assert store.renew_due(db, NOW + 5 * c.PERIOD) == []
    assert coins(db, FAN) == 90


def test_people_invited_free_are_never_touched_by_renewals(db):
    priced(db, 10)
    store.subscribe_guild(db, GUILD, FAN, NOW)
    store.renew_due(db, NOW + 400 * DAY)
    assert contributor(db, INVITED) is True and coins(db, INVITED) == 50


def test_an_account_circle_and_a_guild_circle_are_both_settled_in_one_pass(db):
    store.set_price(db, STRANGER, 5, NOW)               # STRANGER has a Circle of their own, FAN subscribes to it
    store.subscribe(db, STRANGER, FAN, NOW)
    priced(db, 10)
    store.subscribe_guild(db, GUILD, STRANGER, NOW)     # and STRANGER pays to be in the guild
    events = sorted(store.renew_due(db, NOW + c.PERIOD))
    assert [(e[0], e[4]) for e in events] == [("renewed", None), ("renewed", GUILD)]


# --- the lists --------------------------------------------------------------------------------------------

def test_paying_members_and_memberships_list_only_those_with_access_now(db):
    priced(db, 10)
    store.subscribe_guild(db, GUILD, FAN, NOW)
    store.subscribe_guild(db, GUILD, STRANGER, NOW - 29 * DAY)
    assert [s.username for s in store.guild_subscribers(db, GUILD, NOW)] == ["stranger", "fan"]
    assert [s.username for s in store.guild_subscribers(db, GUILD, NOW + 2 * DAY)] == ["fan"]
    assert [tuple(m)[:2] for m in store.guild_memberships(db, FAN, NOW)] == [(GUILD, "circleclub")]
    assert store.guild_memberships(db, FAN, NOW + c.PERIOD) == []
    assert store.guild_memberships(db, INVITED, NOW) == []
