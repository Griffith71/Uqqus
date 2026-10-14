"""The Dailymotion provider (ruqqus/helpers/media/dailymotion.py) and the rules around a member's
own video, with Dailymotion played by the fake from test_media_google."""
import json
from types import SimpleNamespace as NS

import pytest

from ruqqus.helpers.media import attach, dailymotion, oauth, rules
from ruqqus.helpers.media.base import ProviderDown
from test_media_google import Answer, FakeGoogle, account, asset, no_real_decrypt  # fixtures and fakes shared with the Google tests

UPLOADED = "https://upload-04.dc3.dailymotion.com/files/abc.mp4"
TOKEN = Answer(200, {"access_token": "dm-access", "expires_in": 36000})


@pytest.fixture
def dm(monkeypatch):
    fake = FakeGoogle()
    monkeypatch.setattr(oauth, "_http", fake)
    monkeypatch.setattr(oauth, "_redis", lambda: None)
    monkeypatch.setenv("DAILYMOTION_API_KEY", "dm-key")
    monkeypatch.setenv("DAILYMOTION_API_SECRET", "dm-secret")
    oauth._local_tokens.clear()
    fake.on("POST", "api.dailymotion.com/oauth/token", TOKEN)
    return fake


def clip(**kw):
    base = dict(id=901, kind="video", ext="", size=8_000_000, provider_ref="")
    base.update(kw)
    return asset(**base)


def begin(fake, acc=None, a=None, **details):
    fake.on("GET", "api.dailymotion.com/file/upload", Answer(200, {"upload_url": "https://upload-04.dc3.dailymotion.com/upload?uuid=U&seal=S",
                                                                    "progress_url": "https://upload-04.dc3.dailymotion.com/progress?uuid=U"}))
    acc, a = acc or account(), a or clip()
    upload = dailymotion.DailymotionProvider().begin_upload(acc, a, "https://site.test", details or {"title": "My clip", "site": "Ruqqus"})
    return acc, a, upload


# --- rules -----------------------------------------------------------------------------------

@pytest.mark.parametrize("url, video_id", [
    ("https://www.dailymotion.com/video/x8abcde", "x8abcde"),
    ("https://dailymotion.com/video/x8abcde?playlist=x1", "x8abcde"),
    ("https://www.dailymotion.com/embed/video/k2Hm5PqRst", "k2Hm5PqRst"),
    ("https://dai.ly/x8abcde", "x8abcde"),
    ("https://evil.example/video/x8abcde", None),
    ("https://notdailymotion.com/video/x8abcde", None),
    ("", None), (None, None),
])
def test_the_video_id_is_read_from_a_dailymotion_link_only(url, video_id):
    assert rules.dailymotion_id(url) == video_id


def test_a_link_says_which_site_and_which_video():
    assert rules.video_ref("https://youtu.be/dQw4w9WgXcQ") == ("youtube", "dQw4w9WgXcQ")
    assert rules.video_ref("https://www.dailymotion.com/video/x8abcde") == ("dailymotion", "x8abcde")
    assert rules.video_ref("https://vimeo.com/123") is None
    assert set(rules.HOSTED) == {"youtube", "dailymotion"}


def test_only_dailymotion_s_own_upload_servers_are_passed_on():
    assert dailymotion.upload_address_ok(UPLOADED)
    for bad in ("http://upload-04.dc3.dailymotion.com/files/a.mp4", "https://evil.example/files/a.mp4",
                "https://dailymotion.com.evil.example/a.mp4", "https://user@upload.dailymotion.com/a.mp4",
                "", None, "javascript:alert(1)"):
        assert not dailymotion.upload_address_ok(bad), bad


# --- uploading --------------------------------------------------------------------------------

def test_the_browser_gets_only_dailymotion_s_upload_address_and_sends_a_form(dm):
    _acc, _a, upload = begin(dm)
    assert upload["url"].startswith("https://upload-04.dc3.dailymotion.com/upload")
    assert upload["method"] == "POST" and upload["form_field"] == "file" and upload["answer"] is True
    assert "dm-access" not in json.dumps(upload)


def test_a_member_s_daily_limit_is_said_plainly(dm):
    dm.on("GET", "api.dailymotion.com/file/upload", Answer(403, {"error": {"message": "Upload limit exceeded", "type": "access_forbidden"}}))
    with pytest.raises(rules.MediaError) as e:
        dailymotion.DailymotionProvider().begin_upload(account(), clip(), "https://site.test")
    assert e.value.code == 429 and "today" in e.value.message


