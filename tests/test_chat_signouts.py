"""Durable chat sign-outs (ruqqus/helpers/chat_signouts.py), the one credential event (helpers/account_sessions.py revoke) and the
token route's records (helpers/chat_tokens.py), on SQLite with the tables from the migration and a fake chat server. The real app is
tests/pg/test_chat_signout_routes.py; the tripwires are tests/test_revocation_assets.py."""
import importlib.util
import logging
import sys
import types
from pathlib import Path
from types import SimpleNamespace

import pytest
from sqlalchemy import create_engine, event, text
from sqlalchemy.orm import Session, scoped_session, sessionmaker

from ruqqus.helpers import account_sessions, age_declaration, chat_signouts, chat_tokens, retention

ROOT = Path(__file__).resolve().parents[1]
MIGRATION = ROOT / "scripts/migrations/2026-12-20_chat_sign_outs.sql"
NOW = 1_800_000_000
MX = "@ruqqus_7:x"


def statements(with_tables=True):
    out = ["CREATE TABLE chat_identities (user_id INTEGER PRIMARY KEY, matrix_user_id TEXT)",
           # what the token route reads again before it hands a device out (chat_tokens._refusal)
           "CREATE TABLE users (id INTEGER PRIMARY KEY, login_nonce INTEGER DEFAULT 0, is_deleted INTEGER DEFAULT 0, age_declared INTEGER, "
           "age_lock INTEGER)",
           "CREATE TABLE child_controls (child_id INTEGER PRIMARY KEY, chat INTEGER DEFAULT 0)"]
    if with_tables:
        for statement in MIGRATION.read_text().split(";"):
            lines = [line for line in statement.splitlines() if line.strip() and not line.strip().startswith("--")]
            if lines:
                out.append("\n".join(lines).replace("SERIAL PRIMARY KEY", "INTEGER PRIMARY KEY AUTOINCREMENT"))
    return out


def engine_for(path=None):
    """SQLite with real transactions: pysqlite's own handling lets a savepoint's RELEASE commit (SQLAlchemy's documented recipe)."""
    engine = create_engine(f"sqlite:///{path}" if path else "sqlite://")

    @event.listens_for(engine, "connect")
    def _connect(dbapi_connection, _record):
        dbapi_connection.isolation_level = None

    @event.listens_for(engine, "begin")
    def _begin(conn):
        conn.exec_driver_sql("BEGIN")
    return engine


def make(path=None, with_tables=True, identity=True):
    engine = engine_for(path)
    db = Session(engine)
    for statement in statements(with_tables):
        db.execute(text(statement))
    if identity:
        db.execute(text("INSERT INTO chat_identities VALUES (7, :m)"), {"m": MX})
    db.execute(text("INSERT INTO users (id) VALUES (7)"))
    db.commit()
    return db


def device(db, device_id, kind="chat", sign_in=None, created_ms=1000, used=NOW - 100, revoked=0, user_id=7):
    db.execute(text("""INSERT INTO chat_site_devices (user_id, device_id, kind, sign_in, created_ms, last_used_utc, revoked_utc)
                       VALUES (:u, :d, :k, :h, :c, :l, :r)"""),
               {"u": user_id, "d": device_id, "k": kind, "h": sign_in, "c": created_ms, "l": used, "r": revoked})


def revoked(db):
    return {d: reason for d, reason in db.execute(text("SELECT device_id, revoked_reason FROM chat_site_devices WHERE revoked_utc > 0"))}


def rows(db):
    return db.execute(text("SELECT scope, device_ids, spared, reason, done_utc, attempts, last_error FROM chat_device_signouts ORDER BY id")).all()


class FakeMatrix:
    class MatrixError(Exception):
        pass

    class UserUnknown(MatrixError):
        pass

    def __init__(self, devices=(), db=None):
        self.devices = {d: name for d, name in devices}
        self.calls, self.down, self.unknown, self.fail_delete, self.on_login, self.on_list = [], False, False, set(), None, None

    def list_devices(self, mxid):
        self.calls.append(("list", mxid))
        if self.on_list:
            self.on_list()
        if self.unknown:
            raise self.UserUnknown("no")
        if self.down:
            raise self.MatrixError("down")
        return [{"device_id": d, "name": name, "last_seen": None} for d, name in self.devices.items()]

    def delete_device(self, mxid, device_id):
        self.calls.append(("delete", device_id))
        if self.down or device_id in self.fail_delete:
            raise self.MatrixError("down")
        self.devices.pop(device_id, None)

    def login_as(self, mxid, device_id=None):
        self.calls.append(("login", device_id))
        if self.on_login:
            self.on_login(device_id)
        if self.down:
            raise self.MatrixError("down")
        self.devices.setdefault(device_id, "Laba chat")
        return {"user_id": mxid, "device_id": device_id, "access_token": "syt_x", "refresh_token": "never"}

    def deleted(self):
        return [c[1] for c in self.calls if c[0] == "delete"]


