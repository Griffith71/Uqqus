"""The Google account link and the Drive provider (ruqqus/helpers/media/google_oauth.py,
gdrive.py), with Google's side played by a fake. What is checked here is what the code
sends and how it reads the answers; the real service is tried by hand with credentials."""
import base64
import json
from types import SimpleNamespace as NS
from urllib.parse import parse_qs, urlparse

import pytest

from ruqqus.helpers.media import gdrive, google_oauth as go, rules
from ruqqus.helpers.media.base import AccountLost, MediaGone, ProviderDown


class Answer:
    def __init__(self, status=200, body=None, headers=None, chunks=None, text=None):
        self.status_code = status
        self._body = body
        self.headers = headers or {}
        self._chunks = chunks or []
        self.text = text if text is not None else json.dumps(body or {})

    def json(self):
        if self._body is None:
            raise ValueError("no json")
        return self._body

    def iter_content(self, size):
        return iter(self._chunks)


class FakeGoogle:
    """Answers in order of the rules given; records every request."""

    def __init__(self):
        self.calls = []
        self.rules = []

    def on(self, method, fragment, answer):
        self.rules.append((method, fragment, answer))
        return self

    def __call__(self, method, url, **kwargs):
        # a snapshot: the caller may reuse and change its dicts for the next request
        seen = {k: (dict(kwargs[k]) if isinstance(kwargs.get(k), dict) else kwargs.get(k))
                for k in ("headers", "params", "json", "data", "stream")}
        self.calls.append(NS(method=method, url=url, **seen))
        # the most specific rule wins ("/upload/drive/v3/files" also contains "/drive/v3/files")
        matching = [(fragment, answer) for m, fragment, answer in self.rules if m == method and fragment in url]
        if not matching:
            raise AssertionError(f"unexpected request {method} {url}")
        answer = max(matching, key=lambda rule: len(rule[0]))[1]
        if isinstance(answer, list):
            return answer.pop(0) if len(answer) > 1 else answer[0]
        return answer

    def sent(self, fragment):
        return [c for c in self.calls if fragment in c.url]


@pytest.fixture
def google(monkeypatch):
    fake = FakeGoogle()
    monkeypatch.setattr(go, "_http", fake)
    monkeypatch.setattr(go, "_redis", lambda: None)
    monkeypatch.setenv("GOOGLE_CLIENT_ID", "client-id")
    monkeypatch.setenv("GOOGLE_CLIENT_SECRET", "client-secret")
    go._local_tokens.clear()
    return fake


def account(**kw):
    data = {}
    base = dict(id=7, refresh_token_encrypted="enc:refresh", settings=data, status="active")
    base.update(kw)
    acc = NS(**base)
    acc.setting = lambda name, default=None: data.get(name, default)
    acc.set_setting = lambda name, value: data.__setitem__(name, value)
    return acc


def asset(**kw):
    base = dict(id=46655, kind="image", ext="jpg", size=2048, provider_ref="", token="t" * 24)
    base.update(kw)
    return NS(**base)


def id_token(sub):
    payload = base64.urlsafe_b64encode(json.dumps({"sub": sub, "aud": "client-id"}).encode()).decode().rstrip("=")
    return f"header.{payload}.signature"


TOKEN_OK = Answer(200, {"access_token": "ya29.access", "expires_in": 3600})


@pytest.fixture(autouse=True)
def no_real_decrypt(monkeypatch):
    import sys
    import types
    fake = types.SimpleNamespace(decrypt_secret=lambda s: s.replace("enc:", ""), encrypt_secret=lambda s: "enc:" + s)
    monkeypatch.setitem(sys.modules, "ruqqus.helpers.secret_box", fake)


# --- the link ------------------------------------------------------------------------

def sign(text):
    return "sig-" + str(abs(hash(text)) % 10**8)


def verify(text, signature):
    return sign(text) == signature


