"""Reading feeds (ruqqus/helpers/feed_items.py): every format, items without links or titles, and hostile XML."""
import json

import pytest

from ruqqus.helpers import bots
from ruqqus.helpers import feed_items as fi

RSS = b"""<?xml version="1.0" encoding="UTF-8"?>
<rss version="2.0" xmlns:dc="http://purl.org/dc/elements/1.1/"><channel><title>BBC News - Technology</title>
<item><title><![CDATA[Chips & phones]]></title><link>https://www.bbc.co.uk/news/technology-1</link>
  <guid isPermaLink="false">bbc-1</guid><pubDate>Tue, 14 Oct 2026 10:00:00 GMT</pubDate></item>
<item><title>Older one</title><link>https://www.bbc.co.uk/news/technology-0</link><dc:date>2026-10-13T10:00:00Z</dc:date></item>
<item><title>No link here</title></item>
<item><title>A javascript link</title><link>javascript:alert(1)</link></item>
</channel></rss>"""

ATOM = b"""<?xml version="1.0" encoding="utf-8"?>
<feed xmlns="http://www.w3.org/2005/Atom"><title>A YouTube channel</title>
<entry><id>yt:video:abc</id><title>New video</title><link rel="alternate" href="https://www.youtube.com/watch?v=abc"/>
  <published>2026-10-14T09:00:00+00:00</published></entry>
<entry><id>yt:video:def</id><title type="html">&lt;b&gt;Bold&lt;/b&gt; title</title>
  <link rel="self" href="https://example.org/self"/><link href="https://www.youtube.com/watch?v=def"/></entry>
</feed>"""

RDF = b"""<?xml version="1.0"?>
<rdf:RDF xmlns:rdf="http://www.w3.org/1999/02/22-rdf-syntax-ns#" xmlns="http://purl.org/rss/1.0/">
<channel rdf:about="https://example.org/"><title>An old RSS 1.0 feed</title></channel>
<item rdf:about="https://example.org/1"><title>First</title><link>https://example.org/1</link></item>
</rdf:RDF>"""

# a Bluesky profile's feed: items have no title, only text
BLUESKY = b"""<?xml version="1.0"?><rss version="2.0"><channel><title>@someone.bsky.social</title>
<item><link>https://bsky.app/profile/someone/post/1</link><description>Just shipped the new release, notes are up</description>
<guid>at://did/post/1</guid></item></channel></rss>"""


def test_rss_two():
    feed = fi.parse(RSS, "application/rss+xml")
    assert feed.title == "BBC News - Technology"
    assert [(i.title, i.link) for i in feed.items] == [("Chips & phones", "https://www.bbc.co.uk/news/technology-1"),
                                                      ("Older one", "https://www.bbc.co.uk/news/technology-0")]
    assert feed.items[0].key == bots.item_key("bbc-1", "") and feed.items[0].published == 1791972000
    assert feed.items[1].published == 1791885600                     # from dc:date


def test_atom_takes_the_alternate_link_and_cleans_an_html_title():
    feed = fi.parse(ATOM, "application/atom+xml")
    assert feed.title == "A YouTube channel"
    assert [(i.title, i.link) for i in feed.items] == [("New video", "https://www.youtube.com/watch?v=abc"),
                                                      ("Bold title", "https://www.youtube.com/watch?v=def")]
    assert feed.items[0].published is not None and feed.items[1].published is None


def test_rss_one():
    feed = fi.parse(RDF)
    assert feed.title == "An old RSS 1.0 feed" and [i.link for i in feed.items] == ["https://example.org/1"]


def test_an_item_without_a_title_takes_the_start_of_its_text():
    item, = fi.parse(BLUESKY).items
    assert item.title == "Just shipped the new release, notes are up"


def test_json_feed():
    doc = {"version": "https://jsonfeed.org/version/1.1", "title": "A blog",
           "items": [{"id": "1", "url": "https://blog.example/1", "title": "Hello", "date_published": "2026-10-01T00:00:00Z"},
                     {"id": "2", "url": "https://blog.example/2", "content_text": "No title, some text"},
                     {"id": "3", "title": "No url"}, "not an item"]}
    feed = fi.parse(json.dumps(doc).encode(), "application/feed+json")
    assert feed.title == "A blog" and [i.title for i in feed.items] == ["Hello", "No title, some text"]
    with pytest.raises(fi.FeedError):
        fi.parse(b'{"version": "something else", "items": []}', "application/json")