@pytest.fixture
def interim_off(monkeypatch):
    """Decision 2 as it will be from build step 4, when the site's own browsers keep their stores."""
    monkeypatch.setattr(chat_signouts, "KEEP_SITE_BROWSERS", False)


@pytest.fixture(autouse=True)
def forget_ready(monkeypatch):
    monkeypatch.setattr(chat_signouts, "_ready", [])


# --- queue --------------------------------------------------------------------------------------------------------------------

class Recorder(types.ModuleType):
    """Stands in for ruqqus.helpers.matrix_client and chat_devices: any name read from it is recorded (and is a function that records)."""
    def __init__(self, name, seen):
        super().__init__(name)
        self._seen = seen

    def __getattr__(self, attr):
        if attr.startswith("__"):
            raise AttributeError(attr)
        self._seen.append(f"{self.__name__}.{attr}")
        return lambda *a, **k: self._seen.append(f"{self.__name__}.{attr}()")


def test_queue_revokes_the_site_rows_it_covers_in_the_same_transaction_never_the_spared(interim_off, monkeypatch):
    db = make()
    device(db, "UQAAAAAAAAAA")
    device(db, "UQBBBBBBBBBB")
    device(db, "UQLATER00000", created_ms=10**15)          # made after the sign-out: never touched
    db.commit()
    seen = []
    import ruqqus.helpers
    for name in ("ruqqus.helpers.matrix_client", "ruqqus.helpers.chat_devices"):
        stand_in = Recorder(name, seen)
        monkeypatch.setitem(sys.modules, name, stand_in)
        monkeypatch.setattr(ruqqus.helpers, name.rsplit(".", 1)[1], stand_in, raising=False)
    chat_signouts.queue(db, 7, "password", spared=("UQBBBBBBBBBB",), now_ms=5000)
    chat_signouts.queue(db, 7, "deleted", now_ms=6000)
    assert revoked(db) == {"UQAAAAAAAAAA": "password", "UQBBBBBBBBBB": "deleted"}
    assert seen == [], "no Synapse call, nor a look at the chat server's module, inside queue: run_for_user, after the commit"
    db.rollback()                                           # one transaction: nothing survives a rollback
    assert revoked(db) == {} and rows(db) == []


def test_an_account_with_no_chat_gets_no_row():
    db = make(identity=False)
    assert chat_signouts.queue(db, 7, "reset") is None and rows(db) == []


def test_listed_revokes_those_ids_only(interim_off):
    db = make()
    device(db, "UQAAAAAAAAAA")
    device(db, "UQBBBBBBBBBB")
    chat_signouts.queue(db, 7, "signed_out_here", "listed", device_ids=["UQAAAAAAAAAA", "bad,id"])
    assert revoked(db) == {"UQAAAAAAAAAA": "signed_out_here"}
    assert rows(db)[0][:3] == ("listed", "UQAAAAAAAAAA", None), "an id that can't be a device id is never written"


def test_revoke_spares_at_most_one_chat_and_one_call_row_of_this_sign_in(interim_off):
    db = make()
    here = chat_signouts.sign_in_hash("cookie-a")
    device(db, "UQCHATOLD000", sign_in=here, used=NOW - 50)
    device(db, "UQCHATNEW000", sign_in=here, used=NOW - 10)
    device(db, "UQCALL000000", kind="call", sign_in=here, used=NOW - 20)
    device(db, "UQELSEWHERE0", sign_in=chat_signouts.sign_in_hash("cookie-b"))
    db.commit()
    user = SimpleNamespace(id=7, login_nonce=3)
    session = {"session_id": "cookie-a", "login_nonce": 3}
    assert account_sessions.revoke(db, user, "password", session=session, spare_this_sign_in=True) == 4
    assert user.login_nonce == 4 and session["login_nonce"] == 4
    assert revoked(db) == {"UQCHATOLD000": "password", "UQELSEWHERE0": "password"}, "a third row with the hash is signed out"
    assert set(rows(db)[0][2].split(",")) == {"UQCHATNEW000", "UQCALL000000"}


def test_this_sign_ins_devices_are_found_under_the_mint_lock(interim_off, monkeypatch):
    """A device this sign-in made while the credential change waited for the lock is committed before the lock is had: read before the
    lock, the spared set would miss it, and as a device made before the sign-out it would be revoked."""
    db = make()
    here = chat_signouts.sign_in_hash("cookie-a")
    device(db, "UQCHATOLD000", sign_in=here, used=NOW - 50)
    db.commit()
    real_lock = chat_signouts.lock_account

    def minted_meanwhile(*args):
        device(db, "UQCHATNEW000", sign_in=here, used=NOW, created_ms=2000)
        return real_lock(*args)
    monkeypatch.setattr(chat_signouts, "lock_account", minted_meanwhile)
    account_sessions.revoke(db, SimpleNamespace(id=7, login_nonce=0), "password", session={"session_id": "cookie-a"},
                            spare_this_sign_in=True, now_ms=5000)
    assert revoked(db) == {"UQCHATOLD000": "password"} and rows(db)[0][2] == "UQCHATNEW000"


