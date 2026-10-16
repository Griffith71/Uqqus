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
    card = qc.social_card(X_URL, X_SNIPPET, "Elon Musk on X", "Entering Twitter HQ")
    assert card["kind"] == "social" and card["site"] == "x.com" and card["label"] == "X" and card["url"] == X_URL
    assert (card["name"], card["handle"], card["when"]) == ("Elon Musk", "@elonmusk", "26 October 2022")
    # the photo is only a pic.twitter.com link in X's text: it leaves the text and the card says there is one
    assert card["text"] == "Entering Twitter HQ – let that sink in!" and card["media"] == "Photo or video"
    assert card["icon"] == "" and card["letter"] == "X" and card["image"] == "" and card["video"] is False


def test_a_bluesky_post_keeps_its_paragraphs_and_its_date():
    card = qc.social_card(BSKY_URL, BSKY_SNIPPET, "Bluesky (@bsky.app) on Bluesky", "")
    assert (card["name"], card["handle"], card["when"]) == ("Bluesky", "@bsky.app", "17 October 2024")
    assert card["text"] == "Bluesky is an open social network. We're so excited to have you here!" + NL + NL + "A quick orientation thread"
    assert card["media"] == "" and card["image"] == ""


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


# A post on X that quotes another (2026-10-16): Elon Musk's "Makes sense" quotes Tyler Palmer's post, which quotes an
# earlier post of Elon Musk's. X's oEmbed sends each quoted post only as the t.co link that ends the text.
MAKES_SENSE = ('<blockquote class="twitter-tweet" data-dnt="true"><p lang="en" dir="ltr">Makes sense <a href="https://t.co/'
               'qDpOwsUzpY">https://t.co/qDpOwsUzpY</a></p>&mdash; Elon Musk (@elonmusk) <a href="https://x.com/elonmusk/'
               'status/2110832405426933893?ref_src=twsrc%5Etfw">October 15, 2026</a></blockquote>')
PALMER = ('<blockquote class="twitter-tweet" data-dnt="true"><p lang="en" dir="ltr">imagine an Amazon competitor that is:'
          '<br><br>1) agent first - built for personal agents <br>2) america first - sells only products from usa<br><br>'
          'might actually be a huge market for this. <a href="https://t.co/wGKTv1kaBW">https://t.co/wGKTv1kaBW</a></p>'
          '&mdash; Tyler Palmer (@tylerpalmer) <a href="https://x.com/tylerpalmer/status/2110761858231935251?ref_src=twsrc'
          '%5Etfw">October 15, 2026</a></blockquote>')
MAKES_SENSE_URL = "https://x.com/elonmusk/status/2110832405426933893"
PALMER_URL = "https://x.com/tylerpalmer/status/2110761858231935251"
FIRST_URL = "https://x.com/elonmusk/status/2110715556328579219"


PHOTO = "https://pbs.twimg.com/media/GZx1abcWsAA9xyz?format=jpg&name=large"
STILL = "https://pbs.twimg.com/ext_tw_video_thumb/1585341912877146112/pu/img/DwJ7wlGIe9iryk6N?format=webp&name=large"


def _page(card, image):
    """The link-preview tags X puts on a post's page."""
    return ('<html><head><meta name="twitter:card" content="' + card + '"/><meta property="og:image" content="'
            + image + '"/><title>x</title></head></html>')


def _saved_quote():
    return se.quoted_post_snippet(MAKES_SENSE, PALMER_URL, "https://t.co/qDpOwsUzpY", PALMER, FIRST_URL, "https://t.co/wGKTv1kaBW")


