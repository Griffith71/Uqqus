"""Quote cards: a post that links to another post shows that post the way X shows a quoted post, as a bordered card
under the member's own words, in this site's font and at the size of the member's text, with who wrote it, when, and
what it said, so whose words are whose is clear. The rules, with no I/O (tests/test_quote_cards.py). Two kinds:

* **A post on a social site** (X, Bluesky, Reddit, TikTok, Instagram, Facebook, Threads, Pinterest, Tumblr, or any
  Mastodon server): `social_card`, made from what the site's oEmbed service answered when the post was saved (the
  snippet in `embed_url`, `meta_title`, `meta_description`: helpers/embed.py, helpers/thumbs.py). Nothing from the site
  is loaded on the page: no script, no frame, no picture (the post's own thumbnail, fetched by this site, may be). X's
  free service sends no photos or videos, so a post that has them says so ("Photo or video") with a link to view it.
* **A post on this site**: `quoted_post_id` reads the link; the card is drawn for each reader (helpers/jinja2.py
  `quote_of`) with the rules of opening the post (`post_notice`): a reader who may not see it is told it isn't
  available, and an anonymous author stays anonymous.
"""
import html as html_lib
import re
from datetime import datetime
from functools import lru_cache
from urllib.parse import urlsplit

from ruqqus.helpers import social_embeds

TEXT_MAX = 2000
_SPACES = re.compile(r"[ \t\f\v]+")
_BLANK_LINES = re.compile(r"\n{3,}")
_MEDIA_LINK = re.compile(r"^(?:https?://)?(?:pic\.twitter\.com|pic\.x\.com)/\S+$", re.I)
_SIGNATURE = re.compile(r"^\W*(?P<name>.*?)\s*\(\s*(?P<handle>@[^)\s]+)\s*\)\s*(?P<when>.*)$", re.S)
_B36 = r"([0-9a-z]{1,16})"

# what a social site's post link looks like (a profile or a feed link is not a post: it keeps the link preview)
_POST_PATHS = {
    "x.com": re.compile(r"^/(?P<handle>\w{1,15})/status/\d+"),
    "twitter.com": re.compile(r"^/(?P<handle>\w{1,15})/status/\d+"),
    "bsky.app": re.compile(r"^/profile/(?P<handle>[\w.:-]+)/post/\w+"),
    "reddit.com": re.compile(r"^/r/\w+/comments/\w+"),
    "redd.it": re.compile(r"^/\w+"),
    "tiktok.com": re.compile(r"^/@(?P<handle>[\w.]+)/video/\d+"),
    "instagram.com": re.compile(r"^/(?:[\w.]+/)?(?:p|reel|tv)/[\w-]+"),
    "facebook.com": re.compile(r"^/(?:[\w.-]+/(?:posts|videos|photos)/|watch|reel/|permalink\.php|story\.php)"),
    "threads.net": re.compile(r"^/@(?P<handle>[\w.]+)/post/[\w-]+"),
    "threads.com": re.compile(r"^/@(?P<handle>[\w.]+)/post/[\w-]+"),
    "pinterest.com": re.compile(r"^/pin/\d+"),
    "tumblr.com": re.compile(r"^/(?:(?P<handle>[\w-]+)/)?(?:post/)?\d+"),
}

# what a post on these sites usually carries, when the site does not say: the card's line under the text
_MEDIA_WORD = {"tiktok.com": "Video", "instagram.com": "Photo or video", "pinterest.com": "Photo"}

# Font Awesome 6.4 Free brand icons (tests/data/fa_free_6_4_0_names.txt); a site without one shows its first letter
_ICONS = {"reddit.com": "reddit", "redd.it": "reddit", "tiktok.com": "tiktok", "instagram.com": "instagram",
          "facebook.com": "facebook", "pinterest.com": "pinterest", "tumblr.com": "tumblr", "mastodon": "mastodon"}


