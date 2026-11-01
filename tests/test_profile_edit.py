"""The checks behind every place that saves a profile (ruqqus/helpers/profile_edit.py): Settings > Profile, the Edit profile
pop-up and the bots' forms. A check answers with a value or a sentence and assigns nothing."""
import importlib.util
import io
import sys
import types

import pytest
from werkzeug.datastructures import FileStorage

from ruqqus.helpers import profile_edit


# Pillow is in requirements.txt and not in requirements-dev.txt (CI's python job): the picture checks run where it is installed
needs_pillow = pytest.mark.skipif(importlib.util.find_spec("PIL") is None, reason="Pillow is not installed")


def _users():
    from sqlalchemy import create_engine, text
    from sqlalchemy.orm import Session

    db = Session(create_engine("sqlite://"))
    db.execute(text("CREATE TABLE users (id INTEGER PRIMARY KEY, username TEXT, original_username TEXT)"))
    for uid, name in ((1, "ada"), (2, "Big_Admin")):
        db.execute(text("INSERT INTO users VALUES (:i, :n, :n)"), {"i": uid, "n": name})
    return db


# --- display name ------------------------------------------------------------------------------------------------

def test_a_display_name_is_cleaned_or_refused_with_a_sentence():
    assert profile_edit.clean_display_name("  Ada   Lovelace ") == ("Ada Lovelace", None)
    assert profile_edit.clean_display_name("") == ("", None)
    name, problem = profile_edit.clean_display_name("a@b")
    assert name == "" and problem                       # no @, < or > (it must not read as a mention)
    name, problem = profile_edit.clean_display_name("x" * 51)
    assert name == "" and problem


def test_a_clean_name_still_cannot_be_another_accounts_handle_or_the_staffs():
    db = _users()
    assert profile_edit.display_name_refusal(db, 1, "Big Admin")                 # look-alike of another account's @handle
    assert not profile_edit.display_name_refusal(db, 1, "Ada")                   # its own is fine
    assert not profile_edit.display_name_refusal(db, 1, "")                      # empty clears it
    assert profile_edit.display_name_refusal(db, 1, "Site Moderator")            # kept for the staff
    assert profile_edit.display_name_refusal(db, 1, "uqqus", ("uqqus",))         # and for the site
    assert not profile_edit.display_name_refusal(db, 1, "Site Moderator", admin=True)


def test_the_handle_sentence_is_the_callers_to_word():
    db = _users()
    assert profile_edit.display_name_refusal(db, 1, "big admin", handle_message="their own wording") == "their own wording"


# --- job, location, website --------------------------------------------------------------------------------------

def test_details_are_cleaned_together_and_each_error_names_its_field():
    assert profile_edit.clean_details(" Rocket  engineer ", "Berlin", "example.com") == \
        ("Rocket engineer", "Berlin", "https://example.com", {})
    job, location, website, errors = profile_edit.clean_details("<b>x</b>", "y" * 61, "javascript:alert(1)")
    assert (job, location, website) == ("", "", "")
    assert list(errors) == ["profile_job", "profile_location", "profile_website"]       # in the order they are written
    assert profile_edit.DETAIL_FIELDS == tuple(errors)


def test_an_empty_website_asks_nothing_of_the_banned_domain_list():
    assert profile_edit.website_refusal("") is None


# --- bio ---------------------------------------------------------------------------------------------------------

@pytest.fixture
def prepared(monkeypatch):
    """check_bio imports the markdown module (the whole app behind it) when it runs: stood in for here."""
    stand_in = types.SimpleNamespace(preprocess=lambda text: text.strip())
    monkeypatch.setitem(sys.modules, "ruqqus.helpers.markdown", stand_in)


def _render(text):
    return f"<p>{text}</p>"


class _Ban:
    def __init__(self, domain, reason=0):
        self.domain, self.reason, self.reason_text = domain, reason, "a reason"


