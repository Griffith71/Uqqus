"""The token route v2 (ruqqus/helpers/chat_tokens.py, chat plan phase 2 build step 3): the store keys, the modes boot / renew / replace,
`want`, `mint_key`, renewal through Synapse's /refresh, and /api/chat/store_keys, on SQLite with the tables from the migration and a fake
chat server. Step 2's records (the mint lock, adoption, the account read again) are tests/test_chat_signouts.py; the real app, the row
lock's timeout and the rate limits are tests/pg/test_chat_token.py."""
from pathlib import Path
from types import SimpleNamespace

import pytest
from sqlalchemy import text
from sqlalchemy.exc import OperationalError
from sqlalchemy.orm import Session

from ruqqus.helpers import chat_signouts, chat_store_keys, chat_tokens
from tests.test_chat_signouts import MX, NOW, device, engine_for, make, revoked, rows

SECRET = "chat-store-secret"
SEALER = (lambda token: "sealed:" + token,
          lambda sealed: sealed[len("sealed:"):] if sealed.startswith("sealed:") else (_ for _ in ()).throw(ValueError("won't open")))
NEW_FIELDS = {"kind", "kid", "storage_key", "waiting_key", "new_device"}


class Fake:
    """Synapse's side: devices, logins and /refresh with Synapse's rule that an older refresh token still works until its successor's
    access token has been used (so a renewal whose answer was lost can be asked again)."""

    class MatrixError(Exception):
        pass

    class UserUnknown(MatrixError):
        pass

    class RefreshGone(MatrixError):
        pass

    def __init__(self, devices=()):
        self.devices = {d: "Laba chat" for d in devices}
        self.calls, self.refreshable, self.issued = [], {}, 0
        self.down = self.refresh_down = False
        self.on_login = None

    def _pair(self, device_id):
        self.issued += 1
        rt = f"rt{self.issued}"
        self.refreshable[rt] = device_id
        return {"access_token": f"syt_{self.issued}", "refresh_token": rt, "expires_in_ms": 3_600_000}

    def list_devices(self, mxid):
        self.calls.append(("list", None))
        if self.down:
            raise self.MatrixError("down")
        return [{"device_id": d, "name": n, "last_seen": None} for d, n in self.devices.items()]

    def login_as(self, mxid, device_id=None):
        """matrix_client.login_as: always with a refresh token (build step 5), so every token expires."""
        self.calls.append(("login", device_id))
        if self.on_login:
            self.on_login(device_id)
        if self.down:
            raise self.MatrixError("down")
        self.devices.setdefault(device_id, "Laba chat")
        return dict({"user_id": mxid, "device_id": device_id}, **self._pair(device_id))

    def refresh(self, refresh_token):
        self.calls.append(("refresh", refresh_token))
        if self.refresh_down:
            raise self.MatrixError("down")
        device_id = self.refreshable.get(refresh_token)
        if device_id is None or device_id not in self.devices:
            raise self.RefreshGone("gone")
        return self._pair(device_id)

    def synapse_calls(self):
        return [c for c in self.calls if c[0] in ("list", "login", "refresh")]


@pytest.fixture(autouse=True)
def forget_ready(monkeypatch):
    monkeypatch.setattr(chat_signouts, "_ready", [])


def issue(db, fake, form, session="cookie-a", nonce=0, report=None):
    return chat_tokens.issue(db, fake, 7, MX, form, session, "https://chat.example", now=NOW, site_name="Laba", nonce=nonce,
                             secret=SECRET, report_seconds=report, sealer=SEALER)


def row(db, device_id):
    found = db.execute(text("""SELECT kind, sign_in, last_used_utc, last_token_utc, refresh_sealed, revoked_utc, revoked_reason, mint_key,
                                      created_ms FROM chat_site_devices WHERE device_id = :d"""), {"d": device_id}).first()
    return SimpleNamespace(**found._mapping) if found else None


def site_rows(db):
    return db.execute(text("SELECT count(*) FROM chat_site_devices")).scalar()


def keys(device_id):
    return chat_tokens.keys_for(SECRET, MX, device_id)


# --- the answer ---------------------------------------------------------------------------------------------------------------

