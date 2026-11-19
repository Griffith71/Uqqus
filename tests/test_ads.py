"""Ads (ruqqus/helpers/ads.py): where an ad may show, and that nothing shows until the site is set up for it."""
from types import SimpleNamespace as NS

import pytest

from ruqqus.helpers import ads

ENV = {"ADSENSE_CLIENT": "ca-pub-1234567890123456", "ADSENSE_SLOT_FEED": "1111111111",
       "ADSENSE_SLOT_POST": "2222222222", "ADSENSE_SLOT_SIDEBAR": "3333333333", "ADSENSE_FEED_LAYOUT_KEY": "-fb+5w+4e-db+86"}
CFG = ads.config(ENV)


def board(**over):
    return NS(**{"is_banned": False, "is_private": False, "is_circle": False, "is_sensitive": False, "is_nsfl": False,
                 "name_severity": 0, **over})


def post(**over):
    return NS(**{"is_banned": False, "hidden_by_guild": False, "deleted_utc": 0, "post_public": True, "is_sensitive": False, "word_severity": 0,
                 "board": board(), **over})


def member(**over):
    return NS(**{"id": 5, "has_premium_no_renew": False, "filter_level": 1, **over})


def test_nothing_without_a_client_id():
    assert ads.config({}) is None
    assert ads.config({"ADSENSE_CLIENT": "pub-123"}) is None
    assert ads.config({"ADSENSE_CLIENT": 'ca-pub-123"><script>'}) is None
    assert not ads.allows("feed", None, cfg=None)


def test_values_written_into_the_page_are_checked():
    cfg = ads.config({"ADSENSE_CLIENT": "ca-pub-1234567890123456", "ADSENSE_SLOT_FEED": "12345\"x",
                      "ADSENSE_SLOT_POST": "2222222222", "ADSENSE_FEED_LAYOUT_KEY": "<b>"})
    assert cfg["slots"] == {"post": "2222222222"} and cfg["feed_layout_key"] == ""
    assert cfg["publisher"] == "pub-1234567890123456"
    assert not ads.allows("feed", None, cfg=cfg) and ads.allows("post", None, post=post(), cfg=cfg)


def test_ads_txt_names_the_publisher():
    assert ads.ADS_TXT.format(publisher=CFG["publisher"]) == "google.com, pub-1234567890123456, DIRECT, f08c47fec0942fa0\n"


def test_visitors_and_members_see_ads():
    for page in ads.PAGES:
        assert ads.allows(page, None, cfg=CFG) and ads.allows(page, member(), cfg=CFG)


def test_premium_members_child_filter_and_side_panels_see_none():
    assert not ads.allows("feed", member(has_premium_no_renew=True), cfg=CFG)
    assert not ads.allows("feed", member(filter_level=2), cfg=CFG)
    assert ads.allows("feed", member(filter_level=0), cfg=CFG)
    assert not ads.allows("feed", None, embedded=True, cfg=CFG)


def test_premium_is_read_without_renewing():
    class Member:
        id, filter_level, has_premium_no_renew = 5, 1, False

        @property
        def has_premium(self):
            raise AssertionError("has_premium spends a coin to renew")
    assert ads.allows("feed", Member(), cfg=CFG)


@pytest.mark.parametrize("over", [{"post_public": False}, {"is_banned": True}, {"hidden_by_guild": True}, {"deleted_utc": 5},
                                  {"is_sensitive": True}, {"word_severity": 1}, {"word_severity": 2},
                                  {"board": board(is_private=True)}, {"board": board(is_circle=True)},
                                  {"board": board(is_sensitive=True)}, {"board": board(is_nsfl=True)},
                                  {"board": board(is_banned=True)}, {"board": board(name_severity=1)}])
def test_no_ad_beside_a_post_that_is_not_for_everyone(over):
    assert ads.allows("post", None, post=post(), cfg=CFG)
    assert not ads.allows("post", None, post=post(**over), cfg=CFG)
    assert not ads.allows("sidebar", None, post=post(**over), cfg=CFG)


