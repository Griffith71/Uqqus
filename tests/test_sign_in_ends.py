"""Sign-ins that ended stay ended (ruqqus/helpers/sign_in_ends.py) on SQLite, with the table from the migration: only a hash of the
cookie's session id is stored, ending twice is fine, a missing table answers no, retention, and the wiring of the two checks and the
four writers (tests/pg/test_sign_in_ends.py runs them on the real app)."""
import hashlib
import re
from pathlib import Path

from sqlalchemy import create_engine, text
from sqlalchemy.orm import Session

from ruqqus.helpers import retention
from ruqqus.helpers import sign_in_ends as ends

ROOT = Path(__file__).resolve().parents[1]
MIGRATION = ROOT / "scripts/migrations/2026-12-19_ended_sign_ins.sql"
NOW = 1_800_000_000


def read(*parts):
    return (ROOT.joinpath(*parts)).read_text(encoding="utf-8")


def make(with_table=True):
    db = Session(create_engine("sqlite://"))
    if with_table:
        for statement in MIGRATION.read_text().split(";"):
            lines = [line for line in statement.splitlines() if line.strip() and not line.strip().startswith("--")]
            if lines:
                db.execute(text("\n".join(lines)))
    return db


def test_only_a_hash_of_the_session_id_is_stored():
    db = make()
    ends.end(db, "abc123", 7, "logout", NOW)
    rows = db.execute(text("SELECT id, user_id, ended_utc, reason FROM ended_sign_ins")).all()
    assert rows == [(hashlib.sha256(b"abc123").hexdigest(), 7, NOW, "logout")]
    assert "abc123" not in repr(rows)


def test_an_ended_sign_in_reads_ended_and_another_does_not():
    db = make()
    assert not ends.ended(db, "abc123")
    ends.end(db, "abc123", 7, "logout", NOW)
    assert ends.ended(db, "abc123")
    assert not ends.ended(db, "abc124")
    assert not ends.ended(db, "")
    assert not ends.ended(db, None)


def test_ending_twice_is_fine_and_keeps_the_first():
    db = make()
    ends.end(db, "abc123", 7, "logout", NOW)
    ends.end(db, "abc123", 7, "replaced", NOW + 5)
    assert db.execute(text("SELECT reason, ended_utc FROM ended_sign_ins")).all() == [("logout", NOW)]


def test_nothing_is_written_without_a_session_id_or_an_account():
    db = make()
    ends.end(db, None, 7, "logout", NOW)
    ends.end(db, "", 7, "logout", NOW)
    ends.end(db, "abc", None, "logout", NOW)
    assert db.execute(text("SELECT COUNT(*) FROM ended_sign_ins")).scalar() == 0


def test_a_database_without_the_table_answers_no_and_keeps_working():
    db = make(with_table=False)
    assert ends.ended(db, "abc123") is False
    # the savepoint kept the transaction usable
    assert db.execute(text("SELECT 1")).scalar() == 1


def test_ending_without_the_table_keeps_nothing_and_keeps_working():
    db = make(with_table=False)
    ends.end(db, "abc123", 7, "logout", NOW)        # Log out still works while a deploy applies the migration
    assert db.execute(text("SELECT 1")).scalar() == 1


def test_retention_keeps_rows_longer_than_a_cookie_written_back_can_last():
    assert retention.ENDED_SIGN_IN_DAYS == ends.KEEP_DAYS > 365
    db = make()
    ends.end(db, "old", 1, "logout", NOW - (ends.KEEP_DAYS + 1) * 86400)
    ends.end(db, "new", 1, "logout", NOW - 30 * 86400)
    rule = dict(retention.RULES)["old ended sign-ins"]
    db.execute(text(rule), {"ended_sign_ins": NOW - retention.ENDED_SIGN_IN_DAYS * 86400})
    assert not ends.ended(db, "old") and ends.ended(db, "new")


def test_the_migration_is_mirrored_in_schema_sql_and_listed():
    schema = read("schema.sql")
    assert "CREATE TABLE public.ended_sign_ins" in schema
    assert "('2026-12-19_ended_sign_ins.sql')" in schema
    assert "REFERENCES" not in MIGRATION.read_text()


def test_every_request_that_opens_the_database_refuses_an_ended_sign_in():
    main = read("ruqqus", "__main__.py")
    before = main.split("def before_request():", 1)[1].split("\ndef ", 1)[0]
    assert before.index("g.db = db_session()") < before.index("session_cookie.settle(") < before.index(
        "sign_in_ends.refuse(flask_session, g.db, time.time(), g)")
    wrappers = read("ruqqus", "helpers", "wrappers.py")
    branch = wrappers.split("elif 'user_id' in flask_session", 1)[1].split("if x[0]:", 1)[0]
    assert branch.index("nonce < v.login_nonce") < branch.index("sign_in_ends.refuse(flask_session, db, time.time(), g)")


class CookieSession(dict):
    modified = True


def test_refusing_takes_the_sign_in_out_and_writes_nothing_for_a_minute():
    db = make()
    ends.end(db, "abc123", 7, "logout", NOW)
    flags = type("G", (), {})()
    session = CookieSession(user_id=7, session_id="abc123", login_nonce=0, base_feed="all")
    assert ends.refuse(session, db, NOW + 5, flags)
    assert "user_id" not in session and "session_id" not in session and session["base_feed"] == "all"
    assert session.modified is False and flags.ended_sign_in_refused is True