def test_a_state_is_only_good_for_the_member_who_asked_and_for_a_few_minutes():
    state = go.make_state(5, "storage", 1000, sign)
    assert go.read_state(state, 5, 1000 + 60, verify) == "storage"
    assert go.read_state(state, 6, 1000 + 60, verify) is None                 # someone else's
    assert go.read_state(state, 5, 1000 + go.STATE_SECONDS + 1, verify) is None   # too old
    assert go.read_state(state.replace("storage", "video"), 5, 1001, verify) is None   # altered
    for junk in (None, "", "a.b", "x.storage.sig", "1000.everything.sig", "1000.storage"):
        assert go.read_state(junk, 5, 1001, verify) is None


def test_the_member_is_sent_to_google_with_only_what_is_needed(google):
    url = go.authorize_url("https://site.test/settings/media/google/callback", "STATE", "storage")
    assert url.startswith("https://accounts.google.com/o/oauth2/v2/auth?")
    q = parse_qs(urlparse(url).query)
    assert q["client_id"] == ["client-id"] and q["response_type"] == ["code"] and q["state"] == ["STATE"]
    assert q["redirect_uri"] == ["https://site.test/settings/media/google/callback"]
    assert q["access_type"] == ["offline"] and q["include_granted_scopes"] == ["true"]
    assert "select_account" in q["prompt"][0]          # they pick, or create, the account to use
    scopes = q["scope"][0].split()
    assert go.SCOPE_DRIVE in scopes and go.SCOPE_YOUTUBE not in scopes
    # never the whole Drive
    assert "https://www.googleapis.com/auth/drive" not in scopes

    video = parse_qs(urlparse(go.authorize_url("https://site.test/cb", "S", "video")).query)["scope"][0].split()
    assert go.SCOPE_YOUTUBE in video and go.SCOPE_DRIVE in video


def test_the_code_is_exchanged_for_lasting_access(google):
    google.on("POST", "oauth2.googleapis.com/token", Answer(200, {
        "access_token": "ya29.a", "expires_in": 3599, "refresh_token": "1//refresh",
        "scope": f"openid {go.SCOPE_DRIVE}", "id_token": id_token("1122334455")}))
    granted = go.exchange("the-code", "https://site.test/cb")
    assert granted == {"refresh_token": "1//refresh", "scopes": sorted(["openid", go.SCOPE_DRIVE]), "sub": "1122334455"}
    sent = google.calls[0].data
    assert sent["grant_type"] == "authorization_code" and sent["code"] == "the-code"
    assert sent["client_secret"] == "client-secret" and sent["redirect_uri"] == "https://site.test/cb"


@pytest.mark.parametrize("answer, words", [
    (Answer(400, {"error": "invalid_grant"}), "did not accept"),
    (Answer(200, {"access_token": "a", "id_token": id_token("1")}), "lasting access"),       # no refresh token
    (Answer(200, {"access_token": "a", "refresh_token": "r", "id_token": "garbage"}), "which account"),
])
def test_a_bad_exchange_is_explained(google, answer, words):
    google.on("POST", "oauth2.googleapis.com/token", answer)
    with pytest.raises(ValueError) as err:
        go.exchange("c", "https://site.test/cb")
    assert words in str(err.value)


# --- access tokens -------------------------------------------------------------------

def test_an_access_token_is_made_from_the_stored_permission_and_reused(google):
    google.on("POST", "oauth2.googleapis.com/token", TOKEN_OK)
    acc = account()
    assert go.access_token(acc) == "ya29.access"
    assert go.access_token(acc) == "ya29.access"
    assert len(google.calls) == 1
    sent = google.calls[0].data
    assert sent["grant_type"] == "refresh_token" and sent["refresh_token"] == "refresh"     # decrypted, not as stored


def test_access_taken_away_at_google_means_the_account_is_lost(google):
    google.on("POST", "oauth2.googleapis.com/token", Answer(400, {"error": "invalid_grant"}))
    with pytest.raises(AccountLost):
        go.access_token(account())
    with pytest.raises(AccountLost):
        go.access_token(account(refresh_token_encrypted=""))


def test_google_being_down_is_not_mistaken_for_lost_access(google):
    google.on("POST", "oauth2.googleapis.com/token", Answer(503, {"error": "backend"}))
    with pytest.raises(ProviderDown):
        go.access_token(account())