def test_a_request_with_only_formkey_and_device_gets_the_old_answer_plus_the_keys():
    """Exactly what token.js posted before build step 4 (an older cached bundle still may): no mode, want or kind. It is a boot that
    wants a token, for a chat device."""
    db = make()
    device(db, "UQAAAAAAAAAA")
    db.commit()
    fake = Fake(["UQAAAAAAAAAA"])
    status, body = issue(db, fake, {"formkey": "fk", "device_id": "UQAAAAAAAAAA"})
    assert status == 200
    assert body["homeserver_url"] == "https://chat.example" and body["user_id"] == MX and body["device_id"] == "UQAAAAAAAAAA"
    assert body["access_token"].startswith("syt_"), "a client from before build step 4 needs a token"
    assert NEW_FIELDS <= set(body) and body["kind"] == "chat" and body["new_device"] is False
    assert body["storage_key"] == chat_store_keys.b64(chat_store_keys.store_key(SECRET, MX, "UQAAAAAAAAAA"))
    assert body["waiting_key"] == chat_store_keys.b64(chat_store_keys.waiting_key(SECRET, MX))
    assert body["kid"] == chat_store_keys.key_id(SECRET)
    assert body["expires_in_ms"] == 3_600_000 and "previous" not in body, "a token that ends, and says when"
    assert ("login", "UQAAAAAAAAAA") in fake.calls and row(db, "UQAAAAAAAAAA").refresh_sealed == "sealed:rt1"
    status, body = issue(db, fake, {"formkey": "fk"})
    assert status == 200 and body["new_device"] is True and chat_store_keys.is_site_made(body["device_id"])
    assert row(db, body["device_id"]).kind == "chat"


def test_no_answer_ever_holds_a_refresh_token():
    db = make()
    fake = Fake()
    answers = [issue(db, fake, {})[1]]
    d = answers[0]["device_id"]
    answers.append(issue(db, fake, {"mode": "renew", "device_id": d})[1])
    answers.append(issue(db, fake, {"device_id": d, "want": "token"})[1])
    for body in answers:
        assert body["access_token"] and not any("refresh" in k for k in body), body
        assert "rt" not in str({k: v for k, v in body.items() if k not in ("storage_key", "waiting_key")})
    assert row(db, d).refresh_sealed.startswith("sealed:rt"), "kept here, sealed"


def test_expires_in_ms_is_synapses_figure_capped_by_the_report_setting():
    db = make()
    fake = Fake()
    assert issue(db, fake, {})[1]["expires_in_ms"] == 3_600_000
    assert issue(db, fake, {}, report=90)[1]["expires_in_ms"] == 90_000


@pytest.mark.parametrize("form", [
    {"kind": "video"}, {"mode": "start"}, {"want": "both"}, {"device_id": "a,b"}, {"device_id": "UQAAAAAAAAAA\n"},
    {"mode": "replace", "device_id": "UQAAAAAAAAAA"},                         # replace needs a reason
    {"mode": "replace", "why": "key_changed"},                                # ...and a device
    {"mode": "replace", "device_id": "UQAAAAAAAAAA", "why": "bored"},
    {"why": "key_changed"},                                                   # a reason is for replace only
    {"mode": "renew"},                                                        # renew is of a device
    {"mint_key": "0" * 31}, {"mint_key": "G" * 32}, {"mint_key": "A" * 32},   # 32 lower-case hex characters
    {"mode": "renew", "device_id": "UQAAAAAAAAAA", "mint_key": "a" * 32},     # boot only
])
def test_a_present_value_that_is_not_allowed_is_refused_before_anything_is_done(form):
    db = make()
    fake = Fake()
    assert issue(db, fake, form) == (400, chat_tokens.BAD_REQUEST)
    assert fake.calls == [] and site_rows(db) == 0


def test_the_tables_absent_answer_retry_with_no_matrix_call():
    db = make(with_tables=False)
    fake = Fake()
    for form in ({}, {"device_id": "UQAAAAAAAAAA"}, {"mode": "replace", "device_id": "UQAAAAAAAAAA", "why": "key_changed"}):
        assert issue(db, fake, form) == (503, chat_tokens.RETRY)
    assert fake.calls == []
    assert chat_tokens.store_keys(db, 7, MX, ["UQAAAAAAAAAA"], SECRET) == {"keys": {}}


# --- the key-only start ---------------------------------------------------------------------------------------------------------

