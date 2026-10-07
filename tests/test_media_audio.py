"""Audio from linked storage plays in place: a link to this site's own /media/ sound
address becomes a player in the sanitizer (ruqqus/helpers/sanitize.py). Nothing else may."""
import pytest
from bs4 import BeautifulSoup

import markdown_harness as mh
from ruqqus.helpers.media import rules

TOKEN = "AbCdEfGhIjKlMnOpQrStUv-_"
SOUND = rules.media_path(77, TOKEN, "mp3")
PICTURE = rules.media_path(78, TOKEN, "png")


@pytest.fixture
def md(monkeypatch):
    return lambda text: mh.render(monkeypatch, text)


def players(html):
    return BeautifulSoup(html, "html.parser").find_all("audio")


def test_a_link_to_an_own_sound_file_becomes_a_player(md):
    html = md(f"listen\n\n[audio]({SOUND})")
    found = players(html)
    assert len(found) == 1
    tag = found[0]
    assert tag["src"] == SOUND and tag.has_attr("controls") and tag["preload"] == "none"
    assert set(tag.attrs) == {"src", "controls", "preload", "class"}      # nothing else, e.g. no autoplay
    assert "<a" not in html.replace("<audio", "")                          # the link itself is gone


@pytest.mark.parametrize("ext", ["mp3", "m4a", "ogg", "wav", "flac"])
def test_every_supported_sound_type_plays(md, ext):
    assert len(players(md(f"[audio]({rules.media_path(5, TOKEN, ext)})"))) == 1


@pytest.mark.parametrize("href", [
    PICTURE,                                           # a picture address is not a sound
    f"https://evil.example{SOUND}",                    # someone else's host
    f"//evil.example{SOUND}",
    f"{SOUND}?x=1",                                    # not exactly our address
    f"{SOUND}#t",
    "/media/zz/short.mp3",                             # not a real token
    "https://example.com/song.mp3",
    "/assets/sound.mp3",
])
def test_no_other_link_becomes_a_player(md, href):
    html = md(f"[audio]({href})")
    assert players(html) == []


@pytest.mark.parametrize("raw", [
    f'<audio src="{SOUND}" controls autoplay></audio>',
    '<audio src="https://evil.example/x.mp3" autoplay onplay="alert(1)"></audio>',
    f'<audio controls><source src="{SOUND}"></audio>',
    '<video src="https://evil.example/x.mp4" autoplay></video>',
])
def test_a_player_a_member_writes_themselves_never_gets_through(md, raw):
    html = md(raw)
    assert players(html) == []
    assert "autoplay" not in html and "onplay" not in html and "<video" not in html and "<source" not in html


def test_an_address_shown_as_code_stays_text(md):
    html = md(f"`{SOUND}`\n\n    [audio]({SOUND})")
    assert players(html) == []


def test_the_rule_is_exact():
    assert rules.audio_address(SOUND) == SOUND
    for other in (PICTURE, f"https://site.test{SOUND}", SOUND + "x", SOUND + "?a", "", None):
        assert rules.audio_address(other) is None


def test_an_uploaded_sound_is_inserted_as_that_link():
    from pathlib import Path
    root = Path(__file__).resolve().parent.parent / "ruqqus"
    model = (root / "classes" / "media.py").read_text(encoding="utf-8")
    assert 'data["markdown"] = f"[audio]({self.path})"' in model
    uploader = (root / "assets" / "js" / "media_upload.js").read_text(encoding="utf-8")
    assert "['image', 'audio'].forEach" in uploader
    for name in ("main.scss", "main_dark.scss"):
        assert ".media-audio {" in (root / "assets" / "style" / name).read_text(encoding="utf-8"), name
    for name in ("comments.html", "submission.html"):
        assert 'data-media-upload="audio"' in (root / "templates" / name).read_text(encoding="utf-8"), name
