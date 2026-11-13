"""The real helpers/matrix_client.py's token calls against a stubbed HTTP layer (chat plan phase 2, build step 3): what `refresh` and
`login_as` send to Synapse and how each of Synapse's answers is raised. tests/test_chat_tokens.py and tests/pg/test_chat_token.py run the
token route against fakes that raise their own RefreshGone, so a wrong status mapping here (a 401 raised as a plain MatrixError, which
would turn a device with a dead refresh token into a 503 loop instead of the list check and a new login) would pass both; this file
holds the module itself. It is loaded from its file with a stand-in `ruqqus.__main__` (the suite never imports the whole app)."""
import importlib.util
import sys
import types
from pathlib import Path
from types import SimpleNamespace

import pytest
import requests
from sqlalchemy import text

from ruqqus.helpers import chat_signouts, chat_tokens
from tests.test_chat_signouts import MX, NOW, make, revoked, rows

ROOT = Path(__file__).resolve().parent.parent
CONFIG = {"MATRIX_INTERNAL_URL": "http://synapse:8008", "MATRIX_AS_TOKEN": "as-token", "SITE_NAME": "Laba",
          "MATRIX_USER_PREFIX": "ruqqus_", "MATRIX_SERVER_NAME": "x", "MATRIX_BOT_LOCALPART": "bot"}


def answer(status, body=b"{}"):
    """A real requests.Response, so `.json()` fails as requests' own JSONDecodeError does."""
    resp = requests.Response()
    resp.status_code = status
    resp._content = body
    resp.encoding = "utf-8"
    return resp


class Http:
    """requests' post and get, recording each call; answers with what the test gives, or raises it."""

    def __init__(self, reply):
        self.reply, self.calls = reply, []

    def _call(self, method, url, **kwargs):
        self.calls.append((method, url, kwargs))
        if isinstance(self.reply, Exception):
            raise self.reply
        return self.reply

    def post(self, url, **kwargs):
        return self._call("post", url, **kwargs)

    def get(self, url, **kwargs):
        return self._call("get", url, **kwargs)


