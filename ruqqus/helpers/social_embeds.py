"""Posts from the major social sites: which site a link is, what the site is called, and the rules for the sites that
are not one fixed host (Mastodon). Pure: no I/O; the calls to each site's own oEmbed service are in helpers/embed.py,
and the quote card a post shows is made by helpers/quote_cards.py (tests/test_social_embeds.py).

* No page loads a site's script or widget: a post's oEmbed snippet is saved and read into a quote card in this site's
  own font. `card_from_oembed` also gives the link-preview fields (`meta_title` "Elon Musk on X", `meta_description`
  the post's own words), which the card falls back to.
* A video on a social site (Facebook videos and reels, Instagram reels, TikTok) plays in that site's plain player
  frame, built here from the link alone, and loaded only when the reader presses play (`player_layout`: the owner's
  choice, 2026-10-16). Music, video and GIF sites get their players the same way ("Players" at the end).
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
    "t.me": "Telegram",
    "telegram.me": "Telegram",
    "truthsocial.com": "Truth Social",
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
    if (host == "fb.watch" or "/videos/" in path or path.startswith("/watch") or "/reel" in path
            or path.startswith(("/share/v/", "/share/r/"))):
        return "video"
    return "post"


def facebook_names_a_video(url):
    """A Facebook address that itself names one video or reel (not the /watch page, not a share link)."""
    parts = urlsplit(str(url or ""))
    path = parts.path.lower()
    return bool(re.search(r"/videos/(?:[\w.-]+/)?\d{5,25}", path) or re.match(r"^/reel/\d{5,25}", path)
                or (path.rstrip("/") == "/watch" and re.search(r"(?:^|&)v=\d{5,25}", parts.query or "")))


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
    """A Mastodon post's text from the HTML its API answers: paragraphs kept apart, line breaks kept, tags gone. A
    quote post's "RE: <link>" line (`quote-inline`, for apps that cannot show quotes) is left out: the card shows the
    quoted post itself."""
    try:
        from bs4 import BeautifulSoup
        soup = BeautifulSoup(str(content or ""), "html.parser")
    except Exception:
        return ""
    for fallback in soup.find_all(class_="quote-inline"):
        fallback.decompose()
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


_X_STATUS = re.compile(r"^/([A-Za-z0-9_]{1,15})/status(?:es)?/(\d{1,25})/?$")
_TCO_PATH = re.compile(r"^/[A-Za-z0-9]{4,20}$")


def x_status_url(url):
    """The address of a post on X ("https://x.com/<handle>/status/<id>") for an X or Twitter post link, or None for
    anything else (a profile, an article, another site)."""
    parts = urlsplit(str(url or ""))
    if parts.scheme not in ("http", "https") or site_of(url) not in ("x.com", "twitter.com"):
        return None
    match = _X_STATUS.match(parts.path)
    return f"https://x.com/{match.group(1)}/status/{match.group(2)}" if match else None


def is_tco(link):
    """Is this one of X's own t.co short links?"""
    parts = urlsplit(str(link or ""))
    return parts.scheme == "https" and (parts.hostname or "").lower() == "t.co" and bool(_TCO_PATH.match(parts.path))


def last_tco_link(snippet):
    """The t.co link that ends the text of an X oEmbed snippet, written out as itself: where X puts the post a post
    quotes (oEmbed sends nothing else about it). None when the text ends otherwise: a picture's link reads
    "pic.twitter.com/...", an article's its own address."""
    try:
        from bs4 import BeautifulSoup
        quote = BeautifulSoup(str(snippet or ""), "html.parser").find("blockquote")
    except Exception:
        return None
    paragraph = quote.find("p") if quote else None
    links = paragraph.find_all("a") if paragraph else []
    if not links:
        return None
    href = links[-1].get("href") or ""
    return href if is_tco(href) and links[-1].get_text().strip() == href else None


def _https(url):
    """An https address with a host, or "" (what a card may show as a picture)."""
    parts = urlsplit(str(url or ""))
    return str(url) if parts.scheme == "https" and parts.hostname else ""


