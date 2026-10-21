"""Two-factor backup codes: the rules (helpers/mfa_backup.py), the SQL on SQLite (helpers/mfa_backup_store.py, the table
made from the migration file) and authenticator codes accepted once (helpers/totp.py)."""
import re
from pathlib import Path

import pyotp
import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.orm import Session

from ruqqus.helpers import mfa_backup, totp
from ruqqus.helpers import mfa_backup_store as store

ROOT = Path(__file__).resolve().parent.parent
SCHEMA = (ROOT / "schema.sql").read_text(encoding="utf-8")


# ---- the rules ----

def test_ten_different_codes_of_ten_characters_without_look_alikes():
    codes = mfa_backup.generate()
    assert len(codes) == len(set(codes)) == 10
    for code in codes:
        assert len(code) == 10 and all(ch in mfa_backup.ALPHABET for ch in code)
    assert not set("0O1I") & set(mfa_backup.ALPHABET)


def test_a_code_is_typed_in_any_case_with_spaces_or_a_hyphen():
    assert mfa_backup.normalise("abcde-fghjk") == "ABCDEFGHJK"
    assert mfa_backup.normalise(" ABCDE FGHJK ") == "ABCDEFGHJK"
    assert mfa_backup.display("ABCDEFGHJK") == "ABCDE-FGHJK"
    assert mfa_backup.normalise(mfa_backup.display("ABCDEFGHJK")) == "ABCDEFGHJK"


@pytest.mark.parametrize("typed", ["", None, "123456", "ABCDE", "ABCDEFGHJKL", "ABCDE-FGHI0", "abcde fghjk!", "A" * 25])
def test_things_that_cannot_be_a_code_are_not(typed):
    assert mfa_backup.normalise(typed) is None


def test_an_authenticator_code_and_the_legacy_recovery_code_are_never_taken_for_a_backup_code():
    assert mfa_backup.normalise("123456") is None
    assert mfa_backup.normalise("abcde fghij klmno pqrst uvwxy") is None       # the 25-character legacy form


def test_the_hash_is_salted_and_compared_in_constant_time():
    a, b = mfa_backup.digest("s1", "ABCDEFGHJK"), mfa_backup.digest("s2", "ABCDEFGHJK")
    assert a != b and re.fullmatch(r"[0-9a-f]{64}", a)
    assert mfa_backup.matches("s1", a, "ABCDEFGHJK") and not mfa_backup.matches("s2", a, "ABCDEFGHJK")
    assert "compare_digest" in (ROOT / "ruqqus/helpers/mfa_backup.py").read_text(encoding="utf-8")


def test_the_legacy_recovery_code_stops_working_once_codes_exist():
    assert mfa_backup.legacy_code_allowed(False) and not mfa_backup.legacy_code_allowed(True)


def test_the_status_line_warns_when_few_are_left():
    assert mfa_backup.status_line(0, False) == ("You haven't made backup codes yet.", True)
    assert mfa_backup.status_line(0, True)[1] is True
    assert mfa_backup.status_line(1, True) == ("1 unused backup code left.", True)
    assert mfa_backup.status_line(3, True) == ("3 unused backup codes left.", False)


# ---- the SQL ----

def table_body(sql, opening):
    """What is between the parentheses of the CREATE TABLE that starts with `opening`."""
    start = sql.index(opening) + len(opening)
    start = sql.index("(", start) + 1
    return sql[start:sql.index("\n);", start)]


@pytest.fixture
def db():
    """The table as schema.sql makes it (and the migration file says the same: tests/test_migration_ledger.py)."""
    ddl = table_body(SCHEMA, "CREATE TABLE public.mfa_backup_codes")
    ddl = ddl.replace("character varying(32)", "text").replace("character(64)", "text")
    engine = create_engine("sqlite://")
    with engine.begin() as conn:
        conn.execute(text(f"CREATE TABLE mfa_backup_codes ({ddl})"))
    with Session(engine) as session:
        yield session


def test_the_migration_and_schema_sql_make_the_same_table():
    migration = (ROOT / "scripts/migrations/2026-11-15_mfa_backup_codes.sql").read_text(encoding="utf-8")
    a = table_body(migration, "CREATE TABLE IF NOT EXISTS public.mfa_backup_codes")
    b = table_body(SCHEMA, "CREATE TABLE public.mfa_backup_codes")
    assert a.strip() == b.strip()


