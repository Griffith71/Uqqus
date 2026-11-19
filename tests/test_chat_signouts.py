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
           "age_lock INTEGER, quarantined_utc INTEGER DEFAULT 0, admin_level INTEGER DEFAULT 0, is_bot INTEGER DEFAULT 0)",
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

    class RefreshGone(MatrixError):
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
        token = {"user_id": mxid, "device_id": device_id, "access_token": "syt_x"}
        return dict(token, refresh_token="never", expires_in_ms=3_600_000)      # always one (build step 5)

    def deleted(self):
        return [c[1] for c in self.calls if c[0] == "delete"]


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


def test_queue_revokes_the_site_rows_it_covers_in_the_same_transaction_never_the_spared(monkeypatch):
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


def test_listed_revokes_those_ids_only():
    db = make()
    device(db, "UQAAAAAAAAAA")
    device(db, "UQBBBBBBBBBB")
    chat_signouts.queue(db, 7, "signed_out_here", "listed", device_ids=["UQAAAAAAAAAA", "bad,id"])
    assert revoked(db) == {"UQAAAAAAAAAA": "signed_out_here"}
    assert rows(db)[0][:3] == ("listed", "UQAAAAAAAAAA", None), "an id that can't be a device id is never written"


def test_revoke_spares_at_most_one_chat_and_one_call_row_of_this_sign_in():
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


def test_this_sign_ins_devices_are_found_under_the_mint_lock(monkeypatch):
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


def test_events_that_spare_nothing_spare_nothing():
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


# --- the interim is over (build step 4) ------------------------------------------------------------------------------------

def step2_row(db, reason="reset"):
    """A row as build steps 2 and 3 queued it: an `all` sign-out with the word `site` in `spared` (chat apps only)."""
    db.execute(text("""INSERT INTO chat_device_signouts (user_id, mxid, scope, spared, spare_after_ms, reason, created_utc, next_try_utc)
                       VALUES (7, '@ruqqus_7:s', 'all', 'site', 5000, :r, :now, :now)"""), {"r": reason, "now": NOW})
    db.commit()


def test_no_event_writes_the_interims_word_any_more():
    assert not hasattr(chat_signouts, "KEEP_SITE_BROWSERS") and not hasattr(chat_signouts, "interim_spared")
    for reason in ("password", "others", "mfa", "reset", "mfa_recovery", "child_password", "child_locked", "child_chat_off",
                   "child_stopped", "deleted"):
        db = make()
        device(db, "UQSITE000000")
        db.commit()
        chat_signouts.queue(db, 7, reason, spared=("site",))          # even asked for, the word never goes in
        assert rows(db)[0][2] is None, reason
        assert revoked(db) == {"UQSITE000000": reason}, reason


def test_every_credential_event_now_reaches_the_site_browsers_but_spares_this_sign_ins_two():
    """Decision 2 as built: of this sign-in's rows, the most recently used chat and call devices stay; every other site device goes."""
    db = make()
    me = chat_signouts.sign_in_hash("sess-1")
    device(db, "UQMYCHAT0000", sign_in=me, used=NOW - 1)
    device(db, "UQMYOLDCHAT0", sign_in=me, used=NOW - 50)
    device(db, "UQMYCALL0000", kind="call", sign_in=me, used=NOW - 1)
    device(db, "UQELSEWHERE0", sign_in=chat_signouts.sign_in_hash("sess-2"))
    db.commit()
    account_sessions.revoke(db, SimpleNamespace(id=7, login_nonce=0), "password", session={"session_id": "sess-1"},
                            spare_this_sign_in=True)
    assert set(rows(db)[0][2].split(",")) == {"UQMYCHAT0000", "UQMYCALL0000"}
    assert revoked(db) == {"UQMYOLDCHAT0": "password", "UQELSEWHERE0": "password"}
    db.commit()
    fake = FakeMatrix([(d, "Laba chat") for d in ("UQMYCHAT0000", "UQMYOLDCHAT0", "UQMYCALL0000", "UQELSEWHERE0")] + [("APP0000000", "Element")])
    assert chat_signouts.run_one(db, fake, 1, now=NOW, site_name="Laba") == "done"
    assert sorted(fake.deleted()) == ["APP0000000", "UQELSEWHERE0", "UQMYOLDCHAT0"]


