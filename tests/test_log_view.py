"""How the Log is drawn and filtered (ruqqus/helpers/log_view.py): what a query string may ask for, the sentence of an entry
(which names its subject by id, never by title), and the guild log's filters. No I/O."""
import inspect
import re
from pathlib import Path

import pytest

from ruqqus.helpers import log_view as view
from ruqqus.helpers.i18n_runtime import N_

MOD_LOGS = (Path(__file__).resolve().parent.parent / "ruqqus" / "classes" / "mod_logs.py").read_text(encoding="utf-8")
# the model can't be imported here (the suite never imports the whole app): the words a sentence needs, as it holds them
ACTIONTYPES = {
    "hide_post_from_guild": {"str": N_("hid post %(target)s from this guild")},
    "exile_user": {"str": N_("exiled user %(target)s")},
    "ban_guild": {"str": N_("banned the guild +%(guild)s")},
    "ban_user": {"str": N_("banned %(target)s")},
}
KINDS = set(re.findall(r'^    "([a-z_]+)": ?\{', MOD_LOGS, re.M))

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
    assert view.parse_page(raw) == expected


@pytest.mark.parametrize("raw, expected", [
    (None, None), ("", None), ("0", 0), ("42", 42), ("999999999999999", 999999999999999), ("1234567890123456", None),
    ("-1", None), ("4 ", None), ("1; DROP", None), (ARABIC_INDIC_THREE, None), (FULL_WIDTH_TWELVE, None),
])
def test_the_cursor_is_a_plain_number_and_nothing_else(raw, expected):
    assert view.parse_cursor(raw) == expected


def test_the_public_tabs_are_moderation_sanctions_and_guild_changes_and_never_suppressions():
    assert view.TABS == ("content", "sanction", "guild") == tuple(view.TAB_LABELS)
    assert view.parse_tab("sanction") == "sanction" and view.parse_tab("guild") == "guild"
    for raw in (None, "", "suppression", "CONTENT", "../x", "content;"):
        assert view.parse_tab(raw) == "content"                           # the legal tab is not a thing a visitor can ask for


def test_parse_show_knows_four_filters_and_defaults_to_all():
    assert set(view.SHOW) == {"all", "posts", "comments", "exiles"} == set(view.SHOW_LABELS)
    for name in view.SHOW:
        assert view.parse_show(name) == name
    for raw in (None, "", "hidden", "POSTS", "posts,comments"):
        assert view.parse_show(raw) == "all"


def test_kinds_for_all_is_no_filter_and_the_others_do_not_overlap():
    assert view.kinds_for("all") is None and view.kinds_for("nonsense") is None
    seen = set()
    for name in ("posts", "comments", "exiles"):
        kinds = view.kinds_for(name)
        assert kinds and not (set(kinds) & seen), name
        seen |= set(kinds)
    assert "hide_post_from_guild" in view.kinds_for("posts") and "kick_post" in view.kinds_for("posts")
    assert "hide_comment_from_guild" in view.kinds_for("comments")
    assert "exile_user" in view.kinds_for("exiles")
    assert seen <= KINDS                                                  # every kind a filter names is a real action


def test_a_person_is_a_link_unless_deleted_or_hidden_by_the_readers_filter():
    assert str(view.person("alice")) == '<a href="/@alice" class="font-weight-bold text-black">@alice</a>'
    assert "alice" not in str(view.person("alice", hidden=True)) and "alice" not in str(view.person("alice", deleted=True))
    assert "&lt;script&gt;" in str(view.person("<script>"))               # an unexpected name is escaped, never markup


def test_an_item_is_named_by_its_id_and_never_by_its_title():
    assert str(view.item_link("post", "a1b2c")) == '<a href="/post/a1b2c">#a1b2c</a>'
    assert str(view.item_link("comment", "z9")) == '<a href="/comment/z9">comment</a>'


def test_a_sentence_is_the_guild_logs_words_with_the_subject_by_id():
    who, post = view.site_account("Laba"), view.item_link("post", "a1b2c")
    line = str(view.sentence("hide_post_from_guild", ACTIONTYPES, who, post, "pets"))
    assert line == ('<a href="/@Laba" class="font-weight-bold text-black">@Laba</a> hid post '
                    '<a href="/post/a1b2c">#a1b2c</a> from this guild')
    assert "@alice" in str(view.sentence("exile_user", ACTIONTYPES, view.person("gm"), view.person("alice"), "pets"))
    assert "+pets" in str(view.sentence("ban_guild", ACTIONTYPES, who, "", "pets"))
    assert "@alice" in str(view.sentence("ban_user", ACTIONTYPES, who, view.person("alice")))


def test_an_action_the_table_does_not_know_reads_as_its_own_name_not_as_markup():
    line = str(view.sentence("made_up_action", ACTIONTYPES, view.person("gm"), "", ""))
    assert line.endswith("made up action") and "<script" not in line
    assert "&lt;" in str(view.sentence("<script>", ACTIONTYPES, view.person("gm")))


def test_every_public_action_has_words_and_no_sentence_asks_for_a_title():
    # a sentence names a post by id: even a template that asks for %(title)s gets the id
    for kind in ("suspend_user", "unsuspend_user", "ban_user", "unban_user", "ban_guild", "unban_guild",
                 "remove_wiki_revision", "hide_revision"):
        assert kind in KINDS, kind                                        # the Log's own actions have words too
    values = ('values = {"target": target, "name": target, "guild": guild or "", "title": target}')
    assert values in inspect.getsource(view.sentence)


def test_pictures_and_sounds_become_markers_because_their_uploads_are_gone():
    html = '<p>look <img src="/media/ab/xyz.png" alt="x"> and <IMG src=/media/cd/q.jpg> here</p>'
    out = view.without_media(html)
    assert "<img" not in out.lower() and "/media/" not in out
    assert out.count('<span class="text-muted">[picture]</span>') == 2 and out.startswith("<p>look ") and out.endswith(" here</p>")
    sound = '<p>hear <audio controls src="/media/ab/s.mp3"><source src="/media/ab/s.mp3"></audio> this</p>'
    assert view.without_media(sound) == '<p>hear <span class="text-muted">[sound]</span> this</p>'


def test_without_media_leaves_other_markup_alone_and_never_raises():
    html = '<p>a <strong>b</strong> <a href="https://example.com">c</a></p><imgur>not a picture</imgur>'
    assert view.without_media(html) == html
    assert view.without_media(None) == "" and view.without_media("") == ""


def test_without_media_cannot_be_used_to_add_markup():
    out = view.without_media('<img src="x" onerror="alert(1)"><audio onloadstart="alert(2)"></audio>')
    assert "onerror" not in out and "onloadstart" not in out and "alert" not in out
