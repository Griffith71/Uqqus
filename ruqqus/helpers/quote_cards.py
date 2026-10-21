"""Quote cards: a post that links to another post shows that post the way X shows a quoted post, as a bordered card
under the member's own words, in this site's font and at the size of the member's text, with who wrote it, when, and
what it said, so whose words are whose is clear. The rules, with no I/O (tests/test_quote_cards.py). Two kinds:

* **A post on a social site** (X, Bluesky, Reddit, TikTok, Instagram, Facebook, Threads, Pinterest, Tumblr, or any
  Mastodon server): `social_card`, made from what the site's oEmbed service answered when the post was saved (the
  snippet in `embed_url`, `meta_title`, `meta_description`: helpers/embed.py, helpers/thumbs.py). Nothing from the site
  is loaded on the page: no script, no frame. Its picture is the post's preview picture (`image`, as every link
  preview on this site shows it; for X a photo or a video's still frame from the post's link-preview tags,
  social_embeds.x_media), and a post whose picture could not be read says so ("Photo or video").
* **A post on this site**: `quoted_post_id` reads the link; the card is drawn for each reader (helpers/jinja2.py
  `quote_of`) with the rules of opening the post (`post_notice`): a reader who may not see it is told it isn't
  available, and an anonymous author stays anonymous.
"""
import html as html_lib
import re
from datetime import datetime
from functools import lru_cache
from urllib.parse import urlsplit

from ruqqus.helpers import permalinks, social_embeds

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
    "t.me": re.compile(r"^/(?:s/)?(?P<handle>[A-Za-z]\w{3,31})/\d+"),
    "telegram.me": re.compile(r"^/(?:s/)?(?P<handle>[A-Za-z]\w{3,31})/\d+"),
    "truthsocial.com": re.compile(r"^/@(?P<handle>\w{1,30})/(?:posts/)?\d+"),
}

# what a post on these sites usually carries, when the site does not say: the card's line under the text
_MEDIA_WORD = {"tiktok.com": "Video", "instagram.com": "Photo or video", "pinterest.com": "Photo"}

# Font Awesome 6.4 Free brand icons (tests/data/fa_free_6_4_0_names.txt); a site without one shows its first letter
_ICONS = {"reddit.com": "reddit", "redd.it": "reddit", "tiktok.com": "tiktok", "instagram.com": "instagram",
          "facebook.com": "facebook", "pinterest.com": "pinterest", "tumblr.com": "tumblr", "mastodon": "mastodon",
          "t.me": "telegram", "telegram.me": "telegram"}


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


def _signed_quote(snippet, drop_link=""):
    """{name, handle, when, text, media, video, sensitive, ours} from an X or Bluesky snippet, or one this site built
    (social_embeds.quote_snippet: Bluesky, Mastodon, Instagram): `<blockquote><p>text</p>&mdash; Name (@handle)
    <a>date</a></blockquote>`. A photo or video in an X post is only a pic.twitter.com link in the text; `drop_link` is
    the t.co link that pointed at the post it quotes (drawn as a card instead)."""
    soup = _soup(snippet)
    quote = soup.find("blockquote") if soup else None
    if quote is None:
        return {}
    found = {"media": "Photo or video" if quote.get("data-media") else "", "video": quote.get("data-video") == "1",
             "sensitive": quote.get("data-sensitive") == "1", "ours": quote.has_attr("data-video")}
    paragraphs = quote.find_all("p", recursive=False)       # one on X and Bluesky, one per paragraph on Mastodon
    for paragraph in paragraphs:
        for link in paragraph.find_all("a"):
            if drop_link and link.get("href") == drop_link:
                link.decompose()
            elif _MEDIA_LINK.match(link.get_text().strip()):
                found["media"] = "Photo or video"
                link.decompose()
        for br in paragraph.find_all("br"):
            br.replace_with("\n")
    if paragraphs:
        text = _plain("\n\n".join(paragraph.get_text() for paragraph in paragraphs))
        found["text"] = text.replace(_BSKY_EMBED_NOTE, "").strip()
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


# what Bluesky's oEmbed writes in place of a quote or a picture (only seen when its API did not answer)
_BSKY_EMBED_NOTE = "[contains quote post or other embedded content]"

# the sites whose saved snippet may hold the post it quotes (social_embeds.quoted_post_snippet)
_QUOTING_SITES = ("x.com", "twitter.com", "bsky.app", "mastodon")


