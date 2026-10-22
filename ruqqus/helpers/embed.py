import re
from urllib.parse import *
import requests
from os import environ
from bs4 import BeautifulSoup
import json
from ruqqus.__main__ import app
from .get import *
from .embed_trust import same_provider_player
from . import social_embeds
from . import page_fetch

youtube_regex = re.compile(r"^.*(youtu.be\/|v\/|u\/\w\/|embed\/|watch\?v=|\&v=)([^#\&\?]*).*")
ruqqus_regex = re.compile(r"^https?://.*ruqqus\.com/\+\w+/post/(\w+)(/[a-zA-Z0-9_-]+/(\w+))?")
twitter_regex = re.compile(r"/status/(\d+)")
rumble_regex = re.compile(r"/embed/(\w+)-/")
bilibili_bvid_regex = re.compile(r"(BV[0-9A-Za-z]+)")
twitch_vod_regex = re.compile(r"twitch\.tv/videos/(\d+)")
twitch_clip_regex = re.compile(r"clips\.twitch\.tv/([A-Za-z0-9_-]+)|twitch\.tv/\w+/clip/([A-Za-z0-9_-]+)")
twitch_channel_regex = re.compile(r"twitch\.tv/(\w+)/?(?:[?#]|$)")

FACEBOOK_TOKEN=environ.get("FACEBOOK_TOKEN","").lstrip().rstrip()



def youtube_embed(url):

    # watch?v=, youtu.be/, embed/, v/, shorts/ and live/ (social_embeds.youtube_id)
    yt_id = social_embeds.youtube_id(url)
    if not yt_id or len(yt_id) != 11:
        return "error"

    x = urlparse(url)
    params = parse_qs(x.query)
    t = params.get('t', params.get('start', [0]))[0]
    if t:
        return f"https://youtube.com/embed/{yt_id}?start={t}"
    else:
        return f"https://youtube.com/embed/{yt_id}"


# def ruqqus_embed(url):

#     print(f'embedding {url}')

#     matches = re.match(ruqqus_regex, url)

#     post_id = matches.group(1)
#     comment_id = matches.group(3)

#     print(post_id, comment_id)

#     if comment_id:
#         return f"https://{app.config['SERVER_NAME']}/embed/comment/{comment_id}"
#     else:
#         return render_template(
#             "site_embeds/ruqqus_post.html", 
#             b36id=post_id,
#             v=g.v
#             )


def bitchute_embed(url):

    return url.replace("/video/", "/embed/")

def twitter_embed(url):


    oembed_url=f"https://publish.twitter.com/oembed"
    params={
        "url":url,
        "omit_script":"t"
        }
    x=requests.get(oembed_url, params=params, timeout=6)

    return x.json()["html"]

def instagram_embed(url):

    # Meta reversed its 2020 access-token requirement on 2026-06-15 - this
    # endpoint is tokenless again for public posts/reels. FACEBOOK_TOKEN is
    # included when set (higher rate limit) but is no longer required.
    oembed_url=f"https://graph.facebook.com/v9.0/instagram_oembed"
    params={
        "url":url,
        "omitscript":'true'
    }
    if FACEBOOK_TOKEN:
        params["access_token"] = FACEBOOK_TOKEN

    headers={
        "User-Agent":"Instagram embedder for Ruqqus"
    }

    x=requests.get(oembed_url, params=params, headers=headers, timeout=6)

    return x.json()["html"]


# The social sites (helpers/social_embeds.py has the rules). Each answers (snippet, thumbnail, card): the site's own
# oEmbed snippet (saved, never drawn: helpers/quote_cards.py reads it into the quote card), a preview picture when the
# site gives one, and the (title, text) of the link-preview fields the card falls back to. Each calls one fixed, first-party API host, as twitter_embed and instagram_embed always did.
# twitter_embed / instagram_embed above stay as they were: the old per-domain Domain.embed_function path calls them
# by name and wants a plain string.
def _oembed(endpoint, url, params=None, headers=None):
    resp = requests.get(endpoint, params={"url": url, **(params or {})}, headers=headers, timeout=6)
    data = resp.json()
    if not data.get("html"):
        return None
    card = social_embeds.card_from_oembed(data, url)
    return data["html"], data.get("thumbnail_url"), card if card[0] else social_embeds.default_card(url)


_X_OEMBED = "https://publish.x.com/oembed"          # publish.twitter.com answers with a redirect here
_X_PARAMS = {"dnt": "true", "omit_script": "true"}   # the snippet is only read (helpers/quote_cards.py): no script


def x_oembed(url):
    found = _oembed(_X_OEMBED, url, _X_PARAMS)
    if not found:
        return found
    snippet, thumbnail, card = found
    return _with_x_quote(snippet), _x_picture(url) or thumbnail, card