def test_a_bio_is_rendered_and_checked_in_the_settings_order(prepared):
    ok = profile_edit.check_bio("  hello  ", "", render=_render, bans=lambda html: [])
    assert (ok.code, ok.text, ok.html) == (None, "hello", "<p>hello</p>")
    same = profile_edit.check_bio("hello", "hello", render=_render, bans=lambda html: [])
    assert same.code == "unchanged" and same.message
    cleared = profile_edit.check_bio("", "hello", render=_render, bans=lambda html: [])
    assert (cleared.code, cleared.text) == (None, "")


def test_a_bio_the_browser_sent_with_crlf_counts_and_compares_as_typed(prepared):
    # a form sends CRLF, the text box counts one character, and the stored bio has LF
    assert profile_edit.check_bio("a\r\nb", "a\nb", render=_render, bans=lambda html: []).code == "unchanged"
    near_limit = "x\r\n" * 86                              # 172 characters as typed, 258 with the CR counted as well
    assert len(near_limit) > profile_edit.BIO_MAX and len(profile_edit.newlines(near_limit)) <= profile_edit.BIO_MAX
    assert profile_edit.check_bio(near_limit, "", render=_render, bans=lambda html: []).code is None


def test_a_bio_that_did_not_change_is_never_looked_at_by_the_banned_domain_list(prepared):
    def boom(_):
        raise AssertionError("looked at")
    # a domain banned since the bio was written must not refuse (or, for the worst kind, ban for) the bio they already have
    assert profile_edit.check_bio("see bad.example", "see bad.example", render=boom, bans=boom).code == "unchanged"
    # what the preprocessor makes of it counts as the same text (an older bio is stored as it was prepared)
    assert profile_edit.check_bio("  see bad.example ", "see bad.example", render=boom, bans=boom).code == "unchanged"
    # a longer stored bio than today's limit is still "unchanged", not "too long" (the member is saving something else)
    assert profile_edit.check_bio("x" * 300, "x" * 300, render=boom, bans=boom).code == "unchanged"
    assert profile_edit.check_bio("x" * 300, "x" * 299, render=boom, bans=boom).code == "too_long"         # but a new one is held to it


def test_a_bio_over_256_as_members_count_is_refused_before_anything_is_rendered(prepared):
    def boom(_):
        raise AssertionError("rendered")
    refused = profile_edit.check_bio("x" * 257, "", render=boom, bans=boom)
    assert refused.code == "too_long" and refused.message
    assert profile_edit.check_bio("漢" * 129, "", render=boom, bans=boom).code == "too_long"     # CJK counts 2


def test_a_banned_domain_names_itself_and_the_worst_kind_is_marked(prepared):
    found = profile_edit.check_bio("see", "", render=_render, bans=lambda html: [_Ban("bad.example"), _Ban("worse.example", 4)])
    assert found.code == "links" and "bad.example" in found.message and found.malicious
    mild = profile_edit.check_bio("see", "", render=_render, bans=lambda html: [_Ban("bad.example", 1)])
    assert mild.code == "links" and not mild.malicious


def test_a_bio_that_would_not_fit_its_column_is_refused_not_a_500(prepared):
    assert profile_edit.BIO_HTML_MAX == 1000                            # users.bio_html is varchar(1000)
    long_html = lambda text: "<a href='https://example.com/%s'>x</a>" % ("p" * 1000)        # a few links: short text, long HTML
    refused = profile_edit.check_bio("see", "", render=long_html, bans=lambda html: [])
    assert refused.code == "formatting" and refused.message


def test_a_banned_domain_beats_the_length_so_the_ban_still_applies(prepared):
    long_html = lambda text: "x" * 2000
    found = profile_edit.check_bio("see", "", render=long_html, bans=lambda html: [_Ban("bad.example", 4)])
    assert found.code == "links" and found.malicious