def test_the_quoted_post_on_x_is_the_t_co_link_that_ends_the_text():
    assert se.last_tco_link(MAKES_SENSE) == "https://t.co/qDpOwsUzpY"
    assert se.last_tco_link(PALMER) == "https://t.co/wGKTv1kaBW"
    assert se.last_tco_link(X_SNIPPET) is None                       # a picture: "pic.twitter.com/..."
    article = MAKES_SENSE.replace('qDpOwsUzpY">https://t.co/qDpOwsUzpY<', 'qDpOwsUzpY">bbc.co.uk/news<')
    assert se.last_tco_link(article) is None                          # an article's link reads as its own address
    assert se.last_tco_link("") is None and se.last_tco_link("<p>no quote</p>") is None
    assert se.is_tco("https://t.co/qDpOwsUzpY") and not se.is_tco("http://t.co/qDpOwsUzpY")
    assert not se.is_tco("https://t.co.evil.test/qDpOwsUzpY") and not se.is_tco("https://t.co/a/b")


@pytest.mark.parametrize("url, status", [
    ("https://twitter.com/tylerpalmer/status/2110761858231935251", PALMER_URL),
    ("https://x.com/tylerpalmer/status/2110761858231935251?s=20", PALMER_URL),
    ("https://mobile.twitter.com/tylerpalmer/statuses/2110761858231935251", PALMER_URL),
    ("https://x.com/tylerpalmer", None),
    ("https://x.com/i/spaces/1abc", None),
    ("https://example.com/tylerpalmer/status/1", None),
    ("javascript:alert(1)", None),
    (None, None),
])
def test_only_a_post_on_x_counts_as_a_quoted_post(url, status):
    assert se.x_status_url(url) == status


def test_an_x_post_shows_the_post_it_quotes_inside_and_no_deeper():
    card = qc.social_card(MAKES_SENSE_URL, _saved_quote(), "Elon Musk on X", "Makes sense https://t.co/qDpOwsUzpY")
    assert (card["name"], card["text"], card["quoting"]) == ("Elon Musk", "Makes sense", "")   # the t.co link is the card
    inner = card["inner"]
    assert (inner["name"], inner["handle"], inner["url"], inner["when"]) == ("Tyler Palmer", "@tylerpalmer", PALMER_URL, "15 October 2026")
    assert inner["text"].startswith("imagine an Amazon competitor") and inner["text"].endswith("a huge market for this.")
    # as on X, the post under that one is not drawn: one line says what it quotes
    assert inner["inner"] is None and inner["quoting"] == "Quoting @elonmusk on X" and inner["image"] == ""


def test_a_quote_with_no_words_of_its_own_stays_without_them():
    bare = MAKES_SENSE.replace("Makes sense ", "")
    saved = se.quoted_post_snippet(bare, PALMER_URL, "https://t.co/qDpOwsUzpY", PALMER)
    card = qc.social_card(MAKES_SENSE_URL, saved, "Elon Musk on X", "https://t.co/qDpOwsUzpY")
    assert card["text"] == "" and card["inner"]["name"] == "Tyler Palmer" and card["inner"]["quoting"] == ""


def test_a_post_saved_before_keeps_its_link_and_nothing_inside():
    card = qc.social_card(MAKES_SENSE_URL, MAKES_SENSE, "Elon Musk on X", "")
    assert card["text"] == "Makes sense https://t.co/qDpOwsUzpY" and card["inner"] is None


def test_what_is_saved_for_a_quoted_post_is_escaped_and_only_an_x_post_is_drawn():
    saved = se.quoted_post_snippet(MAKES_SENSE, 'https://x.com/a/status/1"><script>', "https://t.co/x", PALMER)
    assert "<script>" not in saved.split("quoted-post")[1].split(">")[0] and "&quot;&gt;&lt;script&gt;" in saved
    # a saved address that is not a post on X, Bluesky or Mastodon is not drawn as a quoted post
    odd = se.quoted_post_snippet(MAKES_SENSE, "https://evil.example/a/status/1", "https://t.co/qDpOwsUzpY", PALMER)
    assert qc.social_card(MAKES_SENSE_URL, odd, "", "")["inner"] is None


def test_a_card_inside_a_card_says_what_it_quotes_in_one_line_and_keeps_its_own_picture():
    card = qc.social_card(MAKES_SENSE_URL, _saved_quote(), "Elon Musk on X", "", PHOTO)
    inner = qc.inside(card)
    assert inner["inner"] is None and inner["quoting"] == "Quoting @tylerpalmer on X" and inner["image"] == PHOTO
    assert card["inner"] is not None and card["quoting"] == ""                  # the cached card is left as it was
    plain = qc.inside(qc.social_card(X_URL, X_SNIPPET, "", ""))
    assert plain["quoting"] == "" and plain["inner"] is None
    assert qc.inside(None) is None