def test_events_that_spare_nothing_spare_nothing(interim_off):
    db = make()
    device(db, "UQCHAT000000", sign_in=chat_signouts.sign_in_hash("cookie-a"))
    db.commit()
    account_sessions.revoke(db, SimpleNamespace(id=7, login_nonce=0), "reset")
    assert revoked(db) == {"UQCHAT000000": "reset"} and rows(db)[0][2] is None


def test_sparing_needs_the_session():
    with pytest.raises(ValueError):
        account_sessions.revoke(make(), SimpleNamespace(id=7, login_nonce=0), "password", spare_this_sign_in=True)


def test_the_tables_absent_revoke_still_raises_the_nonce_and_says_so(caplog):
    db = make(with_tables=False)
    user = SimpleNamespace(id=7, login_nonce=1)
    with caplog.at_level(logging.ERROR):
        assert account_sessions.revoke(db, user, "password", session={"session_id": "s"}, spare_this_sign_in=True) == 2
    assert "chat sign-out not queued: migration missing" in caplog.text and "user 7" in caplog.text
    assert db.execute(text("SELECT 1")).scalar() == 1          # the savepoint kept the transaction usable
    assert chat_signouts.tables_ready(db) is False


# --- the interim (build steps 2 and 3) ----------------------------------------------------------------------------------------

def test_the_interim_keeps_every_site_browser_for_all_but_todays_three():
    assert chat_signouts.KEEP_SITE_BROWSERS is True, "build step 4 turns this off, in the same deploy as the client that keeps stores"
    assert chat_signouts.SIGNS_OUT_SITE_BROWSERS_TODAY == {"child_chat_off", "child_stopped", "deleted"}
    for reason in ("password", "others", "mfa", "reset", "mfa_recovery", "child_password", "child_locked"):
        assert chat_signouts.interim_spared(reason) == ("site",), reason
    for reason in ("child_chat_off", "child_stopped", "deleted"):
        assert chat_signouts.interim_spared(reason) == (), reason


def test_an_interim_sign_out_revokes_no_site_row_and_deletes_only_apps():
    db = make()
    device(db, "UQSITE000000")
    db.commit()
    fake = FakeMatrix([("UQSITE000000", "Laba chat"), ("OLDSITEDEV0", ""), ("OLDNAMED000", "Laba chat"), ("ELEMENT0000", "Element Web")])
    account_sessions.revoke(db, SimpleNamespace(id=7, login_nonce=0), "reset")
    assert revoked(db) == {} and rows(db)[0][2] == "site"
    db.commit()
    assert chat_signouts.run_one(db, fake, 1, now=NOW, site_name="Laba") == "done"
    assert fake.deleted() == ["ELEMENT0000"], "today's apps-only rule: a row, or the site's name, keeps a device"


def test_in_the_interim_a_site_row_keeps_its_device_whatever_synapse_calls_it():
    """The row is what says a device is the site's (its name on Synapse can be changed from an outside app), and a revoked row too: its
    own `listed` sign-out deletes it, never the interim's `all`."""
    db = make()
    device(db, "UQRENAMED000")
    device(db, "UQREVOKED000", revoked=NOW - 5)
    db.commit()
    fake = FakeMatrix([("UQRENAMED000", "Element Web"), ("UQREVOKED000", "Firefox"), ("ELEMENT0000", "Element Web")])
    account_sessions.revoke(db, SimpleNamespace(id=7, login_nonce=0), "reset")
    db.commit()
    assert chat_signouts.run_one(db, fake, 1, now=NOW, site_name="Laba") == "done"
    assert fake.deleted() == ["ELEMENT0000"]


def test_todays_three_sign_out_the_site_browsers_too():
    db = make()
    device(db, "UQSITE000000")
    db.commit()
    fake = FakeMatrix([("UQSITE000000", "Laba chat"), ("ELEMENT0000", "Element Web")])
    chat_signouts.queue(db, 7, "child_chat_off")
    assert revoked(db) == {"UQSITE000000": "child_chat_off"}
    db.commit()
    chat_signouts.run_one(db, fake, 1, now=NOW, site_name="Laba")
    assert sorted(fake.deleted()) == ["ELEMENT0000", "UQSITE000000"]


# --- run_one ------------------------------------------------------------------------------------------------------------------

def test_all_deletes_everything_but_spared_and_devices_made_after_it(interim_off):
    db = make()
    device(db, "UQOLD0000000", created_ms=1000)
    device(db, "UQSPARED0000", created_ms=1000)
    db.commit()
    chat_signouts.queue(db, 7, "others", spared=("UQSPARED0000",), now_ms=5000)
    device(db, "UQNEW0000000", created_ms=10**15)          # minted after the sign-out
    db.commit()
    fake = FakeMatrix([("UQOLD0000000", "Laba chat"), ("UQSPARED0000", "Laba chat"), ("UQNEW0000000", "Laba chat"), ("APP0000000", "Element")])
    assert chat_signouts.run_one(db, fake, 1, now=NOW) == "done"
    assert sorted(fake.deleted()) == ["APP0000000", "UQOLD0000000"]
    assert rows(db)[0][4] == NOW