def quoted_post_ok(url):
    """Can this address be a quoted post drawn inside a card: a post on X, on Bluesky or on a Mastodon server?"""
    return bool(x_status_url(url) or bsky_post_ok(url) or mastodon_post(url))


def quoted_post_snippet(snippet, quoted_url, link, quoted_snippet, next_url="", next_link="", image=""):
    """What is saved for a post that quotes another (X, Bluesky, Mastodon): the post's own snippet, then the quoted
    post's snippet in a `div.quoted-post` that says which post it is, which link in the text pointed at it (X's t.co),
    its picture, and what that post quotes in turn (its address only: X shows one quoted post, not the one under it).
    Never drawn as HTML: helpers/quote_cards.py reads it. (X posts saved before 2026-10-16 have a `div.x-quoted`.)"""
    def escape(value):
        return html.escape(str(value or ""), quote=True)

    return (str(snippet or "") + '<div class="quoted-post" data-url="' + escape(quoted_url) + '" data-link="' + escape(link)
            + '" data-next-url="' + escape(next_url if quoted_post_ok(next_url) else "") + '" data-next-link="'
            + escape(next_link) + '" data-image="' + escape(_https(image)) + '">' + str(quoted_snippet or "") + "</div>")


def x_media_ok(url):
    """Is this the picture of a post on X: https on X's own picture host, never an account's profile picture?"""
    parts = urlsplit(str(url or ""))
    return (parts.scheme == "https" and (parts.hostname or "").lower() == "pbs.twimg.com" and parts.port is None
            and not parts.path.startswith("/profile_images/") and "/" in parts.path.strip("/"))


def x_media_is_video(url):
    """A video's still frame (X keeps them under *_video_thumb/) rather than a photo."""
    return x_media_ok(url) and "video_thumb/" in urlsplit(str(url)).path


def x_media(page):
    """The picture of a post on X from its page's link-preview tags (what X sends every link preview: Slack, Discord,
    iMessage), or "": only when X calls it a large-picture card (`twitter:card` "summary_large_image": a photo, or a
    video's still frame); a post with no picture has its author's profile picture there, which is never taken."""
    try:
        from bs4 import BeautifulSoup
        soup = BeautifulSoup(str(page or ""), "html.parser")
    except Exception:
        return ""

    def meta(key):
        tag = soup.find("meta", attrs={"name": key}) or soup.find("meta", attrs={"property": key})
        return (tag.get("content") or "").strip() if tag else ""

    if meta("twitter:card") != "summary_large_image":
        return ""
    image = meta("og:image") or meta("twitter:image")
    return image if x_media_ok(image) else ""


def quote_snippet(cls, src, name, handle, when, text, media=False, video=False, sensitive=False):
    """What is saved for a post this site reads from a site's API or link-preview tags (Bluesky, Mastodon,
    Instagram): the shape X and Bluesky send theirs in (the text, then "- Name (@handle) date"), built by us and
    escaped throughout. `data-video`: its picture is a video's still frame; `data-sensitive`: its media is marked
    sensitive there, so no picture is shown; `data-media`: it has media whose picture could not be read.
    helpers/quote_cards.py reads it; nothing draws it as HTML."""
    def escape(value):
        return html.escape(str(value or ""), quote=True)

    paragraphs = [part for part in str(text or "").split(_PARAGRAPH_BREAK) if part.strip()]
    body = "".join("<p>" + escape(part) + "</p>" for part in paragraphs) or "<p></p>"
    flags = (' data-media="1"' if media else "") + ' data-video="' + ("1" if video else "0") + '"'
    flags += ' data-sensitive="1"' if sensitive else ""
    return ('<blockquote class="' + escape(cls) + '" data-embed-url="' + escape(src) + '"' + flags + ">" + body
            + "&mdash; " + escape(name) + " (" + escape(handle) + ') <a href="' + escape(src) + '">' + escape(when)
            + "</a></blockquote>")


def mastodon_quote_snippet(src, name, handle, when, text, media, video=False, sensitive=False):
    return quote_snippet("mastodon-embed", src, name, handle, when, text, media, video, sensitive)