# X's page as a link preview reads it (the same browser name as helpers/thumbs.py and /submit/title)
_PREVIEW_AGENT = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) "
                  "Chrome/89.0.4389.72 Safari/537.36")


def _x_picture(url):
    """The picture of a post on X, or "": X's oEmbed sends none, so it is read from the link-preview tags X puts on the
    post's page for every link preview (social_embeds.x_media: a photo or a video's still frame, never a profile
    picture). Only ever the post's own x.com address."""
    status = social_embeds.x_status_url(url)
    if not status:
        return ""
    try:
        page = requests.get(status, headers={"User-Agent": _PREVIEW_AGENT}, timeout=6)
    except Exception:
        return ""
    return social_embeds.x_media(page.text[:500000]) if page.status_code == 200 else ""


def _tco_target(link):
    """Where one of X's own t.co links leads: one HEAD to that fixed host, its Location read, no redirect followed."""
    if not social_embeds.is_tco(link):
        return None
    try:
        return requests.head(link, allow_redirects=False, timeout=4).headers.get("Location")
    except Exception:
        return None


def _with_x_quote(snippet):
    """X's oEmbed gives a quoted post only as a t.co link at the end of the text. When that link leads to a post on X,
    its own oEmbed snippet is saved with this one (social_embeds.quoted_post_snippet), and the address of what IT quotes,
    so the quote card can show the post the way X does: with the post it quotes inside, and no deeper."""
    link = social_embeds.last_tco_link(snippet)
    quoted_url = social_embeds.x_status_url(_tco_target(link)) if link else None
    if not quoted_url:
        return snippet
    try:
        data = requests.get(_X_OEMBED, params={"url": quoted_url, **_X_PARAMS}, timeout=6).json()
    except Exception:
        return snippet
    quoted = data.get("html") if isinstance(data, dict) else None
    if not quoted:
        return snippet
    next_link = social_embeds.last_tco_link(quoted)
    next_url = social_embeds.x_status_url(_tco_target(next_link)) if next_link else None
    return social_embeds.quoted_post_snippet(snippet, quoted_url, link, quoted, next_url or "", next_link if next_url else "",
                                         _x_picture(quoted_url))


# What this site calls itself when it reads a page for a link preview: never another company's crawler's name
_HONEST_AGENT = "Mozilla/5.0 (compatible; RuqqusBot/1.0; link previews)"

# what a handler may answer as a player of its own choosing (`_player`); the templates draw each one
# (social_embeds.player_layout): "iframe" a video, "vertical" a 9:16 video, "audio" a music or podcast player,
# "image" a picture or GIF from a fixed CDN
PLAYER_KINDS = ("iframe", "vertical", "audio", "image")


def _player(kind, src, thumbnail=None, card=None):
    """A player whose kind the handler decides (a Facebook video is a player, a Facebook post a quote card)."""
    return {"kind": kind, "src": src, "thumbnail": thumbnail, "card": card}


def _redirect_target(link, domain):
    """Where a share or short link leads: one HEAD to that site, its Location read, no redirect followed. Only an https
    address on the same site counts (the caller then checks it names a video or a post)."""
    try:
        location = requests.head(link, headers={"User-Agent": _HONEST_AGENT}, allow_redirects=False,
                                 timeout=4).headers.get("Location")
    except Exception:
        return None
    return location if social_embeds.host_in(location, (domain,)) else None


def _json(endpoint, params):
    """A site's own answer (a fixed host), as a dict ({} when it does not answer or says something else)."""
    try:
        data = requests.get(endpoint, params=params, headers={"User-Agent": _HONEST_AGENT}, timeout=6).json()
    except Exception:
        return {}
    return data if isinstance(data, dict) else {}


def instagram_oembed(url):
    """An Instagram post. Its oEmbed now answers only "View this post on Instagram" (no author, caption or picture),
    so the post's own page is read as a link preview reads it (social_embeds.instagram_card: name, @handle, date,
    caption, picture), never following a redirect (a login page). The oEmbed is what is left when that fails."""
    path = urlsplit(url).path
    try:
        page = requests.get(f"https://www.instagram.com{path}", headers={"User-Agent": _HONEST_AGENT},
                            allow_redirects=False, timeout=6)
        found = social_embeds.instagram_card(page.text[:500000]) if page.status_code == 200 else None
    except Exception:
        found = None
    # a reel or a video plays in Instagram's own plain player (loaded on click: social_embeds.player_layout)
    code = social_embeds.instagram_reel_code(url)
    if code:
        card = (f"{found['name']} on Instagram"[:500], found["text"][:1000]) if found else social_embeds.default_card(url)
        return _player("vertical", social_embeds.instagram_player(code), (found or {}).get("picture") or None, card)
    if found:
        snippet = social_embeds.quote_snippet("instagram-media", url, found["name"], found["handle"], found["when"],
                                              found["text"], not found["picture"], found["video"])
        return snippet, found["picture"] or None, (f"{found['name']} on Instagram"[:500], found["text"][:1000])
    params = {"access_token": FACEBOOK_TOKEN} if FACEBOOK_TOKEN else {}
    return _oembed("https://graph.facebook.com/v9.0/instagram_oembed", url, params,
                   {"User-Agent": "Instagram embedder for Ruqqus"})


