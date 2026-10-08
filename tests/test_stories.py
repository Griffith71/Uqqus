"""Stories, the rules with no I/O (ruqqus/helpers/stories.py): what a story may hold, how long it lives, what the ring shows,
how a Highlight is named and filled."""
import pytest

from ruqqus.helpers import circles
from ruqqus.helpers import stories as s

NOW = 2_000_000_000


# --- kinds, colours, text ---------------------------------------------------------------------------------

@pytest.mark.parametrize("raw, kind", [("image", "image"), (" TEXT ", "text"), ("Video", "video")])
def test_a_kind_is_one_of_three(raw, kind):
    assert s.parse_kind(raw) == (kind, None)


@pytest.mark.parametrize("raw", ["", None, "gif", "audio", "image text", "<script>"])
def test_any_other_kind_is_refused_with_a_message(raw):
    kind, message = s.parse_kind(raw)
    assert kind is None and message


def test_a_colour_is_one_of_our_names_and_anything_else_is_the_first():
    for name in s.BACKGROUNDS:
        assert s.parse_background(name) == name
        assert s.parse_background(name.upper()) == name
    for raw in (None, "", "red", "url(javascript:alert(1))", "ocean; background: red", "../../x"):
        assert s.parse_background(raw) == s.BACKGROUNDS[0]


def test_the_colour_names_are_plain_lowercase_words_the_stylesheets_can_hold():
    assert s.BACKGROUNDS and all(name.isascii() and name.isalpha() and name == name.lower() for name in s.BACKGROUNDS)


def test_text_loses_control_characters_and_outer_space_and_keeps_the_rest():
    assert s.clean_text("  hello \x00world\x07  ") == ("hello world", None)
    assert s.clean_text("line one" + chr(10) + "line two") == ("line one" + chr(10) + "line two", None)
    assert s.clean_text(None) == ("", None)
    assert s.clean_text("<b>bold</b> & more") == ("<b>bold</b> & more", None)       # escaping is the page's job, never here


def test_text_stops_at_its_limit():
    assert s.clean_text("x" * s.TEXT_MAX) == ("x" * s.TEXT_MAX, None)
    text, message = s.clean_text("x" * (s.TEXT_MAX + 1))
    assert text is None and str(s.TEXT_MAX) in message


def test_the_limit_counts_what_is_kept_not_what_was_typed():
    assert s.clean_text("\x00" * 50 + "x" * s.TEXT_MAX)[1] is None


# --- video ------------------------------------------------------------------------------------------------

@pytest.mark.parametrize("raw", [
    "dQw4w9WgXcQ",
    "https://www.youtube.com/watch?v=dQw4w9WgXcQ",
    "http://youtube.com/watch?v=dQw4w9WgXcQ&t=10s",
    "https://m.youtube.com/watch?feature=share&v=dQw4w9WgXcQ",
    "https://youtu.be/dQw4w9WgXcQ?t=5",
    "https://www.youtube.com/shorts/dQw4w9WgXcQ",
    "https://www.youtube.com/embed/dQw4w9WgXcQ",
    "  https://youtu.be/dQw4w9WgXcQ  ",
])
def test_a_youtube_link_or_id_gives_only_the_id(raw):
    assert s.parse_video(raw) == "dQw4w9WgXcQ"


@pytest.mark.parametrize("raw", [
    "", None, "dQw4w9WgXc", "dQw4w9WgXcQQ", "https://example.com/watch?v=dQw4w9WgXcQ",
    "https://youtube.com.evil.example/watch?v=dQw4w9WgXcQ", "https://evil.example/https://youtu.be/dQw4w9WgXcQ",
    "javascript:alert(1)", "ftp://youtu.be/dQw4w9WgXcQ", "https://youtu.be/dQw4w9WgXcQQQ", "https://youtu.be/",
    "https://www.youtube.com/watch?v=<script>", "dQw4w9WgXc&", "dQw4w9WgX/Q",
])
def test_anything_else_is_not_a_video(raw):
    assert s.parse_video(raw) == ""


# --- what may be made -------------------------------------------------------------------------------------

def test_a_text_card_needs_words():
    assert s.refusal(s.TEXT, circles.PUBLIC, "", False, "")
    assert s.refusal(s.TEXT, circles.PUBLIC, "hello", False, "") is None


