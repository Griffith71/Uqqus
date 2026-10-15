"""Posts from the major social sites: which site a link is, what the site is called, and the rules for the sites that
are not one fixed host (Mastodon). Pure: no I/O; the calls to each site's own oEmbed service are in helpers/embed.py,
and the quote card a post shows is made by helpers/quote_cards.py (tests/test_social_embeds.py).

* No page loads a site's script, frame or widget: the oEmbed snippet is saved and read into a quote card in this site's
  own font. `card_from_oembed` also gives the link-preview fields (`meta_title` "Elon Musk on X", `meta_description`
  the post's own words), which the card falls back to.
* Mastodon is any server, so it is never trusted like a fixed host: only a link shaped like a Mastodon post
  (`mastodon_post`), its oEmbed and its public API fetched through helpers/safe_fetch.py, only that server's own
  `/embed` page accepted as proof (`mastodon_iframe_ok`), and what is saved built and escaped by us
  (`mastodon_quote_snippet`).
"""
import html
import re
from urllib.parse import parse_qs, urlsplit

CARD_TEXT_MAX = 300

# site -> what it is called
SITES = {
    "x.com": "X",
    "twitter.com": "X",
    "instagram.com": "Instagram",
    "facebook.com": "Facebook",
    "tiktok.com": "TikTok",
    "reddit.com": "Reddit",
    "redd.it": "Reddit",
    "bsky.app": "Bluesky",
    "threads.net": "Threads",
    "threads.com": "Threads",
    "pinterest.com": "Pinterest",
    "tumblr.com": "Tumblr",
}

_SPACE = re.compile(r"\s+")
_YOUTUBE_ID = re.compile(r"^[A-Za-z0-9_-]{6,20}$")
_MASTODON_PATH = re.compile(r"^/@([A-Za-z0-9_]{1,30})(?:@[A-Za-z0-9.-]{1,253})?/(\d{1,25})/?$")
_PARAGRAPH_BREAK = chr(10) * 2
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
    return SITES[site] if site else ""


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


def mastodon_status_id(url):
    """The id of the post in a Mastodon post link (`/@user/<id>`), for that server's public API, or None."""
    match = _MASTODON_PATH.match(urlsplit(str(url or "")).path)
    return match.group(2) if match else None


def mastodon_text(content):
    """A Mastodon post's text from the HTML its API answers: paragraphs kept apart, line breaks kept, tags gone."""
    try:
        from bs4 import BeautifulSoup
        soup = BeautifulSoup(str(content or ""), "html.parser")
    except Exception:
        return ""
    for br in soup.find_all("br"):
        br.replace_with(chr(10))
    paragraphs = [p.get_text() for p in soup.find_all("p")] or [soup.get_text()]
    return _PARAGRAPH_BREAK.join(part.strip() for part in paragraphs if part.strip())[:2000]


_ACCT = re.compile(r"^[A-Za-z0-9_]{1,30}(?:@[A-Za-z0-9.-]{1,253})?$")


def mastodon_handle(acct, host):
    """"@user@host" from an account's `acct` (local accounts have no host in it), or "" for anything unexpected."""
    acct = str(acct or "")
    if not _ACCT.match(acct):
        return ""
    return "@" + acct if "@" in acct else "@" + acct + "@" + host


def mastodon_quote_snippet(src, name, handle, when, text, media):
    """What is saved for a Mastodon post: the quote's own shape (as X and Bluesky send theirs: the text, then
    "- Name (@handle) date"), built from the server's public API answer and escaped throughout. helpers/quote_cards.py
    reads it; nothing draws it as HTML."""
    def escape(value):
        return html.escape(str(value or ""), quote=True)

    paragraphs = [part for part in str(text or "").split(_PARAGRAPH_BREAK) if part.strip()]
    body = "".join("<p>" + escape(part) + "</p>" for part in paragraphs) or "<p></p>"
    media_attr = ' data-media="1"' if media else ""
    return ('<blockquote class="mastodon-embed" data-embed-url="' + escape(src) + '"' + media_attr + '>' + body
            + "&mdash; " + escape(name) + " (" + escape(handle) + ') <a href="' + escape(src) + '">' + escape(when) + "</a></blockquote>")


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


def threads_snippet(url):
    """What is saved for a Threads post (Threads has no oEmbed service): its own embed markup for the link, or None.
    Built from the link alone; the only thing taken from it is the escaped address. Never drawn: the quote card is
    made from `threads_card`."""
    parts = urlsplit(str(url or ""))
    if site_of(url) not in ("threads.net", "threads.com") or not _THREADS_PATH.match(parts.path):
        return None
    permalink = html.escape(f"https://www.threads.com{parts.path.rstrip('/')}", quote=True)
    return (f'<blockquote class="text-post-media" data-text-post-permalink="{permalink}" data-text-post-version="0">'
            f'<a href="{permalink}" target="_blank" rel="noopener nofollow">View on Threads</a></blockquote>')


def threads_card(url):
    match = _THREADS_PATH.match(urlsplit(str(url or "")).path)
    return (f"@{match.group(1)} on Threads", "") if match else ("Post on Threads", "")
