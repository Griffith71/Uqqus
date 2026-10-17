"""Players and cards for links to other sites (ruqqus/helpers/embed.py, ruqqus/helpers/social_embeds.py, 2026-10-16):
Facebook videos and reels, Instagram reels and TikTok videos play in the site's plain player, loaded on click; music,
more video sites and GIFs get their players; Telegram and Truth Social posts get quote cards; a Facebook post (no token,
by the owner's choice) names its page. Every player address is kept only on that site's own hosts.

The handlers run for real here: embed.py is loaded with the app replaced by a stand-in and `requests` by answers
recorded from each site on 2026-10-16 (shapes trimmed to what the code reads)."""
import importlib.util
import sys
import types
from pathlib import Path

import pytest

from ruqqus.helpers import social_embeds as se

ROOT = Path(__file__).resolve().parent.parent


# --- the stand-in network ------------------------------------------------------------------------------------------

class Answer:
    def __init__(self, status=200, data=None, text="", headers=None):
        self.status_code, self._data, self.text, self.headers = status, data, text, headers or {}

    def json(self):
        if self._data is None:
            raise ValueError("not json")
        return self._data


class Net:
    """`requests` for embed.py: answers by address prefix; anything not listed fails like a dead host."""

    def __init__(self, gets=None, heads=None):
        self.gets, self.heads, self.asked = gets or {}, heads or {}, []

    def _find(self, table, url):
        self.asked.append(url)
        for prefix, answer in table.items():
            if url.startswith(prefix):
                if isinstance(answer, Exception):
                    raise answer
                return answer
        raise ConnectionError(url)

    def get(self, url, params=None, headers=None, timeout=None, allow_redirects=True):
        return self._find(self.gets, url)

    def head(self, url, headers=None, allow_redirects=True, timeout=None):
        assert allow_redirects is False                      # a share link's target is read, never followed
        return self._find(self.heads, url)


@pytest.fixture
def embed(monkeypatch):
    main = types.ModuleType("ruqqus.__main__")
    main.app = types.SimpleNamespace(config={"SERVER_NAME": "uqqus.test"})
    monkeypatch.setitem(sys.modules, "ruqqus.__main__", main)
    monkeypatch.setitem(sys.modules, "ruqqus.helpers.get", types.ModuleType("ruqqus.helpers.get"))
    spec = importlib.util.spec_from_file_location("ruqqus.helpers.embed", ROOT / "ruqqus" / "helpers" / "embed.py")
    module = importlib.util.module_from_spec(spec)
    module.__package__ = "ruqqus.helpers"
    spec.loader.exec_module(module)
    return module


def use(embed, monkeypatch, **tables):
    net = Net(**tables)
    monkeypatch.setattr(embed, "requests", net)
    return net


# Facebook's video oEmbed, tokenless (recorded 2026-10-16)
FB_VIDEO = ('<div id="fb-root"></div><script async src="https://connect.facebook.net/en_GB/sdk.js"></script>'
            '<div class="fb-video" data-href="https://www.facebook.com/facebook/videos/10153231379946729/">'
            '<blockquote cite="https://www.facebook.com/facebook/videos/10153231379946729/" class="fb-xfbml-parse-ignore">'
            '<a href="https://www.facebook.com/facebook/videos/10153231379946729/">How to Share With Just Friends</a>'
            '<p>How to share with just friends.</p>Posted by <a href="https://www.facebook.com/facebook">Facebook</a>'
            ' on Friday, December 5, 2014</blockquote></div>')


# --- Facebook --------------------------------------------------------------------------------------------------------

def test_facebook_reads_title_text_page_and_date_from_its_snippet():
    assert se.facebook_card(FB_VIDEO) == {"name": "Facebook", "when": "Friday, December 5, 2014",
                                          "title": "How to Share With Just Friends", "text": "How to share with just friends."}
    assert se.card_from_facebook(FB_VIDEO, "https://www.facebook.com/facebook/videos/1/") == (
        "Facebook on Facebook", "How to Share With Just Friends - How to share with just friends.")
    assert se.facebook_card("") == {}
    assert se.card_from_facebook("", "https://www.facebook.com/NASA/posts/123") == ("NASA on Facebook", "")
    assert se.card_from_facebook("", "https://www.facebook.com/permalink.php?story_fbid=1") == ("Post on Facebook", "")