def test_a_device_revoked_after_the_sign_out_but_made_later_is_still_kept_only_while_not_revoked(interim_off):
    db = make()
    chat_signouts.queue(db, 7, "others", now_ms=5000)
    device(db, "UQNEW0000000", created_ms=10**15, revoked=NOW)
    db.commit()
    fake = FakeMatrix([("UQNEW0000000", "Laba chat")])
    chat_signouts.run_one(db, fake, 1, now=NOW)
    assert fake.deleted() == ["UQNEW0000000"]


def test_listed_deletes_those_ids():
    db = make()
    chat_signouts.queue(db, 7, "signed_out_here", "listed", device_ids=["A1", "GONE"])
    db.commit()
    fake = FakeMatrix([("A1", ""), ("B1", "")])
    chat_signouts.run_one(db, fake, 1, now=NOW)
    assert fake.deleted() == ["A1"]


def test_a_failure_backs_off_and_never_gives_up_and_a_later_run_lands_it():
    db = make()
    chat_signouts.queue(db, 7, "reset")
    db.commit()
    fake = FakeMatrix([("APP0000000", "Element")])
    fake.down = True
    waits = []
    for n in range(8):
        assert chat_signouts.run_one(db, fake, 1, now=NOW) == "failed"
        nxt = db.execute(text("SELECT next_try_utc, attempts, done_utc FROM chat_device_signouts")).first()
        waits.append(nxt[0] - NOW)
        assert nxt[1] == n + 1 and nxt[2] == 0
    assert waits == [60, 120, 300, 900, 3600, 3600, 3600, 3600]
    assert chat_signouts.run_due(db, fake, now=NOW) == {}, "not due yet"
    fake.down = False
    assert chat_signouts.run_due(db, fake, now=NOW + 3600) == {"done": 1}
    assert fake.deleted()[-1] == "APP0000000"


def test_running_twice_is_fine():
    db = make()
    chat_signouts.queue(db, 7, "reset")
    db.commit()
    fake = FakeMatrix([("APP0000000", "Element")])
    assert chat_signouts.run_one(db, fake, 1, now=NOW) == "done"
    assert chat_signouts.run_one(db, fake, 1, now=NOW) is None
    assert fake.deleted() == ["APP0000000"]


def test_an_account_the_chat_server_doesnt_know_is_done():
    db = make()
    chat_signouts.queue(db, 7, "reset")
    db.commit()
    fake = FakeMatrix()
    fake.unknown = True
    assert chat_signouts.run_one(db, fake, 1, now=NOW) == "no_user"
    assert rows(db)[0][4] == NOW and rows(db)[0][6] == "no_user"


def test_run_for_user_runs_outside_any_request(tmp_path, monkeypatch):
    """__main__.after_request spawns each g.after_commit with no app or request context: run_for_user makes its own."""
    from flask import Flask
    path = tmp_path / "t.db"
    db = make(path)
    chat_signouts.queue(db, 7, "reset")
    db.commit()
    db.close()
    engine = create_engine(f"sqlite:///{path}")
    fake = FakeMatrix([("APP0000000", "Element")])
    main = types.ModuleType("ruqqus.__main__")
    main.db_session = scoped_session(sessionmaker(bind=engine))
    monkeypatch.setitem(sys.modules, "ruqqus.__main__", main)
    matrix_module = types.ModuleType("ruqqus.helpers.matrix_client")
    for name in ("list_devices", "delete_device", "MatrixError", "UserUnknown"):
        setattr(matrix_module, name, getattr(fake, name))
    monkeypatch.setitem(sys.modules, "ruqqus.helpers.matrix_client", matrix_module)
    import ruqqus.helpers
    monkeypatch.setattr(ruqqus.helpers, "matrix_client", matrix_module, raising=False)
    chat_signouts.run_for_user(Flask("t"), 7)
    assert fake.deleted() == ["APP0000000"]
    check = Session(engine)
    assert check.execute(text("SELECT done_utc FROM chat_device_signouts")).scalar() > 0


def test_inside_a_request_the_run_waits_for_the_commit():
    from flask import Flask, g
    app = Flask("t")
    db = make()
    with app.test_request_context("/"):
        chat_signouts.queue(db, 7, "reset")
        chat_signouts.queue(db, 7, "reset")
        assert len(g.after_commit) == 1, "one run per account, after the commit"
        assert g.after_commit[0].func is chat_signouts.run_for_user and g.after_commit[0].args[1] == 7