def facebook_post_oembed(url):
    """A Facebook post or video. Share links and fb.watch only redirect, so the address they lead to is asked first
    (`_redirect_target`; it counts only when it names a video or a page's post). A video or reel plays in Facebook's
    plain player (social_embeds.facebook_player, loaded on click), with its title, text, page and date from the video
    oEmbed, which answers without a token (tested 2026-10-16). A post's oEmbed needs a token Meta gives only to reviewed
    apps; the owner chose to have none (2026-10-16), so a post's card names the page from its link and opens it there."""
    if social_embeds.facebook_needs_resolving(url):
        target = _redirect_target(url, "facebook.com")
        if target and (social_embeds.facebook_names_a_video(target) or social_embeds.facebook_page_name(target)):
            url = target
    url = social_embeds.facebook_canonical(url)
    params = {"url": url, **({"access_token": FACEBOOK_TOKEN} if FACEBOOK_TOKEN else {})}
    if social_embeds.facebook_kind(url) == "video":
        data = _json("https://graph.facebook.com/v21.0/oembed_video", params)
        kind = "vertical" if social_embeds.facebook_is_reel(url) else "iframe"
        return _player(kind, social_embeds.facebook_player(url), data.get("thumbnail_url"),
                       social_embeds.card_from_facebook(data.get("html"), url))
    snippet = _json("https://graph.facebook.com/v21.0/oembed_post", params).get("html")
    if snippet:
        return snippet, None, social_embeds.card_from_facebook(snippet, url)
    return (social_embeds.quote_snippet("fb-post", url, social_embeds.facebook_page_name(url), "", "", ""), None,
            social_embeds.card_from_facebook("", url))


def tiktok_card_oembed(url):
    """A TikTok video plays in TikTok's plain player (/player/v1/<id>, loaded on click). A short link (vm., vt., /t/)
    only redirects, so the video it leads to is asked first. The words come from TikTok's oEmbed, which may not answer
    (from the local container it times out): the player does not need them. A link that names no video (a photo post,
    a profile) keeps its quote card."""
    if social_embeds.tiktok_needs_resolving(url):
        target = _redirect_target(url, "tiktok.com")
        if target and social_embeds.tiktok_video_id(target):
            url = target
    try:
        answer = _oembed("https://www.tiktok.com/oembed", url)
    except Exception:
        answer = None
    video_id = social_embeds.tiktok_video_id(url)
    if not video_id:
        return answer
    thumbnail, card = (answer[1], answer[2]) if answer else (None, social_embeds.default_card(url))
    return _player("vertical", social_embeds.tiktok_player(video_id), thumbnail, card)


def bluesky_oembed(url):
    """A Bluesky post, read from Bluesky's own public API (a fixed host, no key): its text, author and date, its
    picture, and the post it quotes (social_embeds.bsky_snippet). Bluesky's oEmbed (which says only "[contains quote
    post or other embedded content]" for those) is what is left when the API does not answer."""
    at_uri = social_embeds.bsky_at_uri(url)
    try:
        data = requests.get("https://public.api.bsky.app/xrpc/app.bsky.feed.getPostThread",
                            params={"uri": at_uri, "depth": 0, "parentHeight": 0}, timeout=6).json() if at_uri else None
        thread = data.get("thread") if isinstance(data, dict) else None
        found = social_embeds.bsky_snippet(thread.get("post")) if isinstance(thread, dict) else None
    except Exception:
        found = None
    if found:
        return found["snippet"], found["picture"] or None, (found["title"], found["text"])
    return _oembed("https://embed.bsky.app/oembed", url, {"format": "json"})


def reddit_oembed(url):
    return _oembed("https://www.reddit.com/oembed", url, {"format": "json"}, {"User-Agent": "Ruqqus embedder"})


def pinterest_oembed(url):
    return _oembed("https://www.pinterest.com/oembed.json", url)


def tumblr_oembed(url):
    return _oembed("https://www.tumblr.com/oembed/1.0", url)


def threads_embed(url):
    # Threads has no oEmbed service: its own markup, built from the link, is saved and the card comes from the link
    snippet = social_embeds.threads_snippet(url)
    return (snippet, None, social_embeds.threads_card(url)) if snippet else None


