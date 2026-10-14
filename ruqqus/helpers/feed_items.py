"""Reading a feed a bot was approved with: RSS 2.0, RSS 1.0 (RDF), Atom and JSON Feed, into items a bot can post.

Pure: bytes in, items out (helpers/feed_fetch.py fetches them, scripts/run_bots.py posts them). XML is parsed with
`defusedxml`, which refuses entity declarations and anything external, so a hostile feed can't expand into gigabytes
or make the parser fetch a file. Every item needs an http(s) link; an item without a title (Bluesky and Mastodon
feeds have none) takes the start of its text as the title. Only the title and the link are ever posted.
"""
import calendar
import email.utils
import json
from collections import namedtuple
from datetime import datetime

from ruqqus.helpers import bots

MAX_ITEMS = 100

Item = namedtuple("Item", "key title link published")       # published: unix time, or None when the feed says none
Feed = namedtuple("Feed", "title items")


class FeedError(ValueError):
    """The answer is not a feed we can read. The message is shown to the bot's owner and the admins."""

    def __init__(self, message):
        super().__init__(message)
        self.message = message


NOT_A_FEED = "That address does not answer with an RSS, Atom or JSON feed."


def _local(tag):
    return tag.rsplit("}", 1)[-1] if isinstance(tag, str) else ""


def _child(node, name):
    for child in node:
        if _local(child.tag) == name:
            return child
    return None


def _children(node, name):
    return [child for child in node if _local(child.tag) == name]


def _text(node, name):
    child = _child(node, name)
    if child is None:
        return ""
    return "".join(child.itertext()).strip()


def _link_ok(link):
    link = (link or "").strip()
    return link if link.startswith(("https://", "http://")) and " " not in link and len(link) <= 2048 else ""


def _when_rfc822(value):
    try:
        parsed = email.utils.parsedate_to_datetime(value)
    except (TypeError, ValueError, IndexError):
        return None
    if parsed is None:
        return None
    return int(parsed.timestamp())


def _when_iso(value):
    value = (value or "").strip()
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        return calendar.timegm(parsed.timetuple())
    return int(parsed.timestamp())


def _item(guid, title, text, link, published):
    link = _link_ok(link)
    if not link:
        return None
    title = bots.clean_title(title) or bots.clean_title(text)
    if not title:
        return None
    return Item(bots.item_key(guid, link), title, link, published)


def _rss_items(container):
    out = []
    for node in _children(container, "item"):
        guid = _text(node, "guid") or (node.get("{http://www.w3.org/1999/02/22-rdf-syntax-ns#}about") or "")
        when = _when_rfc822(_text(node, "pubDate")) or _when_iso(_text(node, "date"))
        item = _item(guid, _text(node, "title"), _text(node, "description"), _text(node, "link"), when)
        if item:
            out.append(item)
    return out


def _atom_link(entry):
    links = _children(entry, "link")
    for link in links:
        if (link.get("rel") or "alternate") == "alternate" and link.get("href"):
            return link.get("href")
    return links[0].get("href", "") if links else ""


def _atom_items(feed):
    out = []
    for entry in _children(feed, "entry"):
        when = _when_iso(_text(entry, "published")) or _when_iso(_text(entry, "updated"))
        text = _text(entry, "summary") or _text(entry, "content")
        item = _item(_text(entry, "id"), _text(entry, "title"), text, _atom_link(entry), when)
        if item:
            out.append(item)
    return out


def _parse_xml(data):
    from defusedxml import ElementTree, DefusedXmlException

    try:
        root = ElementTree.fromstring(data)
    except DefusedXmlException:
        raise FeedError("That feed declares entities, which are not read here.")
    except ElementTree.ParseError:
        raise FeedError(NOT_A_FEED)

    kind = _local(root.tag)
    if kind == "rss":
        channel = _child(root, "channel")
        if channel is None:
            raise FeedError(NOT_A_FEED)
        return Feed(bots.plain_text(_text(channel, "title"), 200), _rss_items(channel))
    if kind == "RDF":
        channel = _child(root, "channel")
        title = _text(channel, "title") if channel is not None else ""
        return Feed(bots.plain_text(title, 200), _rss_items(root))
    if kind == "feed":
        return Feed(bots.plain_text(_text(root, "title"), 200), _atom_items(root))
    raise FeedError(NOT_A_FEED)


def _parse_json(data):
    try:
        doc = json.loads(data.decode("utf-8-sig"))
    except (ValueError, UnicodeDecodeError):
        raise FeedError(NOT_A_FEED)
    if not isinstance(doc, dict) or not str(doc.get("version", "")).startswith("https://jsonfeed.org/version/"):
        raise FeedError(NOT_A_FEED)
    out = []
    for entry in doc.get("items") or []:
        if not isinstance(entry, dict):
            continue
        text = entry.get("content_text") or entry.get("summary") or entry.get("content_html") or ""
        item = _item(str(entry.get("id") or ""), str(entry.get("title") or ""), str(text),
                     str(entry.get("url") or entry.get("external_url") or ""),
                     _when_iso(str(entry.get("date_published") or entry.get("date_modified") or "")))
        if item:
            out.append(item)
    return Feed(bots.plain_text(doc.get("title"), 200), out)


def parse(data, content_type=""):
    """The feed's title and up to MAX_ITEMS items, in the feed's own order (newest first, by convention).
    FeedError when it is not a feed."""
    if not data:
        raise FeedError(NOT_A_FEED)
    head = data.lstrip()[:1]
    if "json" in (content_type or "").lower() or head in (b"{", b"["):
        feed = _parse_json(data)
    else:
        feed = _parse_xml(data)
    seen, items = set(), []
    for item in feed.items:
        if item.key not in seen:
            seen.add(item.key)
            items.append(item)
        if len(items) >= MAX_ITEMS:
            break
    return Feed(feed.title, items)


def newest_first(items):
    """Items by publication time, newest first; items without a time keep the feed's order after the dated ones."""
    dated = sorted((i for i in items if i.published), key=lambda i: i.published, reverse=True)
    return dated + [i for i in items if not i.published]
