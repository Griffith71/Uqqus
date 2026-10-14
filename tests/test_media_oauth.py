"""The shared OAuth link (ruqqus/helpers/media/oauth.py) and the two services built on it
(microsoft_oauth.py, dailymotion_oauth.py), with the services' side played by a fake."""
from urllib.parse import parse_qs, urlparse

import pytest

from ruqqus.helpers.media import dailymotion_oauth, microsoft_oauth, oauth
from ruqqus.helpers.media.base import AccountLost, ProviderDown
from test_media_google import Answer, FakeGoogle, account, id_token, no_real_decrypt, sign, verify  # fixtures and fakes shared with the Google tests

MS = microsoft_oauth.SERVICE
DM = dailymotion_oauth.SERVICE


@pytest.fixture
def fake(monkeypatch):
    service = FakeGoogle()
    monkeypatch.setattr(oauth, "_http", service)
    monkeypatch.setattr(oauth, "_redis", lambda: None)
    for key, value in (("MICROSOFT_CLIENT_ID", "ms-id"), ("MICROSOFT_CLIENT_SECRET", "ms-secret"),
                       ("DAILYMOTION_API_KEY", "dm-key"), ("DAILYMOTION_API_SECRET", "dm-secret")):
        monkeypatch.setenv(key, value)
    monkeypatch.delenv("MICROSOFT_TENANT", raising=False)
    oauth._local_tokens.clear()
    return service


# --- configuration and the link --------------------------------------------------------------

def test_a_service_without_its_keys_is_not_offered(monkeypatch):
    monkeypatch.delenv("MICROSOFT_CLIENT_ID", raising=False)
    monkeypatch.setenv("MICROSOFT_CLIENT_SECRET", "x")
    assert MS.configured() is False
    from ruqqus.helpers.media import registry
    assert "microsoft" not in registry.account_kinds() and registry.get("onedrive") is None


def test_with_its_keys_it_is_offered_in_the_sites_order(fake):
    from ruqqus.helpers.media import registry
    kinds = registry.account_kinds()
    assert "microsoft" in kinds and "dailymotion" in kinds
    assert kinds.index("microsoft") < kinds.index("dailymotion")
    assert registry.get("onedrive") is not None and registry.get("dailymotion") is not None


def test_a_state_is_only_good_for_this_member_this_service_and_a_few_minutes():
    state = MS.make_state(5, "storage", 1000, sign)
    assert MS.read_state(state, 5, 1060, verify) == "storage"
    assert MS.read_state(state, 6, 1060, verify) is None
    assert MS.read_state(state, 5, 1000 + oauth.STATE_SECONDS + 1, verify) is None
    # a state made for one service is not good for another
    assert DM.read_state(state.replace("storage", "video"), 5, 1001, verify) is None
    assert DM.read_state(DM.make_state(5, "video", 1000, sign), 5, 1001, verify) == "video"
    for junk in (None, "", "a.b", "1000.storage", "x.storage.sig", "1000.anything.sig"):
        assert MS.read_state(junk, 5, 1001, verify) is None


def test_microsoft_is_asked_for_its_own_app_folder_only(fake):
    url = MS.authorize_url("https://site.test/settings/media/microsoft/callback", "STATE", "storage")
    assert url.startswith("https://login.microsoftonline.com/consumers/oauth2/v2.0/authorize?")
    q = parse_qs(urlparse(url).query)
    assert q["client_id"] == ["ms-id"] and q["state"] == ["STATE"] and q["response_type"] == ["code"]
    scopes = q["scope"][0].split()
    assert microsoft_oauth.SCOPE_APPFOLDER in scopes and "offline_access" in scopes
    assert not any(s in scopes for s in ("Files.ReadWrite", "Files.ReadWrite.All", "Files.Read.All"))   # never the whole drive
    assert q["prompt"] == ["select_account"]


def test_the_microsoft_tenant_is_a_setting(fake, monkeypatch):
    monkeypatch.setenv("MICROSOFT_TENANT", "common")
    assert MS.authorize_url("https://x.test/cb", "S", "storage").startswith("https://login.microsoftonline.com/common/")
    assert MS.token_url == "https://login.microsoftonline.com/common/oauth2/v2.0/token"


def test_dailymotion_is_asked_for_uploading_videos_only(fake):
    q = parse_qs(urlparse(DM.authorize_url("https://site.test/cb", "S", "video")).query)
    assert q["scope"] == [dailymotion_oauth.SCOPE_VIDEOS] and q["client_id"] == ["dm-key"]


def test_a_granted_scope_is_recognised_with_or_without_its_resource_address():
    assert MS.has_scope(["https://graph.microsoft.com/Files.ReadWrite.AppFolder"], "Files.ReadWrite.AppFolder")
    assert MS.has_scope(["files.readwrite.appfolder", "openid"], "Files.ReadWrite.AppFolder")
    assert not MS.has_scope(["openid", "offline_access"], "Files.ReadWrite.AppFolder")
    assert not MS.has_scope(["Files.ReadWrite.AppFolderX"], "Files.ReadWrite.AppFolder")