def test_a_key_only_start_makes_no_synapse_call_and_marks_the_device_as_this_sign_ins():
    db = make()
    device(db, "UQAAAAAAAAAA", sign_in="older", used=1)
    db.execute(text("UPDATE chat_site_devices SET refresh_sealed = 'sealed:rt0'"))
    db.commit()
    fake = Fake(["UQAAAAAAAAAA"])
    status, body = issue(db, fake, {"mode": "boot", "want": "key", "device_id": "UQAAAAAAAAAA"})
    assert status == 200 and body["access_token"] is None and body["device_id"] == "UQAAAAAAAAAA" and body["new_device"] is False
    assert body["storage_key"] == keys("UQAAAAAAAAAA")["storage_key"]
    assert fake.calls == [], "the browser's own token still works: nothing is asked of the chat server"
    found = row(db, "UQAAAAAAAAAA")
    assert found.sign_in == chat_signouts.sign_in_hash("cookie-a") and found.last_used_utc == NOW and found.last_token_utc == 0


def test_a_key_only_start_of_a_device_with_no_refresh_token_is_given_an_expiring_token():
    """A device made before tokens expired (build step 5) holds a token that never expires: its next start moves it to one that does,
    on the same device. So a browser whose sealed token doesn't expire (`exp` null) still ends up renewing."""
    db = make()
    device(db, "UQAAAAAAAAAA")
    db.commit()
    fake = Fake(["UQAAAAAAAAAA"])
    status, body = issue(db, fake, {"want": "key", "device_id": "UQAAAAAAAAAA"})
    assert status == 200 and body["access_token"] and body["expires_in_ms"] == 3_600_000
    assert ("list", None) in fake.calls and ("login", "UQAAAAAAAAAA") in fake.calls
    assert row(db, "UQAAAAAAAAAA").refresh_sealed == "sealed:rt1"


def test_a_key_only_start_of_a_device_with_a_refresh_token_is_key_only():
    db = make()
    fake = Fake()
    d = issue(db, fake, {})[1]["device_id"]
    fake.calls.clear()
    status, body = issue(db, fake, {"want": "key", "device_id": d})
    assert status == 200 and body["access_token"] is None and fake.calls == []


def test_a_key_only_start_is_refused_once_the_sign_in_has_ended_and_writes_nothing():
    db = make()
    device(db, "UQAAAAAAAAAA", used=5)
    db.execute(text("UPDATE users SET login_nonce = 3"))
    db.commit()
    assert issue(db, Fake(["UQAAAAAAAAAA"]), {"want": "key", "device_id": "UQAAAAAAAAAA"}, nonce=2) == (401, chat_tokens.SIGNED_OUT)
    assert row(db, "UQAAAAAAAAAA").last_used_utc == 5


# --- renewal -----------------------------------------------------------------------------------------------------------------

def test_renew_asks_refresh_not_login_and_keeps_the_new_refresh_token_sealed():
    db = make()
    fake = Fake()
    d = issue(db, fake, {})[1]["device_id"]
    assert row(db, d).refresh_sealed == "sealed:rt1"
    fake.calls.clear()
    status, body = issue(db, fake, {"mode": "renew", "device_id": d}, session="cookie-b")
    assert status == 200 and body["device_id"] == d and body["access_token"] == "syt_2" and body["new_device"] is False
    assert fake.calls == [("refresh", "rt1")], "renewal goes through /refresh, never a new login"
    found = row(db, d)
    assert found.refresh_sealed == "sealed:rt2" and found.last_token_utc == NOW and found.sign_in == chat_signouts.sign_in_hash("cookie-b")


def test_renew_wants_a_token_whatever_want_says():
    db = make()
    fake = Fake()
    d = issue(db, fake, {})[1]["device_id"]
    assert issue(db, fake, {"mode": "renew", "want": "key", "device_id": d})[1]["access_token"]


def test_every_token_answered_comes_from_a_login_that_asked_for_a_refresh_token_or_from_refresh():
    """No path re-tokens a device with a token that never expires (there is no switch for it since build step 5): the fake only knows
    `login_as` with a refresh token, and every answer carries `expires_in_ms`."""
    db = make()
    device(db, "UQAAAAAAAAAA")
    db.commit()
    fake = Fake(["UQAAAAAAAAAA"])
    answers = [issue(db, fake, {"mode": "boot", "want": "token", "device_id": "UQAAAAAAAAAA"})[1],
               issue(db, fake, {"mode": "renew", "device_id": "UQAAAAAAAAAA"})[1],
               issue(db, fake, {"mint_key": "9" * 32})[1]]
    assert all(a["access_token"] and a["expires_in_ms"] == 3_600_000 for a in answers)
    assert [c[0] for c in fake.synapse_calls()] == ["list", "login", "refresh", "login"]
    assert row(db, "UQAAAAAAAAAA").refresh_sealed == "sealed:rt2"