def mastodon_parts(status, host):
    """What a card needs from a status of a Mastodon server's public API (`/api/v1/statuses/<id>`), or None: its
    address on that server, name, handle, date, text, first picture (a video's or GIF's still frame marked so) and
    whether it is sensitive. A sensitive status or one behind a content warning shows no picture, and only its
    warning as text."""
    if not isinstance(status, dict):
        return None
    account = status.get("account") if isinstance(status.get("account"), dict) else {}
    acct, status_id = str(account.get("acct") or ""), str(status.get("id") or "")
    handle = mastodon_handle(acct, host)
    if not handle or not re.match(r"^\d{1,25}$", status_id):
        return None
    media = [item for item in status.get("media_attachments") or [] if isinstance(item, dict)]
    first = next((item for item in media if item.get("type") in ("image", "video", "gifv")), None)
    warning = _plain(status.get("spoiler_text"), 300)
    sensitive = bool(status.get("sensitive") or warning)
    picture = "" if sensitive or not first else _https(first.get("preview_url"))
    return {"url": f"https://{host}/@{acct}/{status_id}", "name": _plain(account.get("display_name"), 120),
            "handle": handle, "when": str(status.get("created_at") or "")[:40],
            "text": ("Content warning: " + warning) if warning else mastodon_text(status.get("content")),
            "picture": picture, "video": bool(picture and first.get("type") in ("video", "gifv")),
            "sensitive": sensitive and bool(media), "media": bool(media) and not picture}


def mastodon_quoted(status):
    """The status a Mastodon status quotes (Mastodon 4.5 quote posts, only once its author accepted), or None."""
    quote = status.get("quote") if isinstance(status, dict) else None
    if isinstance(quote, dict) and quote.get("state") == "accepted" and isinstance(quote.get("quoted_status"), dict):
        return quote["quoted_status"]
    return None


def mastodon_status_snippet(status, host, src):
    """(snippet, picture) for a Mastodon status: our own quote (`src`: the post's own embed page, checked), and the
    status it quotes inside it (`quoted_post_snippet`), or None when the status is not one we can read."""
    parts = mastodon_parts(status, host)
    if not parts:
        return None
    snippet = mastodon_quote_snippet(src, parts["name"], parts["handle"], parts["when"], parts["text"], parts["media"],
                                     parts["video"], parts["sensitive"])
    quoted = mastodon_parts(mastodon_quoted(status), host)
    if quoted:
        under = mastodon_parts(mastodon_quoted(mastodon_quoted(status)), host)
        inner = mastodon_quote_snippet(quoted["url"], quoted["name"], quoted["handle"], quoted["when"], quoted["text"],
                                       quoted["media"], quoted["video"], quoted["sensitive"])
        snippet = quoted_post_snippet(snippet, quoted["url"], "", inner, under["url"] if under else "", "", quoted["picture"])
    return snippet, parts["picture"]


_BSKY_PATH = re.compile(r"^/profile/([A-Za-z0-9.:_-]{1,253})/post/([A-Za-z0-9]{2,40})/?$")
_BSKY_AT = re.compile(r"^at://([A-Za-z0-9.:_-]{1,253})/app[.]bsky[.]feed[.]post/([A-Za-z0-9]{2,40})$")
# Bluesky's own moderation labels for media that is hidden behind a warning there: no picture is shown for them
BSKY_HIDDEN_LABELS = frozenset({"porn", "sexual", "nudity", "graphic-media", "gore"})


def bsky_post_ok(url):
    """Is this a post on Bluesky (https://bsky.app/profile/<handle or did>/post/<id>)?"""
    parts = urlsplit(str(url or ""))
    return parts.scheme == "https" and _host(url) == "bsky.app" and bool(_BSKY_PATH.match(parts.path))


def bsky_at_uri(url):
    """The AT address of a Bluesky post link, for its public API, or None."""
    match = _BSKY_PATH.match(urlsplit(str(url or "")).path) if bsky_post_ok(url) else None
    return f"at://{match.group(1)}/app.bsky.feed.post/{match.group(2)}" if match else None