def test_a_reset_reaches_every_site_browser():
    db = make()
    device(db, "UQSITE000000", sign_in=chat_signouts.sign_in_hash("sess-1"))
    db.commit()
    account_sessions.revoke(db, SimpleNamespace(id=7, login_nonce=0), "reset", session={"session_id": "sess-1"})
    assert revoked(db) == {"UQSITE000000": "reset"} and rows(db)[0][2] is None
    db.commit()
    fake = FakeMatrix([("UQSITE000000", "Laba chat"), ("OLDNAMED000", "Laba chat"), ("ELEMENT0000", "Element Web")])
    chat_signouts.run_one(db, fake, 1, now=NOW, site_name="Laba")
    assert sorted(fake.deleted()) == ["ELEMENT0000", "OLDNAMED000", "UQSITE000000"]


def test_a_row_queued_in_the_interim_still_finishes_apps_only():
    """A row build steps 2 and 3 wrote keeps its meaning: a site row, revoked or not, or the site's name keeps a device."""
    db = make()
    device(db, "UQRENAMED000")
    device(db, "UQREVOKED000", revoked=NOW - 5)
    db.commit()
    step2_row(db)
    fake = FakeMatrix([("UQRENAMED000", "Element Web"), ("UQREVOKED000", "Firefox"), ("OLDNAMED000", "Laba chat"),
                       ("ELEMENT0000", "Element Web")])
    assert chat_signouts.run_one(db, fake, 1, now=NOW, site_name="Laba") == "done"
    assert fake.deleted() == ["ELEMENT0000"]


# --- run_one ------------------------------------------------------------------------------------------------------------------

def test_all_deletes_everything_but_spared_and_devices_made_after_it():
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


def test_a_device_revoked_after_the_sign_out_but_made_later_is_still_kept_only_while_not_revoked():
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
    # the refresh token every login answers with is sealed by a stand-in: secret_box needs `cryptography`, which these tests go without
    return chat_tokens.issue(db, fake, 7, MX, form, session, "https://chat.example", now=NOW, site_name="Laba", nonce=nonce, secret="s",
                             sealer=(lambda t: "sealed:" + t, lambda s: s[len("sealed:"):]))


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
    assert {"homeserver_url", "user_id", "access_token", "device_id"} <= set(body), "today's shape"
    assert not {k for k in body if "refresh" in k}, "never a refresh token"
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
    step2_row(db, "password")                           # queued in the interim: chat apps only
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


def test_a_mint_revoked_while_synapse_made_it_answers_retry_and_queues_its_sign_out():
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


def test_a_failed_mints_sign_out_waits_for_a_device_synapse_makes_after_the_route_gave_up():
    """Synapse doesn't cancel a /login whose client timed out: the device can appear after the token route queued its sign-out. The
    sign-out is not called done because the device isn't listed yet until MINT_GRACE_SECONDS after it was queued; a later pass then
    deletes the device. Without the wait the row was done at once and the device stayed for good (a '<site> chat' row nothing removes)."""
    db = make()
    fake = FakeMatrix()
    fake.down = True
    assert issue(db, fake, {}) == (503, chat_tokens.RETRY)
    made = db.execute(text("SELECT device_id FROM chat_site_devices")).scalar()
    fake.down = False
    created = db.execute(text("SELECT created_utc FROM chat_device_signouts")).scalar()
    assert chat_signouts.run_one(db, fake, 1, now=created + 1) == "waiting", "not on Synapse yet: not done"
    assert rows(db)[0][4] == 0 and rows(db)[0][6] == "waiting"
    assert db.execute(text("SELECT next_try_utc FROM chat_device_signouts")).scalar() == created + chat_signouts.MINT_GRACE_SECONDS
    assert chat_signouts.pending_covers(db, 7, made)
    fake.devices[made] = "Laba chat"                       # Synapse finished the login nobody waited for
    assert chat_signouts.run_due(db, fake, now=created + 2) == {}, "not due before the wait is over"
    assert chat_signouts.run_due(db, fake, now=created + chat_signouts.MINT_GRACE_SECONDS) == {"done": 1}
    assert fake.deleted() == [made] and made not in fake.devices