def test_a_refresh_token_synapse_refuses_falls_back_to_a_login_while_the_device_is_listed():
    db = make()
    fake = Fake()
    d = issue(db, fake, {})[1]["device_id"]
    fake.refreshable.clear()                                        # Synapse no longer knows it (RefreshGone)
    fake.calls.clear()
    status, body = issue(db, fake, {"mode": "renew", "device_id": d})
    assert status == 200 and body["device_id"] == d
    assert fake.calls == [("refresh", "rt1"), ("list", None), ("login", d)]
    assert row(db, d).refresh_sealed == "sealed:rt2"


def test_a_seal_that_wont_open_falls_back_to_a_login_while_the_device_is_listed():
    """MASTER_KEY rotated: every sealed refresh token is void, and each device re-tokens once on the same device."""
    db = make()
    device(db, "UQAAAAAAAAAA")
    db.execute(text("UPDATE chat_site_devices SET refresh_sealed = 'under-an-old-key'"))
    db.commit()
    fake = Fake(["UQAAAAAAAAAA"])
    status, body = issue(db, fake, {"mode": "renew", "device_id": "UQAAAAAAAAAA"})
    assert status == 200 and body["device_id"] == "UQAAAAAAAAAA"
    assert [c[0] for c in fake.calls] == ["list", "login"], "the seal never reached Synapse"


def test_a_refused_refresh_with_the_device_gone_revokes_it_then_renew_is_409_and_boot_mints_with_previous():
    db = make()
    fake = Fake()
    d = issue(db, fake, {})[1]["device_id"]
    del fake.devices[d]                                             # signed out on Synapse (an app's sign-out, an admin)
    status, body = issue(db, fake, {"mode": "renew", "device_id": d})
    assert (status, body) == (409, {"device_gone": True, "reason": "deleted_elsewhere"})
    assert revoked(db) == {d: "deleted_elsewhere"}
    assert not [c for c in fake.calls if c[0] == "login" and c[1] == d][1:], "never re-tokened"
    fake.calls.clear()
    assert issue(db, fake, {"mode": "renew", "device_id": d})[0] == 409
    assert fake.calls == [], "a revoked device is answered from its row"
    status, body = issue(db, fake, {"mode": "boot", "device_id": d})
    assert status == 200 and body["device_id"] != d and body["new_device"] is True
    assert body["previous"] == {"device_id": d, "reason": "deleted_elsewhere"}


def test_a_device_with_no_refresh_token_gone_from_synapse_is_revoked_and_never_logged_in_again():
    db = make()
    device(db, "UQAAAAAAAAAA")
    db.commit()
    fake = Fake()
    status, body = issue(db, fake, {"mode": "renew", "device_id": "UQAAAAAAAAAA"})
    assert status == 409 and body["reason"] == "deleted_elsewhere"
    assert revoked(db) == {"UQAAAAAAAAAA": "deleted_elsewhere"}
    assert not [c for c in fake.calls if c[0] == "login"]


@pytest.mark.parametrize("reason", ["password", "signed_out_here", "key_changed"])
def test_renew_of_a_revoked_device_answers_its_reason_and_mints_nothing(reason):
    db = make()
    device(db, "UQAAAAAAAAAA")
    chat_signouts.revoke_rows(db, 7, ["UQAAAAAAAAAA"], reason, NOW)
    db.commit()
    fake = Fake(["UQAAAAAAAAAA"])
    assert issue(db, fake, {"mode": "renew", "device_id": "UQAAAAAAAAAA"}) == (409, {"device_gone": True, "reason": reason})
    assert fake.calls == [] and site_rows(db) == 1


def test_renew_of_a_device_with_no_row_of_this_account_or_of_another_kind_is_409():
    db = make()
    device(db, "UQCALL000000", kind="call")
    device(db, "UQOTHER00000", user_id=8)
    db.commit()
    fake = Fake(["UQCALL000000", "UQOTHER00000", "OLDDEVICE0"])
    assert issue(db, fake, {"mode": "renew", "device_id": "UQCALL000000"}) == (409, {"device_gone": True, "reason": "other_kind"})
    for named in ("UQOTHER00000", "OLDDEVICE0"):
        assert issue(db, fake, {"mode": "renew", "device_id": named}) == (409, {"device_gone": True, "reason": "deleted_elsewhere"})
    assert not [c for c in fake.calls if c[0] == "login"]


