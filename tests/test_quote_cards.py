"""Quote cards (ruqqus/helpers/quote_cards.py): a linked post reads like a post quoted on X. The card of a social site's
post from the snippets those sites really send, the date, which links are posts at all, a link to a post on this site,
and what a reader is told instead of a post they may not read."""
from pathlib import Path

import pytest

from ruqqus.helpers import quote_cards as qc
from ruqqus.helpers import social_embeds as se

ROOT = Path(__file__).resolve().parent.parent
NL = chr(10)

# what the sites answered from the local container (2026-10-15), as saved in `embed_url`
X_SNIPPET = ('<blockquote class="twitter-tweet" data-dnt="true"><p lang="en" dir="ltr">Entering Twitter HQ – let that sink '
             'in! <a href="https://t.co/D68z4K2wq7">pic.twitter.com/D68z4K2wq7</a></p>&mdash; Elon Musk (@elonmusk) '
             '<a href="https://x.com/elonmusk/status/1585341984679469056?ref_src=twsrc%5Etfw">October 26, 2022</a></blockquote>'
             '<script async src="https://platform.x.com/widgets.js" charset="utf-8"></script>')
BSKY_SNIPPET = ('<blockquote class="bluesky-embed" data-bluesky-uri="at://did:plc:z72i7hdynmk6r22z27h6tvur/app.bsky.feed.post/'
                '3l6oveex3ii2l"><p lang="en">Bluesky is an open social network. We&#39;re so excited to have you here! ' + NL
                + NL + 'A quick orientation thread</p>&mdash; <a href="https://bsky.app/profile/did:plc:z72i7hdynmk6r22z27h6tvur'
                '?ref_src=embed">Bluesky (@bsky.app)</a> <a href="https://bsky.app/profile/did:plc:z72i7hdynmk6r22z27h6tvur/'
                'post/3l6oveex3ii2l?ref_src=embed">2024-10-17T07:06:51.491Z</a></blockquote>'
                '<script async src="https://embed.bsky.app/static/embed.js" charset="utf-8"></script>')
REDDIT_SNIPPET = ('<blockquote class="reddit-embed-bq" style="height:316px" >' + NL
                  + '<a href="https://www.reddit.com/r/IAmA/comments/z1c9z/i_am_barack_obama_president_of_the_united_states/">'
                  'I am Barack Obama, President of the United States -- AMA</a><br> by' + NL
                  + '<a href="https://www.reddit.com/user/PresidentObama/">u/PresidentObama</a> in' + NL
                  + '<a href="https://www.reddit.com/r/IAmA/">IAmA</a>' + NL + '</blockquote>')
X_URL = "https://x.com/elonmusk/status/1585341984679469056"
BSKY_URL = "https://bsky.app/profile/bsky.app/post/3l6oveex3ii2l"
REDDIT_URL = "https://www.reddit.com/r/IAmA/comments/z1c9z/i_am_barack_obama_president_of_the_united_states/"


def test_an_x_post_reads_like_a_quoted_post():
    card = qc.social_card(X_URL, X_SNIPPET, "Elon Musk on X", "Entering Twitter HQ", True)
    assert card["kind"] == "social" and card["site"] == "x.com" and card["label"] == "X" and card["url"] == X_URL
    assert (card["name"], card["handle"], card["when"]) == ("Elon Musk", "@elonmusk", "26 October 2022")
    # the photo is only a pic.twitter.com link in X's text: it leaves the text and the card says there is one
    assert card["text"] == "Entering Twitter HQ – let that sink in!" and card["media"] == "Photo or video"
    assert card["icon"] == "" and card["letter"] == "X" and card["thumb"] is True


def test_a_bluesky_post_keeps_its_paragraphs_and_its_date():
    card = qc.social_card(BSKY_URL, BSKY_SNIPPET, "Bluesky (@bsky.app) on Bluesky", "")
    assert (card["name"], card["handle"], card["when"]) == ("Bluesky", "@bsky.app", "17 October 2024")
    assert card["text"] == "Bluesky is an open social network. We're so excited to have you here!" + NL + NL + "A quick orientation thread"
    assert card["media"] == "" and card["thumb"] is False