def test_facebook_links_are_sorted_into_videos_and_posts():
    for url in ("https://www.facebook.com/share/v/1A2b3C/", "https://www.facebook.com/share/r/1A2b3C/",
                "https://www.facebook.com/reel/1046328406826497", "https://fb.watch/abc/",
                "https://m.facebook.com/watch/?v=10153231379946729", "https://www.facebook.com/NASA/videos/123456/"):
        assert se.facebook_kind(url) == "video", url
    for url in ("https://www.facebook.com/share/p/1A2b3C/", "https://www.facebook.com/NASA/posts/pfbid02x"):
        assert se.facebook_kind(url) == "post", url
    assert se.facebook_needs_resolving("https://www.facebook.com/share/p/1A2b3C/")
    assert se.facebook_needs_resolving("https://fb.watch/abc/")
    assert not se.facebook_needs_resolving("https://www.facebook.com/NASA/posts/123")
    assert se.facebook_canonical("https://m.facebook.com/NASA/videos/123/?ref=share") == "https://www.facebook.com/NASA/videos/123/"
    assert se.facebook_canonical("https://m.facebook.com/watch/?v=123") == "https://www.facebook.com/watch/?v=123"
    assert se.facebook_names_a_video("https://www.facebook.com/facebook/videos/10153231379946729/")
    assert se.facebook_names_a_video("https://www.facebook.com/reel/1046328406826497")
    assert not se.facebook_names_a_video("https://www.facebook.com/watch/")             # what a dead fb.watch leads to
    assert se.facebook_page_name("https://www.facebook.com/NASA/posts/1") == "NASA"
    for url in ("https://www.facebook.com/login/?next=x", "https://www.facebook.com/100064860875397/posts/1",
                "https://www.facebook.com/groups/abc/posts/1", "https://www.facebook.com/share/p/x/"):
        assert se.facebook_page_name(url) == "", url


def test_a_facebook_video_plays_in_facebooks_own_player_with_its_words(embed, monkeypatch):
    use(embed, monkeypatch, gets={"https://graph.facebook.com/v21.0/oembed_video": Answer(data={"html": FB_VIDEO})})
    kind, src, thumb, card = embed.known_provider_embed("https://m.facebook.com/facebook/videos/10153231379946729/")
    assert kind == "iframe"
    assert src == ("https://www.facebook.com/plugins/video.php?href=https%3A%2F%2Fwww.facebook.com%2Ffacebook%2Fvideos"
                   "%2F10153231379946729%2F&show_text=false")
    assert card == ("Facebook on Facebook", "How to Share With Just Friends - How to share with just friends.")


def test_a_reel_is_a_vertical_player_and_still_plays_when_the_oembed_is_down(embed, monkeypatch):
    use(embed, monkeypatch)                                                              # every call fails
    kind, src, thumb, card = embed.known_provider_embed("https://www.facebook.com/reel/1046328406826497")
    assert kind == "vertical" and src.startswith("https://www.facebook.com/plugins/video.php?href=")
    assert card == ("Post on Facebook", "")


def test_a_facebook_post_without_a_token_names_its_page(embed, monkeypatch):
    use(embed, monkeypatch, gets={"https://graph.facebook.com/v21.0/oembed_post": Answer(400, {"error": {"code": 100}})})
    kind, snippet, thumb, card = embed.known_provider_embed("https://www.facebook.com/NASA/posts/pfbid02abc")
    assert kind == "widget" and 'class="fb-post"' in snippet
    assert card == ("NASA on Facebook", "")


def test_a_share_link_counts_only_when_it_leads_to_a_video_or_a_post(embed, monkeypatch):
    video = "https://www.facebook.com/facebook/videos/10153231379946729/"
    net = use(embed, monkeypatch, heads={"https://www.facebook.com/share/v/good/": Answer(302, headers={"Location": video})},
              gets={"https://graph.facebook.com": Answer(data={"html": FB_VIDEO})})
    kind, src, _, _ = embed.known_provider_embed("https://www.facebook.com/share/v/good/")
    assert kind == "iframe" and "10153231379946729" in src
    assert "https://www.facebook.com/share/v/good/" in net.asked
    for location in ("https://www.facebook.com/login/?next=x", "https://evil.example/videos/1", "http://www.facebook.com/x/videos/1"):
        use(embed, monkeypatch, heads={"https://www.facebook.com/share/v/bad/": Answer(302, headers={"Location": location})})
        kind, src, _, _ = embed.known_provider_embed("https://www.facebook.com/share/v/bad/")
        assert "share%2Fv%2Fbad" in src, location                                        # the link itself, unchanged