def bsky_web_url(at_uri, handle=""):
    """A Bluesky post's link from its AT address (by its handle when it has a real one), or ""."""
    match = _BSKY_AT.match(str(at_uri or ""))
    if not match:
        return ""
    actor = handle if re.match(r"^[A-Za-z0-9.-]{1,253}$", str(handle or "")) and handle != "handle.invalid" else match.group(1)
    return f"https://bsky.app/profile/{actor}/post/{match.group(2)}"


def bsky_sensitive(labels):
    """Does a Bluesky post or account carry one of the labels its media is hidden behind?"""
    return any(isinstance(label, dict) and label.get("val") in BSKY_HIDDEN_LABELS for label in labels or [])


def bsky_media(embed):
    """(picture, is_video) from a Bluesky embed view: the first photo, a video's still frame, a link card's picture,
    or the media of a quote with media."""
    if not isinstance(embed, dict):
        return "", False
    kind = str(embed.get("$type") or "")
    if kind.startswith("app.bsky.embed.recordWithMedia"):
        return bsky_media(embed.get("media"))
    if kind.startswith("app.bsky.embed.images"):
        images = [item for item in embed.get("images") or [] if isinstance(item, dict)]
        return (_https(images[0].get("fullsize") or images[0].get("thumb")), False) if images else ("", False)
    if kind.startswith("app.bsky.embed.video"):
        still = _https(embed.get("thumbnail"))
        return still, bool(still)
    if kind.startswith("app.bsky.embed.external"):
        external = embed.get("external") if isinstance(embed.get("external"), dict) else {}
        return _https(external.get("thumb")), False
    return "", False


def bsky_quoted(embed):
    """The post a Bluesky embed view quotes (`record#view`, or the record of `recordWithMedia#view`), or None (a
    post that was deleted or blocked, a feed, a list...)."""
    if not isinstance(embed, dict):
        return None
    kind = str(embed.get("$type") or "")
    record = embed.get("record") if isinstance(embed.get("record"), dict) else None
    if kind.startswith("app.bsky.embed.recordWithMedia") and record:
        record = record.get("record") if isinstance(record.get("record"), dict) else None
    elif not kind.startswith("app.bsky.embed.record#view"):
        return None
    return record if record and str(record.get("$type") or "").endswith("embed.record#viewRecord") else None


def _bsky_quote(author, value, url, embed, labels):
    """(snippet, picture) for one Bluesky post, from its author, its record and its embed view."""
    author = author if isinstance(author, dict) else {}
    value = value if isinstance(value, dict) else {}
    handle = str(author.get("handle") or "")
    picture, video = bsky_media(embed)
    sensitive = bsky_sensitive(labels) and bool(picture)
    picture = "" if sensitive else picture
    snippet = quote_snippet("bluesky-embed", url, _plain(author.get("displayName"), 120) or handle,
                            "@" + handle if handle else "", str(value.get("createdAt") or "")[:40],
                            str(value.get("text") or "")[:2000], False, video and bool(picture), sensitive)
    return snippet, picture


def bsky_snippet(post):
    """{snippet, picture, title, text} for a post from Bluesky's public API (`getPostThread`'s `thread.post`), with
    the post it quotes inside it, or None."""
    if not isinstance(post, dict):
        return None
    author = post.get("author") if isinstance(post.get("author"), dict) else {}
    record = post.get("record") if isinstance(post.get("record"), dict) else {}
    url = bsky_web_url(post.get("uri"), author.get("handle"))
    if not url:
        return None
    snippet, picture = _bsky_quote(author, record, url, post.get("embed"), (post.get("labels") or []) + (author.get("labels") or []))
    quoted = bsky_quoted(post.get("embed"))
    if quoted:
        quoted_author = quoted.get("author") if isinstance(quoted.get("author"), dict) else {}
        quoted_url = bsky_web_url(quoted.get("uri"), quoted_author.get("handle"))
        embeds = [item for item in quoted.get("embeds") or [] if isinstance(item, dict)]
        if quoted_url:
            inner, inner_picture = _bsky_quote(quoted_author, quoted.get("value"), quoted_url, embeds[0] if embeds else None,
                                               (quoted.get("labels") or []) + (quoted_author.get("labels") or []))
            under = bsky_quoted(embeds[0]) if embeds else None
            under_url = bsky_web_url(under.get("uri"), (under.get("author") or {}).get("handle")) if under else ""
            snippet = quoted_post_snippet(snippet, quoted_url, "", inner, under_url, "", inner_picture)
    name = _plain(author.get("displayName"), 120) or str(author.get("handle") or "")
    return {"snippet": snippet, "picture": picture, "title": f"{name} on Bluesky"[:500],
            "text": _plain(record.get("text"), 1000)}