def test_codes_are_stored_hashed_and_a_used_one_cannot_be_used_again(db):
    codes = mfa_backup.generate()
    store.replace(db, 7, codes, now=100)
    assert store.has_codes(db, 7) and store.unused(db, 7) == 10
    stored = {row[0] for row in db.execute(text("SELECT code_hash FROM mfa_backup_codes"))}
    assert not stored & set(codes), "a code must never be stored as typed"

    assert store.spend(db, 7, mfa_backup.display(codes[0]).lower(), now=200) is True
    assert store.unused(db, 7) == 9
    assert store.spend(db, 7, codes[0], now=300) is False, "a code works once"
    assert store.has_codes(db, 7), "a used code still counts as having codes (the legacy code stays off)"


def test_a_wrong_code_a_short_one_and_another_accounts_code_are_refused(db):
    mine, theirs = mfa_backup.generate(), mfa_backup.generate()
    store.replace(db, 1, mine, now=1)
    store.replace(db, 2, theirs, now=1)
    assert not store.spend(db, 1, "AAAAAAAAAA", now=2)
    assert not store.spend(db, 1, "123456", now=2)
    assert not store.spend(db, 1, theirs[0], now=2)
    assert store.unused(db, 1) == 10 and store.unused(db, 2) == 10


def test_making_new_codes_replaces_every_old_one_used_or_not(db):
    first = mfa_backup.generate()
    store.replace(db, 3, first, now=1)
    store.spend(db, 3, first[1], now=2)
    second = mfa_backup.generate()
    store.replace(db, 3, second, now=3)
    assert store.unused(db, 3) == 10
    assert not store.spend(db, 3, first[0], now=4), "an old unused code must stop working"
    assert store.spend(db, 3, second[0], now=4)


def test_two_presentations_of_one_code_at_once_spend_it_once(db):
    """`spend` ends with a conditional UPDATE (`used_utc = 0` in its WHERE), so the second of two requests changes no row."""
    code = mfa_backup.generate()
    store.replace(db, 4, code, now=1)
    row = db.execute(text("SELECT code_hash FROM mfa_backup_codes WHERE user_id = 4")).first()
    sql = "UPDATE mfa_backup_codes SET used_utc = :now WHERE user_id = 4 AND code_hash = :h AND used_utc = 0"
    assert db.execute(text(sql), {"now": 5, "h": row[0]}).rowcount == 1
    assert db.execute(text(sql), {"now": 6, "h": row[0]}).rowcount == 0
    assert "used_utc = 0" in (ROOT / "ruqqus/helpers/mfa_backup_store.py").read_text(encoding="utf-8")


def test_delete_all_forgets_the_account_has_ever_had_codes(db):
    store.replace(db, 5, mfa_backup.generate(), now=1)
    store.delete_all(db, 5)
    assert not store.has_codes(db, 5) and store.unused(db, 5) == 0


# ---- an authenticator code is accepted once ----

class FakeRedis:
    def __init__(self):
        self.keys = {}

    def set(self, key, value, nx=False, ex=None):
        if nx and key in self.keys:
            return None
        self.keys[key] = value
        return True


def test_matched_step_finds_the_step_a_code_was_made_in():
    secret = pyotp.random_base32()
    now = 1_700_000_000
    token = pyotp.TOTP(secret).at(now)
    assert totp.matched_step(secret, token, now) == now // 30
    assert totp.matched_step(secret, token, now + 30) == now // 30          # one step later: still inside the window
    assert totp.matched_step(secret, token, now + 90) is None                # too old
    assert totp.matched_step(secret, "000000" if token != "000000" else "111111", now) is None
    assert totp.matched_step(secret, "12345", now) is None and totp.matched_step(secret, "abcdef", now) is None
    assert totp.matched_step(secret, " ".join([token[:3], token[3:]]), now) == now // 30      # typed with a space


def test_a_code_is_accepted_once_per_account():
    redis = FakeRedis()
    assert totp.claim(redis, 1, 100) is True
    assert totp.claim(redis, 1, 100) is False, "the same step again is a replay"
    assert totp.claim(redis, 1, 101) is True, "the next step is a new code"
    assert totp.claim(redis, 2, 100) is True, "another account's code is its own"


def test_without_redis_or_when_it_fails_the_code_is_still_accepted():
    class Broken:
        def set(self, *a, **k):
            raise ConnectionError("down")
    assert totp.claim(None, 1, 1) is True and totp.claim(Broken(), 1, 1) is True