def mastodon_embed(url):
    """A post on any Mastodon server, saved as a quote (social_embeds.mastodon_status_snippet, read by
    helpers/quote_cards.py). The server is whatever the member linked, so both of its answers are fetched through
    safe_fetch (public addresses only, small, quick): its oEmbed says it is a Mastodon post with its own embed page on the
    same server (social_embeds.mastodon_iframe_ok), and its public API (`/api/v1/statuses/<id>`) gives the text, the
    author, the date, the first picture and the status it quotes. Nothing of either answer is drawn as HTML; the
    picture is an https address on whatever server hosts it, as any link preview's."""
    from ruqqus.helpers import safe_fetch

    found = social_embeds.mastodon_post(url)
    if not found:
        return None
    host, endpoint = found
    try:
        data = safe_fetch.get_json(endpoint, params={"url": url, "format": "json"})
    except Exception:
        return None
    if not isinstance(data, dict):
        return None
    src = social_embeds.mastodon_embed_src(data.get("html") or "")
    if not src or not social_embeds.mastodon_iframe_ok(src, host):
        return None
    name = social_embeds._plain(data.get("author_name"), 120)
    status_id = social_embeds.mastodon_status_id(url)
    try:
        status = safe_fetch.get_json(f"https://{host}/api/v1/statuses/{status_id}") if status_id else None
    except Exception:
        status = None
    # its picture and the status it quotes (Mastodon 4.5) come with it (social_embeds.mastodon_status_snippet)
    parts = social_embeds.mastodon_parts(status, host)
    found = social_embeds.mastodon_status_snippet(status, host, src) if parts else None
    if not found:
        return social_embeds.mastodon_quote_snippet(src, name, "", "", "", False), None, (
            f"{name} on Mastodon" if name else "Post on Mastodon", "")
    name = parts["name"] or name
    title = f"{name} on Mastodon" if name else "Post on Mastodon"
    return found[0], found[1] or None, (title, parts["text"][:1000])


def rumble_embed(url):

    # through safe_fetch's guard (helpers/page_fetch.py): a redirect on rumble.com can't lead to this site's network
    r=page_fetch.get(url, user_agent="Rumble embedder for Ruqqus")

    soup=BeautifulSoup(r.body, features="html.parser")

    script=soup.find("script", attrs={"type":"application/ld+json"})

    return json.loads(script.string)[0]['embedUrl']


def vimeo_oembed(url):
    # Returns (iframe_src, thumbnail_url) - a tuple, unlike the legacy
    # functions above, since nothing else calls this one and
    # known_provider_embed() below knows how to unpack it for the
    # preview-image pipeline.
    resp = requests.get("https://vimeo.com/api/oembed.json", params={"url": url}, timeout=6)
    data = resp.json()
    return _extract_iframe_src(data.get("html", "")), data.get("thumbnail_url")


def dailymotion_oembed(url):
    resp = requests.get("https://www.dailymotion.com/services/oembed", params={"url": url}, timeout=6)
    data = resp.json()
    return _extract_iframe_src(data.get("html", "")), data.get("thumbnail_url")


def odysee_oembed(url):
    # Odysee's own documented oEmbed endpoint - not on the generic
    # allowlist-and-discover path below because the <link> tag often isn't
    # present on the page itself; calling the known endpoint directly is
    # what actually fixes embedding for this site.
    resp = requests.get("https://odysee.com/$/oembed", params={"url": url, "format": "json"}, timeout=6)
    data = resp.json()
    return _extract_iframe_src(data.get("html", "")), data.get("thumbnail_url")


def facebook_oembed(url):
    # Tokenless for public video posts since Meta's 2026-06-15 reversal -
    # FACEBOOK_TOKEN included only if set, for the rate-limit headroom.
    # Verified live: the response's html is an SDK div (<div class="fb-video">
    # + connect.facebook.net/.../sdk.js), not a bare iframe - same widget-card
    # category as twitter_embed/tiktok_oembed, not a plain playable iframe.
    params = {"url": url}
    if FACEBOOK_TOKEN:
        params["access_token"] = FACEBOOK_TOKEN
    resp = requests.get("https://graph.facebook.com/v21.0/oembed_video", params=params, timeout=6)
    data = resp.json()
    return data["html"], data.get("thumbnail_url")


def tiktok_oembed(url):
    # TikTok's oEmbed returns a <blockquote class="tiktok-embed"> + a
    # reference to their own embed.js widget script, not a bare iframe -
    # there's no official alternative that gives a plain iframe, so this
    # renders as an interactive card (same category as twitter_embed/
    # instagram_embed above), not the muted-autoplay video pipeline.
    resp = requests.get("https://www.tiktok.com/oembed", params={"url": url}, timeout=6)
    data = resp.json()
    return data["html"], data.get("thumbnail_url")