@pytest.fixture
def client(monkeypatch):
    main = types.ModuleType("ruqqus.__main__")
    main.app = SimpleNamespace(config=dict(CONFIG))
    monkeypatch.setitem(sys.modules, "ruqqus.__main__", main)
    spec = importlib.util.spec_from_file_location("matrix_client_under_test", ROOT / "ruqqus" / "helpers" / "matrix_client.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)

    def stub(reply):
        http = Http(reply)
        monkeypatch.setattr(module, "requests", SimpleNamespace(post=http.post, get=http.get, put=http.post, delete=http.post))
        return http
    module.stub = stub
    return module


# --- refresh ------------------------------------------------------------------------------------------------------------------

def test_refresh_sends_only_the_refresh_token_with_no_authorization_header(client):
    http = client.stub(answer(200, b'{"access_token": "syt_2", "refresh_token": "rt2", "expires_in_ms": 3600000}'))
    assert client.refresh("rt1") == {"access_token": "syt_2", "refresh_token": "rt2", "expires_in_ms": 3600000}
    [(method, url, kwargs)] = http.calls
    assert method == "post" and url == "http://synapse:8008/_matrix/client/v3/refresh"
    assert kwargs["json"] == {"refresh_token": "rt1"}, "exactly the refresh token"
    assert "Authorization" not in (kwargs.get("headers") or {}), "the refresh token is the credential: never the appservice's"
    assert kwargs["timeout"] == 10


@pytest.mark.parametrize("status", [400, 401, 403])
def test_refresh_refused_is_refresh_gone(client, status):
    client.stub(answer(status, b'{"errcode": "M_UNKNOWN_TOKEN"}'))
    with pytest.raises(client.RefreshGone):
        client.refresh("rt1")


@pytest.mark.parametrize("status", [404, 429, 500, 502, 503])
def test_refresh_failing_otherwise_is_a_matrix_error_and_not_refresh_gone(client, status):
    """A Synapse that is down must answer the browser `retry`, never be read as a dead refresh token (which would revoke nothing but
    re-token the device on every renewal)."""
    client.stub(answer(status))
    with pytest.raises(client.MatrixError) as raised:
        client.refresh("rt1")
    assert not isinstance(raised.value, client.RefreshGone)


@pytest.mark.parametrize("error", [requests.ConnectionError("refused"), requests.Timeout("slow")])
def test_refresh_with_no_answer_is_unreachable(client, error):
    client.stub(error)
    with pytest.raises(client.Unreachable) as raised:
        client.refresh("rt1")
    assert not isinstance(raised.value, client.RefreshGone) and "rt1" not in str(raised.value)


@pytest.mark.parametrize("body", [b"<html>Bad gateway</html>", b"", b"[]", b'{"refresh_token": "rt2"}', b'{"access_token": ""}'])
def test_a_200_from_refresh_that_isnt_a_token_is_a_matrix_error_that_names_no_token(client, body):
    client.stub(answer(200, body))
    with pytest.raises(client.MatrixError) as raised:
        client.refresh("rt1")
    assert type(raised.value) is client.MatrixError, "a failure of the call, not Unreachable or RefreshGone"
    assert "rt" not in str(raised.value)


# --- login_as -----------------------------------------------------------------------------------------------------------------

def test_login_as_always_asks_for_a_refresh_token(client):
    """Every site token expires (build step 5): there is no way to ask for one that doesn't."""
    import inspect
    assert list(inspect.signature(client.login_as).parameters) == ["mxid", "device_id"]


def test_login_as_sends_the_appservice_login_with_a_refresh_token(client):
    http = client.stub(answer(200, b'{"access_token": "syt_1", "refresh_token": "rt1", "expires_in_ms": 3600000, "device_id": "UQAAAAAAAAAA", "user_id": "@ruqqus_7:x"}'))
    got = client.login_as(MX, "UQAAAAAAAAAA")
    assert got["access_token"] == "syt_1" and got["refresh_token"] == "rt1" and got["expires_in_ms"] == 3600000
    [(method, url, kwargs)] = http.calls
    assert method == "post" and url == "http://synapse:8008/_matrix/client/v3/login"
    body = kwargs["json"]
    assert body["type"] == "m.login.application_service" and body["identifier"] == {"type": "m.id.user", "user": MX}
    assert body["device_id"] == "UQAAAAAAAAAA" and body["initial_device_display_name"] == "Laba chat"
    assert body["refresh_token"] is True
    assert kwargs["headers"] == {"Authorization": "Bearer as-token"}


def test_login_as_with_no_device_sends_none(client):
    http = client.stub(answer(200, b'{"access_token": "syt_1", "device_id": "SYNAPSEDEV"}'))
    client.login_as(MX)
    assert "device_id" not in http.calls[0][2]["json"] and http.calls[0][2]["json"]["refresh_token"] is True


@pytest.mark.parametrize("status", [400, 401, 403, 500])
def test_login_as_refused_is_a_matrix_error(client, status):
    client.stub(answer(status))
    with pytest.raises(client.MatrixError) as raised:
        client.login_as(MX, "UQAAAAAAAAAA")
    assert not isinstance(raised.value, client.RefreshGone)


@pytest.mark.parametrize("body", [b"not json", b"[]", b'{"device_id": "UQAAAAAAAAAA"}', b'{"access_token": "syt_1"}'])
def test_a_200_from_login_as_that_isnt_a_token_is_a_matrix_error(client, body):
    client.stub(answer(200, body))
    with pytest.raises(client.MatrixError) as raised:
        client.login_as(MX, "UQAAAAAAAAAA")
    assert type(raised.value) is client.MatrixError and "syt_1" not in str(raised.value)


def test_login_as_with_no_answer_is_unreachable(client):
    client.stub(requests.ConnectionError("refused"))
    with pytest.raises(client.Unreachable):
        client.login_as(MX, "UQAAAAAAAAAA")


# --- list_devices -------------------------------------------------------------------------------------------------------------

def test_list_devices_reads_the_list_and_a_body_that_isnt_one_is_a_matrix_error(client):
    client.stub(answer(200, b'{"devices": [{"device_id": "UQAAAAAAAAAA", "display_name": "Laba chat", "last_seen_ts": 5}]}'))
    assert client.list_devices(MX) == [{"device_id": "UQAAAAAAAAAA", "name": "Laba chat", "last_seen": 5}]
    for body in (b"<html></html>", b"[]", b'{"devices": "none"}', b'{"devices": ["x"]}'):
        client.stub(answer(200, body))
        with pytest.raises(client.MatrixError):
            client.list_devices(MX)
    client.stub(answer(403, b'{"error": "Application service has not registered this user"}'))
    with pytest.raises(client.UserUnknown):
        client.list_devices(MX)


# --- the token route with the real module -------------------------------------------------------------------------------------

@pytest.fixture
def forget_ready(monkeypatch):
    monkeypatch.setattr(chat_signouts, "_ready", [])


def issue(db, matrix, form):
    return chat_tokens.issue(db, matrix, 7, MX, form, "cookie-a", "https://chat.example", now=NOW, nonce=0, secret="chat-store-secret",
                             sealer=(lambda t: "sealed:" + t, lambda s: s[len("sealed:"):]))


def test_a_mint_whose_200_isnt_json_answers_retry_and_queues_the_devices_sign_out(client, forget_ready):
    """Synapse may have made the device before its answer went wrong: the committed row is revoked and its sign-out queued, as for
    any failed mint, never a 500 with a live row and a device nothing will sign out."""
    db = make()
    client.stub(answer(200, b"<html>proxy error</html>"))
    assert issue(db, client, {}) == (503, chat_tokens.RETRY)
    made = db.execute(text("SELECT device_id FROM chat_site_devices")).scalar()
    assert revoked(db) == {made: "mint_failed"}
    assert rows(db)[-1][:2] == ("listed", made)


def test_a_renewal_whose_refresh_answer_is_refused_falls_back_to_the_list_check(client, forget_ready):
    """The status mapping as the route sees it: a 401 from /refresh is a dead refresh token (list, then login on the same device)."""
    db = make()
    db.execute(text("""INSERT INTO chat_site_devices (user_id, device_id, kind, created_ms, last_used_utc, refresh_sealed)
                       VALUES (7, 'UQAAAAAAAAAA', 'chat', 1000, :n, 'sealed:rt1')"""), {"n": NOW})
    db.commit()
    replies = iter([answer(401, b'{"errcode": "M_UNKNOWN_TOKEN"}'),
                    answer(200, b'{"devices": [{"device_id": "UQAAAAAAAAAA"}]}'),
                    answer(200, b'{"access_token": "syt_9", "device_id": "UQAAAAAAAAAA", "refresh_token": "rt9", "expires_in_ms": 60000}')])
    seen = []

    def call(method):
        def go(url, **kwargs):
            seen.append((method, url.rsplit("/", 1)[-1]))
            return next(replies)
        return go
    client.requests = SimpleNamespace(post=call("post"), get=call("get"))
    status, body = issue(db, client, {"mode": "renew", "device_id": "UQAAAAAAAAAA"})
    assert status == 200 and body["access_token"] == "syt_9" and body["expires_in_ms"] == 60000
    assert seen == [("post", "refresh"), ("get", "devices"), ("post", "login")]