def test_a_picture_story_needs_a_picture_and_may_have_no_words():
    assert s.refusal(s.IMAGE, circles.PUBLIC, "", False, "")
    assert s.refusal(s.IMAGE, circles.PUBLIC, "", True, "") is None
    assert s.refusal(s.IMAGE, circles.SUBSCRIBERS, "caption", True, "") is None


def test_a_video_needs_a_link_and_is_only_ever_public():
    assert s.refusal(s.VIDEO, circles.PUBLIC, "", False, "")
    assert s.refusal(s.VIDEO, circles.PUBLIC, "", False, "dQw4w9WgXcQ") is None
    assert "public" in s.refusal(s.VIDEO, circles.SUBSCRIBERS, "", False, "dQw4w9WgXcQ")
    assert "public" in s.refusal(s.VIDEO, circles.FRIENDS, "", False, "dQw4w9WgXcQ")


def test_every_circle_audience_may_hold_text_and_pictures():
    for audience in (circles.PUBLIC, circles.SUBSCRIBERS, circles.FRIENDS):
        assert s.refusal(s.TEXT, audience, "hi", False, "") is None
        assert s.refusal(s.IMAGE, audience, "", True, "") is None


def test_the_internal_marks_are_not_choices_a_member_can_make():
    assert circles.STORY_OPEN not in circles.CHOICES and circles.GUILD not in circles.CHOICES
    for audience in (circles.STORY_OPEN, circles.GUILD):
        assert circles.parse_audience(str(audience))[0] is None


# --- life -------------------------------------------------------------------------------------------------

def test_a_story_lasts_a_day():
    assert s.TTL == 86400
    assert s.expires_at(NOW) == NOW + 86400


def test_a_story_is_live_until_its_last_second_unless_deleted():
    assert s.is_live(NOW + 1, 0, NOW)
    assert not s.is_live(NOW, 0, NOW)
    assert not s.is_live(NOW - 1, 0, NOW)
    assert not s.is_live(NOW + 1000, NOW - 5, NOW)


@pytest.mark.parametrize("left, words", [
    (86400, "24h left"), (3600, "1h left"), (3599, "59m left"), (61, "1m left"), (1, "1m left"), (0, "ended"), (-50, "ended"),
])
def test_time_left_is_said_in_words(left, words):
    assert s.describe_left(NOW + left, NOW) == words


# --- the ring ---------------------------------------------------------------------------------------------

def test_the_ring_is_unseen_while_anything_is_new_to_the_viewer():
    assert s.ring([1, 2, 3], {1, 2}) == s.UNSEEN
    assert s.ring([1], set()) == s.UNSEEN


def test_the_ring_goes_seen_when_everything_was_watched():
    assert s.ring([1, 2], {1, 2, 99}) == s.SEEN


def test_no_stories_no_ring():
    assert s.ring([], {1}) is None
    assert s.ring(iter(()), set()) is None


# --- Highlights -------------------------------------------------------------------------------------------

def test_a_highlight_needs_a_name_within_the_limit():
    assert s.parse_title("  Trip  ") == ("Trip", None)
    assert s.parse_title("t" * s.TITLE_MAX) == ("t" * s.TITLE_MAX, None)
    for raw in ("", "   ", None, "\x00\x07", "t" * (s.TITLE_MAX + 1)):
        title, message = s.parse_title(raw)
        assert title is None and message


def test_story_ids_are_plain_digits_once_each_in_order():
    assert s.clean_ids(["3", " 1 ", "3", "2"]) == [3, 1, 2]
    assert s.clean_ids(["-1", "1.5", "abc", "", "0x10", "1e3", " "]) == []
    assert s.clean_ids([7, "8"]) == [7, 8]


def test_digits_from_other_scripts_are_not_ids():
    assert s.clean_ids([chr(0x663), chr(0x967) + chr(0x968), chr(0xFF11)]) == []        # Arabic-Indic, Devanagari, full-width digits


def test_story_ids_stop_at_the_limit_and_at_ten_digits():
    assert len(s.clean_ids([str(i) for i in range(1, 500)])) == s.HIGHLIGHT_STORIES_MAX
    assert s.clean_ids(["12345678901"]) == []
    assert s.clean_ids(["1", "2", "3"], limit=2) == [1, 2]


def test_a_reason_is_cut_to_its_limit_and_cleaned():
    assert s.parse_reason("  too  \x00 rude ") == "too   rude"
    assert len(s.parse_reason("r" * 1000)) == s.REASON_MAX
    assert s.parse_reason(None) == ""