def twitch_embed(url):
    # Twitch refuses to play at all without a parent= param matching the
    # actual embedding domain (shows a click-through-to-Twitch error
    # otherwise) - this is Twitch-specific, not something oEmbed-style
    # discovery can solve generically for an unknown site.
    parent = urlparse(f"https://{app.config['SERVER_NAME']}").hostname or app.config["SERVER_NAME"]

    m = twitch_vod_regex.search(url)
    if m:
        return f"https://player.twitch.tv/?video={m.group(1)}&parent={parent}&autoplay=true&muted=true"

    m = twitch_clip_regex.search(url)
    if m:
        slug = m.group(1) or m.group(2)
        return f"https://clips.twitch.tv/embed?clip={slug}&parent={parent}&autoplay=true&muted=true"

    m = twitch_channel_regex.search(url)
    if m:
        return f"https://player.twitch.tv/?channel={m.group(1)}&parent={parent}&autoplay=true&muted=true"

    return "error"


def bilibili_embed(url):
    m = bilibili_bvid_regex.search(url)
    if not m:
        return "error"
    return f"https://player.bilibili.com/player.html?bvid={m.group(1)}&page=1&high_quality=1&danmaku=0&autoplay=1"


# --- Music, more video, GIFs, Telegram, Truth Social (2026-10-16) -------------------------------------------------
# Each asks one fixed host (the site's own oEmbed service or page) or builds the player from the link alone, and keeps
# only a player address on that site's own player hosts (social_embeds.AUDIO_HOSTS, IMAGE_HOSTS, host_in): nothing a
# submitted page says is drawn. What each site answered when tested is in CLAUDE.md ("Social embeds and quote cards").

def _oembed_player(kind, endpoint, url, hosts, params=None, must_contain=""):
    """A player from a site's oEmbed answer: the iframe address in its snippet, only on `hosts`."""
    data = _json(endpoint, {"url": url, **(params or {})})
    src = _extract_iframe_src(data.get("html") or "")
    if not social_embeds.host_in(src, hosts) or must_contain not in src:
        return None
    return _player(kind, src, data.get("thumbnail_url"), _player_card(data))


def _player_card(data):
    """(meta_title, meta_description) for a song, a talk or a clip: its title and who made it."""
    title = social_embeds.clean_name(data.get("title"), 200)
    author = social_embeds.clean_name(data.get("author_name"))
    if not title and not author:
        return None
    return (title or author), (author if title else "")


def spotify_embed(url):
    return _oembed_player("audio", "https://open.spotify.com/oembed", url, ("open.spotify.com",), must_contain="/embed/")


def soundcloud_embed(url):
    found = _oembed_player("audio", "https://soundcloud.com/oembed", url, ("w.soundcloud.com",), {"format": "json"},
                           must_contain="/player/")
    if found:
        found["src"] = found["src"].replace("visual=true", "visual=false")      # the compact player, not the 400px one
    return found


def mixcloud_embed(url):
    return _oembed_player("audio", "https://app.mixcloud.com/oembed/", url, ("www.mixcloud.com", "player-widget.mixcloud.com"),
                          {"format": "json"}, must_contain="/widget/")


def deezer_embed(url):
    return _oembed_player("audio", "https://api.deezer.com/oembed", url, ("widget.deezer.com",), {"format": "json"})


def apple_embed(url):
    src = social_embeds.apple_player(url)
    return _player("audio", src) if src else None


def bandcamp_embed(url):
    """A Bandcamp track or album: its page names the item (`bc-page-properties`), and Bandcamp's own player is built
    from that number (the page's own og:video is no longer there). Only an artist's *.bandcamp.com page is read."""
    if not social_embeds.host_in(url, ("bandcamp.com",)) or not re.search(r"^/(track|album)/", urlparse(url).path):
        return None
    try:
        page = requests.get(url, headers={"User-Agent": _HONEST_AGENT}, allow_redirects=False, timeout=6)
    except Exception:
        return None
    src = social_embeds.bandcamp_player(page.text[:500000]) if page.status_code == 200 else None
    return _player("audio", src) if src else None


def streamable_embed(url):
    return _oembed_player("iframe", "https://api.streamable.com/oembed.json", url, ("streamable.com",))


def ted_embed(url):
    return _oembed_player("iframe", "https://www.ted.com/services/v1/oembed.json", url, ("embed.ted.com",))


def kick_embed(url):
    src = social_embeds.kick_player(url)
    return _player("iframe", src) if src else None


def loom_embed(url):
    src = social_embeds.loom_player(url)
    return _player("iframe", src) if src else None