_IG_DESCRIPTION = re.compile(r"-\s*([A-Za-z0-9._]{1,30}) on ([A-Z][a-z]+ \d{1,2}, \d{4}):\s*(.*)$", re.S)


def instagram_card(page):
    """What a card needs from an Instagram post's page, from the link-preview tags it serves (og:title "Name on
    Instagram: ...", og:description "N likes, N comments - handle on June 16, 2015: "caption"", og:image, og:type
    "video"), or None when they are not there (a login page)."""
    try:
        from bs4 import BeautifulSoup
        soup = BeautifulSoup(str(page or ""), "html.parser")
    except Exception:
        return None

    def meta(key):
        tag = soup.find("meta", attrs={"property": key}) or soup.find("meta", attrs={"name": key})
        return (tag.get("content") or "").strip() if tag else ""

    match = _IG_DESCRIPTION.search(meta("og:description"))
    if not match:
        return None
    caption = match.group(3).strip()
    caption = caption[:-1].rstrip() if caption.endswith(".") else caption
    quotes = ('"', "\u201c", "\u201d")
    if len(caption) >= 2 and caption[0] in quotes and caption[-1] in quotes:
        caption = caption[1:-1]
    title = meta("og:title")
    name = title.split(" on Instagram")[0].strip() if " on Instagram" in title else ""
    picture = _https(meta("og:image"))
    return {"name": _plain(name, 120) or match.group(1), "handle": "@" + match.group(1), "when": match.group(2),
            "text": caption.strip()[:2000], "picture": picture,
            "video": bool(picture) and (meta("og:type") == "video" or bool(meta("og:video")))}


def picture_expires(url):
    """When a picture's address stops working, for the sites that sign theirs with an end date (Instagram and
    Facebook's `oe=<hex>`, TikTok's `x-expires=<seconds>`), or None for an address that does not expire."""
    query = parse_qs(urlsplit(str(url or "")).query)
    signed = (query.get("oe") or [""])[0]
    if re.match(r"^[0-9A-Fa-f]{8}$", signed):
        return int(signed, 16)
    seconds = (query.get("x-expires") or [""])[0]
    return int(seconds) if re.match(r"^\d{10}$", seconds) else None


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


# --- Players -----------------------------------------------------------------------------------------------------
# A link to a video, a song or a GIF is drawn as that site's own player (helpers/embed.py asks each site's fixed
# oEmbed host or builds the address from the link). Everything here is pure: it reads links and answers, and says
# where a player may come from. `player_layout` is what the feed card and the post page draw from.

# the hosts a player frame may come from, per kind; a player address on any other host is refused
AUDIO_HOSTS = ("open.spotify.com", "w.soundcloud.com", "embed.music.apple.com", "embed.podcasts.apple.com",
               "bandcamp.com", "www.mixcloud.com", "player-widget.mixcloud.com", "widget.deezer.com")
IMAGE_HOSTS = ("giphy.com", "media.tenor.com", "live.staticflickr.com")
# a social site's player is loaded only when the reader presses play: its frame is that site's page, with its cookies
CLICK_HOSTS = ("facebook.com", "instagram.com", "tiktok.com", "kick.com")

_FB_RESERVED = {"watch", "reel", "reels", "share", "permalink.php", "story.php", "photo", "photo.php", "groups",
                "events", "plugins", "video.php", "people", "profile.php", "login", "marketplace", "stories"}