def _plain(text, limit=TEXT_MAX):
    text = html_lib.unescape(str(text or "")).replace("\r\n", "\n").replace("\r", "\n")
    lines = [_SPACES.sub(" ", line).strip() for line in text.split("\n")]
    text = _BLANK_LINES.sub("\n\n", "\n".join(lines)).strip()
    return text if len(text) <= limit else text[:limit - 1].rstrip() + "…"


def _soup(snippet):
    try:
        from bs4 import BeautifulSoup
        return BeautifulSoup(str(snippet or ""), "html.parser")
    except Exception:
        return None


def _signed_quote(snippet):
    """{name, handle, when, text, media} from an X, Bluesky or saved Mastodon snippet: `<blockquote><p>text</p>&mdash;
    Name (@handle) <a>date</a></blockquote>`. A photo or video in an X post is only a pic.twitter.com link in the text."""
    soup = _soup(snippet)
    quote = soup.find("blockquote") if soup else None
    if quote is None:
        return {}
    found = {"media": "Photo or video" if quote.get("data-media") else ""}
    paragraphs = quote.find_all("p", recursive=False)       # one on X and Bluesky, one per paragraph on Mastodon
    for paragraph in paragraphs:
        for link in paragraph.find_all("a"):
            if _MEDIA_LINK.match(link.get_text().strip()):
                found["media"] = "Photo or video"
                link.decompose()
        for br in paragraph.find_all("br"):
            br.replace_with("\n")
    if paragraphs:
        found["text"] = _plain("\n\n".join(paragraph.get_text() for paragraph in paragraphs))
        for paragraph in paragraphs:
            paragraph.extract()
    signature = _SIGNATURE.match(_plain(quote.get_text(" ")).replace("\n", " "))
    if signature:
        found.update(name=signature.group("name").strip(), handle=signature.group("handle"),
                     when=_when(signature.group("when")))
    return found


def _when(text):
    """The date a quote was written, as the site writes dates ("26 October 2022"): X sends "October 26, 2022",
    Bluesky and Mastodon a timestamp. Anything else is kept as it came, short."""
    text = str(text or "").strip()
    for parse in (lambda t: datetime.fromisoformat(t.replace("Z", "+00:00")), lambda t: datetime.strptime(t, "%B %d, %Y")):
        try:
            day = parse(text)
        except ValueError:
            continue
        return f"{day.day} {day.strftime('%B %Y')}"
    return text[:40]


def _reddit_quote(snippet):
    """{name, text, where} from Reddit's snippet: the post's title, `u/name` and `r/community` as links."""
    soup = _soup(snippet)
    links = soup.find_all("a") if soup else []
    found = {}
    for link in links:
        href, label = link.get("href") or "", _plain(link.get_text(), 300)
        if "/comments/" in href and "text" not in found:
            found["text"] = label
        elif "/user/" in href or "/u/" in href:
            found["name"] = label if label.startswith("u/") else f"u/{label}"
        elif re.search(r"/r/[\w]+/?$", href):
            found["where"] = label if label.startswith("r/") else f"r/{label}"
    return found


def _name_from_meta(meta_title, label):
    """"Elon Musk on X" -> "Elon Musk"; "Post on Instagram" (the site named nobody) -> ""."""
    title = _plain(meta_title, 200)
    suffix = f" on {label}"
    if label and title.endswith(suffix):
        title = title[:-len(suffix)].strip()
    return "" if title.lower() == "post" else title


def _mastodon_handle(url):
    parts = urlsplit(url)
    match = re.match(r"^/(@[\w]+)(@[\w.-]+)?/", parts.path + "/")
    if not match:
        return ""
    return match.group(1) + (match.group(2) or "@" + (parts.hostname or ""))