def giphy_embed(url):
    """A GIPHY GIF, drawn as its looping mp4 (a tenth of the GIF's size) from GIPHY's own media host."""
    data = _json("https://giphy.com/services/oembed", {"url": url})
    gif = data.get("url") or ""
    if not social_embeds.host_in(gif, ("giphy.com",)) or not gif.endswith("/giphy.gif"):
        return None
    return _player("image", gif[:-len("giphy.gif")] + "giphy.mp4", gif, _player_card(data))


_TENOR_MEDIA = re.compile(r"^(https://media\.tenor\.com/[A-Za-z0-9_-]{6,40}?)AAAA[A-Za-z0-9]/([A-Za-z0-9._-]{1,120})\.(?:png|gif|webp|jpg)$")


def tenor_embed(url):
    """A Tenor GIF: Tenor's oEmbed names its still picture; the GIF is the same file in Tenor's GIF size (AAAAC)."""
    data = _json("https://tenor.com/oembed", {"url": url})
    match = _TENOR_MEDIA.match(data.get("thumbnail_url") or "")
    if not match:
        return None
    return _player("image", f"{match.group(1)}AAAAC/{match.group(2)}.gif", data.get("thumbnail_url"), _player_card(data))


def flickr_embed(url):
    data = _json("https://www.flickr.com/services/oembed", {"url": url, "format": "json"})
    photo = data.get("url") or ""
    if data.get("type") != "photo" or not social_embeds.host_in(photo, ("live.staticflickr.com",)):
        return None
    return _player("image", photo, data.get("thumbnail_url"), _player_card(data))


def telegram_embed(url):
    """A post in a public Telegram channel, read from Telegram's own widget page for it (plain HTML, no script run):
    the channel, the text, the date and the first picture, saved as our own escaped quote (social_embeds.quote_snippet)."""
    found = social_embeds.telegram_post(url)
    if not found:
        return None
    channel, message = found
    try:
        page = requests.get(f"https://t.me/{channel}/{message}", params={"embed": "1"},
                            headers={"User-Agent": _HONEST_AGENT}, allow_redirects=False, timeout=6)
    except Exception:
        return None
    card = social_embeds.telegram_card(page.text[:500000]) if page.status_code == 200 else None
    if not card:
        return None
    snippet = social_embeds.quote_snippet("telegram-post", f"https://t.me/{channel}/{message}", card["name"] or channel,
                                          "@" + channel, card["when"], card["text"], card["video"] and not card["picture"],
                                          card["video"])
    title = f"{card['name'] or channel} on Telegram"
    return snippet, card["picture"] or None, (title[:500], card["text"][:1000])


def truthsocial_embed(url):
    """A Truth Social post: it runs Mastodon's software, so its public API answers as a Mastodon server does
    (/api/v1/statuses/<id>, a fixed host), read by the Mastodon card (social_embeds.mastodon_status_snippet)."""
    status_id = social_embeds.truthsocial_status_id(url)
    if not status_id:
        return None
    status = _json(f"https://truthsocial.com/api/v1/statuses/{status_id}", {})
    found = social_embeds.mastodon_status_snippet(status, "truthsocial.com", url) if status.get("id") else None
    if not found:
        return None
    parts = social_embeds.mastodon_parts(status, "truthsocial.com")
    title = f"{parts['name']} on Truth Social" if parts and parts["name"] else "Post on Truth Social"
    return found[0], found[1] or None, (title[:500], (parts["text"] if parts else "")[:1000])