def test_a_failed_commit_leaves_the_old_refresh_token_usable(monkeypatch):
    """The new access token never reached the browser, so it was never used, and Synapse still takes the old refresh token."""
    db = make()
    fake = Fake()
    d = issue(db, fake, {})[1]["device_id"]
    real = Session.commit
    failed = []

    def commit_once_fails(self):
        if not failed:
            failed.append(True)
            raise OperationalError("COMMIT", {}, Exception("the connection dropped"))
        return real(self)
    monkeypatch.setattr(Session, "commit", commit_once_fails)
    with pytest.raises(OperationalError):
        issue(db, fake, {"mode": "renew", "device_id": d})
    db.rollback()
    assert row(db, d).refresh_sealed == "sealed:rt1", "the row still holds the old one"
    status, body = issue(db, fake, {"mode": "renew", "device_id": d})
    assert status == 200 and body["device_id"] == d
    assert [c for c in fake.calls if c[0] == "refresh"] == [("refresh", "rt1"), ("refresh", "rt1")]


def test_a_chat_server_error_on_refresh_answers_retry_and_revokes_nothing():
    db = make()
    fake = Fake()
    d = issue(db, fake, {})[1]["device_id"]
    fake.refresh_down = True
    assert issue(db, fake, {"mode": "renew", "device_id": d}) == (503, chat_tokens.RETRY)
    assert revoked(db) == {} and row(db, d).refresh_sealed == "sealed:rt1"


# --- a new device -------------------------------------------------------------------------------------------------------------

def test_a_new_device_is_committed_before_login_with_the_sites_id_and_says_what_it_replaces(tmp_path):
    path = tmp_path / "t.db"
    db = make(path)
    device(db, "UQAAAAAAAAAA")
    chat_signouts.revoke_rows(db, 7, ["UQAAAAAAAAAA"], "password", NOW)
    db.commit()
    other = Session(engine_for(path))
    fake = Fake(["UQAAAAAAAAAA"])
    seen = []

    def look(d):                     # another connection sees only what is committed
        seen.append(other.execute(text("SELECT kind, created_ms, revoked_utc, mint_key FROM chat_site_devices WHERE device_id = :d"),
                                  {"d": d}).first())
        other.rollback()
    fake.on_login = look
    status, body = issue(db, fake, {"mode": "boot", "kind": "call", "device_id": "UQAAAAAAAAAA", "mint_key": "a" * 32})
    assert status == 200 and chat_store_keys.is_site_made(body["device_id"]) and body["device_id"] != "UQAAAAAAAAAA"
    assert seen == [("call", seen[0][1], 0, "a" * 32)] and seen[0][1] > 0
    assert body["previous"] == {"device_id": "UQAAAAAAAAAA", "reason": "password"} and body["new_device"] is True
    assert body["kind"] == "call" and body["storage_key"] == keys(body["device_id"])["storage_key"]
    assert ("login", "UQAAAAAAAAAA") not in fake.calls


def test_nothing_describes_the_client_from_before_build_step_4_as_todays():
    """Since build step 4 the site's own client always sends `mode` and keeps its store when handed another device: the no-mode path is
    for an older cached bundle or an outside script, and is never described as what the site's pages do now."""
    stale = ("today" + "'s client", "today" + "'s browser", "today" + "'s chat page", "deletes its store when " + "handed")
    for path in (Path(chat_tokens.__file__), Path(__file__)):
        text = path.read_text(encoding="utf-8").lower()
        for phrase in stale:
            assert phrase.lower() not in text, (path.name, phrase)