@pytest.mark.parametrize("over", [{"is_private": True}, {"is_circle": True}, {"is_sensitive": True}, {"is_nsfl": True},
                                  {"is_banned": True}, {"name_severity": 2}])
def test_no_ad_in_a_guild_that_is_not_for_everyone(over):
    assert ads.allows("feed", None, board=board(), cfg=CFG)
    assert not ads.allows("feed", None, board=board(**over), cfg=CFG)


def comment(**over):
    return NS(**{"is_banned": False, "deleted_utc": 0, "is_sensitive": False, "word_severity": 0, "body_html": "<p>Fine.</p>", **over})


def test_live_needs_a_client_and_a_slot():
    assert not ads.live(None)
    assert not ads.live(ads.config({"ADSENSE_CLIENT": "ca-pub-1234567890123456"}))     # enough for /ads.txt, nothing else
    assert ads.live(ads.config({"ADSENSE_CLIENT": "ca-pub-1234567890123456", "ADSENSE_SLOT_POST": "2222222222"}))


def test_a_child_account_sees_no_ads_because_it_is_locked_to_the_child_filter():
    # what helpers/age_declaration.py fields_for writes, and the database's rule keeps
    child = member(age_declared=2, filter_level=2, hide_offensive=True)
    for page in ads.PAGES:
        assert not ads.allows(page, child, cfg=CFG)


@pytest.mark.parametrize("country", ["GB", "UK", "DE", "FR", "IE", "NO", "IS", "LI", "CH", "GG", "RE"])
def test_never_personalised_in_the_uk_the_eea_and_switzerland_whoever_reads(country):
    for viewer in (None, member(age_declared=None), member(age_declared=1), member(age_declared=2)):
        assert ads.personalised_allowed(country, viewer) is False


@pytest.mark.parametrize("country", [None, "", "T1", "XX", "ZZ", "gb", "G", "GBR", '"><b>', 7])
def test_never_personalised_where_the_country_isnt_known(country):
    assert ads.personalised_allowed(country, member(age_declared=1)) is False


def test_elsewhere_only_an_account_that_said_it_is_18_or_over():
    assert ads.personalised_allowed("US", member(age_declared=1)) is True
    assert ads.personalised_allowed("US", member(age_declared=None)) is False       # undeclared is as a visitor
    assert ads.personalised_allowed("US", member(age_declared=2)) is False
    assert ads.personalised_allowed("US", NS(id=5)) is False                        # a row with no answer on it
    assert ads.personalised_allowed("US", None) is False                            # so a visitor's page doesn't vary by country
    assert ads.personalised_allowed("JP", None) is ads.personalised_allowed("GB", None)


def test_never_personalised_for_anyone_reading_at_child_level_where_they_are_australia_included():
    # Australia is strict for the child lock but not one of the consent regions: a ticked "18 or over" counts for nothing there,
    # in the ads as in what they may read (the owner, 2026-11-19)
    assert ads.personalised_allowed("AU", member(age_declared=1)) is False
    assert ads.personalised_allowed("AU", member(age_declared=1, age_assured_utc=1700000000)) is True     # an age-check service confirmed it
    assert ads.personalised_allowed("AU", None) is False
    assert ads.personalised_allowed("US", member(age_declared=1, age_assured_utc=1700000000)) is True
    assert ads.personalised_allowed("GB", member(age_declared=1, age_assured_utc=1700000000)) is False    # consent region: never


def test_the_hold_after_a_sign_in_ends():
    assert ads.held_after_sign_in(1000, 999) and not ads.held_after_sign_in(1000, 1000)
    assert not ads.held_after_sign_in(None, 5) and not ads.held_after_sign_in("9999999999", 5)


@pytest.mark.parametrize("html", ['<p><img src="/media/1/a.png"></p>', '<audio controls src="/media/1/a.mp3"></audio>',
                                  '<IMG SRC="https://example.org/a.gif">', '<video src="x"></video>', "<iframe src='x'></iframe>"])
def test_a_picture_or_a_sound_in_a_comment_is_media(html):
    assert ads.holds_media(html) and not ads.comment_allows(comment(body_html=html))


