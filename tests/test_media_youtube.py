"""The YouTube provider (ruqqus/helpers/media/youtube.py) and the rules around video,
with YouTube's side played by the fake from test_media_google. The real service needs
Google's approval of the app before it can be tried."""
import json

import pytest

from ruqqus.helpers.media import google_oauth as go, rules, youtube
from ruqqus.helpers.media.base import ProviderDown
from test_media_google import Answer, TOKEN_OK, account, asset, google, no_real_decrypt  # noqa: F401  (fixtures)

SESSION = "https://www.googleapis.com/upload/youtube/v3/videos?uploadType=resumable&upload_id=SESSION&part=snippet,status"


def linked(video=True, **settings):
    acc = account()
    acc.scopes = " ".join(["openid", go.SCOPE_DRIVE] + ([go.SCOPE_YOUTUBE] if video else []))
    for key, value in settings.items():
        acc.set_setting(key, value)
    return acc


def clip(**kw):
    base = dict(id=900, kind="video", ext="", size=5_000_000, provider_ref="")
    base.update(kw)
    return asset(**base)


def provider(fake):
    fake.on("POST", "oauth2.googleapis.com/token", TOKEN_OK)
    return youtube.YouTubeProvider()


def error(reason, status=403):
    return Answer(status, {"error": {"errors": [{"reason": reason}], "code": status}})


# --- rules -----------------------------------------------------------------------------

@pytest.mark.parametrize("url, video_id", [
    ("https://www.youtube.com/watch?v=dQw4w9WgXcQ", "dQw4w9WgXcQ"),
    ("https://youtu.be/dQw4w9WgXcQ?t=3", "dQw4w9WgXcQ"),
    ("https://www.youtube.com/watch?list=x&v=dQw4w9WgXcQ&t=1", "dQw4w9WgXcQ"),
    ("https://m.youtube.com/shorts/dQw4w9WgXcQ", "dQw4w9WgXcQ"),
    ("https://youtube.com/embed/dQw4w9WgXcQ", "dQw4w9WgXcQ"),
    ("https://evil.example/watch?v=dQw4w9WgXcQ", None),
    ("https://notyoutube.com/watch?v=dQw4w9WgXcQ", None),
    ("https://www.youtube.com/watch?v=tooshort", None),
    ("https://www.youtube.com/watch?v=dQw4w9WgXcQextra", None),
    ("", None), (None, None),
])
def test_the_video_id_is_read_from_a_youtube_link_only(url, video_id):
    assert rules.youtube_id(url) == video_id


def test_a_title_youtube_accepts():
    assert rules.video_title("  My <b>clip</b>\n today ") == "My bclip/b today"
    assert len(rules.video_title("x" * 300)) == rules.VIDEO_TITLE_MAX
    assert rules.video_title("") == "Video" and rules.video_title(None, "clip.mp4") == "clip.mp4"
    assert "<" not in rules.video_title("<<>>a") and ">" not in rules.video_title("<<>>a")


def test_unlisted_is_the_default_and_private_is_never_asked_for():
    assert rules.VIDEO_VISIBILITY[0] == "unlisted"
    assert rules.video_visibility("public") == "public"
    for other in (None, "", "private", "PUBLIC", "anything"):
        assert rules.video_visibility(other) == "unlisted"


# --- starting an upload -------------------------------------------------------------------

def test_an_upload_opens_a_session_on_the_members_channel(google):
    p = provider(google)
    google.on("POST", "/upload/youtube/v3/videos", Answer(200, {}, headers={"Location": SESSION}))
    acc, a = linked(), clip()

    upload = p.begin_upload(acc, a, "https://site.test", {"title": "My <first> clip", "visibility": "public", "site": "Ruqqus"})

    assert upload == {"url": SESSION, "method": "PUT", "headers": {}}
    assert "ya29" not in json.dumps(upload)                  # no credential leaves the server
    start = google.sent("/upload/youtube/v3/videos")[0]
    assert "uploadType=resumable" in start.url and "part=snippet,status" in start.url
    assert start.json == {
        "snippet": {"title": "My first clip", "description": "Uploaded from Ruqqus.", "categoryId": "22"},
        "status": {"privacyStatus": "public", "embeddable": True, "selfDeclaredMadeForKids": False},
    }
    assert start.headers["X-Upload-Content-Length"] == "5000000" and start.headers["X-Upload-Content-Type"] == "video/*"
    assert start.headers["Origin"] == "https://site.test"


