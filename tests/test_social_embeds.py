"""Posts from the major social sites (ruqqus/helpers/social_embeds.py, ruqqus/helpers/embed.py): which site a link is,
what it is called, the link-preview fields, YouTube Shorts and live, and the Mastodon rule. The quote card itself:
tests/test_quote_cards.py."""
from pathlib import Path

import pytest

from ruqqus.helpers import social_embeds as se

ROOT = Path(__file__).resolve().parent.parent
EMBED = (ROOT / "ruqqus" / "helpers" / "embed.py").read_text(encoding="utf-8")


@pytest.mark.parametrize("url, site", [
    ("https://x.com/elonmusk/status/1", "x.com"),
    ("https://twitter.com/a/status/1", "twitter.com"),
    ("https://mobile.twitter.com/a/status/1", "twitter.com"),
    ("https://www.instagram.com/p/abc/", "instagram.com"),
    ("https://old.reddit.com/r/x/comments/1/y/", "reddit.com"),
    ("https://bsky.app/profile/a.bsky.social/post/3abc", "bsky.app"),
    ("https://www.threads.net/@zuck/post/C3abc", "threads.net"),
    ("https://www.facebook.com/x/posts/1", "facebook.com"),
    ("https://example.com/x", None),
    ("https://notx.com/a", None),
    ("https://x.com.evil.example/a", None),
    ("javascript:alert(1)", None),
])
def test_which_site_a_link_is(url, site):
    assert se.site_of(url) == site


def test_every_site_has_a_name_and_nothing_loads_its_script():
    assert se.label_of("https://x.com/a/status/1") == "X" and se.label_of("https://mobile.twitter.com/a/status/1") == "X"
    assert se.label_of("https://old.reddit.com/r/x/comments/1/") == "Reddit"
    assert se.label_of("https://example.com/a") == ""
    assert all(isinstance(label, str) and label for label in se.SITES.values())
    # posts are quote cards in this site's own font (helpers/quote_cards.py): no site's script, widget or frame
    assert not hasattr(se, "script_for") and not hasattr(se, "mastodon_snippet")


def test_the_feed_card_is_the_author_and_the_posts_own_words():
    x = {"author_name": "Elon Musk", "html": '<blockquote class="twitter-tweet"><p lang="en">Hello &amp;   world</p>&mdash; Elon</blockquote>'}
    assert se.card_from_oembed(x, "https://x.com/elonmusk/status/1") == ("Elon Musk on X", "Hello & world")
    reddit = {"author_name": "PresidentObama", "title": "I am Barack Obama", "html": "<blockquote>...</blockquote>"}
    assert se.card_from_oembed(reddit, "https://www.reddit.com/r/IAmA/comments/1/") == ("PresidentObama on Reddit", "I am Barack Obama")
    long = {"author_name": "A", "title": "x" * 400}
    assert len(se.card_from_oembed(long, "https://bsky.app/profile/a/post/1")[1]) == se.CARD_TEXT_MAX
    assert se.card_from_oembed(None, "https://x.com/a/status/1") == ("", "")
    assert se.card_from_oembed({}, "https://x.com/a/status/1") == ("", "")


@pytest.mark.parametrize("url, video", [
    ("https://www.youtube.com/watch?v=dQw4w9WgXcQ", "dQw4w9WgXcQ"),
    ("https://youtu.be/dQw4w9WgXcQ?t=10", "dQw4w9WgXcQ"),
    ("https://www.youtube.com/shorts/dQw4w9WgXcQ", "dQw4w9WgXcQ"),
    ("https://youtube.com/live/dQw4w9WgXcQ?feature=share", "dQw4w9WgXcQ"),
    ("https://m.youtube.com/embed/dQw4w9WgXcQ", "dQw4w9WgXcQ"),
    ("https://www.youtube.com/channel/UCabc", None),
    ("https://www.youtube.com/watch?v=bad\"id", None),
    ("https://notyoutube.com/watch?v=dQw4w9WgXcQ", None),
])
def test_youtube_shorts_and_live_are_videos_too(url, video):
    assert se.youtube_id(url) == video


def test_facebook_posts_and_videos_ask_different_services():
    assert se.facebook_kind("https://www.facebook.com/facebook/posts/10153231379946729") == "post"
    assert se.facebook_kind("https://www.facebook.com/watch/?v=1") == "video"
    assert se.facebook_kind("https://www.facebook.com/x/videos/1/") == "video"
    assert se.facebook_kind("https://fb.watch/abc/") == "video"
    assert se.facebook_kind("https://www.facebook.com/reel/1") == "video"


def test_a_mastodon_post_is_only_a_link_shaped_like_one():
    assert se.mastodon_post("https://mastodon.social/@Gargron/1") == ("mastodon.social", "https://mastodon.social/api/oembed")
    assert se.mastodon_post("https://fosstodon.org/@a@other.example/109/") == ("fosstodon.org", "https://fosstodon.org/api/oembed")
    for url in ("http://mastodon.social/@a/1",            # https only
                "https://mastodon.social:8443/@a/1",      # the usual port
                "https://mastodon.social/@a",             # a profile, not a post
                "https://mastodon.social/@a/1/../../etc",
                "https://www.threads.net/@a/1",           # a fixed site of its own
                "https://example.com/x"):
        assert se.mastodon_post(url) is None, url