def test_a_failed_mints_sign_out_is_done_once_its_device_was_found_or_the_wait_is_over():
    db = make()
    fake = FakeMatrix()
    fake.down = True
    issue(db, fake, {})
    made = db.execute(text("SELECT device_id FROM chat_site_devices")).scalar()
    fake.down = False
    created = db.execute(text("SELECT created_utc FROM chat_device_signouts")).scalar()
    fake.devices[made] = "Laba chat"
    assert chat_signouts.run_one(db, fake, 1, now=created) == "done", "made already: deleted and done at once"
    assert fake.deleted() == [made]
    chat_signouts.queue(db, 7, "mint_failed", "listed", device_ids=["UQNEVER00000"])
    db.commit()
    late = db.execute(text("SELECT created_utc FROM chat_device_signouts WHERE id = 2")).scalar()
    assert chat_signouts.run_one(db, fake, 2, now=late + chat_signouts.MINT_GRACE_SECONDS) == "done", "never made: done after the wait"


def test_only_a_failed_mints_sign_out_waits_for_its_device():
    """A device signed out from Settings > Chat apps, replaced, or found revoked while Synapse made it (its login answered) is either on
    Synapse now or gone: done at once, as before."""
    db = make()
    for reason in ("signed_out_here", "store_unopenable", "revoked_while_made"):
        chat_signouts.queue(db, 7, reason, "listed", device_ids=["UQGONE000000"])
    db.commit()
    created = db.execute(text("SELECT max(created_utc) FROM chat_device_signouts")).scalar()
    assert [chat_signouts.run_one(db, FakeMatrix(), i, now=created) for i in (1, 2, 3)] == ["done"] * 3


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
    ("UPDATE users SET quarantined_utc = 1700000000", 403),                             # put on hold: a held account has no chat
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
    assert answer[0] == status and ("age_locked" in answer[1] or "held" in answer[1]) == (status == 403)
    assert db.execute(text("SELECT count(*) FROM chat_site_devices")).scalar() == 0 and fake.calls == []


@pytest.mark.parametrize("who", ["UPDATE users SET quarantined_utc = 1700000000, admin_level = 3", "UPDATE users SET quarantined_utc = 1700000000, is_bot = 1"])
def test_an_admin_or_a_bot_is_never_held_so_still_gets_its_device(who):
    """`User.is_quarantined` is false for an admin or a bot account: the mint reads the hold the same way."""
    db = make()
    db.execute(text(who))
    db.commit()
    assert issue(db, FakeMatrix(), {})[0] == 200


@pytest.mark.parametrize("change, status", [
    ("UPDATE users SET login_nonce = 1", 401),
    ("UPDATE users SET is_deleted = 1", 401),
    ("UPDATE users SET age_declared = 2, age_lock = 2", 403),
])
def test_a_device_from_before_the_rows_is_given_no_row_once_the_account_may_no_longer_chat(change, status, monkeypatch):
    """The device was listed, then the account was deleted (its sign-out may even have finished, so no pending one covers the device),
    a sign-in ended, or a parent switched chat off, all committed before the take-up had the lock. The row is never written: the account
    is read again under the lock before it, as for a new device. It used to be committed first and refused after, leaving a deleted
    account a live row."""
    db = make()
    fake = FakeMatrix([("OLDDEVICE0", "Laba chat")])
    real_lock = chat_signouts.lock_account

    def committed_meanwhile(*args):
        db.execute(text(change))
        db.commit()
        return real_lock(*args)
    monkeypatch.setattr(chat_signouts, "lock_account", committed_meanwhile)
    answer = issue(db, fake, {"device_id": "OLDDEVICE0"})
    assert answer[0] == status
    assert db.execute(text("SELECT count(*) FROM chat_site_devices")).scalar() == 0
    assert ("login", "OLDDEVICE0") not in fake.calls


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


