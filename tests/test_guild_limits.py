"""Guild limits (ruqqus/helpers/guild_limits.py): at most 5 guilds per account and 5 per network. The rules are pure;
the address queries run for real on in-memory SQLite (tables as in schema.sql)."""
from types import SimpleNamespace as NS

import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.orm import Session

from ruqqus.helpers import guild_limits as gl

NOW = 2_000_000_000
DAY = 24 * 60 * 60
HOME, ELSEWHERE = "203.0.113.7", "198.51.100.9"


@pytest.fixture
def db():
    engine = create_engine("sqlite://")
    with engine.begin() as conn:
        conn.execute(text("CREATE TABLE users (id integer PRIMARY KEY, creation_ip varchar(255), is_banned integer DEFAULT 0, "
                          "unban_utc integer DEFAULT 0, is_deleted boolean DEFAULT 0)"))
        conn.execute(text("CREATE TABLE login_events (id integer PRIMARY KEY AUTOINCREMENT, user_id integer, created_utc integer, ip varchar(255))"))
        conn.execute(text("CREATE TABLE boards (id integer PRIMARY KEY, is_banned boolean DEFAULT 0, is_siegable boolean DEFAULT 1)"))
        conn.execute(text("CREATE TABLE mods (id integer PRIMARY KEY AUTOINCREMENT, user_id integer, board_id integer, "
                          "accepted boolean DEFAULT 1, invite_rescinded boolean DEFAULT 0)"))
    session = Session(engine)
    yield session
    session.close()


def user(db, uid, ip=HOME, banned=0, unban=0, deleted=0):
    db.execute(text("INSERT INTO users (id, creation_ip, is_banned, unban_utc, is_deleted) VALUES (:i, :ip, :b, :u, :d)"),
               {"i": uid, "ip": ip, "b": banned, "u": unban, "d": deleted})


def login(db, uid, ip, at=NOW - DAY):
    db.execute(text("INSERT INTO login_events (user_id, created_utc, ip) VALUES (:u, :t, :ip)"), {"u": uid, "t": at, "ip": ip})


def guild(db, bid, banned=0, siegable=1):
    db.execute(text("INSERT INTO boards (id, is_banned, is_siegable) VALUES (:i, :b, :s)"), {"i": bid, "b": banned, "s": siegable})


def lead(db, uid, *boards, accepted=1, rescinded=0):
    for bid in boards:
        db.execute(text("INSERT INTO mods (user_id, board_id, accepted, invite_rescinded) VALUES (:u, :b, :a, :r)"),
                   {"u": uid, "b": bid, "a": accepted, "r": rescinded})


def account(uid, boards=0, admin=0):
    """What `check` reads of a User: its id, admin level and the guilds it leads (boards_modded)."""
    return NS(id=uid, admin_level=admin, boards_modded=[NS(is_siegable=True) for _ in range(boards)])


# --- the pure rules -----------------------------------------------------------------------------------

def test_the_limits_are_five_and_five():
    assert (gl.PER_PERSON, gl.PER_IP) == (5, 5)


@pytest.mark.parametrize("person, ip, expected", [
    (0, 0, None), (4, 4, None), (4, 5, gl.IP), (5, 5, gl.PERSON), (5, 0, gl.PERSON), (9, 9, gl.PERSON), (0, 12, gl.IP),
])
def test_refusal_table(person, ip, expected):
    assert gl.refusal(person, ip) == expected


def test_admins_are_exempt_from_both_limits():
    assert gl.refusal(20, 20, admin_level=gl.ADMIN_LEVEL) is None
    assert gl.refusal(20, 20, admin_level=4) is None
    assert gl.refusal(20, 20, admin_level=gl.ADMIN_LEVEL - 1) == gl.PERSON


def test_messages_name_the_limit_and_never_the_other_accounts_or_addresses():
    assert str(gl.PER_PERSON) in gl.say(gl.PERSON)
    assert str(gl.PER_IP) in gl.say(gl.IP)
    assert gl.say(gl.IP) != gl.say(gl.PERSON)
    # about someone else the reason is not given (it could be who shares an address)
    assert gl.say(gl.IP, "bob") == gl.say(gl.PERSON, "bob") == "@bob can't lead another guild right now."
    for message in (gl.say(gl.PERSON), gl.say(gl.IP), gl.say(gl.IP, "bob")):
        assert HOME not in message and "203." not in message


# --- the addresses of an account ----------------------------------------------------------------------

def test_an_account_has_its_signup_address_and_the_ones_it_logged_in_from_within_ninety_days(db):
    user(db, 1)
    login(db, 1, ELSEWHERE, at=NOW - 10 * DAY)
    login(db, 1, "192.0.2.1", at=NOW - 91 * DAY)            # too old
    assert gl.addresses_of(db, 1, NOW) == sorted([HOME, ELSEWHERE])