def test_only_the_servers_own_embed_page_counts_and_what_is_saved_is_ours_and_escaped():
    assert se.mastodon_iframe_ok("https://mastodon.social/@Gargron/1/embed", "mastodon.social")
    assert not se.mastodon_iframe_ok("https://evil.example/@Gargron/1/embed", "mastodon.social")
    assert not se.mastodon_iframe_ok("http://mastodon.social/@Gargron/1/embed", "mastodon.social")
    assert not se.mastodon_iframe_ok("https://mastodon.social/@Gargron/1", "mastodon.social")
    assert not se.mastodon_iframe_ok("javascript:alert(1)", "mastodon.social")
    saved = se.mastodon_quote_snippet('https://mastodon.social/@a/1/embed"><script>', "Eve <b>", "@a@mastodon.social",
                                      "2016-03-16T14:34:26.392Z", "Hello <world>" + chr(10) * 2 + "Second", True)
    assert "<script>" not in saved and "<b>" not in saved and "&quot;&gt;&lt;script&gt;" in saved
    assert "<p>Hello &lt;world&gt;</p><p>Second</p>" in saved and 'data-media="1"' in saved
    assert "&mdash; Eve &lt;b&gt; (@a@mastodon.social)" in saved
    assert 'data-media' not in se.mastodon_quote_snippet("https://m.example/@a/1/embed", "", "", "", "", False)


def test_a_mastodon_post_is_read_from_the_servers_public_api():
    assert se.mastodon_status_id("https://mastodon.social/@Gargron/109") == "109"
    assert se.mastodon_status_id("https://mastodon.social/@Gargron") is None
    assert se.mastodon_text("<p>One<br>two</p><p>Three <a href='x'>#tag</a></p>") == "One" + chr(10) + "two" + chr(10) * 2 + "Three #tag"
    assert se.mastodon_text(None) == ""
    assert se.mastodon_handle("Gargron", "mastodon.social") == "@Gargron@mastodon.social"
    assert se.mastodon_handle("a@other.example", "mastodon.social") == "@a@other.example"
    assert se.mastodon_handle("bad name<", "mastodon.social") == "" and se.mastodon_handle(None, "x") == ""


def test_threads_is_its_own_markup_built_from_the_link():
    snippet = se.threads_snippet("https://www.threads.net/@zuck/post/C3_dPZfyfFq")
    assert 'data-text-post-permalink="https://www.threads.com/@zuck/post/C3_dPZfyfFq"' in snippet
    assert se.threads_snippet("https://www.threads.net/@zuck") is None
    assert se.threads_snippet('https://www.threads.net/@zuck/post/C3"><x') is None
    assert se.threads_card("https://www.threads.net/@zuck/post/C3_dPZfyfFq") == ("@zuck on Threads", "")


def test_embed_py_wires_every_site_and_mastodon_goes_through_safe_fetch():
    table = EMBED[EMBED.index("KNOWN_PROVIDER_HANDLERS = {"):EMBED.index("}", EMBED.index("KNOWN_PROVIDER_HANDLERS = {"))]
    for site in ("x.com", "twitter.com", "instagram.com", "facebook.com", "tiktok.com", "bsky.app", "reddit.com",
                 "redd.it", "pinterest.com", "threads.net", "threads.com", "tumblr.com"):
        assert f'"{site}": ("widget",' in table, site
    gap = chr(10) * 3
    assert '"omit_script": "true"' in EMBED.split("def x_oembed(")[1].split(gap)[0]       # the snippet is only read
    mastodon = EMBED.split("def mastodon_embed(")[1].split(gap)[0]
    assert "safe_fetch.get_json(endpoint" in mastodon and "social_embeds.mastodon_iframe_ok(src, host)" in mastodon
    assert 'safe_fetch.get_json(f"https://{host}/api/v1/statuses/{status_id}")' in mastodon
    assert "social_embeds.mastodon_embed_src(" in mastodon and "social_embeds.mastodon_quote_snippet(" in mastodon
    assert "requests.get" not in mastodon
    assert "yt_id = social_embeds.youtube_id(url)" in EMBED
    thumbs = (ROOT / "ruqqus" / "helpers" / "thumbs.py").read_text(encoding="utf-8")
    assert "known_type, known_src, known_thumb, known_card = known_provider_embed(fetch_url)" in thumbs
    assert "post.submission_aux.meta_title = card_title[:500]" in thumbs


def test_a_social_post_is_a_quote_card_and_never_a_widget():
    templates = ROOT / "ruqqus" / "templates"
    listing = (templates / "submission_listing.html").read_text(encoding="utf-8")
    assert "{% if quote %}" in listing and "{% elif p.embed_url and p.embed_kind != 'widget' %}" in listing
    page = (templates / "submission.html").read_text(encoding="utf-8")
    assert "{% if p.embed_kind and p.embed_kind != 'widget' %}" in page
    assert not (templates / "site_embeds" / "widget.html").exists()


def test_a_mastodon_embed_page_is_read_from_a_frame_or_the_new_placeholder():
    assert se.mastodon_embed_src('<iframe src="https://m.example/@a/1/embed"></iframe>') == "https://m.example/@a/1/embed"
    quote = '<blockquote class="mastodon-embed" data-embed-url="https://mastodon.social/@Gargron/1/embed"><a href="x">x</a></blockquote>'
    assert se.mastodon_embed_src(quote) == "https://mastodon.social/@Gargron/1/embed"
    assert se.mastodon_embed_src("<p>nothing</p>") is None and se.mastodon_embed_src(None) is None


def test_a_site_that_names_nobody_still_gets_a_card():
    assert se.default_card("https://www.instagram.com/p/x/") == ("Post on Instagram", "")
    assert se.default_card("https://example.com/x") == ("", "")