def test_dailymotion_not_answering_is_temporary(dm):
    dm.on("GET", "api.dailymotion.com/file/upload", Answer(502, {}))
    with pytest.raises(ProviderDown):
        dailymotion.DailymotionProvider().begin_upload(account(), clip(), "https://site.test")


def test_an_unlisted_video_is_a_private_one_and_the_post_links_its_private_id(dm):
    acc, a, _ = begin(dm, visibility="unlisted", title="My <clip>", site="Ruqqus")
    dm.on("POST", "api.dailymotion.com/me/videos", Answer(200, {"id": "x8abcde", "private_id": "k2Hm5PqRst", "private": True}))
    info = dailymotion.DailymotionProvider().finish_upload(acc, a, UPLOADED)
    assert info == {"ref": "k2Hm5PqRst", "size": a.size, "checksum": ""}
    sent = dm.sent("me/videos")[0].data
    assert sent["url"] == UPLOADED and sent["private"] == "true" and sent["published"] == "true"
    assert sent["title"] == "My clip" and sent["is_created_for_kids"] == "false" and sent["channel"]
    a.provider_ref = info["ref"]
    assert dailymotion.DailymotionProvider().link(a) == "https://www.dailymotion.com/video/k2Hm5PqRst"
    assert rules.video_ref(dailymotion.DailymotionProvider().link(a)) == ("dailymotion", "k2Hm5PqRst")


def test_a_public_video_links_its_own_id(dm):
    acc, a, _ = begin(dm, visibility="public", title="t")
    dm.on("POST", "api.dailymotion.com/me/videos", Answer(200, {"id": "x8abcde", "private_id": "k2Hm5PqRst", "private": False}))
    assert dailymotion.DailymotionProvider().finish_upload(acc, a, UPLOADED)["ref"] == "x8abcde"
    assert dm.sent("me/videos")[0].data["private"] == "false"


def test_anything_but_an_upload_server_address_is_refused_before_dailymotion_is_asked(dm):
    acc, a, _ = begin(dm)
    with pytest.raises(rules.MediaError):
        dailymotion.DailymotionProvider().finish_upload(acc, a, "https://evil.example/x.mp4")
    assert not dm.sent("me/videos")


def test_an_expired_upload_is_said_so(dm):
    with pytest.raises(rules.MediaError) as e:
        dailymotion.DailymotionProvider().finish_upload(account(), clip(id=7777), UPLOADED)
    assert "expired" in e.value.message


def test_a_video_dailymotion_refused_is_not_added(dm):
    acc, a, _ = begin(dm)
    dm.on("POST", "api.dailymotion.com/me/videos", Answer(200, {"id": "x8abcde", "status": "rejected"}))
    with pytest.raises(rules.MediaError):
        dailymotion.DailymotionProvider().finish_upload(acc, a, UPLOADED)


def test_it_is_a_hosting_provider_that_needs_the_browser_s_answer():
    p = dailymotion.DailymotionProvider()
    assert not p.served and p.wants_answer and p.label == "Dailymotion"
    assert "dailymotion" not in rules.SERVED and rules.PROVIDERS["dailymotion"] == {"video": "dailymotion"}


# --- a member's own video stays off anonymous and Circle posts --------------------------------

class _Query:
    def __init__(self, rows):
        self.rows = rows

    def filter_by(self, **kw):
        return _Query([r for r in self.rows if all(getattr(r, k) == v for k, v in kw.items())])

    def first(self):
        return self.rows[0] if self.rows else None


class _DB:
    def __init__(self, rows):
        self.rows = rows

    def query(self, model):
        return _Query(self.rows)


def test_an_own_dailymotion_video_is_found_like_an_own_youtube_one(monkeypatch):
    rows = [NS(user_id=1, provider="dailymotion", provider_ref="k2Hm5PqRst"), NS(user_id=1, provider="youtube", provider_ref="dQw4w9WgXcQ")]
    monkeypatch.setattr(attach, "_models", lambda: (object, object, object))
    db = _DB(rows)
    assert attach.own_video(db, 1, "https://www.dailymotion.com/video/k2Hm5PqRst") is rows[0]
    assert attach.own_video(db, 1, "https://youtu.be/dQw4w9WgXcQ") is rows[1]
    assert attach.own_video(db, 2, "https://www.dailymotion.com/video/k2Hm5PqRst") is None        # someone else's
    assert attach.own_video(db, 1, "https://www.dailymotion.com/video/x0other") is None
    assert "Dailymotion" in attach.ANONYMOUS_VIDEO


def test_the_circle_rule_names_both_sites():
    from ruqqus.helpers import circles
    message = circles.post_refusal(circles.FRIENDS, own_video=True)
    assert "YouTube" in message and "Dailymotion" in message