_IG_REEL = re.compile(r"^/(?:[A-Za-z0-9._]{1,30}/)?(?:reel|reels|tv)/([A-Za-z0-9_-]{5,40})/?$")
_TIKTOK_VIDEO = re.compile(r"^/(?:@[A-Za-z0-9._]{1,40}/video|embed/v2|video|player/v1)/(\d{8,25})/?$")
_TELEGRAM_POST = re.compile(r"^/(?:s/)?([A-Za-z][A-Za-z0-9_]{3,31})/(\d{1,12})/?$")
_TRUTH_POST = re.compile(r"^/@([A-Za-z0-9_]{1,30})/(?:posts/)?(\d{6,25})/?$")
_KICK_PATH = re.compile(r"^/([A-Za-z0-9_-]{2,40})(?:/clips/(clip_[A-Za-z0-9]{5,40}))?/?$")
_LOOM_PATH = re.compile(r"^/(?:share|embed)/([0-9a-f]{20,40})/?$")
_BIDI = re.compile("[\u200e\u200f\u202a-\u202e\u2066-\u2069]")


def _on(host, domain):
    return host == domain or host.endswith("." + domain)


def host_in(url, hosts):
    """Is `url` an https address on one of `hosts` (or below one)?"""
    parts = urlsplit(str(url or ""))
    host = (parts.hostname or "").lower()
    return parts.scheme == "https" and bool(host) and any(_on(host, h) for h in hosts)


def clean_name(text, limit=120):
    """A name from another site, as plain text: no bidirectional controls (Flickr sends them), spaces collapsed."""
    return _plain(_BIDI.sub("", str(text or "")), limit)


# Facebook ---------------------------------------------------------------------------------------------------------

def facebook_needs_resolving(url):
    """A Facebook link that only redirects to the post (share links, fb.watch): its canonical address is asked first."""
    host, path = _host(url), urlsplit(str(url or "")).path.lower()
    return host == "fb.watch" or (_on(host, "facebook.com") and path.startswith("/share/"))


def facebook_canonical(url):
    """The link on www.facebook.com (m., mbasic., web. and touch. are the same post), https, without its query unless
    the query is what names the post (watch?v=, permalink.php, story.php, photo.php)."""
    parts = urlsplit(str(url or ""))
    host = (parts.hostname or "").lower()
    if not _on(host, "facebook.com"):
        return url
    keep = parts.path.lower().rstrip("/") in ("/watch", "/permalink.php", "/story.php", "/photo.php", "/photo")
    query = parts.query if keep else ""
    return "https://www.facebook.com" + (parts.path or "/") + ("?" + query if query else "")


def facebook_is_reel(url):
    path = urlsplit(str(url or "")).path.lower()
    return "/reel" in path or path.startswith("/share/r/")


def facebook_player(url):
    """Facebook's plain video player for a public video or reel (no script; a frame of facebook.com)."""
    from urllib.parse import quote
    return "https://www.facebook.com/plugins/video.php?href=" + quote(facebook_canonical(url), safe="") + "&show_text=false"


def facebook_page_name(url):
    """The page or profile a Facebook post link names (`/NASA/posts/...`), or "" (a number, or a link that names none)."""
    first = (urlsplit(str(url or "")).path.strip("/").split("/") or [""])[0]
    if not first or first.lower() in _FB_RESERVED or first.isdigit() or not re.match(r"^[A-Za-z0-9.-]{2,50}$", first):
        return ""
    return first


def facebook_card(snippet):
    """What Facebook's oEmbed snippet says about a post or video: its title (the first link), its text (the first
    paragraph), and "Posted by <a>Name</a> on <date>". Plain text; {} when the snippet says nothing."""
    try:
        from bs4 import BeautifulSoup
        quote_tag = BeautifulSoup(str(snippet or ""), "html.parser").find("blockquote")
    except Exception:
        return {}
    if quote_tag is None:
        return {}
    links = quote_tag.find_all("a")
    paragraph = quote_tag.find("p")
    text = _plain(paragraph.get_text(" ")) if paragraph else ""
    whole = _plain(quote_tag.get_text(" "), 1000)
    match = re.search(r"Posted by (.+?) on (.+)$", whole)
    name = clean_name(match.group(1)) if match else ""
    when = _plain(match.group(2), 60) if match else ""
    title = clean_name(links[0].get_text(" "), 200) if links and links[0].get_text(strip=True) != name else ""
    if title and title == text:
        title = ""
    out = {"name": name, "when": when, "title": title, "text": text}
    return out if any(out.values()) else {}