def test_the_link_spam_rule_is_a_new_empty_account_with_a_link():
    new = types.SimpleNamespace(created_utc=1000, post_count=0, comment_count=0)
    assert profile_edit.looks_like_link_spam(new, "<p><a href='https://x.example'>x</a></p>", 1000 + 3600)
    assert not profile_edit.looks_like_link_spam(new, "<p>no link</p>", 1000 + 3600)
    assert not profile_edit.looks_like_link_spam(new, "<a href='https://x.example'>x</a>", 1000 + 60 * 60 * 24)   # a day old
    posted = types.SimpleNamespace(created_utc=1000, post_count=1, comment_count=0)
    assert not profile_edit.looks_like_link_spam(posted, "<a href='https://x.example'>x</a>", 1000 + 3600)


# --- pictures ----------------------------------------------------------------------------------------------------

def _file(data, name="p.png"):
    return FileStorage(stream=io.BytesIO(data), filename=name)


def _picture(fmt="PNG", size=(20, 10), mode="RGB"):
    from PIL import Image
    out = io.BytesIO()
    Image.new(mode, size, "red" if mode in ("RGB", "RGBA") else 0).save(out, fmt)
    return out.getvalue()


@pytest.mark.parametrize("fmt", ["PNG", "JPEG", "GIF", "WEBP"])
@needs_pillow
def test_a_real_picture_passes_and_is_left_at_the_start_for_the_upload(fmt):
    sent = _file(_picture(fmt))
    picture, code = profile_edit.check_image(sent)
    assert code is None and picture is sent and sent.stream.tell() == 0


def test_nothing_sent_is_neither_a_picture_nor_a_refusal():
    assert profile_edit.check_image(None) == (None, None)
    assert profile_edit.check_image(_file(b"", name="")) == (None, None)


@needs_pillow
def test_a_file_that_only_looks_like_a_picture_is_not_one():
    assert profile_edit.check_image(_file(b"<svg xmlns='http://www.w3.org/2000/svg'/>", "a.png"))[1] == "not_a_picture"
    assert profile_edit.check_image(_file(b"not a picture at all", "a.png"))[1] == "not_a_picture"
    assert profile_edit.check_image(_file(b"", "a.png"))[1] == "not_a_picture"


@needs_pillow
def test_a_picture_in_another_format_is_refused_by_its_content_not_its_name():
    assert profile_edit.check_image(_file(_picture("BMP"), "a.png"))[1] == "format"
    assert profile_edit.check_image(_file(_picture("PNG"), "a.exe"))[1] is None


