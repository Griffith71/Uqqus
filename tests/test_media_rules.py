"""Rules for media stored in a member's own linked account (ruqqus/helpers/media/rules.py)."""
from types import SimpleNamespace as NS

import pytest

from ruqqus.helpers.media import rules as mr

JPEG = b"\xff\xd8\xff\xe0" + b"\x00" * 60
PNG = b"\x89PNG\r\n\x1a\n" + b"\x00" * 56
GIF = b"GIF89a" + b"\x00" * 58
WEBP = b"RIFF\x00\x00\x00\x00WEBPVP8 " + b"\x00" * 48
WAV = b"RIFF\x00\x00\x00\x00WAVEfmt " + b"\x00" * 48
MP3_ID3 = b"ID3\x04\x00" + b"\x00" * 59
MP3_FRAME = b"\xff\xfb\x90\x00" + b"\x00" * 60
OGG = b"OggS" + b"\x00" * 60
FLAC = b"fLaC" + b"\x00" * 60
M4A = b"\x00\x00\x00\x20ftypM4A " + b"\x00" * 52


# --- what a file really is ---------------------------------------------------------

@pytest.mark.parametrize("head, ext", [
    (JPEG, "jpg"), (PNG, "png"), (GIF, "gif"), (WEBP, "webp"), (WAV, "wav"),
    (MP3_ID3, "mp3"), (MP3_FRAME, "mp3"), (OGG, "ogg"), (FLAC, "flac"), (M4A, "m4a"),
])
def test_the_first_bytes_decide_the_type(head, ext):
    assert mr.sniff(head) == ext


@pytest.mark.parametrize("head", [
    b"", b"<svg xmlns='http://www.w3.org/2000/svg'><script>alert(1)</script></svg>",
    b"<!DOCTYPE html><html>", b"<?xml version='1.0'?>", b"%PDF-1.7", b"MZ\x90\x00", b"PK\x03\x04",
    b"RIFF\x00\x00\x00\x00AVI LIST",
])
def test_anything_that_could_run_or_is_unknown_has_no_type(head):
    assert mr.sniff(head) is None


def test_a_file_named_as_an_image_must_be_one():
    assert mr.check_uploaded("image", PNG, 1000) == "png"
    for fake in (b"<svg onload=alert(1)>", b"<html>", MP3_ID3):
        with pytest.raises(mr.MediaError):
            mr.check_uploaded("image", fake, 1000)
    with pytest.raises(mr.MediaError):
        mr.check_uploaded("audio", JPEG, 1000)


def test_the_real_type_wins_over_the_name():
    # uploaded as "photo.png" but the bytes are a JPEG: it is served as a JPEG
    assert mr.check_request("image", "photo.png", 10) == "png"
    assert mr.check_uploaded("image", JPEG, 10) == "jpg"


def test_no_type_that_can_carry_script_is_ever_served():
    for ext, (kind, mime) in mr.TYPES.items():
        assert kind in (mr.IMAGE, mr.AUDIO)
        assert mime.startswith(("image/", "audio/"))
        assert "svg" not in mime and "html" not in mime and "xml" not in mime


# --- before the upload starts ------------------------------------------------------

def test_a_request_names_a_kind_a_supported_file_and_a_size():
    assert mr.check_request("image", "Holiday Photo.JPEG", 5 * mr.MIB) == "jpg"
    assert mr.check_request("audio", "song.mp3", mr.MIB) == "mp3"
    assert mr.check_request("video", "clip.mov", 900 * mr.MIB) == ""     # the video site decides


@pytest.mark.parametrize("kind, name, size, words", [
    ("image", "a.svg", 10, "not supported"),
    ("image", "a.mp3", 10, "not supported"),
    ("audio", "a.png", 10, "not supported"),
    ("image", "noextension", 10, "not supported"),
    ("document", "a.pdf", 10, "image, audio or video"),
    ("image", "a.png", 0, "empty"),
    ("image", "a.png", "abc", "size"),
    ("image", "a.png", None, "size"),
])
def test_bad_requests_are_refused_with_a_message(kind, name, size, words):
    with pytest.raises(mr.MediaError) as err:
        mr.check_request(kind, name, size)
    assert words in err.value.message


def test_size_limits():
    assert mr.check_request("image", "a.png", mr.SIZE_MAX["image"]) == "png"
    with pytest.raises(mr.MediaError) as err:
        mr.check_request("image", "a.png", mr.SIZE_MAX["image"] + 1)
    assert err.value.code == 413 and "20 MB" in err.value.message
    with pytest.raises(mr.MediaError):
        mr.check_request("audio", "a.mp3", mr.SIZE_MAX["audio"] + 1)
    # the claimed size is not trusted afterwards either
    with pytest.raises(mr.MediaError):
        mr.check_uploaded("image", PNG, mr.SIZE_MAX["image"] + 1)


def test_everyone_logged_in_may_upload_but_not_the_suspended():
    assert mr.may_upload(NS(is_suspended=False, is_deleted=False))
    assert not mr.may_upload(None)
    assert not mr.may_upload(NS(is_suspended=True, is_deleted=False))
    assert not mr.may_upload(NS(is_suspended=False, is_deleted=True))


# --- providers ---------------------------------------------------------------------

def test_a_google_account_takes_all_three_kinds():
    assert mr.provider_for("google", "image") == "gdrive"
    assert mr.provider_for("google", "audio") == "gdrive"
    assert mr.provider_for("google", "video") == "youtube"
    assert mr.provider_for("dev", "video") is None
    assert mr.provider_for("nothing", "image") is None