def test_a_rejected_token_is_replaced_once(google):
    google.on("POST", "oauth2.googleapis.com/token", [Answer(200, {"access_token": "old", "expires_in": 3600}),
                                                    Answer(200, {"access_token": "new", "expires_in": 3600})])
    google.on("GET", "/drive/v3/files/x", [Answer(401, {}), Answer(200, {"id": "x"})])
    r = go.api(account(), "GET", "https://www.googleapis.com/drive/v3/files/x")
    assert r.status_code == 200
    assert [c.headers["Authorization"] for c in google.sent("/files/x")] == ["Bearer old", "Bearer new"]


# --- Drive: uploading ----------------------------------------------------------------

def drive(google):
    google.on("POST", "oauth2.googleapis.com/token", TOKEN_OK)
    return gdrive.DriveProvider()


def test_an_upload_makes_the_folders_once_and_gives_the_browser_only_a_session_address(google):
    provider = drive(google)
    google.on("GET", "/files/generateIds", Answer(200, {"ids": ["FILE123"]}))
    google.on("POST", "/drive/v3/files", [Answer(200, {"id": "ROOT"}), Answer(200, {"id": "IMAGES"})])
    google.on("POST", "/upload/drive/v3/files", Answer(200, {}, headers={"Location": "https://www.googleapis.com/upload/drive/v3/files?uploadType=resumable&upload_id=SESSION"}))
    acc, a = account(), asset()

    upload = provider.begin_upload(acc, a, "https://site.test")

    assert upload == {"url": "https://www.googleapis.com/upload/drive/v3/files?uploadType=resumable&upload_id=SESSION", "method": "PUT", "headers": {}}
    assert "ya29" not in json.dumps(upload)                     # no credential leaves the server
    assert a.provider_ref == "FILE123"
    assert acc.setting("folders") == {"root": "ROOT", "image": "IMAGES"}

    made = [c.json for c in google.calls if c.method == "POST" and c.url == f"{gdrive.API}/files"]
    assert made[0]["mimeType"] == gdrive.FOLDER and "parents" not in made[0]
    assert made[1] == {"name": "Images", "mimeType": gdrive.FOLDER, "parents": ["ROOT"]}

    start = google.sent("/upload/drive/v3/files")[0]
    assert start.json == {"id": "FILE123", "name": start.json["name"], "parents": ["IMAGES"]}
    assert start.json["name"].endswith("-zzz.jpg")
    assert start.headers["X-Upload-Content-Type"] == "image/jpeg" and start.headers["X-Upload-Content-Length"] == "2048"
    assert start.headers["Origin"] == "https://site.test"       # the browser that will send the bytes

    # the second upload reuses the folders
    google.calls.clear()
    provider.begin_upload(acc, asset(id=2), "https://site.test")
    assert not [c for c in google.calls if c.method == "POST" and c.url == f"{gdrive.API}/files"]     # no folder made again
    assert google.sent("/upload/drive/v3/files")[0].json["parents"] == ["IMAGES"]


def test_a_folder_the_member_deleted_is_made_again(google):
    provider = drive(google)
    google.on("GET", "/files/generateIds", Answer(200, {"ids": ["F"]}))
    google.on("POST", "/drive/v3/files", [Answer(200, {"id": "ROOT2"}), Answer(200, {"id": "IMAGES2"})])
    google.on("POST", "/upload/drive/v3/files", [Answer(404, {"error": {"message": "File not found: OLD."}}),
                                                Answer(200, {}, headers={"Location": "https://upload/session"})])
    acc = account()
    acc.set_setting("folders", {"root": "OLDROOT", "image": "OLD"})
    assert provider.begin_upload(acc, asset(), "https://site.test")["url"] == "https://upload/session"
    assert acc.setting("folders") == {"root": "ROOT2", "image": "IMAGES2"}


def test_a_full_drive_is_explained(google):
    provider = drive(google)
    google.on("GET", "/files/generateIds", Answer(200, {"ids": ["F"]}))
    google.on("POST", "/drive/v3/files", Answer(200, {"id": "X"}))
    google.on("POST", "/upload/drive/v3/files", Answer(403, {"error": {}}, text='{"error":{"errors":[{"reason":"storageQuotaExceeded"}]}}'))
    with pytest.raises(rules.MediaError) as err:
        provider.begin_upload(account(), asset(), "https://site.test")
    assert "full" in err.value.message