@needs_pillow
def test_a_truncated_file_is_found_out_here_and_not_halfway_through_the_upload():
    data = _picture("JPEG", (400, 400))
    assert profile_edit.check_image(_file(data[: len(data) // 2], "a.jpg"))[1] == "not_a_picture"


@needs_pillow
def test_a_phones_multi_picture_jpeg_is_a_picture():
    """Pillow names a JPEG that holds more than one picture (most phones' photos) "MPO": turning it away would refuse real photos."""
    from PIL import Image
    first, second = Image.new("RGB", (30, 20), "red"), Image.new("RGB", (30, 20), "blue")
    out = io.BytesIO()
    first.save(out, "MPO", save_all=True, append_images=[second])
    if Image.open(io.BytesIO(out.getvalue())).format != "MPO":
        pytest.skip("this Pillow does not write a file it reads back as MPO")
    assert profile_edit.check_image(_file(out.getvalue(), "IMG_0001.JPG"))[1] is None


@needs_pillow
def test_the_size_limit_counts_bytes_and_the_pixel_limit_counts_what_would_be_decoded(monkeypatch):
    data = _picture("PNG", (50, 50))
    assert profile_edit.check_image(_file(data), max_bytes=len(data) - 1) == (None, "too_big")
    assert profile_edit.check_image(_file(data), max_bytes=len(data))[1] is None
    monkeypatch.setattr(profile_edit, "MAX_PIXELS", 2499)
    assert profile_edit.check_image(_file(data)) == (None, "pixels")


@needs_pillow
def test_only_the_formats_the_caller_allows(monkeypatch):
    assert profile_edit.check_image(_file(_picture("GIF")), formats=("PNG",))[1] == "format"


def test_the_sentences_for_each_refusal():
    for code in ("too_big", "format", "pixels", "not_a_picture"):
        assert profile_edit.picture_message(code, 16)
    assert profile_edit.picture_message("too_big", 8) != profile_edit.picture_message("too_big", 16)     # a bot's 8 MB keeps its own words
    assert profile_edit.IMAGE_FORMATS == ("PNG", "JPEG", "MPO", "GIF", "WEBP")                              # never SVG


@needs_pillow
def test_a_cmyk_photo_can_be_saved_as_the_png_an_avatar_is():
    from pathlib import Path
    from PIL import Image, ImageOps
    # aws.py needs boto3 and the rest of the app: its two lines are read from the source and run on their own
    source = (Path(__file__).resolve().parent.parent / "ruqqus" / "helpers" / "aws.py").read_text(encoding="utf-8")
    part = "PNG_MODES = " + source.split("PNG_MODES = ", 1)[1].split("\ndef upload_file(")[0]
    namespace = {"ImageOps": ImageOps}
    exec(part, namespace)
    cmyk = Image.new("CMYK", (40, 20))
    out = io.BytesIO()
    namespace["crop_and_resize"](cmyk, (10, 10)).save(out, "PNG")      # it used to raise after the old picture was gone
    assert out.getvalue().startswith(b"\x89PNG")


@needs_pillow
def test_a_portrait_photo_stored_sideways_is_stood_up_before_it_is_cropped():
    from pathlib import Path
    from PIL import Image, ImageOps
    source = (Path(__file__).resolve().parent.parent / "ruqqus" / "helpers" / "aws.py").read_text(encoding="utf-8")
    namespace = {"ImageOps": ImageOps}
    exec("PNG_MODES = " + source.split("PNG_MODES = ", 1)[1].split("\ndef upload_file(")[0], namespace)
    wide = Image.new("RGB", (40, 10), "red")                       # 40 x 10 with a note that it is to be turned a quarter
    exif = Image.Exif()
    exif[0x0112] = 6
    out = io.BytesIO()
    wide.save(out, "JPEG", exif=exif)
    turned = namespace["crop_and_resize"](Image.open(io.BytesIO(out.getvalue())), (10, 40))
    assert turned.size == (10, 40)
    assert ImageOps.exif_transpose(Image.open(io.BytesIO(out.getvalue()))).size == (10, 40)       # what the crop worked from


# --- the lock ----------------------------------------------------------------------------------------------------

class _Db:
    def __init__(self, dialect):
        self.seen = []
        self._bind = types.SimpleNamespace(dialect=types.SimpleNamespace(name=dialect))

    def get_bind(self):
        return self._bind

    def execute(self, statement, params=None):
        self.seen.append((str(statement), params))


def test_one_edit_of_an_account_at_a_time_on_postgres_and_nothing_elsewhere():
    pg = _Db("postgresql")
    profile_edit.lock_account(pg, 7)
    # the wait gives up (a save that hangs must not queue the member's others for ever), then the lock is taken
    assert [statement for statement, _ in pg.seen] == ["SET LOCAL lock_timeout = '15s'", "SELECT pg_advisory_xact_lock(:ns, :user)"]
    assert pg.seen[1][1] == {"ns": profile_edit.LOCK_NAMESPACE, "user": 7}
    lite = _Db("sqlite")
    profile_edit.lock_account(lite, 7)
    assert lite.seen == []
    assert 0 < profile_edit.LOCK_NAMESPACE < 2 ** 31                  # the first key of the two-integer lock is an int4