def test_only_plain_storage_is_served_by_the_site():
    assert "gdrive" in mr.SERVED and "youtube" not in mr.SERVED


# --- addresses ---------------------------------------------------------------------

TOKEN = "AbCdEfGhIjKlMnOpQrStUv-_"


def test_an_address_round_trips():
    path = mr.media_path(46655, TOKEN, "jpg")
    assert path == f"/media/zzz/{TOKEN}.jpg"
    assert mr.parse_path(path) == (46655, TOKEN, "jpg")
    assert mr.b36(0) == "0" and mr.b36(35) == "z" and mr.b36(36) == "10"


@pytest.mark.parametrize("path", [
    "/media/zz/short.jpg", "/media/ZZ/" + TOKEN + ".jpg", f"/media/zz/{TOKEN}", f"/media/zz/{TOKEN}.jpg/x",
    f"/media/zz/../{TOKEN}.jpg", f"/media//{TOKEN}.jpg", "", None, f"/x/media/zz/{TOKEN}.jpg",
])
def test_anything_else_is_not_an_address(path):
    assert mr.parse_path(path) is None


def test_mentions_are_found_in_a_link_and_in_text_once_each():
    a, b = mr.media_path(10, TOKEN, "png"), mr.media_path(11, TOKEN, "mp3")
    text = f"look ![]({a}) and again ![]({a})\n[song]({b})"
    assert mr.find_refs(f"https://example.test{a}", text) == [(10, TOKEN, "png"), (11, TOKEN, "mp3")]
    assert mr.find_refs("no media here", None, "") == []
    # a longer name is a different file, never this one with extra letters
    assert (10, TOKEN, "png") not in mr.find_refs(f"{a}x")
    assert (10, TOKEN, "png") not in mr.find_refs(f"{a[:-4]}x.png")


# --- who may see a file --------------------------------------------------------------

def test_a_file_in_a_live_post_is_public():
    assert mr.access(mr.READY, attached_live=True, is_owner=False) == mr.PUBLIC


def test_a_file_nobody_attached_is_only_its_owners():
    assert mr.access(mr.READY, attached_live=False, is_owner=True) == mr.PRIVATE
    assert mr.access(mr.READY, attached_live=False, is_owner=False) is None


def test_a_file_in_a_post_made_for_a_circle_is_private_to_the_people_allowed_to_see_it():
    # never PUBLIC: a public answer is cached by CDNs and carries no session, so it could not be taken back
    for audience in (1, 2, 3):
        assert mr.access(mr.READY, True, False, audience, True) == mr.PRIVATE          # a member of the Circle
        assert mr.access(mr.READY, True, True, audience, False) == mr.PRIVATE          # its owner, always
        assert mr.access(mr.READY, True, False, audience, False) is None               # anyone else, and a visitor
        assert mr.access(mr.READY, True, False, audience) is None                      # allowed defaults to nobody


def test_an_audience_of_nothing_still_means_a_public_file():
    assert mr.access(mr.READY, True, False, 0, False) == mr.PUBLIC
    assert mr.access(mr.READY, True, False) == mr.PUBLIC


def test_allowed_never_opens_a_file_that_is_not_attached_or_not_ready():
    assert mr.access(mr.READY, False, False, 1, True) is None
    for status in (mr.PENDING, mr.RESTRICTED, mr.REMOVED, mr.GONE):
        assert mr.access(status, True, True, 1, True) is None


@pytest.mark.parametrize("status", [mr.PENDING, mr.RESTRICTED, mr.REMOVED, mr.GONE])
def test_a_file_that_is_not_ready_is_nobodys(status):
    assert mr.access(status, attached_live=True, is_owner=True) is None


def test_removal_is_final_and_a_reconnect_brings_files_back():
    assert mr.can_change(mr.PENDING, mr.READY) and mr.can_change(mr.READY, mr.GONE)
    assert mr.can_change(mr.GONE, mr.READY)
    for status in mr.STATUSES:
        assert not mr.can_change(mr.REMOVED, status)
    assert not mr.can_change(mr.READY, mr.PENDING)


# --- ranges (audio seeking) ------------------------------------------------------------

@pytest.mark.parametrize("header, expected", [
    ("bytes=0-99", (0, 99)), ("bytes=100-", (100, 999)), ("bytes=-100", (900, 999)),
    ("bytes=0-5000", (0, 999)), ("bytes=999-999", (999, 999)),
    (None, None), ("", None), ("bytes=-", None), ("bytes=1000-", None), ("bytes=5-2", None),
    ("bytes=0-1,5-9", None), ("items=0-5", None), ("bytes=a-b", None), ("bytes=-0", None),
])
def test_ranges(header, expected):
    assert mr.parse_range(header, 1000) == expected


def test_a_range_needs_a_known_size():
    assert mr.parse_range("bytes=0-10", 0) is None
    assert mr.parse_range("bytes=0-10", None) is None


def test_a_real_account_is_used_before_the_test_storage():
    # someone who tried the test storage and then linked Google must get Google
    assert mr.in_order(["dev", "google"]) == ["google", "dev"]
    assert mr.in_order(["google", "dev"]) == ["google", "dev"]
    assert mr.in_order(["dev"]) == ["dev"] and mr.in_order([]) == []
    from pathlib import Path
    routes = (Path(__file__).resolve().parent.parent / "ruqqus" / "routes" / "media.py").read_text(encoding="utf-8")
    assert "rules.in_order(by_kind)" in routes[routes.index("def _account_for("):routes.index("def _my_asset(")]