def test_finishing_asks_drive_whether_the_file_arrived(google):
    provider = drive(google)
    google.on("GET", "/drive/v3/files/FILE123", Answer(200, {"id": "FILE123", "size": "2048", "sha256Checksum": "abc", "md5Checksum": "m"}))
    assert provider.finish_upload(account(), asset(provider_ref="FILE123")) == {"ref": "FILE123", "size": 2048, "checksum": "abc"}
    assert google.sent("/files/FILE123")[0].params == {"fields": gdrive.META}


@pytest.mark.parametrize("answer", [Answer(404, {}), Answer(200, {"id": "F", "size": "1", "sha256Checksum": "a", "trashed": True}),
                                    Answer(200, {"id": "F"})])
def test_an_upload_that_is_not_there_is_refused(google, answer):
    provider = drive(google)
    google.on("GET", "/drive/v3/files/F", answer)
    with pytest.raises(rules.MediaError):
        provider.finish_upload(account(), asset(provider_ref="F"))


# --- Drive: showing ------------------------------------------------------------------

def test_the_checksum_comes_from_drive_and_a_missing_file_is_gone(google):
    provider = drive(google)
    google.on("GET", "/drive/v3/files/F", [Answer(200, {"id": "F", "size": "9", "sha256Checksum": "abc"}), Answer(404, {})])
    assert provider.checksum(account(), asset(provider_ref="F")) == "abc"
    with pytest.raises(MediaGone):
        provider.checksum(account(), asset(provider_ref="F"))


def test_the_file_is_streamed_and_a_range_is_passed_on(google):
    provider = drive(google)
    google.on("GET", "/drive/v3/files/F", [Answer(200, {}, headers={"Content-Length": "2048"}, chunks=[b"ab", b"cd"]),
                                          Answer(206, {}, headers={"Content-Length": "10"}, chunks=[b"0123456789"])])
    whole = provider.open(account(), asset(provider_ref="F"))
    assert b"".join(whole.chunks) == b"abcd" and whole.size == 2048 and whole.byte_range is None
    first = google.sent("/files/F")[0]
    assert first.params == {"alt": "media"} and first.stream is True and "Range" not in first.headers

    part = provider.open(account(), asset(provider_ref="F"), (0, 9))
    assert part.size == 10 and part.total == 2048 and part.byte_range == (0, 9)
    assert google.sent("/files/F")[1].headers["Range"] == "bytes=0-9"


def test_a_range_drive_ignored_is_answered_as_the_whole_file(google):
    provider = drive(google)
    google.on("GET", "/drive/v3/files/F", Answer(200, {}, headers={"Content-Length": "2048"}, chunks=[b"x"]))
    assert provider.open(account(), asset(provider_ref="F"), (0, 9)).byte_range is None


@pytest.mark.parametrize("status, error", [(404, MediaGone), (403, MediaGone), (500, ProviderDown), (429, ProviderDown)])
def test_a_missing_file_is_gone_and_a_failing_service_is_only_down(google, status, error):
    provider = drive(google)
    google.on("GET", "/drive/v3/files/F", Answer(status, {}))
    with pytest.raises(error):
        provider.open(account(), asset(provider_ref="F"))


def test_disconnecting_gives_the_access_back_at_google(google):
    provider = drive(google)
    google.on("POST", "oauth2.googleapis.com/revoke", Answer(200, {}))
    provider.revoke(account())
    assert google.sent("/revoke")[0].data == {"token": "refresh"}


# --- wiring ----------------------------------------------------------------------------

def test_google_is_only_offered_when_the_site_has_credentials(monkeypatch):
    from ruqqus.helpers.media import registry
    monkeypatch.delenv("GOOGLE_CLIENT_ID", raising=False)
    monkeypatch.delenv("GOOGLE_CLIENT_SECRET", raising=False)
    assert "google" not in registry.account_kinds() and registry.get("gdrive") is None
    monkeypatch.setenv("GOOGLE_CLIENT_ID", "x")
    monkeypatch.setenv("GOOGLE_CLIENT_SECRET", "y")
    assert registry.account_kinds()[0] == "google" and registry.get("gdrive") is not None