def test_a_client_that_sends_a_mode_never_adopts_a_device_from_before_the_rows():
    """The site's own pages (build step 4 on) always send a `mode`: a device from before the rows is never taken up for them, and they
    are told so (`legacy`). A request with no mode (a page from an older cached bundle, an outside script) still has it taken up
    (tests/test_chat_signouts.py), as the client from before build step 4 expected."""
    db = make()
    fake = Fake(["OLDDEVICE0"])
    status, body = issue(db, fake, {"mode": "boot", "device_id": "OLDDEVICE0"})
    assert status == 200 and body["device_id"] != "OLDDEVICE0" and body["previous"] == {"device_id": "OLDDEVICE0", "reason": "legacy"}
    assert row(db, "OLDDEVICE0") is None and ("login", "OLDDEVICE0") not in fake.calls
    status, body = issue(db, fake, {"device_id": "OLDDEVICE0"})
    assert status == 200 and body["device_id"] == "OLDDEVICE0" and "previous" not in body


def test_a_device_of_another_kind_is_not_served_and_previous_says_so():
    db = make()
    device(db, "UQCALL000000", kind="call")
    db.commit()
    fake = Fake(["UQCALL000000"])
    status, body = issue(db, fake, {"mode": "boot", "device_id": "UQCALL000000"})
    assert status == 200 and body["device_id"] != "UQCALL000000" and body["previous"]["reason"] == "other_kind"
    assert revoked(db) == {}, "the call window's device is left alone"


def test_a_retry_of_the_same_page_start_gets_the_same_device_not_a_second():
    db = make()
    fake = Fake()
    first = issue(db, fake, {"mint_key": "b" * 32})[1]
    again = issue(db, fake, {"mint_key": "b" * 32})[1]
    assert again["device_id"] == first["device_id"] and again["new_device"] is True and site_rows(db) == 1
    assert [c[0] for c in fake.calls] == ["login", "refresh"], "the retry renews the device made for it"
    other = issue(db, fake, {"mint_key": "c" * 32})[1]
    assert other["device_id"] != first["device_id"] and site_rows(db) == 2


def test_a_retry_whose_device_was_revoked_meanwhile_gets_a_new_device_that_takes_the_key():
    db = make()
    fake = Fake()
    first = issue(db, fake, {"mint_key": "d" * 32})[1]["device_id"]
    chat_signouts.revoke_rows(db, 7, [first], "password", NOW)
    db.commit()
    second = issue(db, fake, {"mint_key": "d" * 32})[1]["device_id"]
    assert second != first and row(db, second).mint_key == "d" * 32 and row(db, first).mint_key is None


def test_a_mint_whose_row_was_revoked_while_synapse_made_it_answers_retry_and_queues_its_sign_out():
    db = make()
    fake = Fake()
    made = []
    fake.on_login = lambda d: (made.append(d), chat_signouts.revoke_rows(db, 7, [d], "password", NOW), db.commit())
    assert issue(db, fake, {"mint_key": "e" * 32}) == (503, chat_tokens.RETRY)
    last = rows(db)[-1]
    assert last[0] == "listed" and last[1] == made[0] and last[3] == "revoked_while_made"


# --- replace ------------------------------------------------------------------------------------------------------------------

def test_replace_revokes_and_queues_and_mints_nothing():
    db = make()
    device(db, "UQAAAAAAAAAA")
    db.commit()
    fake = Fake(["UQAAAAAAAAAA"])
    assert issue(db, fake, {"mode": "replace", "device_id": "UQAAAAAAAAAA", "why": "store_unopenable"}) == (200, chat_tokens.REPLACED)
    assert revoked(db) == {"UQAAAAAAAAAA": "store_unopenable"} and site_rows(db) == 1
    assert rows(db)[-1][:4] == ("listed", "UQAAAAAAAAAA", None, "store_unopenable")
    assert fake.calls == [], "the sign-out runs after the commit, from the queue"


def test_a_device_its_page_dropped_is_replaced_as_taken_over():
    """A page that lost chat to another tab while its new device was being minted drops it (token.js `dropMinted`): signed out, so it
    can't be the device a later credential change spares (chat_signouts.this_sign_in) in place of the one in use."""
    db = make()
    device(db, "UQAAAAAAAAAA")
    db.commit()
    fake = Fake(["UQAAAAAAAAAA"])
    assert issue(db, fake, {"mode": "replace", "device_id": "UQAAAAAAAAAA", "why": "taken_over"}) == (200, chat_tokens.REPLACED)
    assert revoked(db) == {"UQAAAAAAAAAA": "taken_over"}
    assert rows(db)[-1][:4] == ("listed", "UQAAAAAAAAAA", None, "taken_over")