def test_oidc_waits_for_a_pending_sign_out_and_is_blocked_while_a_fresh_one_fails():
    db = make()
    chat_signouts.queue(db, 7, "reset")
    db.commit()
    fake = FakeMatrix([("APP0000000", "Element")])
    fake.down = True
    db.execute(text("UPDATE chat_device_signouts SET created_utc = :n"), {"n": NOW - chat_signouts.OIDC_WAIT_SECONDS})
    db.commit()                                         # never the real clock: NOW is a fixed date
    assert chat_signouts.flush_for_oidc(db, fake, 7, now=NOW + 10) is True, "a row a day old no longer blocks"
    db.execute(text("UPDATE chat_device_signouts SET created_utc = :n"), {"n": NOW})
    db.commit()
    assert chat_signouts.flush_for_oidc(db, fake, 7, now=NOW + 10) is False
    fake.down = False
    assert chat_signouts.flush_for_oidc(db, fake, 7, now=NOW + 10) is True and fake.deleted()[-1] == "APP0000000"


def test_the_admin_summary_counts_and_flags_late_rows():
    db = make()
    chat_signouts.queue(db, 7, "reset")
    db.commit()
    db.execute(text("UPDATE chat_device_signouts SET created_utc = :n"), {"n": NOW - 7200})
    assert chat_signouts.pending_summary(db, now=NOW) == {"ready": True, "pending": 1, "late": 1}
    chat_signouts._ready.clear()                       # remembered per worker once true
    assert chat_signouts.pending_summary(make(with_tables=False), now=NOW)["ready"] is False


def test_retention_drops_finished_rows_after_thirty_days_and_never_a_pending_one():
    db = make()
    for done in (0, NOW - 40 * 86400, NOW - 10 * 86400):
        db.execute(text("""INSERT INTO chat_device_signouts (user_id, mxid, scope, spare_after_ms, reason, created_utc, next_try_utc, done_utc)
                           VALUES (7, :m, 'all', 0, 'reset', 0, 0, :d)"""), {"m": MX, "d": done})
    db.commit()
    assert retention.CHAT_SIGNOUT_DAYS == chat_signouts.KEEP_DAYS == 30
    retention.prune(db, NOW)
    assert sorted(d for (d,) in db.execute(text("SELECT done_utc FROM chat_device_signouts"))) == [0, NOW - 10 * 86400]


def test_a_newly_locked_account_is_told_apart():
    assert age_declaration.newly_locked(False, True)
    assert not age_declaration.newly_locked(True, True) and not age_declaration.newly_locked(False, False)
    assert not age_declaration.newly_locked(True, False)


# --- the token route's records (helpers/chat_tokens.py) -----------------------------------------------------------------------

def issue(db, fake, form, session="cookie-a", nonce=0):
    return chat_tokens.issue(db, fake, 7, MX, form, session, "https://chat.example", now=NOW, site_name="Laba", nonce=nonce)


def test_a_new_device_has_a_site_chosen_id_and_its_row_is_committed_before_synapse_is_asked(tmp_path):
    path = tmp_path / "t.db"
    db = make(path)
    other = Session(engine_for(path))
    fake = FakeMatrix()
    seen = []
    def look(d):                    # another connection: only what is committed is seen
        seen.append(other.execute(text("SELECT kind, sign_in, created_ms FROM chat_site_devices WHERE device_id = :d"), {"d": d}).first())
        other.rollback()
    fake.on_login = look
    status, body = issue(db, fake, {})
    assert status == 200 and chat_tokens.SITE_DEVICE.match(body["device_id"])
    assert seen[0][0] == "chat" and seen[0][1] == chat_signouts.sign_in_hash("cookie-a") and seen[0][2] > 0
    assert set(body) == {"homeserver_url", "user_id", "access_token", "device_id"}, "today's shape, never a refresh token"
    assert not chat_tokens.SITE_DEVICE.match("ABCDEFGHIJ"), "never one of Synapse's own ids"
    assert len({chat_tokens.new_device_id() for _ in range(50)}) == 50


def test_a_usable_device_on_synapse_is_taken_up_again_and_marked_as_this_sign_in():
    db = make()
    device(db, "UQAAAAAAAAAA", sign_in="old", used=1)
    db.commit()
    fake = FakeMatrix([("UQAAAAAAAAAA", "Laba chat")])
    status, body = issue(db, fake, {"device_id": "UQAAAAAAAAAA"})
    assert status == 200 and body["device_id"] == "UQAAAAAAAAAA"
    assert ("login", "UQAAAAAAAAAA") in fake.calls
    sign_in, used = db.execute(text("SELECT sign_in, last_used_utc FROM chat_site_devices")).first()
    assert sign_in == chat_signouts.sign_in_hash("cookie-a") and used == NOW


@pytest.mark.parametrize("revoked_utc, kind", [(NOW, "chat"), (0, "call")])
def test_a_revoked_device_or_another_kind_is_never_tokened_again(revoked_utc, kind):
    db = make()
    device(db, "UQAAAAAAAAAA", kind=kind, revoked=revoked_utc)
    db.commit()
    fake = FakeMatrix([("UQAAAAAAAAAA", "Laba chat")])
    status, body = issue(db, fake, {"device_id": "UQAAAAAAAAAA"})
    assert status == 200 and body["device_id"] != "UQAAAAAAAAAA"
    assert ("login", "UQAAAAAAAAAA") not in fake.calls