def _quoted(snippet):
    """What a saved snippet says about the post it quotes (social_embeds.quoted_post_snippet; X posts saved before
    2026-10-16 have a `div.x-quoted`), or None."""
    text = str(snippet or "")
    soup = _soup(text) if ("quoted-post" in text or "x-quoted" in text) else None
    box = soup.find("div", class_=("quoted-post", "x-quoted")) if soup else None
    if box is None or not social_embeds.quoted_post_ok(box.get("data-url")):
        return None
    next_url = box.get("data-next-url") or ""
    image = box.get("data-image") or ""
    return {"url": box.get("data-url"), "link": box.get("data-link") or "", "snippet": "".join(str(part) for part in box.contents),
            "next_url": next_url if social_embeds.quoted_post_ok(next_url) else "", "next_link": box.get("data-next-link") or "",
            "image": image if urlsplit(image).scheme == "https" else ""}


@lru_cache(maxsize=4096)
def social_card(url, snippet="", meta_title="", meta_description="", image=""):
    """The card of a post on a social site, or None when the link is not a post there (a profile, a feed, another
    site). Plain text only: the template escapes it and turns its addresses into links.

    As on X, a post that quotes another shows it inside (`inner`, a card of its own), and no deeper: what that one
    quotes is a line (`quoting` on the inner card). X, Bluesky and Mastodon say what a post quotes (helpers/embed.py)."""
    site = social_embeds.site_of(url) or ("mastodon" if social_embeds.mastodon_post(url) else None)
    quoted = _quoted(snippet) if site in _QUOTING_SITES else None
    card = _social(url, snippet, meta_title, meta_description, image, quoted["link"] if quoted else "")
    if card is None:
        return None
    card["inner"], card["quoting"] = None, ""
    if quoted:
        inner = _social(quoted["url"], quoted["snippet"], "", "", quoted["image"], quoted["next_link"])
        if inner:
            inner["inner"] = None
            inner["quoting"] = quoting_line(quoted["next_url"], "") if quoted["next_url"] else ""
            card["inner"] = inner
    return card


def inside(card):
    """A card drawn inside another card: what it quotes in turn is only its one line, as on X ("Quoting @x on X")."""
    if not card:
        return card
    inner, line = card.get("inner"), card.get("quoting") or ""
    if inner:
        line = f"Quoting {inner['handle'] or 'a post'} on {inner['label']}" if inner.get("kind") == "social" else "Quoting a post"
    return dict(card, inner=None, quoting=line)


def _social(url, snippet, meta_title, meta_description, image="", drop_link=""):
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
    text_known, signed = False, {}                        # a quote with no words of its own stays without them
    if site in ("x.com", "twitter.com", "bsky.app", "mastodon", "instagram.com", "t.me", "telegram.me", "truthsocial.com"):
        signed = _signed_quote(snippet, drop_link)
        text_known = "text" in signed and (bool(drop_link) or signed.get("ours"))
        card.update({k: v for k, v in signed.items() if v and k in card})
    elif site in ("reddit.com", "redd.it"):
        card.update({k: v for k, v in _reddit_quote(snippet).items() if v})

    if not card["name"]:
        card["name"] = _name_from_meta(meta_title, label)
    if not card["text"] and not text_known:
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
    # the post's picture: an https address only (the template escapes it); a picture shown needs no "Photo" line.
    # Media the site itself hides behind a warning (Bluesky's labels, Mastodon's sensitive) is never shown here.
    image = str(image or "")
    card["sensitive"] = bool(signed.get("sensitive"))
    card["image"] = image if urlsplit(image).scheme == "https" and not card["sensitive"] else ""
    card["video"] = bool(card["image"]) and (signed.get("video") or social_embeds.x_media_is_video(card["image"])
                                             or site == "tiktok.com")
    if card["image"]:
        card["media"] = ""
    elif card["sensitive"]:
        card["media"] = "Sensitive media"
    return card


def quoted_post_id(url, server_name, short_domain=""):
    """The base 36 id of the post on this site a link points at, or None: any address the site gives a post, old or new
    (`/@alice/post/<id>`, `/+guild/@carol/post/<id>`, `/post/<id>`, the older forms with a title slug:
    helpers/permalinks.py), and `/<id>` on the short domain. A comment link is not a post."""
    parts = urlsplit(str(url or ""))
    host = (parts.hostname or "").lower()
    if parts.scheme not in ("http", "https") or not host:
        return None
    if host == str(server_name or "").split(":")[0].lower():
        found = permalinks.parse(parts.path)
        if found and found[0] == "post":
            return found[1]
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