# Built-in handlers for named platforms - checked by detect_video_embed()
# before the generic allowlisted-discovery/og:video fallback below. Unlike
# that generic path, these run unconditionally for their domain (no
# per-site hostname re-validation needed) since each function targets one
# specific, known platform's own official embedding mechanism rather than
# whatever an arbitrary submitted page happens to advertise.
#
# embed_type "iframe": participates in the feed/single-post muted-autoplay
# video pipeline, same as the generic detector's "iframe"/"video" results.
# embed_type "widget": a social site's post, its oEmbed snippet from a fixed,
# hardcoded API host (not something the submitted page gets to redirect). The
# snippet is never drawn: helpers/quote_cards.py reads it into a quote card.
# embed_type "audio" / "image": a music or podcast player, a GIF or picture. A
# handler may answer a player of another kind than its entry (`_player`): a
# Facebook, Instagram or TikTok video is a player ("iframe" or "vertical",
# loaded on click), a post on those sites stays a "widget" quote card.
KNOWN_PROVIDER_HANDLERS = {
    "youtube.com": ("iframe", youtube_embed),
    "youtu.be": ("iframe", youtube_embed),
    "vimeo.com": ("iframe", vimeo_oembed),
    "dailymotion.com": ("iframe", dailymotion_oembed),
    "odysee.com": ("iframe", odysee_oembed),
    "bitchute.com": ("iframe", bitchute_embed),
    "rumble.com": ("iframe", rumble_embed),
    "twitch.tv": ("iframe", twitch_embed),
    "clips.twitch.tv": ("iframe", twitch_embed),
    "bilibili.com": ("iframe", bilibili_embed),
    "facebook.com": ("widget", facebook_post_oembed),
    "fb.watch": ("widget", facebook_post_oembed),
    "twitter.com": ("widget", x_oembed),
    "x.com": ("widget", x_oembed),
    "instagram.com": ("widget", instagram_oembed),
    "tiktok.com": ("widget", tiktok_card_oembed),
    "bsky.app": ("widget", bluesky_oembed),
    "reddit.com": ("widget", reddit_oembed),
    "redd.it": ("widget", reddit_oembed),
    "pinterest.com": ("widget", pinterest_oembed),
    "threads.net": ("widget", threads_embed),
    "threads.com": ("widget", threads_embed),
    "tumblr.com": ("widget", tumblr_oembed),
    "t.me": ("widget", telegram_embed),
    "telegram.me": ("widget", telegram_embed),
    "truthsocial.com": ("widget", truthsocial_embed),
    "streamable.com": ("iframe", streamable_embed),
    "kick.com": ("iframe", kick_embed),
    "loom.com": ("iframe", loom_embed),
    "ted.com": ("iframe", ted_embed),
    "open.spotify.com": ("audio", spotify_embed),
    "soundcloud.com": ("audio", soundcloud_embed),
    "music.apple.com": ("audio", apple_embed),
    "podcasts.apple.com": ("audio", apple_embed),
    "bandcamp.com": ("audio", bandcamp_embed),
    "mixcloud.com": ("audio", mixcloud_embed),
    "deezer.com": ("audio", deezer_embed),
    "giphy.com": ("image", giphy_embed),
    "tenor.com": ("image", tenor_embed),
    "flickr.com": ("image", flickr_embed),
}


IFRAME_PROVIDER_DOMAINS = tuple(d for d, (kind, _) in KNOWN_PROVIDER_HANDLERS.items() if kind == "iframe")


def known_provider_embed(url):
    """Returns (embed_type, embed_src, thumbnail_url, card) for a URL matching
    one of the named platforms above, or (None, None, None) if its domain
    isn't one of them (in which case the caller falls through to the fully
    generic detection below) or the handler itself failed (bad/
    unrecognized URL shape, network error, unexpected response).

    Handlers return either a plain string (the legacy functions also used
    by the old synchronous Domain.embed_function path - youtube_embed,
    bitchute_embed, rumble_embed, twitter_embed, instagram_embed - whose
    existing call site expects exactly that) or a (content, thumbnail_url)
    tuple (the new oEmbed-calling functions written for this dispatch
    table specifically, which have no other caller to stay compatible
    with).
    """
    hostname = (urlparse(url).hostname or "").lower()

    for domain, (embed_type, handler) in KNOWN_PROVIDER_HANDLERS.items():
        if hostname == domain or hostname.endswith("." + domain):
            try:
                result = handler(url)
            except Exception:
                return None, None, None, None
            return _unpack(embed_type, result)

    # Mastodon is any server, not a fixed host: only a link shaped like a post, and only through safe_fetch
    if social_embeds.mastodon_post(url):
        try:
            return _unpack("widget", mastodon_embed(url))
        except Exception:
            return None, None, None, None

    return None, None, None, None


def _unpack(embed_type, result):
    """A handler's answer as (embed_type, content, thumbnail, card): a plain string (the older handlers), a
    (content, thumbnail) or (content, thumbnail, card) tuple, or a player (`_player`: a dict that names its own kind,
    so one site can answer a quote card for a post and a player for a video). Nothing usable is (None, None, None, None)."""
    thumbnail_url, card = None, None
    if isinstance(result, dict):
        if not result.get("src") or result.get("kind") not in PLAYER_KINDS:
            return None, None, None, None
        return result["kind"], result["src"], result.get("thumbnail") or None, result.get("card")
    if isinstance(result, tuple):
        result, thumbnail_url, *rest = result
        card = rest[0] if rest else None
    if not result or result == "error":
        return None, None, None, None
    return embed_type, result, thumbnail_url, card


# Generic (non-domain-configured) video detection, used as a fallback by
# thumbnail_thread (ruqqus/helpers/thumbs.py) when a submitted URL's domain
# has no Domain.embed_function configured. Unlike youtube_embed/etc above,
# this has to work for arbitrary, unvetted domains, so the oEmbed path is
# allowlisted by endpoint hostname and we only ever extract a bare iframe
# src out of a provider's response - never render their raw returned html
# (which would be stored XSS, since unlike twitter_embed/instagram_embed
# above, the oEmbed endpoint here is advertised BY the submitted page
# itself, not a fixed trusted first-party API).
OEMBED_PROVIDER_HOSTS = (
    "youtube.com", "youtu.be",
    "vimeo.com",
    "dailymotion.com",
    "soundcloud.com",
    "twitch.tv",
    "ted.com",
    "streamable.com",
    "loom.com",
    "wistia.com", "wistia.net",
    "flickr.com",
)