def test_a_reddit_post_names_its_account_and_community():
    card = qc.social_card(REDDIT_URL, REDDIT_SNIPPET, "PresidentObama on Reddit", "")
    assert (card["name"], card["where"], card["icon"]) == ("u/PresidentObama", "r/IAmA", "reddit")
    assert card["text"] == "I am Barack Obama, President of the United States -- AMA" and card["handle"] == ""


def test_a_mastodon_post_reads_back_what_was_saved_for_it():
    url = "https://mastodon.social/@Gargron/1"
    saved = se.mastodon_quote_snippet("https://mastodon.social/@Gargron/1/embed", "Eugen Rochko", "@Gargron@mastodon.social",
                                      "2016-03-16T14:44:31.580Z", "Hello <world>" + NL + NL + "Two", True)
    card = qc.social_card(url, saved, "Eugen Rochko on Mastodon", "Hello <world>")
    assert (card["site"], card["label"], card["icon"]) == ("mastodon", "Mastodon", "mastodon")
    assert (card["name"], card["handle"], card["when"]) == ("Eugen Rochko", "@Gargron@mastodon.social", "16 March 2016")
    assert card["text"] == "Hello <world>" + NL + NL + "Two" and card["media"] == "Photo or video"
    # a post saved before (an iframe, nothing to read) still gets its handle from the link and its text from the preview
    old = qc.social_card(url, '<iframe src="https://mastodon.social/@Gargron/1/embed"></iframe>', "Eugen Rochko on Mastodon", "Hello")
    assert (old["name"], old["handle"], old["text"]) == ("Eugen Rochko", "@Gargron@mastodon.social", "Hello")


def test_the_sites_that_send_no_quote_get_their_card_from_the_preview_fields():
    instagram = qc.social_card("https://www.instagram.com/p/DAbc_1/", "<blockquote></blockquote>", "Post on Instagram", "")
    assert (instagram["name"], instagram["media"], instagram["icon"]) == ("", "Photo or video", "instagram")
    pin = qc.social_card("https://www.pinterest.com/pin/99360735500167749/", "", "Jane Doe on Pinterest", "Blue kitchen")
    assert (pin["name"], pin["text"], pin["media"]) == ("Jane Doe", "Blue kitchen", "Photo")
    tiktok = qc.social_card("https://www.tiktok.com/@scout2015/video/6718335390845095173", "", "Scout on TikTok", "")
    assert (tiktok["handle"], tiktok["media"]) == ("@scout2015", "Video")
    # "@zuck on Threads" says the handle once, not as a name and a handle
    threads = qc.social_card("https://www.threads.net/@zuck/post/C3_dPZfyfFq", "", "@zuck on Threads", "")
    assert (threads["name"], threads["handle"]) == ("", "@zuck")
    assert qc.social_card("https://www.facebook.com/x/videos/1/", "", "", "")["media"] == "Video"
    # a Bluesky link by a did names nobody by it
    assert qc.social_card("https://bsky.app/profile/did:plc:abc/post/3l6o", "", "", "")["handle"] == ""


def test_the_card_is_plain_text_whatever_the_site_sent():
    hostile = ('<blockquote><p>Hi <b>there</b><img src=x onerror=alert(1)><script>alert(1)</script></p>'
               '&mdash; A &lt;b&gt; (@a) <a href="javascript:alert(1)">March 1, 2024</a></blockquote>')
    card = qc.social_card("https://x.com/a/status/1", hostile, "", "")
    assert card["text"] == "Hi there" and card["name"] == "A <b>" and card["when"] == "1 March 2024"
    assert all("<" not in str(card[key]) for key in ("url", "handle", "when", "where", "media"))