def test_the_backfill_re_works_x_posts_saved_with_only_a_t_co_link():
    import importlib.util
    spec = importlib.util.spec_from_file_location("backfill", ROOT / "scripts" / "backfill_social_embeds.py")
    source = spec.loader.get_data(spec.origin).decode("utf-8")
    assert "def wanted(url, no_card=True, framed=False, saved=" in source
    assert 'if not quoted and social_embeds.last_tco_link(saved):' in source
    assert 'if read_now and saved and "data-video=" not in saved:' in source          # Bluesky, Mastodon, Instagram
    # and X posts with a photo or video but no picture, or a quoted post saved before pictures were read
    assert 'if not picture and ("pic.twitter.com" in saved or "pic.x.com" in saved):' in source
    assert 'if quoted and "data-image=" not in saved:' in source


def test_an_x_posts_picture_is_its_photo_or_video_still_never_a_profile_picture():
    assert se.x_media(_page("summary_large_image", PHOTO)) == PHOTO
    assert se.x_media(_page("summary_large_image", STILL.replace("&", "&amp;"))) == STILL
    # a post with no picture: X puts its author's profile picture there, which is not the post's
    assert se.x_media(_page("summary", "https://pbs.twimg.com/profile_images/2103567689515954176/aR-Kjpp4_400x400.jpg")) == ""
    assert se.x_media(_page("summary_large_image", "https://pbs.twimg.com/profile_images/1/a.jpg")) == ""
    assert se.x_media(_page("summary_large_image", "https://evil.example/media/a.jpg")) == ""
    assert se.x_media(_page("summary_large_image", "http://pbs.twimg.com/media/a.jpg")) == ""
    assert se.x_media("") == "" and se.x_media(None) == ""
    assert se.x_media_is_video(STILL) and not se.x_media_is_video(PHOTO)
    assert se.x_media_is_video("https://pbs.twimg.com/amplify_video_thumb/1/img/a.jpg")


def test_a_card_shows_its_posts_picture_and_a_video_as_a_still():
    photo = qc.social_card(X_URL, X_SNIPPET, "Elon Musk on X", "", PHOTO)
    assert photo["image"] == PHOTO and photo["video"] is False and photo["media"] == ""   # the picture says it
    video = qc.social_card(X_URL, X_SNIPPET, "Elon Musk on X", "", STILL)
    assert video["image"] == STILL and video["video"] is True and video["media"] == ""
    # any site's preview picture is shown, but only an https address
    insta = qc.social_card("https://www.instagram.com/p/DAbc_1/", "", "Post on Instagram", "", "https://cdn.example/p.jpg")
    assert insta["image"] == "https://cdn.example/p.jpg" and insta["video"] is False
    assert qc.social_card(X_URL, X_SNIPPET, "", "", "javascript:alert(1)")["image"] == ""
    assert qc.social_card(X_URL, X_SNIPPET, "", "", "http://cdn.example/p.jpg")["image"] == ""


def test_the_quoted_post_on_x_carries_its_own_picture():
    saved = se.quoted_post_snippet(MAKES_SENSE, PALMER_URL, "https://t.co/qDpOwsUzpY", PALMER, image=PHOTO)
    card = qc.social_card(MAKES_SENSE_URL, saved, "Elon Musk on X", "")
    assert card["image"] == "" and card["inner"]["image"] == PHOTO
    # only an https picture is saved for it
    odd = se.quoted_post_snippet(MAKES_SENSE, PALMER_URL, "https://t.co/qDpOwsUzpY", PALMER, image="http://pbs.twimg.com/media/a.jpg")
    assert 'data-image=""' in odd and qc.social_card(MAKES_SENSE_URL, odd, "", "")["inner"]["image"] == ""