# --- Instagram, TikTok -------------------------------------------------------------------------------------------------

def test_an_instagram_reel_plays_in_instagrams_player(embed, monkeypatch):
    assert se.instagram_reel_code("https://www.instagram.com/reel/C5qLPhxsQMJ/") == "C5qLPhxsQMJ"
    assert se.instagram_reel_code("https://www.instagram.com/someone/reel/C5qLPhxsQMJ") == "C5qLPhxsQMJ"
    assert se.instagram_reel_code("https://www.instagram.com/p/C5qLPhxsQMJ/") is None      # a photo post: its card
    use(embed, monkeypatch)
    kind, src, _, card = embed.known_provider_embed("https://www.instagram.com/reel/C5qLPhxsQMJ/")
    assert (kind, src) == ("vertical", "https://www.instagram.com/reel/C5qLPhxsQMJ/embed/")
    assert card == ("Post on Instagram", "")


def test_a_tiktok_video_plays_even_when_tiktoks_oembed_times_out(embed, monkeypatch):
    assert se.tiktok_video_id("https://www.tiktok.com/@scout2015/video/6718335390845095173") == "6718335390845095173"
    assert se.tiktok_video_id("https://www.tiktok.com/@scout2015/photo/6718335390845095173") is None
    assert se.tiktok_needs_resolving("https://vm.tiktok.com/ZMabc/") and se.tiktok_needs_resolving("https://www.tiktok.com/t/ZT1/")
    use(embed, monkeypatch, gets={"https://www.tiktok.com/oembed": TimeoutError("read timed out")})
    kind, src, _, card = embed.known_provider_embed("https://www.tiktok.com/@scout2015/video/6718335390845095173")
    assert (kind, src) == ("vertical", "https://www.tiktok.com/player/v1/6718335390845095173?rel=0")
    assert card == ("Post on TikTok", "")


def test_a_tiktok_short_link_is_followed_once_and_only_to_a_video(embed, monkeypatch):
    target = "https://www.tiktok.com/@scout2015/video/6718335390845095173"
    use(embed, monkeypatch, heads={"https://vm.tiktok.com/": Answer(302, headers={"Location": target})},
        gets={"https://www.tiktok.com/oembed": Answer(data={"html": "<blockquote></blockquote>", "author_name": "Scout"})})
    kind, src, _, card = embed.known_provider_embed("https://vm.tiktok.com/ZMabc/")
    assert kind == "vertical" and src.endswith("/player/v1/6718335390845095173?rel=0") and card[0] == "Scout on TikTok"
    use(embed, monkeypatch, heads={"https://vm.tiktok.com/": Answer(302, headers={"Location": "https://www.tiktok.com/?_r=1"})})
    assert embed.known_provider_embed("https://vm.tiktok.com/dead/") == (None, None, None, None)


# --- music -------------------------------------------------------------------------------------------------------------

SPOTIFY = ('<iframe style="border-radius: 12px" width="100%" height="152" title="Spotify Embed: Never Gonna Give You Up" '
           'src="https://open.spotify.com/embed/track/4cOdK2wGLETKBW3PvgPWqT?utm_source=oembed"></iframe>')


def test_spotify_soundcloud_mixcloud_and_deezer_are_audio_players_from_their_own_hosts(embed, monkeypatch):
    use(embed, monkeypatch, gets={
        "https://open.spotify.com/oembed": Answer(data={"html": SPOTIFY, "title": "Never Gonna Give You Up", "type": "rich",
                                                        "thumbnail_url": "https://image-cdn-ak.spotifycdn.com/x"}),
        "https://soundcloud.com/oembed": Answer(data={"html": '<iframe src="https://w.soundcloud.com/player/?visual=true&url=https%3A%2F%2Fapi.soundcloud.com%2Ftracks%2F293"></iframe>',
                                                      "title": "Flickermood by Forss", "author_name": "Forss"}),
        "https://app.mixcloud.com/oembed/": Answer(data={"html": '<iframe width="100%" height="120" src="https://www.mixcloud.com/widget/iframe/?feed=x"></iframe>'}),
        "https://api.deezer.com/oembed": Answer(data={"html": '<iframe src="https://widget.deezer.com/widget/dark/track/3135556?app_id=457142"></iframe>'}),
    })
    kind, src, thumb, card = embed.known_provider_embed("https://open.spotify.com/track/4cOdK2wGLETKBW3PvgPWqT")
    assert (kind, card) == ("audio", ("Never Gonna Give You Up", "")) and src.startswith("https://open.spotify.com/embed/track/")
    kind, src, _, card = embed.known_provider_embed("https://soundcloud.com/forss/flickermood")
    assert kind == "audio" and "visual=false" in src and card == ("Flickermood by Forss", "Forss")
    assert embed.known_provider_embed("https://www.mixcloud.com/spartacus/party-time/")[0] == "audio"
    assert embed.known_provider_embed("https://www.deezer.com/track/3135556")[0] == "audio"