def test_a_missing_or_empty_address_is_not_an_address(db):
    user(db, 1, ip=None)
    login(db, 1, "")
    assert gl.addresses_of(db, 1, NOW) == []
    assert gl.led_on(db, [], NOW) == 0


# --- guilds led on an address -------------------------------------------------------------------------

def test_guilds_of_two_accounts_on_one_address_are_added_up(db):
    user(db, 1)
    user(db, 2)
    for b in range(1, 7):
        guild(db, b)
    lead(db, 1, 1, 2, 3)
    lead(db, 2, 4, 5, 6)
    assert gl.led_on(db, [HOME], NOW) == 6


def test_a_guild_led_by_two_accounts_counts_once(db):
    user(db, 1)
    user(db, 2)
    guild(db, 1)
    guild(db, 2)
    lead(db, 1, 1, 2)
    lead(db, 2, 1)
    assert gl.led_on(db, [HOME], NOW) == 2


def test_an_account_that_only_logged_in_from_the_address_counts_inside_the_window_only(db):
    user(db, 1)
    user(db, 2, ip=ELSEWHERE)
    user(db, 3, ip=ELSEWHERE)
    for b in range(1, 5):
        guild(db, b)
    lead(db, 1, 1)
    lead(db, 2, 2, 3)
    lead(db, 3, 4)
    login(db, 2, HOME, at=NOW - 30 * DAY)
    login(db, 3, HOME, at=NOW - 120 * DAY)                  # long ago: not "on" the address any more
    assert gl.led_on(db, [HOME], NOW) == 3


def test_other_addresses_do_not_leak_in(db):
    user(db, 1)
    user(db, 2, ip=ELSEWHERE)
    guild(db, 1)
    guild(db, 2)
    lead(db, 1, 1)
    lead(db, 2, 2)
    assert gl.led_on(db, [HOME], NOW) == 1
    assert gl.led_on(db, [ELSEWHERE], NOW) == 1
    assert gl.led_on(db, [HOME, ELSEWHERE], NOW) == 2


def test_what_does_not_count(db):
    user(db, 1)
    user(db, 2, banned=1)                                   # permanently banned account
    user(db, 3, deleted=1)
    user(db, 4, banned=1, unban=NOW + DAY)                  # a temporary ban still leads its guilds
    for b in range(1, 12):
        guild(db, b)
    guild(db, 20, banned=1)                                 # a banned guild
    guild(db, 21, siegable=0)                               # a siege-protected guild
    lead(db, 1, 1, 20, 21)
    lead(db, 1, 2, accepted=0)                              # an invitation not accepted
    lead(db, 1, 3, rescinded=1)                             # a withdrawn one
    lead(db, 2, 4, 5)
    lead(db, 3, 6)
    lead(db, 4, 7)
    assert gl.led_on(db, [HOME], NOW) == 2                  # guild 1 and guild 7


# --- the whole check ----------------------------------------------------------------------------------

def test_an_account_alone_on_its_address_may_lead_up_to_five(db):
    user(db, 1)
    for b in range(1, 5):
        guild(db, b)
    lead(db, 1, 1, 2, 3, 4)
    assert gl.check(db, account(1, boards=4), NOW) is None
    assert gl.check(db, account(1, boards=5), NOW) == gl.PERSON


def test_a_second_account_on_the_address_cannot_get_round_the_per_person_limit(db):
    user(db, 1)
    user(db, 2)
    for b in range(1, 6):
        guild(db, b)
    lead(db, 1, 1, 2, 3)
    lead(db, 2, 4, 5)
    assert gl.check(db, account(2, boards=2), NOW) == gl.IP
    assert gl.check(db, account(1, boards=3), NOW) == gl.IP


def test_someone_who_already_leads_more_than_the_limit_is_refused_but_nothing_is_taken_away(db):
    user(db, 1)
    for b in range(1, 8):
        guild(db, b)
    lead(db, 1, *range(1, 8))
    assert gl.check(db, account(1, boards=7), NOW) == gl.PERSON
    assert db.execute(text("SELECT COUNT(*) FROM mods WHERE user_id = 1")).scalar() == 7


def test_siege_protected_guilds_do_not_count_for_the_person(db):
    user(db, 1)
    boards = [NS(is_siegable=True)] * 4 + [NS(is_siegable=False)] * 3
    assert gl.check(db, NS(id=1, admin_level=0, boards_modded=boards), NOW) is None


def test_an_admin_is_never_refused_and_the_database_is_not_even_asked(db):
    class Explodes:
        def execute(self, *a, **k):
            raise AssertionError("an admin must not cost a query")
    assert gl.check(Explodes(), account(1, boards=10, admin=3), NOW) is None


def test_a_person_over_the_limit_is_refused_without_the_address_query(db):
    class Explodes:
        def execute(self, *a, **k):
            raise AssertionError("the cheap check comes first")
    assert gl.check(Explodes(), account(1, boards=5), NOW) == gl.PERSON
