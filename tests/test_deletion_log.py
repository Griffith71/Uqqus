"""The deletion log's rules (ruqqus/helpers/deletion_log.py): what a query string may ask for, how long something lived,
and the mod log's filters. No I/O."""
import pytest

from ruqqus.helpers import deletion_log as rules

ARABIC_INDIC_THREE = chr(0x663)
FULL_WIDTH_TWELVE = chr(0xFF11) + chr(0xFF12)


@pytest.mark.parametrize("raw, expected", [
    (None, 1), ("", 1), ("1", 1), ("7", 7), ("0", 1), ("25", 25), ("1000", 1000), ("1001", 1000), ("9999", 1000),
    ("12345", 1),            # more than four digits is not a page
    ("-3", 1), ("2.5", 1), ("abc", 1), (" 4", 1), ("4 ", 1),
    (ARABIC_INDIC_THREE, 1),  # str.isdigit() would accept this one
    (FULL_WIDTH_TWELVE, 1),
])
def test_parse_page_takes_only_plain_digits_and_never_raises(raw, expected):
    assert rules.parse_page(raw) == expected


def test_parse_tab_defaults_to_posts():
    assert rules.parse_tab("comments") == rules.COMMENTS
    assert rules.parse_tab("posts") == rules.POSTS
    for raw in (None, "", "all", "COMMENTS", "../x", "comments;"):
        assert rules.parse_tab(raw) == rules.POSTS


def test_parse_show_knows_four_filters_and_defaults_to_all():
    assert set(rules.SHOW) == {"all", "posts", "comments", "exiles"} == set(rules.SHOW_LABELS)
    for name in rules.SHOW:
        assert rules.parse_show(name) == name
    for raw in (None, "", "hidden", "POSTS", "posts,comments"):
        assert rules.parse_show(raw) == "all"


def test_kinds_for_all_is_no_filter_and_the_others_do_not_overlap():
    assert rules.kinds_for("all") is None and rules.kinds_for("nonsense") is None
    seen = set()
    for name in ("posts", "comments", "exiles"):
        kinds = rules.kinds_for(name)
        assert kinds and not (set(kinds) & seen), name
        seen |= set(kinds)
    assert "hide_post_from_guild" in rules.kinds_for("posts") and "kick_post" in rules.kinds_for("posts")
    assert "hide_comment_from_guild" in rules.kinds_for("comments")
    assert "exile_user" in rules.kinds_for("exiles")


@pytest.mark.parametrize("seconds, expected", [
    (0, "less than a minute"), (59, "less than a minute"), (60, "1 minute"), (119, "1 minute"), (120, "2 minutes"),
    (3599, "59 minutes"), (3600, "1 hour"), (7200, "2 hours"), (86399, "23 hours"), (86400, "1 day"),
    (3 * 86400 + 5, "3 days"), (400 * 86400, "400 days"),
])
def test_lived_in_words(seconds, expected):
    assert rules.lived(seconds) == expected


def test_lived_never_goes_negative_or_raises():
    assert rules.lived(-5) == "less than a minute"
    assert rules.lived(None) == "less than a minute"


def test_pictures_and_sounds_become_markers_because_their_uploads_are_gone():
    html = '<p>look <img src="/media/ab/xyz.png" alt="x"> and <IMG src=/media/cd/q.jpg> here</p>'
    out = rules.without_media(html)
    assert "<img" not in out.lower() and "/media/" not in out
    assert out.count('<span class="text-muted">[picture]</span>') == 2 and out.startswith("<p>look ") and out.endswith(" here</p>")
    sound = '<p>hear <audio controls src="/media/ab/s.mp3"><source src="/media/ab/s.mp3"></audio> this</p>'
    assert rules.without_media(sound) == '<p>hear <span class="text-muted">[sound]</span> this</p>'


def test_without_media_leaves_other_markup_alone_and_never_raises():
    html = '<p>a <strong>b</strong> <a href="https://example.com">c</a></p><imgur>not a picture</imgur>'
    assert rules.without_media(html) == html
    assert rules.without_media(None) == "" and rules.without_media("") == ""


def test_without_media_cannot_be_used_to_add_markup():
    # it only removes tags and puts in two fixed markers
    out = rules.without_media('<img src="x" onerror="alert(1)"><audio onloadstart="alert(2)"></audio>')
    assert "onerror" not in out and "onloadstart" not in out and "alert" not in out
    assert out == '<span class="text-muted">[sound]</span>'.replace("[sound]", "[picture]") + '<span class="text-muted">[sound]</span>'


def test_has_text_is_false_only_when_everything_was_scrubbed():
    assert rules.has_text("a title", None, None)
    assert rules.has_text(None, "body", None)
    assert rules.has_text(None, None, "<p>x</p>")
    assert not rules.has_text(None, None, None)
    assert not rules.has_text("", "", "")