def _host_allowed(hostname, allowlist=OEMBED_PROVIDER_HOSTS):
    if not hostname:
        return False
    hostname = hostname.lower()
    return any(hostname == host or hostname.endswith("." + host) for host in allowlist)


def _expand_url(post_url, fragment_url):
    # Same logic as thumbs.py's expand_url - duplicated rather than
    # imported to avoid a circular import (thumbs.py imports
    # detect_video_embed from this module).
    if fragment_url.startswith("https://"):
        return fragment_url
    elif fragment_url.startswith("http://"):
        return f"https://{fragment_url.split('http://')[1]}"
    elif fragment_url.startswith("//"):
        return f"https:{fragment_url}"
    elif fragment_url.startswith("/"):
        parsed_url = urlparse(post_url)
        return f"https://{parsed_url.netloc}{fragment_url}"
    else:
        return f"{post_url}{'/' if not post_url.endswith('/') else ''}{fragment_url}"


def _extract_iframe_src(html_snippet):
    try:
        frag = BeautifulSoup(html_snippet, "html.parser")
        iframe = frag.find("iframe", attrs={"src": True})
        return iframe["src"] if iframe else None
    except Exception:
        return None


def detect_video_embed(url, response, soup):
    """Video/embed detection for domains with no Domain.embed_function
    configured. Returns (embed_type, embed_src, thumbnail_url) where
    embed_type is one of:
      "video"            - a direct playable video file URL
      "iframe"           - a bare iframe src, either from one of the named
                            platforms in KNOWN_PROVIDER_HANDLERS or from an
                            allowlisted oEmbed provider discovered generically
      "iframe_untrusted" - a bare iframe src scraped from the submitted
                            page's own og:video tag - fully attacker-
                            controlled, render with a strict sandbox
      "widget"           - a social site's post (X, Instagram, TikTok...): its
                            oEmbed snippet, never drawn, read into a quote
                            card (helpers/quote_cards.py)
    or (None, None, None) if nothing could be detected.
    """
    known_type, known_src, known_thumbnail, _card = known_provider_embed(url)
    if known_type:
        return known_type, known_src, known_thumbnail

    content_type = response.headers.get("Content-Type", "")

    if content_type.startswith("video/"):
        return "video", url, None

    if not content_type.startswith("text/html") or soup is None:
        return None, None, None

    # Layer 2: oEmbed autodiscovery, gated to a hostname allowlist since
    # the <link> tag is self-advertised by the (unvetted) submitted page.
    oembed_link = soup.find("link", attrs={"type": "application/json+oembed", "href": True})
    if oembed_link:
        endpoint = oembed_link["href"]
        if _host_allowed(urlparse(endpoint).hostname):
            # the address comes from the member's page: through safe_fetch's guard (helpers/page_fetch.py), so a
            # redirect on an allowed host can't lead to this site's own network
            try:
                answer = page_fetch.get(endpoint, read=("application/json", "text/"), max_bytes=256 * 1024, timeout=6)
                data = json.loads(answer.body) if answer.status == 200 else {}
                data = data if isinstance(data, dict) else {}
            except (page_fetch.FetchError, ValueError):
                data = {}
            # "rich" is how audio players answer (SoundCloud); the player's own host is checked either way
            if data.get("type") in ("video", "rich") and data.get("html"):
                src = _extract_iframe_src(data["html"])
                if src and _host_allowed(urlparse(src).hostname):
                    kind = "audio" if social_embeds.host_in(src, social_embeds.AUDIO_HOSTS) else "iframe"
                    return kind, src, data.get("thumbnail_url")

    # Layer 3: og:video fallback - no third-party gate possible here, the
    # value is a meta tag on the submitter's own page. Caller must render
    # "iframe_untrusted" results with a restrictive sandbox.
    og_video = None
    for prop in ("og:video:secure_url", "og:video:url", "og:video"):
        tag = soup.find("meta", attrs={"property": prop, "content": True})
        if tag:
            og_video = _expand_url(url, tag["content"])
            break

    if og_video:
        og_video_type_tag = soup.find("meta", attrs={"property": "og:video:type", "content": True})
        og_video_type = og_video_type_tag["content"] if og_video_type_tag else ""
        if og_video_type.startswith("video/"):
            return "video", og_video, None
        # a named platform's own page pointing at its own player (its oEmbed call above failed)
        if same_provider_player(url, og_video, IFRAME_PROVIDER_DOMAINS):
            return "iframe", og_video, None
        return "iframe_untrusted", og_video, None

    return None, None, None