def card_from_facebook(snippet, url):
    """(meta_title, meta_description) for a Facebook post or video from its snippet: "<name> on Facebook" and its words."""
    found = facebook_card(snippet)
    name = found.get("name") or facebook_page_name(url)
    words = " - ".join(part for part in (found.get("title"), found.get("text")) if part)
    title = f"{name} on Facebook" if name else "Post on Facebook"
    return title[:500], words[:1000]


# Instagram, TikTok -------------------------------------------------------------------------------------------------

def instagram_reel_code(url):
    """The code of an Instagram reel or video (`/reel/<code>`, `/tv/<code>`, `/<user>/reel/<code>`), or None."""
    if site_of(url) != "instagram.com":
        return None
    match = _IG_REEL.match(urlsplit(str(url or "")).path)
    return match.group(1) if match else None


def instagram_player(code):
    return f"https://www.instagram.com/reel/{code}/embed/"


def tiktok_needs_resolving(url):
    """A TikTok short link (vm., vt., or tiktok.com/t/...): it only redirects to the video."""
    host, path = _host(url), urlsplit(str(url or "")).path
    return host in ("vm.tiktok.com", "vt.tiktok.com") or (_on(host, "tiktok.com") and path.startswith("/t/"))


def tiktok_video_id(url):
    if site_of(url) != "tiktok.com":
        return None
    match = _TIKTOK_VIDEO.match(urlsplit(str(url or "")).path)
    return match.group(1) if match else None


def tiktok_player(video_id):
    return f"https://www.tiktok.com/player/v1/{video_id}?rel=0"


# Telegram, Truth Social --------------------------------------------------------------------------------------------

def telegram_post(url):
    """(channel, message id) of a post in a public Telegram channel (t.me/<channel>/<id>), or None."""
    if site_of(url) not in ("t.me", "telegram.me"):
        return None
    match = _TELEGRAM_POST.match(urlsplit(str(url or "")).path)
    return (match.group(1), match.group(2)) if match else None


def telegram_card(page):
    """What a card needs from Telegram's own widget page for a post (t.me/<channel>/<id>?embed=1, plain HTML), or None:
    the channel's name, the text, the date, the first picture."""
    try:
        from bs4 import BeautifulSoup
        soup = BeautifulSoup(str(page or ""), "html.parser")
    except Exception:
        return None
    text_tag = soup.select_one(".tgme_widget_message_text")
    owner = soup.select_one(".tgme_widget_message_owner_name")
    if text_tag is None and owner is None:
        return None
    for br in text_tag.find_all("br") if text_tag else []:
        br.replace_with("\n")
    text = (text_tag.get_text() if text_tag else "").strip()
    stamp = soup.find("time", attrs={"datetime": True})
    picture = ""
    photo = soup.select_one(".tgme_widget_message_photo_wrap")
    if photo is not None:
        found = re.search(r"url\(['\"]?(https://[^'\")]+)", photo.get("style") or "")
        picture = _https(found.group(1)) if found else ""
    return {"name": clean_name(owner.get_text(" ") if owner else ""), "text": text[:2000],
            "when": (stamp["datetime"][:10] if stamp else ""), "picture": picture,
            "video": soup.select_one(".tgme_widget_message_video_player") is not None}


def truthsocial_status_id(url):
    if site_of(url) != "truthsocial.com":
        return None
    match = _TRUTH_POST.match(urlsplit(str(url or "")).path)
    return match.group(2) if match else None


# Video sites -------------------------------------------------------------------------------------------------------

def kick_player(url):
    """Kick's player for a channel (`kick.com/<channel>`) or one of its clips (`kick.com/<channel>/clips/clip_...`)."""
    if not host_in(url, ("kick.com",)) or _host(url).startswith("player."):
        return None
    match = _KICK_PATH.match(urlsplit(str(url or "")).path)
    if not match or match.group(1).lower() in ("categories", "browse", "following", "search", "dashboard"):
        return None
    clip = f"?clip={match.group(2)}" if match.group(2) else ""
    return f"https://player.kick.com/{match.group(1)}{clip}"


