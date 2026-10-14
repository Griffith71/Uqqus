"""The OneDrive provider (ruqqus/helpers/media/onedrive.py), with Microsoft Graph played by the
fake from test_media_google. The real service is tried by hand with an app registration."""
import pytest

from ruqqus.helpers.media import oauth, onedrive, rules
from ruqqus.helpers.media.base import MediaGone, ProviderDown
from test_media_google import Answer, FakeGoogle, account, asset, no_real_decrypt  # fixtures and fakes shared with the Google tests

GRAPH = "https://graph.microsoft.com/v1.0"
SESSION = "https://my.microsoftpersonalcontent.com/up/SESSION"
TOKEN = Answer(200, {"access_token": "ms-access", "expires_in": 3600})


@pytest.fixture
def graph(monkeypatch):
    fake = FakeGoogle()
    monkeypatch.setattr(oauth, "_http", fake)
    monkeypatch.setattr(oauth, "_redis", lambda: None)
    monkeypatch.setenv("MICROSOFT_CLIENT_ID", "ms-id")
    monkeypatch.setenv("MICROSOFT_CLIENT_SECRET", "ms-secret")
    oauth._local_tokens.clear()
    fake.on("POST", "/oauth2/v2.0/token", TOKEN)
    return fake


def item(**kw):
    base = {"id": "ITEM1", "size": 2048, "file": {"hashes": {"quickXorHash": "QXH=", "sha1Hash": "SHA1"}}}
    base.update(kw)
    return base


# --- uploading ------------------------------------------------------------------------------

def test_an_upload_goes_to_a_session_in_the_sites_own_app_folder(graph):
    graph.on("POST", "/me/drive/special/approot/children", Answer(201, {"id": "F"}))
    graph.on("POST", "createUploadSession", Answer(200, {"uploadUrl": SESSION}))
    acc, a = account(), asset()
    upload = onedrive.OneDriveProvider().begin_upload(acc, a, "https://site.test")
    assert upload["url"] == SESSION and upload["method"] == "PUT" and upload["headers"] == {}
    # Graph takes pieces of at most 60 MiB, in multiples of 320 KiB
    assert upload["chunk"] % (320 * 1024) == 0 and upload["chunk"] <= 60 * 1024 * 1024
    session = graph.sent("createUploadSession")[0]
    assert session.url.startswith(f"{GRAPH}/me/drive/special/approot:/Images/")
    assert session.url.endswith(f"-{rules.b36(a.id)}.jpg:/createUploadSession")
    assert session.headers["Authorization"] == "Bearer ms-access"
    # the session address is the only thing the browser gets: no token in it, none in the answer
    assert "ms-access" not in str(upload)
    assert a.provider_ref.startswith(onedrive.PATH_PREFIX + "Images/")
    assert acc.setting("folders") == {"image": True}


def test_the_folder_is_made_once_and_already_there_is_fine(graph):
    graph.on("POST", "/me/drive/special/approot/children", Answer(409, {"error": {"code": "nameAlreadyExists"}}))
    graph.on("POST", "createUploadSession", Answer(200, {"uploadUrl": SESSION}))
    acc = account()
    p = onedrive.OneDriveProvider()
    p.begin_upload(acc, asset(kind="audio", ext="mp3"), "https://site.test")
    p.begin_upload(acc, asset(id=46656, kind="audio", ext="mp3"), "https://site.test")
    assert len(graph.sent("/approot/children")) == 1
    assert "/Audio/" in graph.sent("createUploadSession")[0].url


def test_a_deleted_folder_is_made_again(graph):
    graph.on("POST", "/me/drive/special/approot/children", Answer(201, {}))
    graph.on("POST", "createUploadSession", [Answer(404, {}), Answer(200, {"uploadUrl": SESSION})])
    acc = account()
    acc.set_setting("folders", {"image": True})
    assert onedrive.OneDriveProvider().begin_upload(acc, asset(), "https://site.test")["url"] == SESSION
    assert len(graph.sent("/approot/children")) == 1


def test_a_full_onedrive_is_said_plainly(graph):
    graph.on("POST", "/me/drive/special/approot/children", Answer(201, {}))
    graph.on("POST", "createUploadSession", Answer(507, {"error": {"code": "quotaLimitReached"}}))
    with pytest.raises(rules.MediaError) as e:
        onedrive.OneDriveProvider().begin_upload(account(), asset(), "https://site.test")
    assert e.value.code == 507 and "OneDrive is full" in e.value.message