@pytest.mark.parametrize("named", ["UQOTHER00000", "UQNOBODY0000", "ELEMENT000", "UQCALL000000", "UQGONE000000"])
def test_replace_answers_the_same_whatever_the_id_and_touches_nothing_not_its_own(named):
    db = make()
    device(db, "UQOTHER00000", user_id=8)
    device(db, "UQCALL000000", kind="call")
    device(db, "UQGONE000000", revoked=NOW - 5)
    db.commit()
    before = rows(db), revoked(db)
    fake = Fake(["ELEMENT000", "UQOTHER00000"])
    assert issue(db, fake, {"mode": "replace", "device_id": named, "why": "key_changed"}) == (200, chat_tokens.REPLACED)
    assert (rows(db), revoked(db)) == before and fake.calls == []


# --- never someone else's -----------------------------------------------------------------------------------------------------

def test_another_accounts_device_or_an_app_is_never_served_in_any_mode():
    db = make()
    device(db, "UQOTHER00000", user_id=8)
    db.commit()
    fake = Fake(["UQOTHER00000", "ELEMENT000"])
    for named in ("UQOTHER00000", "ELEMENT000"):
        for form in ({"mode": "boot", "want": "key", "device_id": named}, {"mode": "boot", "device_id": named}):
            status, body = issue(db, fake, form)
            assert status == 200 and body["device_id"] != named and body["storage_key"] != keys(named)["storage_key"]
        assert issue(db, fake, {"mode": "renew", "device_id": named})[0] == 409
    assert not [c for c in fake.calls if c[0] in ("login", "refresh") and c[1] in ("UQOTHER00000", "ELEMENT000")]


# --- /api/chat/store_keys -----------------------------------------------------------------------------------------------------

def test_store_keys_serves_only_this_accounts_site_devices_revoked_or_not():
    db = make()
    device(db, "UQAAAAAAAAAA")
    device(db, "UQGONE000000", revoked=NOW)
    device(db, "UQOTHER00000", user_id=8)
    db.commit()
    answer = chat_tokens.store_keys(db, 7, MX, ["UQAAAAAAAAAA", "UQGONE000000", "UQOTHER00000", "ELEMENT000"], SECRET)
    assert set(answer["keys"]) == {"UQAAAAAAAAAA", "UQGONE000000"}
    for d, entry in answer["keys"].items():
        assert entry == {"key": keys(d)["storage_key"], "kid": chat_store_keys.key_id(SECRET)}
    assert chat_tokens.store_keys(db, 7, None, ["UQAAAAAAAAAA"], SECRET) == {"keys": {}}, "no chat yet: no devices"
    assert chat_tokens.store_keys(db, 8, "@ruqqus_8:x", ["UQAAAAAAAAAA"], SECRET) == {"keys": {}}


@pytest.mark.parametrize("value, ids", [
    ("", []), ("UQAAAAAAAAAA", ["UQAAAAAAAAAA"]), ("A,B,,A", ["A", "B"]), ("A,B,C,D", ["A", "B", "C", "D"]),
    ("A,B,C,D,E", None), ("A,b c", None), ("A,B\n", None), (None, []),
    (",".join(["U" * 64] * 4), ["U" * 64]), (",".join(["U" * 64, "V" * 64, "W" * 64, "X" * 64]), ["U" * 64, "V" * 64, "W" * 64, "X" * 64]),
    (",".join(["U" * 64] * 4) + ",", ["U" * 64]), (",".join(["U" * 64] * 4) + ",,", None),   # 260 characters at most
])
def test_store_keys_takes_up_to_four_ids(value, ids):
    assert chat_tokens.parse_store_key_ids(value) == ids


def test_store_key_ids_are_refused_without_reading_a_long_list(monkeypatch):
    """A url-encoded body is capped only by MAX_CONTENT_LENGTH (64 MB): 125,000 distinct short ids took minutes of CPU in one gevent
    worker, which no other request in it could interrupt. Now a long value is refused unread and a fifth distinct id ends the loop."""
    checked = []
    real = chat_store_keys.valid_device
    monkeypatch.setattr(chat_store_keys, "valid_device", lambda d: (checked.append(d), real(d))[1])
    many = ",".join(f"{n:x}" for n in range(200_000))
    assert chat_tokens.parse_store_key_ids(many) is None and checked == [], "over the length: not one id looked at"
    assert chat_tokens.parse_store_key_ids("A,B,C,D,E,F,G,H") is None and len(checked) == 5, "the fifth distinct id ends it"