def test_the_members_saved_choice_is_used_and_the_default_is_unlisted(google):
    p = provider(google)
    google.on("POST", "/upload/youtube/v3/videos", Answer(200, {}, headers={"Location": SESSION}))
    p.begin_upload(linked(), clip(), "https://site.test", {"title": "a"})
    p.begin_upload(linked(youtube_visibility="public"), clip(id=901), "https://site.test", {"title": "a"})
    p.begin_upload(linked(youtube_visibility="public"), clip(id=902), "https://site.test", {"title": "a", "visibility": "unlisted"})
    asked = [c.json["status"]["privacyStatus"] for c in google.sent("/upload/youtube/v3/videos")]
    assert asked == ["unlisted", "public", "unlisted"]


def test_without_youtube_access_the_member_is_asked_for_it_and_google_is_not_called(google):
    p = provider(google)
    with pytest.raises(rules.MediaError) as err:
        p.begin_upload(linked(video=False), clip(), "https://site.test", {"title": "a"})
    assert err.value.need == "video" and err.value.settings == "/settings/media/google/connect?want=video"
    assert err.value.code == 409
    assert google.calls == []


@pytest.mark.parametrize("answer, words, need", [
    (error("youtubeSignupRequired", 401), "no YouTube channel", None),      # YouTube answers this one with 401
    (error("youtubeSignupRequired", 403), "no YouTube channel", None),
    (error("insufficientPermissions"), "allow uploads", "video"),
    (error("quotaExceeded"), "not taking more uploads", None),
    (error("uploadLimitExceeded", 400), "not taking more uploads", None),
])
def test_youtubes_refusals_are_explained(google, answer, words, need):
    p = provider(google)
    google.on("POST", "/upload/youtube/v3/videos", answer)
    with pytest.raises(rules.MediaError) as err:
        p.begin_upload(linked(), clip(), "https://site.test", {"title": "a"})
    assert words in err.value.message and err.value.need == need


def test_a_missing_channel_does_not_cost_the_member_their_connection(google):
    # 401 normally means the token is no good; here it means "no channel", and the Drive side still works
    from ruqqus.helpers.media.base import AccountLost
    p = provider(google)
    google.on("POST", "/upload/youtube/v3/videos", error("youtubeSignupRequired", 401))
    try:
        p.begin_upload(linked(), clip(), "https://site.test", {"title": "a"})
    except AccountLost:
        pytest.fail("a missing YouTube channel was treated as lost access")
    except rules.MediaError:
        pass
    assert len(google.sent("/upload/youtube/v3/videos")) == 1      # asked once, not retried as a bad token
    assert len(google.sent("oauth2.googleapis.com/token")) == 1


def test_youtube_failing_is_only_temporary(google):
    p = provider(google)
    google.on("POST", "/upload/youtube/v3/videos", Answer(503, {}))
    with pytest.raises(ProviderDown):
        p.begin_upload(linked(), clip(), "https://site.test", {"title": "a"})


# --- finishing ------------------------------------------------------------------------------

def started(google, wanted="unlisted"):
    p = provider(google)
    google.on("POST", "/upload/youtube/v3/videos", Answer(200, {}, headers={"Location": SESSION}))
    acc, a = linked(), clip()
    p.begin_upload(acc, a, "https://site.test", {"title": "a", "visibility": wanted})
    return p, acc, a


def test_finishing_asks_the_session_for_the_video(google):
    p, acc, a = started(google)
    google.on("PUT", "/upload/youtube/v3/videos", Answer(201, {"id": "dQw4w9WgXcQ", "status": {"privacyStatus": "unlisted", "uploadStatus": "uploaded"}}))
    info = p.finish_upload(acc, a)
    assert info == {"ref": "dQw4w9WgXcQ", "size": 5_000_000, "checksum": "", "restricted": False}
    ask = [c for c in google.sent("/upload/youtube/v3/videos") if c.method == "PUT"][0]
    assert ask.url == SESSION and ask.headers["Content-Range"] == "bytes */5000000" and ask.headers["Content-Length"] == "0"
    a.provider_ref = info["ref"]
    assert p.link(a) == "https://www.youtube.com/watch?v=dQw4w9WgXcQ"
    assert rules.youtube_id(p.link(a)) == "dQw4w9WgXcQ"      # what the anonymous-post rule looks for