@pytest.mark.parametrize("url", [
    "https://x.com/elonmusk",                          # a profile
    "https://x.com/search?q=cats",
    "https://www.reddit.com/r/IAmA/",                  # a community
    "https://bsky.app/profile/bsky.app",
    "https://www.instagram.com/natgeo/",
    "https://mastodon.social/@Gargron",                # a Mastodon profile
    "http://mastodon.social/@Gargron/1",               # https only
    "https://example.com/a/status/1",
    "",
])
def test_a_link_that_is_not_a_post_keeps_the_link_preview(url):
    assert qc.social_card(url, "", "", "") is None


@pytest.mark.parametrize("raw, shown", [
    ("October 26, 2022", "26 October 2022"),
    ("2024-10-17T07:06:51.491Z", "17 October 2024"),
    ("2016-03-16T14:44:31+00:00", "16 March 2016"),
    ("yesterday", "yesterday"),
    ("x" * 90, "x" * 40),
    ("", ""),
])
def test_dates_read_as_the_site_writes_them(raw, shown):
    assert qc._when(raw) == shown


@pytest.mark.parametrize("url, b36", [
    ("https://example.org/+news/post/za/harbour-lights", "za"),
    ("https://example.org/post/za/harbour-lights", "za"),
    ("https://example.org/post/ZA", "za"),
    ("http://example.org/post/za/", "za"),
    ("https://example.org:443/post/za", "za"),
    ("https://example.org/post/za/harbour-lights/1c", None),     # a comment, not the post
    ("https://example.org/@trend_t1", None),
    ("https://example.org/+news", None),
    ("https://other.org/post/za", None),
    ("https://example.org.evil.test/post/za", None),
    ("ftp://example.org/post/za", None),
    ("javascript:alert(1)//example.org/post/za", None),
    ("https://ex.co/za", "za"),                                  # the short domain
    ("https://ex.co/za/more", None),
    ("https://ex.co/post/za", None),
])
def test_which_links_are_posts_on_this_site(url, b36):
    assert qc.quoted_post_id(url, "example.org", "ex.co") == b36


def test_without_a_short_domain_only_the_sites_own_links_count():
    assert qc.quoted_post_id("https://ex.co/za", "example.org") is None
    assert qc.quoted_post_id("https://example.org/post/za", "example.org:8080") == "za"


def test_what_a_reader_is_told_instead_of_a_post():
    def notice(**given):
        rules = dict(missing=False, deleted=False, removed=False, filtered=False, blocked=False, muted=False, anonymous=False)
        rules.update(given)
        return qc.post_notice(**rules)

    assert notice() is None
    # a post the reader may not see is only "not available": nothing says it is in a Circle or a private guild
    assert notice(missing=True, deleted=True, removed=True) == qc.UNAVAILABLE == "This post isn't available."
    assert notice(deleted=True, removed=True) == qc.DELETED
    assert notice(removed=True, filtered=True) == qc.REMOVED
    assert notice(filtered=True, blocked=True) == qc.FILTERED
    assert notice(blocked=True, muted=True) == qc.UNAVAILABLE
    assert notice(muted=True) == qc.MUTED
    # an anonymous post is never hidden for who wrote it (that would say whose it is)
    assert notice(blocked=True, anonymous=True) is None and notice(muted=True, anonymous=True) is None
    assert notice(filtered=True, anonymous=True) == qc.FILTERED


def test_a_quoted_post_that_quotes_says_so_in_one_line():
    assert qc.quoting_line("https://example.org/post/zz", "example.org") == "Quoting a post"
    assert qc.quoting_line(X_URL, "example.org") == "Quoting @elonmusk on X"
    assert qc.quoting_line(REDDIT_URL, "example.org") == "Quoting a post on Reddit"
    assert qc.quoting_line("https://example.com/news", "example.org") == ""
    assert qc.quoting_line(None, "example.org") == ""


def test_every_site_icon_is_a_font_awesome_free_brand():
    names = set((ROOT / "tests" / "data" / "fa_free_6_4_0_names.txt").read_text(encoding="utf-8").split())
    assert set(qc._ICONS.values()) <= names
    assert set(qc._POST_PATHS) == set(se.SITES)