def loom_player(url):
    if not host_in(url, ("loom.com",)):
        return None
    match = _LOOM_PATH.match(urlsplit(str(url or "")).path)
    return f"https://www.loom.com/embed/{match.group(1)}" if match else None


# Music -------------------------------------------------------------------------------------------------------------

def apple_player(url):
    """Apple's own player for a link to Apple Music or Apple Podcasts: the same path on its embed host."""
    parts = urlsplit(str(url or ""))
    host = (parts.hostname or "").lower()
    target = {"music.apple.com": "embed.music.apple.com", "podcasts.apple.com": "embed.podcasts.apple.com"}.get(host)
    if parts.scheme != "https" or not target or not re.match(r"^/[a-z]{2}/[A-Za-z0-9/_.%-]{3,300}$", parts.path):
        return None
    query = "?" + parts.query if re.match(r"^i=\d{1,20}$", parts.query or "") else ""
    return f"https://{target}{parts.path}{query}"


def bandcamp_player(page):
    """Bandcamp's own player for a track or album page, from the item it names in `bc-page-properties`, or None."""
    found = re.search(r'<meta name="bc-page-properties" content="([^"]{1,300})"', str(page or ""))
    if not found:
        return None
    try:
        import json as json_lib
        props = json_lib.loads(html.unescape(found.group(1)))
    except ValueError:
        return None
    kind = {"t": "track", "a": "album"}.get(props.get("item_type"))
    item = props.get("item_id")
    if not kind or not isinstance(item, int):
        return None
    return (f"https://bandcamp.com/EmbeddedPlayer/{kind}={item}/size=large/bgcol=ffffff/linkcol=0687f5/"
            "tracklist=false/artwork=small/transparent=true/")


def audio_height(src):
    """How tall a music or podcast player is: one track or episode is short, a list is taller."""
    host, parts = _host(src), urlsplit(str(src or ""))
    path = parts.path.lower()
    if host == "open.spotify.com":
        return 152 if re.match(r"^/embed/(track|episode)/", path) else 352
    if host == "w.soundcloud.com":
        return 166 if "%2Fsets%2F" not in src and "/sets/" not in src else 400
    if host in ("embed.music.apple.com", "embed.podcasts.apple.com"):
        return 175 if parts.query.startswith("i=") or "/song/" in path else 450
    if host in ("www.mixcloud.com", "player-widget.mixcloud.com"):
        return 120
    if host == "bandcamp.com":
        return 120
    if host == "widget.deezer.com":
        return 152 if "/track/" in path else 300
    return 166


def player_layout(kind, src):
    """How a player is drawn, from its kind and address: whether it loads when the reader presses play ("click") or
    when it scrolls into view ("auto"), its shape ("16 / 9", "9 / 16") or its height in pixels (music), or None for
    something that is not a player."""
    if kind not in ("iframe", "iframe_untrusted", "video", "vertical", "audio", "image"):
        return None
    if kind == "image":
        return {"load": "now", "aspect": None, "height": None}
    if kind == "audio":
        return {"load": "click", "aspect": None, "height": audio_height(src)}
    load = "click" if host_in(src, CLICK_HOSTS) else "auto"
    return {"load": load, "aspect": "9 / 16" if kind == "vertical" else "16 / 9", "height": None}


# the sites whose links became players on 2026-10-16 (scripts/backfill_social_embeds.py re-works posts saved before)
PLAYER_SITES = ("open.spotify.com", "soundcloud.com", "music.apple.com", "podcasts.apple.com", "bandcamp.com",
                "mixcloud.com", "deezer.com", "streamable.com", "kick.com", "loom.com", "ted.com", "giphy.com",
                "tenor.com", "flickr.com")


def becomes_player(url):
    """A link that is a player now: a Facebook video or reel, an Instagram reel, a TikTok video (or short link), or a
    link to one of PLAYER_SITES."""
    if site_of(url) == "facebook.com" or _host(url) == "fb.watch":
        return facebook_kind(url) == "video"
    if instagram_reel_code(url) or tiktok_video_id(url) or tiktok_needs_resolving(url):
        return True
    return host_in(url, PLAYER_SITES)