@lru_cache(maxsize=4096)
def social_card(url, snippet="", meta_title="", meta_description="", has_thumb=False):
    """The card of a post on a social site, or None when the link is not a post there (a profile, a feed, another
    site). Plain text only: the template escapes it and turns its addresses into links."""
    url = str(url or "")
    site = social_embeds.site_of(url)
    mastodon = None if site else social_embeds.mastodon_post(url)
    if site:
        path_rule = _POST_PATHS.get(site)
        path_match = path_rule.match(urlsplit(url).path) if path_rule else None
        if path_match is None:
            return None
        label = social_embeds.label_of(url)
    elif mastodon:
        site, label, path_match = "mastodon", "Mastodon", None
    else:
        return None

    card = {"kind": "social", "site": site, "label": label, "icon": _ICONS.get(site, ""), "letter": label[:1],
            "url": url, "name": "", "handle": "", "when": "", "text": "", "where": "", "media": ""}
    if site in ("x.com", "twitter.com", "bsky.app", "mastodon"):
        card.update({k: v for k, v in _signed_quote(snippet).items() if v})
    elif site in ("reddit.com", "redd.it"):
        card.update({k: v for k, v in _reddit_quote(snippet).items() if v})

    if not card["name"]:
        card["name"] = _name_from_meta(meta_title, label)
    if not card["text"]:
        card["text"] = _plain(meta_description)
    if not card["handle"]:
        if site == "mastodon":
            card["handle"] = _mastodon_handle(url)
        elif path_match is not None and path_match.groupdict().get("handle") and not path_match.group("handle").startswith("did:"):
            card["handle"] = "@" + path_match.group("handle")
    if card["handle"] and card["name"].replace("@", "") == card["handle"].replace("@", ""):
        card["name"] = ""                                  # "@zuck on Threads": the handle says it once
    if not card["media"]:
        card["media"] = _MEDIA_WORD.get(site, "")
        if site == "facebook.com" and social_embeds.facebook_kind(url) == "video":
            card["media"] = "Video"
    card["thumb"] = bool(has_thumb)
    return card


def quoted_post_id(url, server_name, short_domain=""):
    """The base 36 id of the post on this site a link points at, or None: `/+guild/post/<id>/<slug>`, a profile post's
    `/post/<id>/<slug>`, the shortlink `/post/<id>` and `/<id>` on the short domain. A comment link (one more id after
    the slug) is not a post."""
    parts = urlsplit(str(url or ""))
    host = (parts.hostname or "").lower()
    if parts.scheme not in ("http", "https") or not host:
        return None
    if host == str(server_name or "").split(":")[0].lower():
        for rule in (rf"^/\+\w+/post/{_B36}(?:/[\w-]*)?/?$", rf"^/post/{_B36}(?:/[\w-]*)?/?$"):
            match = re.match(rule, parts.path, re.I)
            if match:
                return match.group(1).lower()
    if short_domain and host == str(short_domain).split(":")[0].lower():
        match = re.match(rf"^/{_B36}/?$", parts.path, re.I)
        if match:
            return match.group(1).lower()
    return None


UNAVAILABLE = "This post isn't available."
DELETED = "This post was deleted."
REMOVED = "This post was removed."
FILTERED = "Hidden by your word filter."
MUTED = "This post is from an account you muted."


def post_notice(*, missing, deleted, removed, filtered, blocked, muted, anonymous):
    """What a reader is told instead of a quoted post of this site, or None when they may read it. Hidden (a Circle, a
    private guild: `missing`), deleted, removed, past their word filter, or by an account they block or that blocks them.
    An anonymous post never says it is someone's (a block or a mute does not hide it, as in threads)."""
    if missing:
        return UNAVAILABLE
    if deleted:
        return DELETED
    if removed:
        return REMOVED
    if filtered:
        return FILTERED
    if blocked and not anonymous:
        return UNAVAILABLE
    if muted and not anonymous:
        return MUTED
    return None


def quoting_line(url, server_name, short_domain=""):
    """A quoted post that quotes something itself says so in one line, so the chain is clear without drawing it all."""
    if quoted_post_id(url, server_name, short_domain):
        return "Quoting a post"
    card = social_card(url) if url else None
    if card:
        return f"Quoting {card['handle'] or 'a post'} on {card['label']}"
    return ""