def test_a_player_on_any_other_host_is_refused(embed, monkeypatch):
    use(embed, monkeypatch, gets={"https://open.spotify.com/oembed": Answer(data={"html": '<iframe src="https://open.spotify.com.evil.example/embed/track/1"></iframe>'}),
                                  "https://api.streamable.com": Answer(data={"html": '<iframe src="http://streamable.com/o/moo"></iframe>'})})
    assert embed.known_provider_embed("https://open.spotify.com/track/1") == (None, None, None, None)
    assert embed.known_provider_embed("https://streamable.com/moo") == (None, None, None, None)     # not https


def test_apple_and_bandcamp_players_are_built_from_the_link_or_the_page():
    assert se.apple_player("https://music.apple.com/us/album/1989-taylors-version/1708308989") == \
        "https://embed.music.apple.com/us/album/1989-taylors-version/1708308989"
    assert se.apple_player("https://podcasts.apple.com/us/podcast/the-daily/id1200361736?i=1000600000000") == \
        "https://embed.podcasts.apple.com/us/podcast/the-daily/id1200361736?i=1000600000000"
    assert se.apple_player("https://music.apple.com/us/album/x/1?app=music&evil=1") == "https://embed.music.apple.com/us/album/x/1"
    assert se.apple_player("http://music.apple.com/us/album/x/1") is None
    page = '<meta name="bc-page-properties" content="{&quot;item_type&quot;:&quot;t&quot;,&quot;item_id&quot;:890894942}">'
    assert se.bandcamp_player(page).startswith("https://bandcamp.com/EmbeddedPlayer/track=890894942/")
    assert se.bandcamp_player('<meta name="bc-page-properties" content="{&quot;item_type&quot;:&quot;t&quot;,&quot;item_id&quot;:&quot;1;x&quot;}">') is None
    assert se.bandcamp_player("") is None


def test_bandcamp_reads_only_an_artists_track_or_album_page(embed, monkeypatch):
    page = '<meta name="bc-page-properties" content="{&quot;item_type&quot;:&quot;a&quot;,&quot;item_id&quot;:42}">'
    use(embed, monkeypatch, gets={"https://c418.bandcamp.com/album/": Answer(text=page)})
    assert embed.known_provider_embed("https://c418.bandcamp.com/album/minecraft")[:2] == (
        "audio", "https://bandcamp.com/EmbeddedPlayer/album=42/size=large/bgcol=ffffff/linkcol=0687f5/tracklist=false/artwork=small/transparent=true/")
    assert embed.known_provider_embed("https://c418.bandcamp.com/")[0] is None             # a profile: no player


# --- video sites, GIFs, pictures ----------------------------------------------------------------------------------------

def test_streamable_ted_kick_and_loom_are_video_players(embed, monkeypatch):
    use(embed, monkeypatch, gets={
        "https://api.streamable.com/oembed.json": Answer(data={"html": '<iframe class="streamable-embed" src="https://streamable.com/o/moo"></iframe>'}),
        "https://www.ted.com/services/v1/oembed.json": Answer(data={"html": '<iframe src="https://embed.ted.com/talks/sir_ken"></iframe>',
                                                                    "title": "Do schools kill creativity?", "author_name": "Sir Ken Robinson"}),
    })
    assert embed.known_provider_embed("https://streamable.com/moo")[:2] == ("iframe", "https://streamable.com/o/moo")
    assert embed.known_provider_embed("https://www.ted.com/talks/ken")[3] == ("Do schools kill creativity?", "Sir Ken Robinson")
    assert embed.known_provider_embed("https://kick.com/xqc")[:2] == ("iframe", "https://player.kick.com/xqc")
    assert se.kick_player("https://kick.com/xqc/clips/clip_01HABC123") == "https://player.kick.com/xqc?clip=clip_01HABC123"
    assert se.kick_player("https://kick.com/categories") is None
    assert embed.known_provider_embed("https://www.loom.com/share/0281766fa2d04bb788eaf19e65135184")[:2] == (
        "iframe", "https://www.loom.com/embed/0281766fa2d04bb788eaf19e65135184")