def test_the_sites_own_emoji_and_plain_text_are_not_media():
    assert not ads.holds_media('<p>Yes <span><img class="emoji" src="/assets/images/emojis/a.png"></span></p>')
    assert not ads.holds_media('<p><img class="profile-pic-20" src="/uid/1/pic/profile">name</p>')
    assert not ads.holds_media("<p>an image of a cat</p>") and not ads.holds_media(None)


def test_a_picture_is_not_excused_by_an_address_that_says_emoji():
    assert ads.holds_media('<img src="https://example.org/emoji.png">') and ads.holds_media('<img alt="emoji" src="/media/1/a.png">')


def test_one_unsafe_card_takes_the_whole_page():
    assert ads.page_allows() and ads.page_allows([post(), post()])
    assert not ads.page_allows([post(), post(is_sensitive=True), post()])
    assert not ads.page_allows([post(board=board(is_private=True))])
    assert not ads.page_allows([post()], [comment(word_severity=1)])


def test_a_comment_in_a_list_answers_for_its_post():
    assert ads.page_allows([], [comment(post=post())])
    assert not ads.page_allows([], [comment(post=post(is_sensitive=True))])


def test_a_comment_drawn_as_a_placeholder_doesnt_count():
    assert ads.page_allows([], [comment(is_banned=True, word_severity=2), comment(deleted_utc=5, is_sensitive=True)])


@pytest.mark.parametrize("over", [{"word_severity": 1}, {"word_severity": 2}, {"is_sensitive": True},
                                  {"body_html": '<img src="/media/1/a.png">'}])
def test_a_post_page_with_an_unfit_comment_has_no_ads(over):
    text = post(body_html="<p>Words.</p>")
    assert ads.post_page_allows(text, [comment()]) and not ads.post_page_allows(text, [comment(), comment(**over)])


def test_a_post_with_neither_text_nor_comments_is_a_low_value_page():
    assert not ads.post_page_allows(post(body_html=""))
    assert not ads.post_page_allows(post(body_html="<p>  </p>"))
    assert not ads.post_page_allows(post(body_html=""), [comment(deleted_utc=5)])      # a placeholder is nothing to read
    assert ads.post_page_allows(post(body_html="<p>Words.</p>"))
    assert ads.post_page_allows(post(body_html=""), [comment()])
    assert not ads.post_page_allows(post(body_html="<p>Words.</p>", is_sensitive=True), [comment()])


# ---- what a comment can show

EMOJI = '<span title=":laugh:"><img class="emoji" src="/assets/images/emojis/laugh.gif"></span>'
MENTION = '<a href="/@ann" class="d-inline-block mention-user"><img src="/uid/5/pic/profile" class="profile-pic-20 mr-1">@ann</a>'
GUILD = '<a href="/+pets"><img src="/+pets/pic/profile" class="profile-pic-20 mr-1">+pets</a>'


@pytest.mark.parametrize("html", ["<p>Words.</p>", EMOJI, MENTION, GUILD, '<img class="emoji" src="/assets/images/primojis/a-b_c.png">', "", None])
def test_the_sites_own_pictures_arent_media(html):
    assert not ads.holds_media(html)
    assert ads.comment_allows(comment(body_html=html or ""))