def test_a_video_youtube_keeps_private_is_reported_as_restricted(google):
    # an API project YouTube has not approved yet: every upload is forced private and cannot be embedded
    p, acc, a = started(google, wanted="unlisted")
    google.on("PUT", "/upload/youtube/v3/videos", Answer(200, {"id": "dQw4w9WgXcQ", "status": {"privacyStatus": "private", "uploadStatus": "uploaded"}}))
    assert p.finish_upload(acc, a)["restricted"] is True


@pytest.mark.parametrize("answer, words", [
    (Answer(308, None, headers={"Range": "bytes=0-99"}), "did not finish"),
    (Answer(404, {}), "expired"),
    (Answer(200, {"id": "dQw4w9WgXcQ", "status": {"uploadStatus": "rejected", "rejectionReason": "length"}}), "did not accept"),
])
def test_an_upload_that_did_not_complete_is_refused(google, answer, words):
    p, acc, a = started(google)
    google.on("PUT", "/upload/youtube/v3/videos", answer)
    with pytest.raises(rules.MediaError) as err:
        p.finish_upload(acc, a)
    assert words in err.value.message


def test_finishing_without_a_started_upload_is_refused(google):
    p = provider(google)
    with pytest.raises(rules.MediaError) as err:
        p.finish_upload(linked(), clip(id=12345))
    assert "expired" in err.value.message


def test_youtube_plays_the_video_itself_and_is_never_served_by_the_site():
    p = youtube.YouTubeProvider()
    assert p.served is False and "youtube" not in rules.SERVED
    assert rules.provider_for("google", "video") == "youtube"


# --- wiring ---------------------------------------------------------------------------------

def test_an_own_uploaded_video_is_kept_off_anonymous_posts():
    from pathlib import Path
    root = Path(__file__).resolve().parent.parent / "ruqqus"
    posts = (root / "routes" / "posts.py").read_text(encoding="utf-8")
    # both when the post is made and when its link is edited later
    assert 'media_attach.link_refusal(g.db, v.id, flag(request.form, "anonymous"), url)' in posts
    assert "media_attach.link_refusal_for(g.db, primary, url) if url_changed else None" in posts
    attach = (root / "helpers" / "media" / "attach.py").read_text(encoding="utf-8")
    assert 'filter_by(user_id=author_id, provider="youtube", provider_ref=video_id)' in attach


def test_only_the_authors_own_video_on_a_post_that_hides_them_is_refused(monkeypatch):
    from ruqqus.helpers.media import attach
    monkeypatch.setattr(attach, "own_video", lambda db, author_id, url: object() if "mine" in url else None)
    assert attach.link_refusal(None, 1, True, "https://youtu.be/mine") == attach.ANONYMOUS_VIDEO
    assert attach.link_refusal(None, 1, False, "https://youtu.be/mine") is None      # a normal post
    assert attach.link_refusal(None, 1, True, "https://youtu.be/someone-elses") is None
    # an edit asks about the post itself
    from types import SimpleNamespace as NS
    assert attach.link_refusal_for(None, NS(author_id=1, is_anonymous=True), "https://youtu.be/mine") == attach.ANONYMOUS_VIDEO
    assert attach.link_refusal_for(None, NS(author_id=1, is_anonymous=False), "https://youtu.be/mine") is None


def test_the_page_script_and_the_routes_agree_about_video():
    from pathlib import Path
    root = Path(__file__).resolve().parent.parent / "ruqqus"
    script = (root / "assets" / "js" / "media_upload.js").read_text(encoding="utf-8")
    routes = (root / "routes" / "media.py").read_text(encoding="utf-8")
    for key in ("video_visibility", '"video"'):
        assert key in routes[routes.index("def media_status("):routes.index("def media_upload_begin(")]
    assert "s.video_visibility" in script and "if (s.video) window.PostEditor.register('video'" in script
    assert "error.need === 'video'" in script and "asset.link" in script
    for value in rules.VIDEO_VISIBILITY:
        assert f"['{value}'," in script, value
    page = (root / "templates" / "settings_media.html").read_text(encoding="utf-8")
    assert 'action="/settings/media/google/video"' in page and '@app.post("/settings/media/google/video")' in routes
    assert "/settings/media/google/connect?want=video" in page
    # what YouTube's API terms ask a client to show
    assert "https://www.youtube.com/t/terms" in page and "https://policies.google.com/privacy" in page
