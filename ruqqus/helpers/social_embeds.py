"""Posts from the major social sites, drawn on this site: which site a link is, the official script its card needs, the
card a feed shows for it, and the rules for the sites that are not one fixed host (Mastodon). Pure: no I/O; the calls to
each site's own oEmbed service are in helpers/embed.py (tests/test_social_embeds.py).

* The post page shows the site's own widget: the snippet its oEmbed service returns, upgraded by that site's official
  script. Some snippets come without it (X and Instagram are asked for it now, but posts saved before were not), so the
  widget template adds the one script on this fixed list for the post's site (`script_for`), never anything else.
* A feed shows no live widgets (dozens of scripts in a scrolling page): a widget post gets the ordinary link-preview
  card, made from the oEmbed answer when the post is saved (`card_from_oembed`: "Elon Musk on X" and the post's text),
  because these sites send no preview to the site's own page fetcher.
* Mastodon is any server, so it is never trusted like a fixed host: only a link shaped like a Mastodon post
  (`mastodon_post`), its oEmbed fetched through helpers/safe_fetch.py, and only that server's own `/embed` page kept
  (`mastodon_iframe_ok`), drawn in a sandboxed frame we write ourselves (`mastodon_snippet`).
"""
import html
import re
from urllib.parse import parse_qs, urlsplit

CARD_TEXT_MAX = 300

# site -> (what it is called, the official script that turns its snippet into a card)
SITES = {
    "x.com": ("X", "https://platform.twitter.com/widgets.js"),
    "twitter.com": ("X", "https://platform.twitter.com/widgets.js"),
    "instagram.com": ("Instagram", "https://www.instagram.com/embed.js"),
    "facebook.com": ("Facebook", "https://connect.facebook.net/en_US/sdk.js#xfbml=1&version=v21.0"),
    "tiktok.com": ("TikTok", "https://www.tiktok.com/embed.js"),
    "reddit.com": ("Reddit", "https://embed.reddit.com/widgets.js"),
    "redd.it": ("Reddit", "https://embed.reddit.com/widgets.js"),
    "bsky.app": ("Bluesky", "https://embed.bsky.app/static/embed.js"),
    "threads.net": ("Threads", "https://www.threads.com/embed.js"),
    "threads.com": ("Threads", "https://www.threads.com/embed.js"),
    "pinterest.com": ("Pinterest", "https://assets.pinterest.com/js/pinit.js"),
    "tumblr.com": ("Tumblr", "https://assets.tumblr.com/post.js"),
}

_SPACE = re.compile(r"\s+")
_YOUTUBE_ID = re.compile(r"^[A-Za-z0-9_-]{6,20}$")
_MASTODON_PATH = re.compile(r"^/@([A-Za-z0-9_]{1,30})(?:@[A-Za-z0-9.-]{1,253})?/(\d{1,25})/?$")
_THREADS_PATH = re.compile(r"^/@([A-Za-z0-9._]{1,30})/post/([A-Za-z0-9_-]{5,40})/?$")


def _host(url):
    try:
        return (urlsplit(str(url or "")).hostname or "").lower()
    except ValueError:
        return ""


def site_of(url):
    """The SITES key a link belongs to (subdomains too: www., m., old.), or None."""
    host = _host(url)
    for site in SITES:
        if host == site or host.endswith("." + site):
            return site
    return None


def label_of(url):
    site = site_of(url)
    return SITES[site][0] if site else ""


def script_for(url, snippet):
    """The official script a saved widget snippet needs on the post page, or None when the snippet already loads one
    (Facebook's and TikTok's do) or the link is not one of SITES."""
    site = site_of(url)
    if not site or "<script" in str(snippet or "").lower():
        return None
    return SITES[site][1]


def _plain(text, limit=CARD_TEXT_MAX):
    text = _SPACE.sub(" ", html.unescape(str(text or ""))).strip()
    return text if len(text) <= limit else text[:limit - 1].rstrip() + "…"


def _first_paragraph(snippet):
    """The text of the first <p> of an oEmbed snippet (X puts the post's text there), as plain text."""
    try:
        from bs4 import BeautifulSoup
        paragraph = BeautifulSoup(str(snippet or ""), "html.parser").find("p")
    except Exception:
        return ""
    return paragraph.get_text(" ") if paragraph else ""


def card_from_oembed(data, url):
    """(title, text) for the feed's link-preview card, from a site's oEmbed answer: "<author> on <site>" and the post's
    own words (its `title` field, or the first paragraph of its snippet). ("", "") when the answer says nothing."""
    if not isinstance(data, dict):
        return "", ""
    label = label_of(url)
    author = _plain(data.get("author_name"), 120)
    text = _plain(data.get("title")) or _plain(_first_paragraph(data.get("html")))
    if not author and not text:
        return "", ""
    title = f"{author} on {label}" if author and label else author or (f"Post on {label}" if label else "")
    return title[:500], text