# --- stale site devices (build step 7: prune_stale, a step of scripts/recomputes.py) ---------------------------------------------

OLD = NOW - 91 * 86400                       # past STALE_DAYS
RECENT = NOW - 10 * 86400


def account(db, user_id):
    db.execute(text("INSERT INTO chat_identities VALUES (:u, :m)"), {"u": user_id, "m": f"@ruqqus_{user_id}:x"})
    db.execute(text("INSERT INTO users (id) VALUES (:u)"), {"u": user_id})


def live(db):
    return {d for (d,) in db.execute(text("SELECT device_id FROM chat_site_devices WHERE revoked_utc = 0"))}


def test_the_stale_rule_is_ninety_days_and_two_hundred_a_cycle():
    assert chat_signouts.STALE_DAYS == 90 and chat_signouts.PRUNE_LIMIT == 200 and chat_signouts.STALE == "stale"


def test_a_device_unused_for_ninety_days_goes_by_a_listed_stale_sign_out_and_synapse_deletes_it_when_the_queue_runs():
    db = make()
    device(db, "UQOLDCHAT000", used=OLD, created_ms=1)
    device(db, "UQOLDCALL000", kind="call", used=OLD, created_ms=2)
    device(db, "UQNEWCHAT000", used=RECENT, created_ms=3)
    device(db, "UQ89DAYS0000", used=NOW - 89 * 86400, created_ms=4)
    db.commit()
    assert chat_signouts.prune_stale(db, NOW) == 2
    assert revoked(db) == {"UQOLDCHAT000": "stale", "UQOLDCALL000": "stale"}
    assert live(db) == {"UQNEWCHAT000", "UQ89DAYS0000"}, "used within 90 days: kept"
    assert [(r[0], r[1], r[3]) for r in rows(db)] == [("listed", "UQOLDCALL000", "stale"), ("listed", "UQOLDCHAT000", "stale")], "one row each, oldest (then by id) first"
    fake = FakeMatrix([(d, "Laba chat") for d in ("UQOLDCHAT000", "UQOLDCALL000", "UQNEWCHAT000", "UQ89DAYS0000")] + [("ELEMENTAPP1", "Element")])
    assert chat_signouts.run_due(db, fake, now=NOW) == {"done": 2}
    assert set(fake.devices) == {"UQNEWCHAT000", "UQ89DAYS0000", "ELEMENTAPP1"}, "an outside app is never touched"
    assert chat_signouts.prune_stale(db, NOW) == 0, "nothing more, and nothing twice"


def test_the_most_recently_used_chat_row_is_never_pruned_however_old():
    db = make()
    device(db, "UQONLYCHAT00", used=OLD - 500 * 86400)
    db.commit()
    assert chat_signouts.prune_stale(db, NOW) == 0 and live(db) == {"UQONLYCHAT00"}, "the account's only chat device stays"
    device(db, "UQOLDER00000", used=OLD - 600 * 86400)
    device(db, "UQOLDCALL000", kind="call", used=OLD - 400 * 86400)          # a newer call row protects no chat row, and isn't kept
    db.commit()
    assert chat_signouts.prune_stale(db, NOW) == 2
    assert live(db) == {"UQONLYCHAT00"} and set(revoked(db)) == {"UQOLDER00000", "UQOLDCALL000"}