def test_gifs_and_pictures_come_from_their_sites_own_cdn(embed, monkeypatch):
    use(embed, monkeypatch, gets={
        "https://giphy.com/services/oembed": Answer(data={"url": "https://media1.giphy.com/media/JIX9t2j0ZTN9S/giphy.gif", "type": "photo"}),
        "https://tenor.com/oembed": Answer(data={"thumbnail_url": "https://media.tenor.com/YPuyoi1rZvAAAAAN/mob-psycho100.png"}),
        "https://www.flickr.com/services/oembed": Answer(data={"type": "photo", "url": "https://live.staticflickr.com/3123/2341623661_7c99f48bbf_b.jpg",
                                                               "title": "ZB8T0193", "author_name": "‮‭‬bees‬"}),
    })
    assert embed.known_provider_embed("https://giphy.com/gifs/cat-JIX9t2j0ZTN9S")[:2] == (
        "image", "https://media1.giphy.com/media/JIX9t2j0ZTN9S/giphy.mp4")
    assert embed.known_provider_embed("https://tenor.com/view/cat-gif-25183442")[:2] == (
        "image", "https://media.tenor.com/YPuyoi1rZvAAAAAC/mob-psycho100.gif")
    kind, src, _, card = embed.known_provider_embed("https://www.flickr.com/photos/bees/2341623661/")
    assert (kind, card) == ("image", ("ZB8T0193", "bees"))                                  # the bidi controls are gone
    use(embed, monkeypatch, gets={"https://giphy.com/services/oembed": Answer(data={"url": "https://evil.example/giphy.gif"}),
                                  "https://www.flickr.com/services/oembed": Answer(data={"type": "photo", "url": "https://evil.example/a.jpg"})})
    assert embed.known_provider_embed("https://giphy.com/gifs/x")[0] is None
    assert embed.known_provider_embed("https://www.flickr.com/photos/x/1/")[0] is None


# --- Telegram, Truth Social -------------------------------------------------------------------------------------------

TELEGRAM = ('<div class="tgme_widget_message"><a class="tgme_widget_message_owner_name"><span dir="auto">Pavel Durov</span></a>'
            '<a class="tgme_widget_message_photo_wrap" style="width:800px;background-image:url(\'https://cdn4.telesco.pe/file/abc.jpg\')"></a>'
            '<div class="tgme_widget_message_text js-message_text" dir="auto">Line one<br/>Line <b>two</b></div>'
            '<time datetime="2016-10-09T18:53:49+00:00" class="time">18:53</time></div>')


def test_a_telegram_post_is_a_quote_card_from_its_widget_page(embed, monkeypatch):
    assert se.telegram_post("https://t.me/durov/43") == ("durov", "43")
    assert se.telegram_post("https://t.me/s/durov/43") == ("durov", "43")
    assert se.telegram_post("https://t.me/durov") is None
    assert se.telegram_card(TELEGRAM) == {"name": "Pavel Durov", "text": "Line one\nLine two", "when": "2016-10-09",
                                          "picture": "https://cdn4.telesco.pe/file/abc.jpg", "video": False}
    use(embed, monkeypatch, gets={"https://t.me/durov/43": Answer(text=TELEGRAM)})
    kind, snippet, picture, card = embed.known_provider_embed("https://t.me/durov/43")
    assert kind == "widget" and picture == "https://cdn4.telesco.pe/file/abc.jpg"
    assert card == ("Pavel Durov on Telegram", "Line one\nLine two") and "@durov" in snippet


def test_a_truth_social_post_is_read_like_a_mastodon_status(embed, monkeypatch):
    status = {"id": "117449717537795453", "created_at": "2026-10-16T08:47:45.359Z", "content": "<p>Hello there</p>",
              "account": {"acct": "realDonaldTrump", "display_name": "Donald J. Trump"}, "media_attachments": []}
    assert se.truthsocial_status_id("https://truthsocial.com/@realDonaldTrump/117449717537795453") == "117449717537795453"
    assert se.truthsocial_status_id("https://truthsocial.com/@realDonaldTrump/posts/117449717537795453") == "117449717537795453"
    use(embed, monkeypatch, gets={"https://truthsocial.com/api/v1/statuses/117449717537795453": Answer(data=status)})
    kind, snippet, _, card = embed.known_provider_embed("https://truthsocial.com/@realDonaldTrump/117449717537795453")
    assert kind == "widget" and card == ("Donald J. Trump on Truth Social", "Hello there") and "Hello there" in snippet
    assert not se.mastodon_post("https://truthsocial.com/@realDonaldTrump/117449717537795453")  # its own site now