def youtube_id(url):
    """The video id of a YouTube link: watch?v=, youtu.be/, embed/, v/, shorts/ and live/. None for anything else."""
    parts = urlsplit(str(url or ""))
    host = (parts.hostname or "").lower()
    if host == "youtu.be":
        candidate = parts.path.strip("/").split("/")[0]
    elif host == "youtube.com" or host.endswith(".youtube.com"):
        query = parse_qs(parts.query)
        if parts.path == "/watch":
            candidate = (query.get("v") or [""])[0]
        else:
            pieces = [p for p in parts.path.split("/") if p]
            candidate = pieces[1] if len(pieces) >= 2 and pieces[0] in ("embed", "v", "shorts", "live") else ""
    else:
        return None
    return candidate if _YOUTUBE_ID.match(candidate or "") else None


def facebook_kind(url):
    """Which of Facebook's oEmbed services a link needs: "video" for videos, reels and fb.watch, else "post"."""
    host, path = _host(url), urlsplit(str(url or "")).path.lower()
    if host == "fb.watch" or "/videos/" in path or path.startswith("/watch") or "/reel" in path:
        return "video"
    return "post"


def mastodon_post(url):
    """(host, oEmbed address) for a link shaped like a Mastodon post (https://<host>/@user/<digits>), or None. Any
    server can be one, so this only decides what to ask; safe_fetch decides whether the address may be asked at all."""
    parts = urlsplit(str(url or ""))
    host = (parts.hostname or "").lower()
    if parts.scheme != "https" or not host or parts.port not in (None, 443) or not _MASTODON_PATH.match(parts.path):
        return None
    if site_of(url):                                  # a fixed site of its own (threads.net/@x/... is not Mastodon)
        return None
    return host, f"https://{host}/api/oembed"


def mastodon_iframe_ok(src, host):
    """Is `src` the post's own embed page on the same Mastodon server? Nothing else from that server's answer is kept."""
    parts = urlsplit(str(src or ""))
    return (parts.scheme == "https" and (parts.hostname or "").lower() == host and parts.port in (None, 443)
            and parts.path.rstrip("/").endswith("/embed") and _MASTODON_PATH.match(parts.path.rstrip("/")[:-len("/embed")] or "/x") is not None)


def mastodon_embed_src(snippet):
    """The embed page a Mastodon server's oEmbed snippet points at: an <iframe src> (older servers) or the
    `data-embed-url` of its placeholder <blockquote> (4.3 and later). Unchecked: mastodon_iframe_ok decides."""
    try:
        from bs4 import BeautifulSoup
        soup = BeautifulSoup(str(snippet or ""), "html.parser")
    except Exception:
        return None
    frame = soup.find("iframe", attrs={"src": True})
    if frame:
        return frame["src"]
    quote = soup.find(attrs={"data-embed-url": True})
    return quote["data-embed-url"] if quote else None


def default_card(url):
    """The card when a site's answer names nobody (Instagram and Facebook no longer do without a key)."""
    label = label_of(url)
    return (f"Post on {label}", "") if label else ("", "")


def mastodon_snippet(src):
    """The frame we draw for a Mastodon post (never the server's own markup or script): sandboxed, its address escaped."""
    return (f'<iframe src="{html.escape(src, quote=True)}" class="mastodon-embed" width="400" height="420" '
            'style="max-width: 100%; border: 0" loading="lazy" referrerpolicy="no-referrer" '
            'sandbox="allow-scripts allow-popups allow-popups-to-escape-sandbox" title="Mastodon post"></iframe>')


def threads_snippet(url):
    """Threads' own embed markup for a post link (a blockquote its embed.js turns into the card), or None. Built from
    the link alone; the only thing taken from it is the escaped address."""
    parts = urlsplit(str(url or ""))
    if site_of(url) not in ("threads.net", "threads.com") or not _THREADS_PATH.match(parts.path):
        return None
    permalink = html.escape(f"https://www.threads.com{parts.path.rstrip('/')}", quote=True)
    return (f'<blockquote class="text-post-media" data-text-post-permalink="{permalink}" data-text-post-version="0">'
            f'<a href="{permalink}" target="_blank" rel="noopener nofollow">View on Threads</a></blockquote>')


def threads_card(url):
    match = _THREADS_PATH.match(urlsplit(str(url or "")).path)
    return (f"@{match.group(1)} on Threads", "") if match else ("Post on Threads", "")