def test_a_revoked_chat_row_never_counts_as_the_most_recent_and_ties_keep_one():
    db = make()
    device(db, "UQREVOKED000", used=NOW - 1, revoked=NOW - 1)                   # signed out already: protects nothing
    device(db, "UQTIEA000000", used=OLD, created_ms=5)
    device(db, "UQTIEB000000", used=OLD, created_ms=5)
    db.commit()
    assert chat_signouts.prune_stale(db, NOW) == 1
    assert live(db) == {"UQTIEB000000"}, "two rows used at the same moment: exactly one is kept (the later id)"
    device(db, "UQMADELATER0", used=OLD - 1, created_ms=9)
    device(db, "UQMADEFIRST0", used=OLD - 1, created_ms=8)
    db.execute(text("UPDATE chat_site_devices SET revoked_utc = 1 WHERE device_id = 'UQTIEB000000'"))
    db.execute(text("UPDATE chat_device_signouts SET done_utc = 1"))                # TIEA's sign-out has run
    db.commit()
    assert chat_signouts.prune_stale(db, NOW) == 1
    assert live(db) == {"UQMADELATER0"}, "used at the same moment: the one made later is kept"


def test_only_call_rows_left_all_go():
    db = make()
    device(db, "UQCALLONLY00", kind="call", used=OLD)
    db.commit()
    assert chat_signouts.prune_stale(db, NOW) == 1 and revoked(db) == {"UQCALLONLY00": "stale"}


def test_an_account_with_a_sign_out_still_pending_is_left_for_a_later_cycle():
    db = make()
    device(db, "UQOLDCHAT000", used=OLD)
    device(db, "UQNEWCHAT000", used=RECENT)
    db.commit()
    chat_signouts.queue(db, 7, "password", "listed", device_ids=["UQSOMETHING0"], mxid=MX, now_ms=NOW * 1000)
    db.commit()
    assert chat_signouts.prune_stale(db, NOW) == 0 and "UQOLDCHAT000" in live(db)
    db.execute(text("UPDATE chat_device_signouts SET done_utc = :n"), {"n": NOW})
    db.commit()
    assert chat_signouts.prune_stale(db, NOW) == 1 and revoked(db) == {"UQOLDCHAT000": "stale"}


def test_each_account_is_read_again_under_its_lock(monkeypatch):
    """A candidate chosen without a lock is pruned only if it still is one once the account's mint lock is held: a sign-out queued
    meanwhile leaves the account, a device used meanwhile is no longer stale, and a newer chat row signed out meanwhile makes this one the
    most recent (kept)."""
    for change, kept in [("pending", {"UQOLDCHAT000", "UQNEWCHAT000"}),
                         ("used", {"UQOLDCHAT000", "UQNEWCHAT000"}),
                         ("newer_gone", {"UQOLDCHAT000"})]:
        db = make()
        device(db, "UQOLDCHAT000", used=OLD)
        device(db, "UQNEWCHAT000", used=RECENT)
        db.commit()
        real_lock = chat_signouts.lock_account

        def meanwhile(*args, change=change, db=db, real_lock=real_lock):
            if change == "pending":
                db.execute(text("INSERT INTO chat_device_signouts (user_id, mxid, scope, spare_after_ms, reason, created_utc, next_try_utc) "
                                "VALUES (7, :m, 'all', 0, 'others', :n, :n)"), {"m": MX, "n": NOW})
            elif change == "used":
                db.execute(text("UPDATE chat_site_devices SET last_used_utc = :n WHERE device_id = 'UQOLDCHAT000'"), {"n": RECENT - 1})
            else:
                db.execute(text("UPDATE chat_site_devices SET revoked_utc = :n, revoked_reason = 'password' "
                                "WHERE device_id = 'UQNEWCHAT000'"), {"n": NOW})
            db.commit()
            monkeypatch.setattr(chat_signouts, "lock_account", real_lock)
            return real_lock(*args)
        monkeypatch.setattr(chat_signouts, "lock_account", meanwhile)
        assert chat_signouts.prune_stale(db, NOW) == 0, change
        assert live(db) == kept, change
        monkeypatch.setattr(chat_signouts, "lock_account", real_lock)