def test_another_accounts_device_or_an_app_is_never_served():
    db = make()
    device(db, "UQOTHER00000", user_id=8)
    db.commit()
    fake = FakeMatrix([("UQOTHER00000", "Laba chat"), ("ELEMENT0000", "Element Web")])
    for named in ("UQOTHER00000", "ELEMENT0000"):
        status, body = issue(db, fake, {"device_id": named})
        assert status == 200 and body["device_id"] != named
        assert ("login", named) not in fake.calls


def test_a_device_from_before_the_rows_is_taken_up_with_a_row_made_before_any_sign_out():
    db = make()
    fake = FakeMatrix([("OLDDEVICE0", "Laba chat")])
    status, body = issue(db, fake, {"device_id": "OLDDEVICE0"})
    assert status == 200 and body["device_id"] == "OLDDEVICE0"
    assert db.execute(text("SELECT kind, created_ms FROM chat_site_devices WHERE device_id = 'OLDDEVICE0'")).first() == ("chat", 0)


def test_a_device_from_before_the_rows_has_its_row_committed_before_it_is_tokened_again(tmp_path):
    """A row only the take-up's transaction could see is one a sign-out queued meanwhile neither revokes nor waits for: it would delete the
    device and the take-up's login would make it again, with a live token no sign-out would ever reach."""
    path = tmp_path / "t.db"
    db = make(path)
    other = Session(engine_for(path))
    fake = FakeMatrix([("OLDDEVICE0", "Laba chat")])
    seen = []

    def look(d):
        seen.append(other.execute(text("SELECT created_ms, revoked_utc FROM chat_site_devices WHERE device_id = :d"), {"d": d}).first())
        other.rollback()
    fake.on_login = look
    assert issue(db, fake, {"device_id": "OLDDEVICE0"})[1]["device_id"] == "OLDDEVICE0"
    assert seen == [(0, 0)], "committed, so a sign-out queued while Synapse logs it in revokes it and waits for the take-up"


def test_a_device_from_before_the_rows_is_given_its_row_under_the_mint_lock(monkeypatch):
    """The lock a sign-out takes before it reads which rows it covers: given first, so a sign-out is wholly before the row or after it."""
    db = make()
    held = []
    real = chat_signouts.lock_account

    def lock_account(*args):
        held.append(db.execute(text("SELECT count(*) FROM chat_site_devices WHERE device_id = 'OLDDEVICE0'")).scalar())
        return real(*args)
    monkeypatch.setattr(chat_signouts, "lock_account", lock_account)
    assert issue(db, FakeMatrix([("OLDDEVICE0", "Laba chat")]), {"device_id": "OLDDEVICE0"})[1]["device_id"] == "OLDDEVICE0"
    assert held == [0], "the lock is taken before the row is written, and the take-up itself takes no other"


@pytest.mark.parametrize("pending", [("signed_out_here", "listed"), ("deleted", "all"), ("child_chat_off", "all")])
def test_a_device_from_before_the_rows_that_a_pending_sign_out_covers_is_never_tokened_again(pending):
    """A sign-out queued before the device had a row didn't revoke it (there was none) and may be about to delete it: a new device is made."""
    db = make()
    reason, scope = pending
    chat_signouts.queue(db, 7, reason, scope, device_ids=["OLDDEVICE0"])
    db.commit()
    fake = FakeMatrix([("OLDDEVICE0", "Laba chat")])
    status, body = issue(db, fake, {"device_id": "OLDDEVICE0"})
    assert status == 200 and body["device_id"] != "OLDDEVICE0"
    assert ("login", "OLDDEVICE0") not in fake.calls
    assert db.execute(text("SELECT count(*) FROM chat_site_devices WHERE device_id = 'OLDDEVICE0'")).scalar() == 0


def test_a_device_from_before_the_rows_spared_by_a_pending_interim_sign_out_is_still_taken_up():
    db = make()
    chat_signouts.queue(db, 7, "password")              # the interim: chat apps only
    db.commit()
    assert issue(db, FakeMatrix([("OLDDEVICE0", "Laba chat")]), {"device_id": "OLDDEVICE0"})[1]["device_id"] == "OLDDEVICE0"


def test_a_device_from_before_the_rows_deleted_by_a_sign_out_that_finished_meanwhile_is_never_tokened_again():
    """The device is listed again once its row is held: a sign-out that ran to the end between the first list and the row deleted it."""
    db = make()
    fake = FakeMatrix([("OLDDEVICE0", "Laba chat")])
    lists = []

    def finished_meanwhile():
        lists.append(True)
        if len(lists) == 2:
            fake.devices.pop("OLDDEVICE0", None)
    fake.on_list = finished_meanwhile
    status, body = issue(db, fake, {"device_id": "OLDDEVICE0"})
    assert status == 200 and body["device_id"] != "OLDDEVICE0"
    assert ("login", "OLDDEVICE0") not in fake.calls and revoked(db) == {"OLDDEVICE0": "deleted_elsewhere"}