def test_onedrive_not_answering_is_temporary(graph):
    graph.on("POST", "/me/drive/special/approot/children", Answer(201, {}))
    graph.on("POST", "createUploadSession", Answer(503, {}))
    with pytest.raises(ProviderDown):
        onedrive.OneDriveProvider().begin_upload(account(), asset(), "https://site.test")


def test_finishing_finds_the_file_by_its_path_and_records_its_hash(graph):
    graph.on("GET", "/me/drive/special/approot:/", Answer(200, item(size=2047)))
    a = asset(provider_ref=onedrive.PATH_PREFIX + "Images/ruqqus-zzz.jpg")
    info = onedrive.OneDriveProvider().finish_upload(account(), a)
    assert info == {"ref": "ITEM1", "size": 2047, "checksum": "QXH="}


@pytest.mark.parametrize("answer", [Answer(404, {}), Answer(200, item(file={})), Answer(200, item(deleted={"state": "deleted"}))])
def test_a_file_that_did_not_arrive_is_said_so(graph, answer):
    graph.on("GET", "/me/drive/special/approot:/", answer)
    with pytest.raises(rules.MediaError):
        onedrive.OneDriveProvider().finish_upload(account(), asset(provider_ref=onedrive.PATH_PREFIX + "Images/x.jpg"))


# --- showing --------------------------------------------------------------------------------

def test_the_checksum_is_read_again_to_spot_a_swapped_file(graph):
    graph.on("GET", f"{GRAPH}/me/drive/items/ITEM1", Answer(200, item(file={"hashes": {"quickXorHash": "OTHER"}})))
    assert onedrive.OneDriveProvider().checksum(account(), asset(provider_ref="ITEM1")) == "OTHER"


def test_a_file_that_is_gone_is_gone(graph):
    graph.on("GET", f"{GRAPH}/me/drive/items/ITEM1", Answer(404, {}))
    with pytest.raises(MediaGone):
        onedrive.OneDriveProvider().checksum(account(), asset(provider_ref="ITEM1"))


def test_a_range_is_asked_for_and_answered_as_a_range(graph):
    graph.on("GET", "/content", Answer(206, None, headers={"Content-Length": "10"}, chunks=[b"0123456789"]))
    stream = onedrive.OneDriveProvider().open(account(), asset(provider_ref="ITEM1", size=100), (10, 19))
    assert b"".join(stream.chunks) == b"0123456789" and stream.size == 10 and stream.byte_range == (10, 19) and stream.total == 100
    sent = graph.sent("/content")[0]
    assert sent.headers["Range"] == "bytes=10-19" and sent.stream is True


def test_the_whole_file_when_onedrive_ignores_the_range(graph):
    graph.on("GET", "/content", Answer(200, None, headers={"Content-Length": "100"}, chunks=[b"x" * 100]))
    stream = onedrive.OneDriveProvider().open(account(), asset(provider_ref="ITEM1", size=100), (10, 19))
    assert stream.byte_range is None and stream.size == 100


@pytest.mark.parametrize("status", [403, 404, 410])
def test_a_file_onedrive_will_not_give_is_gone(graph, status):
    graph.on("GET", "/content", Answer(status, {}))
    with pytest.raises(MediaGone):
        onedrive.OneDriveProvider().open(account(), asset(provider_ref="ITEM1"))


def test_the_space_left_is_what_onedrive_says(graph):
    graph.on("GET", f"{GRAPH}/me/drive", Answer(200, {"quota": {"remaining": 5 * 1024 ** 3}}))
    assert onedrive.OneDriveProvider().space_left(account()) == 5 * 1024 ** 3
    graph.rules = [r for r in graph.rules if r[1] != f"{GRAPH}/me/drive"]
    graph.on("GET", f"{GRAPH}/me/drive", Answer(200, {}))
    assert onedrive.OneDriveProvider().space_left(account(id=9)) is None


def test_it_is_a_served_provider_named_for_the_member():
    p = onedrive.OneDriveProvider()
    assert p.served and p.name == "onedrive" and p.label == "OneDrive"
    assert "onedrive" in rules.SERVED and rules.PROVIDERS["microsoft"] == {"image": "onedrive", "audio": "onedrive"}