def test_oldest_first_across_accounts_and_at_most_the_limit_a_cycle():
    db = make()
    for uid in (8, 9):
        account(db, uid)
    for uid, did, used in [(7, "UQSEVEN00000", OLD - 30), (8, "UQEIGHT00000", OLD - 50), (9, "UQNINE000000", OLD - 10),
                           (8, "UQEIGHTB0000", OLD - 20)]:
        device(db, did, kind="call", used=used, user_id=uid)
    db.commit()
    assert chat_signouts.prune_stale(db, NOW, limit=2) == 2
    assert set(revoked(db)) == {"UQEIGHT00000", "UQSEVEN00000"}, "the two oldest"
    assert chat_signouts.prune_stale(db, NOW, limit=2) == 1, "account 8 has a sign-out pending now: only 9's goes"
    assert chat_signouts.run_due(db, FakeMatrix(), now=NOW) == {"done": 3}
    assert chat_signouts.prune_stale(db, NOW, limit=2) == 1
    assert set(revoked(db)) == {"UQEIGHT00000", "UQSEVEN00000", "UQEIGHTB0000", "UQNINE000000"}
    order = [r[0] for r in db.execute(text("SELECT device_ids FROM chat_device_signouts ORDER BY id"))]
    assert order == ["UQEIGHT00000", "UQSEVEN00000", "UQNINE000000", "UQEIGHTB0000"]


@pytest.mark.parametrize("blocker", ["only_chat_row", "pending", "not_stale_yet"])
def test_what_may_never_go_is_never_chosen_so_it_never_uses_up_the_cycles_limit(blocker):
    """The choice itself leaves out the most recent chat row, accounts with a pending sign-out and devices used within 90 days (the
    recheck under the lock would keep them anyway): one of them, older than everything else, never takes a cycle's only place."""
    db = make()
    account(db, 8)
    used = {"only_chat_row": OLD - 900, "pending": OLD - 900, "not_stale_yet": NOW - 89 * 86400}[blocker]
    device(db, "UQSEVEN00000", used=used, kind="call" if blocker == "pending" else "chat")
    if blocker == "not_stale_yet":
        device(db, "UQSEVENNEW00", used=NOW)
    if blocker == "pending":
        chat_signouts.queue(db, 7, "password", "listed", device_ids=["UQGONE000000"], mxid=MX, now_ms=NOW * 1000)
    device(db, "UQEIGHT00000", kind="call", used=OLD, user_id=8)
    db.commit()
    assert chat_signouts.prune_stale(db, NOW, limit=1) == 1
    assert revoked(db).get("UQEIGHT00000") == "stale" and "UQSEVEN00000" in live(db)


def test_a_pruned_device_is_told_to_its_browser_as_stale():
    """The browser that held it names it at its next chat page: a new device, `previous.reason` "stale" (lines.js has the sentence); a
    running page's renewal answers 409 with the same reason."""
    db = make()
    device(db, "UQOLDCHAT000", used=OLD)
    device(db, "UQNEWCHAT000", used=RECENT)
    db.commit()
    chat_signouts.prune_stale(db, NOW)
    assert issue(db, FakeMatrix(), {"mode": "renew", "device_id": "UQOLDCHAT000"}) == (409, {"device_gone": True, "reason": "stale"})
    status, body = issue(db, FakeMatrix(), {"mode": "boot", "device_id": "UQOLDCHAT000"})
    assert status == 200 and body["previous"] == {"device_id": "UQOLDCHAT000", "reason": "stale"} and body["new_device"] is True