def test_pending_covers_reads_scope_and_spared():
    db = make()
    assert not chat_signouts.pending_covers(db, 7, "OLDDEVICE0")
    chat_signouts.queue(db, 7, "x", "listed", device_ids=["OTHER00000"])
    chat_signouts.queue(db, 7, "others", spared=("OLDDEVICE0",))
    assert not chat_signouts.pending_covers(db, 7, "OLDDEVICE0")
    db.execute(text("UPDATE chat_device_signouts SET done_utc = 1"))
    chat_signouts.queue(db, 7, "deleted")
    assert chat_signouts.pending_covers(db, 7, "OLDDEVICE0")
    db.execute(text("UPDATE chat_device_signouts SET done_utc = 1"))
    assert not chat_signouts.pending_covers(db, 7, "OLDDEVICE0"), "a finished one deleted it already: the list says so"


def test_a_device_signed_out_on_synapse_is_revoked_and_replaced():
    db = make()
    device(db, "UQAAAAAAAAAA")
    db.commit()
    fake = FakeMatrix()
    status, body = issue(db, fake, {"device_id": "UQAAAAAAAAAA"})
    assert status == 200 and body["device_id"] != "UQAAAAAAAAAA"
    assert revoked(db) == {"UQAAAAAAAAAA": "deleted_elsewhere"}


def test_a_mint_revoked_while_synapse_made_it_answers_retry_and_queues_its_sign_out(interim_off):
    db = make()
    fake = FakeMatrix()
    fake.on_login = lambda d: (chat_signouts.queue(db, 7, "others"), db.commit())
    status, body = issue(db, fake, {})
    assert status == 503 and body == {"retry": True}
    assert [r[:4] for r in rows(db)][-1][0] == "listed" and rows(db)[-1][3] == "revoked_while_made"


def test_a_mint_synapse_refused_is_revoked_and_queued():
    db = make()
    fake = FakeMatrix()
    fake.down = True
    status, body = issue(db, fake, {})
    assert status == 503 and body == {"retry": True}
    made = db.execute(text("SELECT device_id, revoked_reason FROM chat_site_devices")).first()
    assert made[1] == "mint_failed" and rows(db)[0][:2] == ("listed", made[0])


def test_a_failed_mint_takes_the_mint_lock_before_it_touches_the_row(monkeypatch):
    """The order every writer keeps, the lock and then rows: a revocation before the lock would hold the row while waiting for a sign-out
    that holds the lock and waits for the row (a deadlock, which could lose a child's chat-off sign-out)."""
    db = make()
    fake = FakeMatrix()
    fake.down = True
    events = []
    real_lock, real_revoke = chat_signouts.lock_account, chat_signouts.revoke_rows
    monkeypatch.setattr(chat_signouts, "lock_account", lambda *a: (events.append("lock"), real_lock(*a))[1])
    monkeypatch.setattr(chat_signouts, "revoke_rows", lambda *a, **k: (events.append("revoke"), real_revoke(*a, **k))[1])
    fake.on_login = lambda d: events.append("login")
    assert issue(db, fake, {}) == (503, chat_tokens.RETRY)
    after = events[events.index("login") + 1:]
    assert after and after[0] == "lock" and "revoke" in after, events
    assert revoked(db) and list(revoked(db).values()) == ["mint_failed"]


@pytest.mark.parametrize("change, status", [
    ("UPDATE users SET login_nonce = 1", 401),                                          # Log out everywhere, a password change...
    ("UPDATE users SET is_deleted = 1", 401),                                           # the account deleted
    ("UPDATE users SET age_declared = 2", 403),                                         # newly child-locked
    ("UPDATE users SET age_declared = 2, age_lock = 2", 403),                           # a sub-account whose parent switched chat off
])
def test_a_request_that_waited_for_a_sign_out_mints_nothing(change, status, monkeypatch):
    """The wrappers read the account when the request began; the mint reads it again under the lock, after whatever it waited for."""
    db = make()
    fake = FakeMatrix()
    real_lock = chat_signouts.lock_account

    def committed_meanwhile(*args):                  # what the request waited for on the lock, committed before it got it
        db.execute(text(change))
        db.commit()
        return real_lock(*args)
    monkeypatch.setattr(chat_signouts, "lock_account", committed_meanwhile)
    answer = issue(db, fake, {})
    assert answer[0] == status and ("age_locked" in answer[1]) == (status == 403)
    assert db.execute(text("SELECT count(*) FROM chat_site_devices")).scalar() == 0 and fake.calls == []


def test_a_sub_account_with_chat_switched_on_still_gets_its_device():
    db = make()
    db.execute(text("UPDATE users SET age_declared = 2, age_lock = 2"))
    db.execute(text("INSERT INTO child_controls (child_id, chat) VALUES (7, 1)"))
    db.commit()
    assert issue(db, FakeMatrix(), {})[0] == 200
    db.execute(text("UPDATE child_controls SET chat = 0"))
    db.commit()
    assert issue(db, FakeMatrix(), {}) == (403, {"age_locked": True, "parent": True})