def test_exchanging_the_code_gives_lasting_access_and_who_it_is(fake):
    fake.on("POST", "/oauth2/v2.0/token", Answer(200, {
        "access_token": "a", "refresh_token": "r", "scope": "Files.ReadWrite.AppFolder openid", "id_token": id_token("ms-sub")}))
    granted = MS.exchange("CODE", "https://site.test/cb", "storage")
    assert granted["refresh_token"] == "r" and granted["sub"] == "ms-sub"
    assert "Files.ReadWrite.AppFolder" in granted["scopes"]
    sent = fake.calls[0].data
    assert sent["grant_type"] == "authorization_code" and sent["code"] == "CODE" and sent["client_secret"] == "ms-secret"


def test_without_a_refresh_token_or_an_id_nothing_is_linked(fake):
    fake.on("POST", "/oauth2/v2.0/token", [Answer(200, {"access_token": "a", "id_token": id_token("s")}),
                                           Answer(200, {"access_token": "a", "refresh_token": "r"}),
                                           Answer(400, {"error": "invalid_grant"})])
    for _ in range(3):
        with pytest.raises(ValueError):
            MS.exchange("CODE", "https://site.test/cb", "storage")


def test_dailymotion_says_who_it_is_when_asked_and_its_scopes_are_taken_as_asked(fake):
    fake.on("POST", "api.dailymotion.com/oauth/token", Answer(200, {"access_token": "a", "refresh_token": "r"}))
    fake.on("GET", "api.dailymotion.com/me", Answer(200, {"id": "x2abc"}))
    granted = DM.exchange("CODE", "https://site.test/cb", "video")
    assert granted["sub"] == "x2abc" and granted["scopes"] == [dailymotion_oauth.SCOPE_VIDEOS]
    me = fake.sent("api.dailymotion.com/me")[0]
    assert me.headers["Authorization"] == "Bearer a"


# --- access tokens -------------------------------------------------------------------------

def test_an_access_token_is_made_once_and_kept_for_less_than_its_life(fake):
    fake.on("POST", "/oauth2/v2.0/token", Answer(200, {"access_token": "tok", "expires_in": 3600}))
    acc = account()
    assert MS.access_token(acc) == "tok" and MS.access_token(acc) == "tok"
    assert len(fake.calls) == 1
    sent = fake.calls[0].data
    assert sent["grant_type"] == "refresh_token" and sent["refresh_token"] == "refresh"     # decrypted, never stored plain
    assert "offline_access" in sent["scope"]                # so Microsoft hands out the next refresh token


def test_a_new_refresh_token_is_kept_on_the_account_for_the_route_to_commit(fake):
    fake.on("POST", "/oauth2/v2.0/token", Answer(200, {"access_token": "tok", "refresh_token": "refresh-2"}))
    acc = account()
    MS.access_token(acc)
    assert acc.refresh_token_encrypted == "enc:refresh-2" and acc._rotated is True


def test_access_taken_away_at_the_service_means_connect_again(fake):
    fake.on("POST", "/oauth2/v2.0/token", Answer(400, {"error": "invalid_grant"}))
    with pytest.raises(AccountLost):
        MS.access_token(account())
    with pytest.raises(AccountLost):
        MS.access_token(account(refresh_token_encrypted=""))


def test_a_service_that_is_down_is_not_a_lost_account(fake):
    fake.on("POST", "/oauth2/v2.0/token", Answer(503, {}))
    with pytest.raises(ProviderDown):
        MS.access_token(account())


def test_a_refused_token_is_replaced_once_then_the_account_counts_as_lost(fake):
    fake.on("POST", "/oauth2/v2.0/token", Answer(200, {"access_token": "tok"}))
    fake.on("GET", "graph.microsoft.com/v1.0/me/drive", [Answer(401, {}), Answer(200, {"ok": True})])
    assert MS.api(account(), "GET", "https://graph.microsoft.com/v1.0/me/drive").status_code == 200
    fake.rules = [r for r in fake.rules if "me/drive" not in r[1]]
    fake.on("GET", "graph.microsoft.com/v1.0/me/drive", Answer(401, {}))
    with pytest.raises(AccountLost):
        MS.api(account(id=8), "GET", "https://graph.microsoft.com/v1.0/me/drive")


def test_disconnecting_dailymotion_ends_its_token_there(fake):
    fake.on("POST", "api.dailymotion.com/oauth/token", Answer(200, {"access_token": "tok"}))
    fake.on("GET", "api.dailymotion.com/logout", Answer(200, {}))
    DM.revoke(account())
    assert fake.sent("api.dailymotion.com/logout")


def test_revoking_never_fails_the_disconnect(fake):
    fake.on("POST", "api.dailymotion.com/oauth/token", Answer(400, {"error": "invalid_grant"}))
    DM.revoke(account())            # nothing raised: the token is forgotten here whatever the service says
