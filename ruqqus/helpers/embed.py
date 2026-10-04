import re
from urllib.parse import *
import requests
from os import environ
from bs4 import BeautifulSoup
import json
from ruqqus.__main__ import app
from .get import *

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

    try:
        yt_id = re.match(youtube_regex, url).group(2)
    except AttributeError:
        return "error"

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
# embed_type "widget": a trusted first-party script/card snippet (same
# trust model as twitter_embed/instagram_embed already had - a fixed,
# hardcoded API host, not something the submitted page gets to redirect),
# rendered via site_embeds/widget.html, not autoplayed.
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
    "facebook.com": ("widget", facebook_oembed),
    "twitter.com": ("widget", twitter_embed),
    "x.com": ("widget", twitter_embed),
    "instagram.com": ("widget", instagram_embed),
    "tiktok.com": ("widget", tiktok_oembed),
}


def known_provider_embed(url):
    """Returns (embed_type, embed_src, thumbnail_url) for a URL matching
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
                return None, None, None

            thumbnail_url = None
            if isinstance(result, tuple):
                result, thumbnail_url = result

            if not result or result == "error":
                return None, None, None
            return embed_type, result, thumbnail_url

    return None, None, None


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
      "widget"           - a trusted first-party script/card snippet (X,
                            Instagram, TikTok) - not part of the video-
                            autoplay pipeline, rendered via
                            site_embeds/widget.html instead
    or (None, None, None) if nothing could be detected.
    """
    known_type, known_src, known_thumbnail = known_provider_embed(url)
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
        return "iframe_untrusted", og_video, None

    return None, None, None