def test_a_device_is_not_taken_up_again_once_the_sign_in_has_ended():
    db = make()
    device(db, "UQAAAAAAAAAA")
    db.execute(text("UPDATE users SET login_nonce = 3"))
    db.commit()
    fake = FakeMatrix([("UQAAAAAAAAAA", "Laba chat")])
    assert issue(db, fake, {"device_id": "UQAAAAAAAAAA"}, nonce=2) == (401, chat_tokens.SIGNED_OUT)
    assert fake.calls == [] and db.execute(text("SELECT last_used_utc FROM chat_site_devices")).scalar() == NOW - 100
    assert issue(db, fake, {"device_id": "UQAAAAAAAAAA"}, nonce=3)[1]["device_id"] == "UQAAAAAAAAAA"


def test_an_old_device_signed_out_in_settings_meanwhile_is_never_tokened_again():
    """Settings > Chat apps gives a site device from before the rows a revoked row before Synapse deletes it; a token request that listed
    the device just before reads that row after its own INSERT does nothing, and makes a new device."""
    db = make()
    fake = FakeMatrix([("OLDDEVICE0", "Laba chat")])
    fake.on_list = lambda: chat_signouts.revoke_device(db, 7, "OLDDEVICE0", "signed_out_here", NOW)
    status, body = issue(db, fake, {"device_id": "OLDDEVICE0"})
    assert status == 200 and body["device_id"] != "OLDDEVICE0"
    assert ("login", "OLDDEVICE0") not in fake.calls and revoked(db) == {"OLDDEVICE0": "signed_out_here"}


def test_revoke_device_revokes_a_row_and_gives_one_to_a_device_without():
    db = make()
    device(db, "UQAAAAAAAAAA")
    assert chat_signouts.revoke_device(db, 7, "UQAAAAAAAAAA", "signed_out_here", NOW) == 1
    assert chat_signouts.revoke_device(db, 7, "OLDDEVICE0", "signed_out_here", NOW) == 0
    assert revoked(db) == {"UQAAAAAAAAAA": "signed_out_here", "OLDDEVICE0": "signed_out_here"}


def test_bad_fields_are_refused_and_absent_ones_take_todays_meaning():
    db = make()
    fake = FakeMatrix()
    assert issue(db, fake, {"kind": "video"})[0] == 400
    assert issue(db, fake, {"device_id": "a,b"})[0] == 400
    assert fake.calls == []
    status, body = issue(db, fake, {"formkey": "x"})                 # exactly what today's token.js posts
    assert status == 200 and db.execute(text("SELECT kind FROM chat_site_devices")).scalar() == "chat"


def test_the_tables_absent_the_route_is_told_before_anything():
    assert chat_signouts.tables_ready(make(with_tables=False)) is False
    assert chat_signouts.tables_ready(make()) is True


# --- scripts/queue_chat_signouts.py -------------------------------------------------------------------------------------------

def test_the_reset_script_refuses_a_database_that_isnt_a_scratch_one():
    spec = importlib.util.spec_from_file_location("queue_chat_signouts", ROOT / "scripts" / "queue_chat_signouts.py")
    script = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(script)
    assert script.scratch("ci_site") and script.scratch("test_x") and script.scratch("ui_preview")
    assert not script.scratch("postgres") and not script.scratch("ruqqus") and not script.scratch("")
    with pytest.raises(SystemExit):
        script.main(["--reason=legacy", "--once"])              # --all must be said
    with pytest.raises(SystemExit):
        script.main(["--all", "--once", "--reason=Bad Reason"])


def test_the_reset_script_commits_each_account_so_it_never_holds_more_than_one_mint_lock(tmp_path, monkeypatch):
    """Each queue takes the account's mint lock (a transaction-level advisory lock) until the commit: one transaction for every account
    runs out of Postgres's shared lock table on a large site, and rolls the whole run back."""
    spec = importlib.util.spec_from_file_location("queue_chat_signouts", ROOT / "scripts" / "queue_chat_signouts.py")
    script = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(script)
    db = make(tmp_path / "t.db")
    for uid in range(8, 13):
        db.execute(text("INSERT INTO chat_identities VALUES (:u, :m)"), {"u": uid, "m": f"@ruqqus_{uid}:x"})
    db.commit()
    held, most = [0], [0]
    real_lock = chat_signouts.lock_account

    def lock(*args):
        held[0] += 1
        most[0] = max(most[0], held[0])
        return real_lock(*args)
    monkeypatch.setattr(chat_signouts, "lock_account", lock)
    real_commit = db.commit

    def commit():                                    # what ends the transaction, and with it every advisory lock it took
        held[0] = 0
        return real_commit()
    monkeypatch.setattr(db, "commit", commit)
    said = []
    assert script.run("legacy", confirm=True, db=db, matrix=FakeMatrix(), out=said.append) == 0
    assert db.execute(text("SELECT count(*) FROM chat_device_signouts")).scalar() == 6
    assert most[0] == 1, f"{most[0]} mint locks held at once"
