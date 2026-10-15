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


def x_oembed(url):
    # the snippet is only read (helpers/quote_cards.py), so X's script is left out of it
    return _oembed("https://publish.twitter.com/oembed", url, {"dnt": "true", "omit_script": "true"})


def instagram_oembed(url):
    params = {"access_token": FACEBOOK_TOKEN} if FACEBOOK_TOKEN else {}
    return _oembed("https://graph.facebook.com/v9.0/instagram_oembed", url, params,
                   {"User-Agent": "Instagram embedder for Ruqqus"})


def facebook_post_oembed(url):
    # a post and a video are two different services at Facebook; both answer without a token for public posts
    service = "oembed_video" if social_embeds.facebook_kind(url) == "video" else "oembed_post"
    params = {"access_token": FACEBOOK_TOKEN} if FACEBOOK_TOKEN else {}
    return _oembed(f"https://graph.facebook.com/v21.0/{service}", url, params)


def tiktok_card_oembed(url):
    return _oembed("https://www.tiktok.com/oembed", url)


def bluesky_oembed(url):
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
    """A post on any Mastodon server, saved as a quote (social_embeds.mastodon_quote_snippet, read by
    helpers/quote_cards.py). The server is whatever the member linked, so both of its answers are fetched through
    safe_fetch (public addresses only, small, quick): its oEmbed says it is a Mastodon post with its own embed page on the
    same server (social_embeds.mastodon_iframe_ok), and its public API (`/api/v1/statuses/<id>`) gives the text, the
    author and the date. Nothing of either answer is drawn as HTML."""
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
    handle, when, text, media = "", "", "", False
    status_id = social_embeds.mastodon_status_id(url)
    try:
        status = safe_fetch.get_json(f"https://{host}/api/v1/statuses/{status_id}") if status_id else None
    except Exception:
        status = None
    if isinstance(status, dict):
        account = status.get("account") if isinstance(status.get("account"), dict) else {}
        name = social_embeds._plain(account.get("display_name"), 120) or name
        handle = social_embeds.mastodon_handle(account.get("acct"), host)
        when = str(status.get("created_at") or "")[:40]
        text = social_embeds.mastodon_text(status.get("content"))
        media = bool(status.get("media_attachments"))
    title = f"{name} on Mastodon" if name else "Post on Mastodon"
    return social_embeds.mastodon_quote_snippet(src, name, handle, when, text, media), None, (title, text[:1000])


def rumble_embed(url):

    #print(url)
    headers={
        "User-Agent":"Rumble embedder for Ruqqus"
    }
    r=requests.get(url, headers=headers)

    soup=BeautifulSoup(r.content, features="html.parser")

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
    """A handler's answer as (embed_type, content, thumbnail, card): a plain string (the older handlers), or a
    (content, thumbnail) or (content, thumbnail, card) tuple. Nothing usable is (None, None, None, None)."""
    thumbnail_url, card = None, None
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
            try:
                resp = requests.get(endpoint, timeout=6)
                data = resp.json()
            except Exception:
                data = {}
            if data.get("type") == "video" and data.get("html"):
                src = _extract_iframe_src(data["html"])
                if src and _host_allowed(urlparse(src).hostname):
                    return "iframe", src, data.get("thumbnail_url")

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