@pytest.mark.parametrize("html", [
    '<img src="/media/5/abc.png">',
    '<img class="in-comment-image rounded-sm my-2" src="/media/5/abc.png">',
    # a member can type the class, and helpers/sanitize.py keeps it: the address has to be the renderer's too
    '<img class="emoji" src="/media/5/abc.png">',
    '<img class="profile-pic-20" src="/media/5/abc.png">',
    '<img class="emoji x" src="https://example.com/a.png">',
    '<img src="https://example.com/a.png" class="profile-pic-20 mr-1">',
    # the right start of an address, and somewhere else by the time the browser has read it
    '<img class="emoji" src="/assets/images/emojis/../../../media/5/abc.png">',
    '<img class="emoji" src="/assets/images/emojis/..%2f..%2fmedia/abc.png">',
    '<img class="emoji" src="/assets/images/emojis/..\\..\\media\\abc.png">',
    '<img class="emoji" src="/assets/images/emojis/a.gif?x=/media/5/abc.png">',
    # one class doesn't lend the other's address
    '<img class="emoji" src="/uid/5/pic/profile">',
    '<img class="xemoji" src="/assets/images/emojis/laugh.gif">',
    '<img data-class="emoji" src="/assets/images/emojis/laugh.gif">',
    '<img class="emoji" data-src="/assets/images/emojis/laugh.gif" src="/media/5/abc.png">',
    '<img class="emoji">',
    '<audio class="emoji" src="/assets/images/emojis/laugh.gif"></audio>',
    '<video src="/media/1/a.mp4"></video>', '<iframe src="https://x.example"></iframe>',
])
def test_anything_else_is_media(html):
    assert ads.holds_media(html)
    assert not ads.comment_allows(comment(body_html=html))


def test_a_sensitive_accounts_comment_counts_unless_it_is_anonymous():
    sensitive, ordinary = NS(is_sensitive=True), NS(is_sensitive=False)
    assert not ads.comment_allows(comment(author=sensitive))
    assert ads.comment_allows(comment(author=ordinary))
    assert ads.comment_allows(comment(author=sensitive, is_anonymous=True))       # never judged by whose it is
    assert ads.comment_allows(comment(author=sensitive, is_banned=True))           # drawn as a placeholder
    assert not ads.post_page_allows(post(body_html="<p>Text.</p>"), [comment(author=sensitive)])


# ---- what a quote card shows

def quote(quoted=None, inner=None, kind="post", notice=""):
    return {"kind": kind, "post": quoted, "notice": notice, "inner": inner}


@pytest.mark.parametrize("over", [{"word_severity": 1}, {"is_sensitive": True}, {"post_public": False}, {"author": NS(is_sensitive=True)},
                                  {"board": board(is_private=True)}])
def test_a_quoted_post_answers_as_a_card_would_at_either_level(over):
    assert not ads.quote_allows(quote(post(**over)))
    assert not ads.quote_allows(quote(post(), inner=quote(post(**over))))
    assert ads.quote_allows(quote(post(), inner=quote(post())))


def test_a_quote_that_shows_nothing_of_a_post_doesnt_count():
    assert ads.quote_allows(None)
    assert ads.quote_allows(quote(None, notice="This post isn't available."))
    assert ads.quote_allows({"kind": "social", "text": "Words from another site."})
    assert not ads.quote_allows({"kind": "social", "inner": quote(post(word_severity=2))})


# ---- a live thread

def test_a_live_thread_has_no_ads_while_live_and_after_that_by_what_it_drew():
    assert ads.live_thread_allows(False)
    assert ads.live_thread_allows(False, [(0, "<p>Fine.</p>")], 0, "<p>A summary.</p>")
    assert not ads.live_thread_allows(True)                                         # its updates arrive on the open page
    assert not ads.live_thread_allows(False, [(1, "<p>Rude.</p>")])
    assert not ads.live_thread_allows(False, [(0, '<p><img src="/media/1/a.png"></p>')])
    assert not ads.live_thread_allows(False, [], 2, "<p>A rude summary.</p>")
    assert not ads.live_thread_allows(False, [], 0, '<img src="/media/1/a.png">')
    assert ads.live_thread_allows(False, [(None, None)], None, None)


def test_the_hold_after_a_password_is_written_only_while_ads_are_live(monkeypatch):
    held = {}
    ads.hold_after_password(held, 100)
    assert held == {}
    monkeypatch.setenv("ADSENSE_CLIENT", ENV["ADSENSE_CLIENT"])
    ads.hold_after_password(held, 100)
    assert held == {}                                                               # a client id alone: no tag to hold back
    monkeypatch.setenv("ADSENSE_SLOT_POST", ENV["ADSENSE_SLOT_POST"])
    ads.hold_after_password(held, 100)
    assert held == {"no_ads_until": 100 + ads.PREKEY_GRACE}