def test_refusing_later_writes_the_visitors_cookie():
    db = make()
    ends.end(db, "abc123", 7, "logout", NOW)
    flags = type("G", (), {})()
    session = CookieSession(user_id=7, session_id="abc123")
    assert ends.refuse(session, db, NOW + ends.QUIET_SECONDS + 1, flags)
    assert "user_id" not in session and session.modified is True and not hasattr(flags, "ended_sign_in_refused")


def test_a_live_sign_in_or_a_visitor_is_left_alone():
    db = make()
    flags = type("G", (), {})()
    for session in (CookieSession(user_id=7, session_id="live"), CookieSession(base_feed="all"), CookieSession(user_id=7)):
        before = dict(session)
        assert not ends.refuse(session, db, NOW, flags)
        assert dict(session) == before


def test_the_cookie_is_written_only_where_it_was_checked():
    main = read("ruqqus", "__main__.py")
    save = main.split("def save_session(self, app, session, response):", 1)[1].split("app.session_interface", 1)[0]
    # a refused cookie's answer only if the route changed it
    assert 'if g.get("ended_sign_in_refused") and not session.modified:\n            return' in save
    assert save.index("return") < save.index("super().save_session(")
    # the answers given before the database is opened write none, so a copy of an ended cookie can't be refreshed by them
    early = main.split("def before_request():", 1)[1].split("g.db = db_session()", 1)[0]
    for line in ('currently in read-only mode', 'abort(503)', 'You are in time out'):
        before = early.split(line, 1)[0].rsplit("\n    if ", 1)[1]
        assert "g.skip_session_cookie = True" in before, line
    errors = read("ruqqus", "routes", "errors.py").split("def error_429(e):", 1)[1].split("\ndef ", 1)[0]
    assert 'if getattr(g, "db", None) is None:\n        g.skip_session_cookie = True' in errors


def test_the_answer_writes_no_cookie_for_a_sign_in_that_ended_while_it_ran():
    main = read("ruqqus", "__main__.py")
    check = main.split("def _sign_in_ended_meanwhile", 1)[1].split("@app.after_request", 1)[0]
    # never opens a database, never asks for a request that wrote its own end, and an error counts as ended
    assert 'getattr(g, "db", None)' in check and 'g.get("skip_session_cookie")' in check and 'g.get("sign_in_ended_here")' in check
    assert "except Exception:\n        return True" in check
    # a request that failed before the database was opened never checked its cookie's sign-in: it counts as ended too (C4)
    assert "    if db is None:\n        return True\n" in check
    assert check.index("if db is None:") < check.index("sign_in_ends.ended(db, sid)")
    assert "or not sid or not flask_session.get(\"user_id\")" in check, "a visitor's cookie still gets written"
    hook = main.split("def after_request(response):", 1)[1]
    assert hook.index("_sign_in_ended_meanwhile()") < hook.index("g.db.commit()")
    assert "g.skip_session_cookie = True" in hook.split("g.db.commit()", 1)[0]


def test_log_out_ends_the_sign_in_on_the_server_and_forgets_it_at_once():
    login = read("ruqqus", "routes", "login.py")
    body = login.split("def logout(v):", 1)[1].split("\ndef ", 1)[0]
    assert 'sign_in_ends.end(g.db, flask_session.get("session_id"), v.id, "logout"' in body
    assert body.index("sign_in_ends.end(") < body.index("session_cookie.forget_sign_in(flask_session)")
    assert "chat_devices" not in body          # plain Log out keeps the chat device (the owner, 2026-11-05)


def test_a_sign_in_over_an_older_one_ends_the_older():
    login = read("ruqqus", "routes", "login.py")
    sign_in = login.split("def complete_sign_in(", 1)[1].split("\ndef ", 1)[0]
    assert sign_in.index('end_old_sign_in(flask_session, "replaced")') < sign_in.index('flask_session["session_id"] = token_hex(16)')
    sign_up = login.split("def sign_up_post(", 1)[1].split("\ndef ", 1)[0]
    assert sign_up.index('end_old_sign_in(session, "replaced")') < sign_up.index('session["session_id"] = token_hex(16)')


def test_the_qr_password_gives_a_new_session_id_and_ends_the_old():
    qr = read("ruqqus", "routes", "qr_login_pages.py")
    body = qr.split("def login_qr_upgrade(v):", 1)[1].split("\ndef ", 1)[0]
    end = body.index('sign_in_ends.end(g.db, flask_session.get("session_id"), v.id, "qr_upgrade"')
    assert end < body.index('flask_session["session_id"] = token_hex(16)') < body.index("g.db.commit()")


def test_closing_your_own_account_ends_this_sign_in_only_when_it_is_this_browsers():
    settings = read("ruqqus", "routes", "settings.py")
    body = settings.split("def close_own_account(", 1)[1].split("\ndef ", 1)[0]
    guarded = body.split("if sign_out_browser:", 1)[1]
    assert re.match(r"\s*#[^\n]*\n\s*sign_in_ends\.end\(", guarded)
    assert body.count("sign_in_ends.end(") == 1