def test_without_the_tables_pruning_is_a_logged_no_op(caplog):
    db = make(with_tables=False)
    with caplog.at_level(logging.WARNING):
        assert chat_signouts.prune_stale(db, NOW) is None
    assert "stale chat devices not pruned: migration missing" in caplog.text


def test_the_cycle_prunes_after_the_sign_outs_in_its_own_step():
    source = (ROOT / "scripts" / "recomputes.py").read_text()
    due, prune = source.index("chat_signouts.run_due(db, signout_matrix)"), source.index("chat_signouts.prune_stale(db")
    assert due < prune and source.index("chat health") < due
    step = source[source.rindex("try:", 0, prune):source.index("except Exception", prune)]
    assert "chat_signouts.run_due" not in step, "in its own try"


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
    # it says first which database it opened, before it refuses or acts
    assert said[0].startswith("Database: ")


def test_the_reset_is_documented_with_the_variable_the_app_really_connects_by():
    """The app opens DATABASE_CONNECTION_POOL_URL before DATABASE_URL (ruqqus/__main__.py) and docker-compose.yml sets both: a second run
    with only DATABASE_URL naming ui_preview still opened postgres, so ui_preview was never reset."""
    runbook = (ROOT / "RESTORE.md").read_text(encoding="utf-8")
    reset = runbook[runbook.index("queue_chat_signouts.py --all --reason=legacy --once"):runbook.index("## Before the site goes live")]
    assert "DATABASE_CONNECTION_POOL_URL=postgresql://postgres@postgres:5432/ui_preview" in reset
    assert "with `DATABASE_URL` pointing at it" not in reset
    # and a MASTER_KEY rotation keeps the store keys only while CHAT_STORE_SECRET is set
    rotation = runbook[runbook.index("A new `MASTER_KEY` also makes the sealed refresh tokens"):]
    flat = " ".join(rotation.split())
    assert "keeps its store key if `CHAT_STORE_SECRET` didn't change" not in flat
    assert "only if `CHAT_STORE_SECRET` is set and didn't change" in flat
    # build step 5: the sealed refresh tokens are in use, and there is no flag to turn off
    assert "CHAT_EXPIRING_TOKENS" not in runbook
    assert "refreshable_access_token_lifetime: 1h" in rotation
    # a re-token leaves the old chains working on Synapse for ever (a login on a device never ends its earlier refresh tokens, and
    # refresh_token_lifetime is unset), so after a leak the rotation must be followed by signing every device out: the runbook
    # once said it needed nothing
    master = " ".join(rotation[:rotation.index("### Chat token lifetime")].split())
    assert "Nothing is lost and no member does anything" not in master
    assert "queue_chat_signouts.py --all --reason=master_key_rotated --once --confirm" in master
    assert "does not end the old ones" in master
    # an older pair restored has forgotten later sign-outs and ended sign-ins: the runbook says to sign every chat device out and
    # raise every nonce (chat plan 9.4, gap 27)
    older = " ".join(runbook[runbook.index("### After restoring an older pair"):runbook.index("## What this does NOT cover")].split())
    assert "queue_chat_signouts.py --all --reason=restore --once --confirm" in older
    assert "UPDATE users SET login_nonce = login_nonce + 1" in older and "chat_pair_check.sh" in older
    spec =importlib.util.spec_from_file_location("queue_chat_signouts", ROOT / "scripts" / "queue_chat_signouts.py")
    script = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(script)
    assert script.REASON.match("master_key_rotated")
    # without refreshable_access_token_lifetime Synapse falls back to its 5-minute default and still gives refresh tokens
    # (synapse/config/registration.py reads it with a "5m" default): it never ignores the request
    lifetime = " ".join(rotation[rotation.index("### Chat token lifetime"):].split())
    assert "ignores the request for a refresh token" not in lifetime
    assert "Synapse uses its default of 5 minutes" in lifetime