# --- how a player is drawn ----------------------------------------------------------------------------------------------

def test_a_social_sites_player_loads_on_click_and_a_video_sites_as_it_scrolls_in():
    assert se.player_layout("iframe", "https://youtube.com/embed/dQw4w9WgXcQ") == {"load": "auto", "aspect": "16 / 9", "height": None}
    assert se.player_layout("iframe", "https://www.facebook.com/plugins/video.php?href=x")["load"] == "click"
    assert se.player_layout("vertical", "https://www.tiktok.com/player/v1/1") == {"load": "click", "aspect": "9 / 16", "height": None}
    assert se.player_layout("iframe", "https://player.kick.com/xqc")["load"] == "click"
    assert se.player_layout("audio", "https://open.spotify.com/embed/track/1") == {"load": "click", "aspect": None, "height": 152}
    assert se.player_layout("audio", "https://open.spotify.com/embed/playlist/1")["height"] == 352
    assert se.player_layout("image", "https://media.tenor.com/a.gif")["load"] == "now"
    assert se.player_layout("widget", "<blockquote>") is None and se.player_layout(None, "") is None


def test_hosts_are_matched_exactly():
    assert se.host_in("https://www.facebook.com/x", ("facebook.com",))
    for url in ("https://evilfacebook.com/x", "https://facebook.com.evil.example/x", "http://www.facebook.com/x", "", None):
        assert not se.host_in(url, ("facebook.com",)), url


def test_the_templates_and_the_script_draw_every_kind():
    listing = (ROOT / "ruqqus" / "templates" / "submission_listing.html").read_text()
    page = (ROOT / "ruqqus" / "templates" / "submission.html").read_text()
    partial = (ROOT / "ruqqus" / "templates" / "partials" / "player.html").read_text()
    script = (ROOT / "ruqqus" / "assets" / "js" / "feed_video_autoplay.js").read_text()
    jinja = (ROOT / "ruqqus" / "helpers" / "jinja2.py").read_text()
    assert "{{ player_box(p, player_of(p), p.permalink, author_of(p, v).is_private) }}" in listing
    assert "{{ player_box(p, layout) }}" in page
    assert 'data-embed-load="{{ layout.load }}"' in partial and 'referrerpolicy="no-referrer"' in partial
    assert "if (container.dataset.embedLoad === 'click')" in script and "fullscreen" in script
    assert "prefers-reduced-motion: reduce" in script
    # a video, song or GIF from a social site is its player, never a quote card on top of it
    quote = jinja.split("def _quote_for(")[1].split("def _post_quote(")[0]
    assert quote.index('in ("iframe", "vertical", "audio", "image", "video")') < quote.index("quote_cards.social_card(")
    for name in ("main.scss", "main_dark.scss"):
        css = (ROOT / "ruqqus" / "assets" / "style" / name).read_text()
        assert '.embed-lg[data-embed-kind="vertical"]' in css and '.embed-lg[data-embed-kind="audio"]' in css, name
        assert ".embed-image img, .embed-image video" in css, name


def test_the_backfill_picks_the_links_that_are_players_now():
    for url in ("https://www.facebook.com/NASA/videos/123456/", "https://fb.watch/abc/", "https://www.instagram.com/reel/C5qLPhxsQMJ/",
                "https://www.tiktok.com/@a/video/6718335390845095173", "https://vm.tiktok.com/ZMabc/",
                "https://open.spotify.com/track/1", "https://tenor.com/view/x-1", "https://www.ted.com/talks/x"):
        assert se.becomes_player(url), url
    for url in ("https://www.facebook.com/NASA/posts/1", "https://www.instagram.com/p/C5qLPhxsQMJ/",
                "https://x.com/a/status/1", "https://example.com/spotify.com", "https://www.youtube.com/watch?v=dQw4w9WgXcQ"):
        assert not se.becomes_player(url), url
    script = (ROOT / "scripts" / "backfill_social_embeds.py").read_text()
    assert "social_embeds.becomes_player(url)" in script
    assert "NOT IN ('iframe', 'vertical', 'audio', 'image', 'video')" in script