def test_duplicates_collapse_and_items_are_capped():
    items = "".join(f"<item><title>T{i % 150}</title><link>https://e.org/{i % 150}</link></item>" for i in range(300))
    feed = fi.parse(f"<rss><channel><title>x</title>{items}</channel></rss>".encode())
    assert len(feed.items) == fi.MAX_ITEMS and len({i.key for i in feed.items}) == fi.MAX_ITEMS


def test_newest_first_keeps_undated_items_after_dated_ones():
    feed = fi.parse(ATOM)
    assert [i.title for i in fi.newest_first(list(reversed(feed.items)))] == ["New video", "Bold title"]


@pytest.mark.parametrize("data", [
    b"",
    b"<html><body>Not a feed</body></html>",
    b"<rss><nochannel/></rss>",
    b"this is not xml at all <",
])
def test_what_is_not_a_feed(data):
    with pytest.raises(fi.FeedError) as err:
        fi.parse(data, "text/html")
    assert err.value.message


def test_entity_expansion_is_refused():
    bomb = (b'<?xml version="1.0"?><!DOCTYPE lolz [<!ENTITY lol "lol"><!ENTITY lol2 "&lol;&lol;&lol;&lol;&lol;">'
            b'<!ENTITY lol3 "&lol2;&lol2;&lol2;&lol2;">]><rss><channel><title>&lol3;</title></channel></rss>')
    with pytest.raises(fi.FeedError) as err:
        fi.parse(bomb)
    assert "entities" in err.value.message


def test_an_external_entity_is_never_fetched():
    xxe = (b'<?xml version="1.0"?><!DOCTYPE r [<!ENTITY x SYSTEM "file:///etc/passwd">]>'
           b'<rss><channel><title>&x;</title></channel></rss>')
    with pytest.raises(fi.FeedError):
        fi.parse(xxe)


def test_a_very_long_title_is_cut_to_a_post_title():
    feed = fi.parse(f"<rss><channel><title>x</title><item><title>{'word ' * 200}</title><link>https://e.org/1</link></item></channel></rss>".encode())
    assert len(feed.items[0].title) == bots.TITLE_MAX


def test_a_posted_link_drops_its_tracking_but_the_key_is_the_feeds_own():
    raw = "https://www.bbc.co.uk/news/articles/c1?at_medium=RSS&at_campaign=rss"
    raw2 = "https://example.org/story?id=4&utm_source=rss"
    data = (f"<rss><channel><title>BBC News</title>"
            f"<item><title>With a guid</title><link>{raw.replace('&', '&amp;')}</link><guid>bbc-c1</guid></item>"
            f"<item><title>No guid</title><link>{raw2.replace('&', '&amp;')}</link></item>"
            f"</channel></rss>").encode()
    first, second = fi.parse(data, "application/rss+xml").items
    assert first.link == "https://www.bbc.co.uk/news/articles/c1" and first.key == bots.item_key("bbc-c1", raw)
    assert second.link == "https://example.org/story?id=4" and second.key == bots.item_key("", raw2)



def test_what_a_feed_asks_for_is_read_from_rss_rdf_and_atom():
    rss = b"<rss><channel><title>T</title><ttl>15</ttl><item><title>A</title><link>https://e.org/a</link></item></channel></rss>"
    assert fi.parse(rss).ask_minutes == 15
    sy = 'xmlns:sy="http://purl.org/rss/1.0/modules/syndication/"'
    rdf = (f'<rdf:RDF xmlns:rdf="http://www.w3.org/1999/02/22-rdf-syntax-ns#" xmlns="http://purl.org/rss/1.0/" {sy}>'
           '<channel rdf:about="https://e.org/"><title>T</title><sy:updatePeriod>hourly</sy:updatePeriod>'
           '<sy:updateFrequency>4</sy:updateFrequency></channel>'
           '<item rdf:about="https://e.org/1"><title>A</title><link>https://e.org/1</link></item></rdf:RDF>').encode()
    assert fi.parse(rdf).ask_minutes == 15
    atom = (f'<feed xmlns="http://www.w3.org/2005/Atom" {sy}><title>T</title><sy:updatePeriod>daily</sy:updatePeriod>'
            '<entry><id>1</id><title>A</title><link href="https://e.org/1"/></entry></feed>').encode()
    assert fi.parse(atom).ask_minutes == 1440
    assert fi.parse(RSS, "application/rss+xml").ask_minutes == 0          # says nothing
    jf = json.dumps({"version": "https://jsonfeed.org/version/1.1", "title": "J",
                     "items": [{"id": "1", "url": "https://e.org/1", "title": "A"}]}).encode()
    assert fi.parse(jf, "application/feed+json").ask_minutes == 0         # JSON Feed has no such field
