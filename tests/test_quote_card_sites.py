"""Quote cards for the other major sites (ruqqus/helpers/social_embeds.py, quote_cards.py, picture_refresh.py): a
Bluesky, Mastodon or Instagram post shows its picture and (Bluesky, Mastodon) the post it quotes, as an X post does.
The answers below are the shapes those services sent on 2026-10-16, trimmed."""
import sqlite3
from pathlib import Path

import pytest

from ruqqus.helpers import picture_refresh
from ruqqus.helpers import quote_cards as qc
from ruqqus.helpers import social_embeds as se

ROOT = Path(__file__).resolve().parent.parent
NL = chr(10)

BSKY_DID = "did:plc:z72i7hdynmk6r22z27h6tvur"
BLUESKY = {"did": BSKY_DID, "handle": "bsky.app", "displayName": "Bluesky", "labels": []}
CLOUDS = {
    "uri": f"at://{BSKY_DID}/app.bsky.feed.post/3mxu3hsnebc2v", "author": BLUESKY, "labels": [],
    "record": {"$type": "app.bsky.feed.post", "createdAt": "2026-10-14T16:52:06.000Z", "text": "saw some cool clouds today"},
    "embed": {"$type": "app.bsky.embed.images#view", "images": [
        {"thumb": "https://cdn.bsky.app/img/feed_thumbnail/plain/did:plc:z72/a@jpeg",
         "fullsize": "https://cdn.bsky.app/img/feed_fullsize/plain/did:plc:z72/a@jpeg", "alt": ""}]},
}
HISTORICA = {"did": "did:plc:vva5bw27atiw7itylau5zkmi", "handle": "historicacanada.bsky.social", "displayName": "Historica Canada", "labels": []}
WINNIE = {
    "uri": f"at://{BSKY_DID}/app.bsky.feed.post/3mxuhakymek23", "author": BLUESKY, "labels": [],
    "record": {"$type": "app.bsky.feed.post", "createdAt": "2026-10-14T21:06:12.578Z", "text": "Canadians remember this classic Heritage Minute"},
    "embed": {"$type": "app.bsky.embed.record#view", "record": {
        "$type": "app.bsky.embed.record#viewRecord", "uri": "at://did:plc:vva5bw27atiw7itylau5zkmi/app.bsky.feed.post/3mxtpokyhko2x",
        "author": HISTORICA, "labels": [],
        "value": {"$type": "app.bsky.feed.post", "createdAt": "2026-10-14T14:04:32.044Z",
                  "text": "100 years ago today, the first Winnie-the-Pooh book was published!"},
        "embeds": [{"$type": "app.bsky.embed.external#view", "external": {
            "uri": "https://youtu.be/MlA6oEs3C18", "title": "Heritage Minutes: Winnie", "description": "",
            "thumb": "https://cdn.bsky.app/img/feed_thumbnail/plain/did:plc:vva/winnie@jpeg"}}]}},
}
MAUD = {"did": "did:plc:j54ofwzkxbeyjs6m2jwvwuro", "handle": "peloriemate.net", "displayName": "maud", "labels": []}
ROBDOG = {"did": "did:plc:5dha4eux2jvil7raud3rlbed", "handle": "robdogdraws.bsky.social", "displayName": "robdog", "labels": []}
FAKEPOSTING = {
    "uri": "at://did:plc:j54ofwzkxbeyjs6m2jwvwuro/app.bsky.feed.post/3mxkb3qz4cs27", "author": MAUD, "labels": [],
    "record": {"$type": "app.bsky.feed.post", "createdAt": "2026-10-10T19:49:31.289Z", "text": "i got so many people calling this tweet fakeposting"},
    "embed": {"$type": "app.bsky.embed.recordWithMedia#view",
              "media": {"$type": "app.bsky.embed.images#view", "images": [
                  {"thumb": "https://cdn.bsky.app/img/feed_thumbnail/plain/did:plc:j54/m@jpeg",
                   "fullsize": "https://cdn.bsky.app/img/feed_fullsize/plain/did:plc:j54/m@jpeg"}]},
              "record": {"record": {
                  "$type": "app.bsky.embed.record#viewRecord", "uri": "at://did:plc:5dha4eux2jvil7raud3rlbed/app.bsky.feed.post/3mxk4ubvur22l",
                  "author": ROBDOG, "labels": [], "value": {"createdAt": "2026-10-10T18:33:45.612Z", "text": "drew this"},
                  "embeds": [{"$type": "app.bsky.embed.recordWithMedia#view",
                              "media": {"$type": "app.bsky.embed.images#view", "images": [
                                  {"fullsize": "https://cdn.bsky.app/img/feed_fullsize/plain/did:plc:5dh/r@jpeg"}]},
                              "record": {"record": {"$type": "app.bsky.embed.record#viewRecord",
                                                    "uri": "at://did:plc:aaa/app.bsky.feed.post/3abc",
                                                    "author": {"handle": "someone.bsky.social"}, "value": {"text": "x"}}}}]}}},
}
MINICOMICS = {
    "uri": "at://did:plc:beqpqpce3wnzazg5vc3mezhr/app.bsky.feed.post/3mxk7gsrfxs2w", "labels": [],
    "author": {"did": "did:plc:beqpqpce3wnzazg5vc3mezhr", "handle": "nsousanis.bsky.social", "displayName": "Nick Sousanis"},
    "record": {"createdAt": "2026-10-10T19:19:54.753Z", "text": "Students made their first minicomics"},
    "embed": {"$type": "app.bsky.embed.video#view", "playlist": "https://video.bsky.app/watch/x/playlist.m3u8",
              "thumbnail": "https://video.bsky.app/watch/x/thumbnail.jpg"},
}


def _card_of(found, url):
    return qc.social_card(url, found["snippet"], found["title"], found["text"], found["picture"])


def test_a_bluesky_post_shows_its_photo():
    found = se.bsky_snippet(CLOUDS)
    url = "https://bsky.app/profile/bsky.app/post/3mxu3hsnebc2v"
    assert found["picture"].startswith("https://cdn.bsky.app/img/feed_fullsize/") and found["title"] == "Bluesky on Bluesky"
    card = _card_of(found, url)
    assert (card["name"], card["handle"], card["when"], card["text"]) == ("Bluesky", "@bsky.app", "14 October 2026", "saw some cool clouds today")
    assert card["image"] == found["picture"] and card["video"] is False and card["inner"] is None


def test_a_bluesky_quote_shows_the_post_it_quotes_with_its_own_picture():
    found = se.bsky_snippet(WINNIE)
    assert found["picture"] == ""                                   # the post itself has none: the quoted one has
    card = _card_of(found, "https://bsky.app/profile/bsky.app/post/3mxuhakymek23")
    assert card["text"] == "Canadians remember this classic Heritage Minute"
    inner = card["inner"]
    assert (inner["name"], inner["handle"], inner["label"]) == ("Historica Canada", "@historicacanada.bsky.social", "Bluesky")
    assert inner["url"] == "https://bsky.app/profile/historicacanada.bsky.social/post/3mxtpokyhko2x"
    assert inner["image"].endswith("winnie@jpeg") and inner["quoting"] == ""


def test_a_bluesky_quote_with_media_shows_both_pictures_and_no_deeper():
    found = se.bsky_snippet(FAKEPOSTING)
    card = _card_of(found, "https://bsky.app/profile/peloriemate.net/post/3mxkb3qz4cs27")
    assert card["image"].endswith("/m@jpeg")
    inner = card["inner"]
    assert inner["name"] == "robdog" and inner["image"].endswith("/r@jpeg")
    # what that one quotes in turn is a line, as on X
    assert inner["inner"] is None and inner["quoting"] == "Quoting @someone.bsky.social on Bluesky"


def test_a_bluesky_video_is_its_still_frame():
    found = se.bsky_snippet(MINICOMICS)
    card = _card_of(found, "https://bsky.app/profile/nsousanis.bsky.social/post/3mxk7gsrfxs2w")
    assert card["image"] == "https://video.bsky.app/watch/x/thumbnail.jpg" and card["video"] is True


def test_media_bluesky_hides_behind_a_label_is_never_shown():
    labelled = dict(CLOUDS, labels=[{"val": "porn"}])
    card = _card_of(se.bsky_snippet(labelled), "https://bsky.app/profile/bsky.app/post/3mxu3hsnebc2v")
    assert card["image"] == "" and card["sensitive"] is True and card["media"] == "Sensitive media"
    # an account label hides it as well; an unrelated label does not
    assert se.bsky_snippet(dict(CLOUDS, author=dict(BLUESKY, labels=[{"val": "nudity"}])))["picture"] == ""
    assert se.bsky_snippet(dict(CLOUDS, labels=[{"val": "!no-unauthenticated"}]))["picture"]


def test_a_quoted_post_bluesky_cannot_show_is_left_out():
    gone = dict(WINNIE, embed={"$type": "app.bsky.embed.record#view",
                               "record": {"$type": "app.bsky.embed.record#viewNotFound", "uri": "at://x/app.bsky.feed.post/1"}})
    card = _card_of(se.bsky_snippet(gone), "https://bsky.app/profile/bsky.app/post/3mxuhakymek23")
    assert card["inner"] is None
    assert se.bsky_snippet(None) is None and se.bsky_snippet({"uri": "nope"}) is None


@pytest.mark.parametrize("url, at", [
    ("https://bsky.app/profile/bsky.app/post/3l6oveex3ii2l", "at://bsky.app/app.bsky.feed.post/3l6oveex3ii2l"),
    ("https://bsky.app/profile/did:plc:abc/post/3l6o", "at://did:plc:abc/app.bsky.feed.post/3l6o"),
    ("https://bsky.app/profile/bsky.app", None),
    ("http://bsky.app/profile/bsky.app/post/3l6o", None),
    ("https://evil.example/profile/bsky.app/post/3l6o", None),
])
def test_a_bluesky_link_and_its_at_address(url, at):
    assert se.bsky_at_uri(url) == at


def test_a_bluesky_post_link_from_its_at_address():
    assert se.bsky_web_url("at://did:plc:a/app.bsky.feed.post/3abc", "a.bsky.social") == "https://bsky.app/profile/a.bsky.social/post/3abc"
    assert se.bsky_web_url("at://did:plc:a/app.bsky.feed.post/3abc", "handle.invalid") == "https://bsky.app/profile/did:plc:a/post/3abc"
    assert se.bsky_web_url("at://did:plc:a/app.bsky.feed.like/3abc") == "" and se.bsky_web_url(None) == ""


def _status(**extra):
    status = {"id": "113432146208839167", "created_at": "2026-10-13T18:15:39.000Z", "content": "<p>Look at this</p>",
              "account": {"acct": "Gargron", "display_name": "Eugen Rochko"}, "media_attachments": [], "sensitive": False,
              "spoiler_text": ""}
    status.update(extra)
    return status


PICTURE = {"type": "image", "preview_url": "https://files.mastodon.social/media_attachments/files/1/small/a.png"}
SRC = "https://mastodon.social/@Gargron/113432146208839167/embed"
MASTO_URL = "https://mastodon.social/@Gargron/113432146208839167"


def _masto_card(status):
    snippet, picture = se.mastodon_status_snippet(status, "mastodon.social", SRC)
    return qc.social_card(MASTO_URL, snippet, "Eugen Rochko on Mastodon", "", picture or "")


def test_a_mastodon_post_shows_its_first_picture_and_a_video_as_its_still():
    card = _masto_card(_status(media_attachments=[PICTURE]))
    assert card["image"] == PICTURE["preview_url"] and card["video"] is False and card["text"] == "Look at this"
    video = _masto_card(_status(media_attachments=[{"type": "gifv", "preview_url": "https://files.example/v.png"}]))
    assert video["image"] == "https://files.example/v.png" and video["video"] is True
    # only an https picture; audio has no picture and says it has media
    assert _masto_card(_status(media_attachments=[{"type": "image", "preview_url": "http://files.example/a.png"}]))["image"] == ""
    audio = _masto_card(_status(media_attachments=[{"type": "audio", "preview_url": None}]))
    assert audio["image"] == "" and audio["media"] == "Photo or video"


def test_a_sensitive_mastodon_post_shows_no_picture_and_only_its_warning():
    marked = _masto_card(_status(sensitive=True, media_attachments=[PICTURE]))
    assert marked["image"] == "" and marked["media"] == "Sensitive media"
    warned = _masto_card(_status(spoiler_text="Spoilers for the finale", media_attachments=[PICTURE]))
    assert warned["image"] == "" and warned["text"] == "Content warning: Spoilers for the finale"
    assert "Look at this" not in warned["text"]


def test_a_mastodon_quote_shows_the_status_it_quotes_and_no_deeper():
    deepest = _status(id="117434963950791499", account={"acct": "c@other.example", "display_name": "C"})
    middle = _status(id="117434963950791500", content="<p>the middle</p>", media_attachments=[PICTURE],
                     account={"acct": "b@other.example", "display_name": "B"},
                     quote={"state": "accepted", "quoted_status": deepest})
    top = _status(quote={"state": "accepted", "quoted_status": middle})
    card = _masto_card(top)
    inner = card["inner"]
    assert (inner["name"], inner["handle"], inner["text"]) == ("B", "@b@other.example", "the middle")
    assert inner["url"] == "https://mastodon.social/@b@other.example/117434963950791500"
    assert inner["image"] == PICTURE["preview_url"] and inner["quoting"] == "Quoting @c@other.example on Mastodon"
    # a quote its author has not accepted (or revoked) is not shown
    pending = _masto_card(_status(quote={"state": "pending", "quoted_status": middle}))
    assert pending["inner"] is None
    assert se.mastodon_parts(_status(account={"acct": "bad name"}), "mastodon.social") is None


IG_PHOTO = ('<html><head><meta property="og:type" content="article" /><meta property="og:title" content="Diego Moreno '
            'Quinteiro on Instagram: &quot;Wii Gato (Lipe Sleep)&quot;" /><meta property="og:image" content="https://scontent'
            '.cdninstagram.com/v/t51/527434648_n.jpg?stp=dst-jpg_e35&amp;oe=6AD7A75A" /><meta property="og:description" '
            'content="3,695 likes, 71 comments - diegoquinteiro on June 16, 2015: &quot;Wii Gato (Lipe Sleep)&quot;. " />'
            '</head></html>')
IG_URL = "https://www.instagram.com/p/fA9uwTtkSN/"


def test_an_instagram_post_is_read_from_its_link_preview_tags():
    found = se.instagram_card(IG_PHOTO)
    assert (found["name"], found["handle"], found["when"], found["text"]) == (
        "Diego Moreno Quinteiro", "@diegoquinteiro", "June 16, 2015", "Wii Gato (Lipe Sleep)")
    assert found["picture"].startswith("https://scontent.cdninstagram.com/") and found["video"] is False
    video = se.instagram_card(IG_PHOTO.replace('content="article"', 'content="video"'))
    assert video["video"] is True
    assert se.instagram_card("<html><head><title>Login</title></head></html>") is None and se.instagram_card(None) is None


def test_an_instagram_card_reads_as_a_quote():
    found = se.instagram_card(IG_PHOTO)
    snippet = se.quote_snippet("instagram-media", IG_URL, found["name"], found["handle"], found["when"], found["text"],
                               False, found["video"])
    card = qc.social_card(IG_URL, snippet, "Diego Moreno Quinteiro on Instagram", "", found["picture"])
    assert (card["name"], card["handle"], card["when"], card["text"]) == (
        "Diego Moreno Quinteiro", "@diegoquinteiro", "16 June 2015", "Wii Gato (Lipe Sleep)")
    assert card["image"] == found["picture"] and card["media"] == "" and card["icon"] == "instagram"


def test_a_tiktok_picture_is_a_videos_still():
    card = qc.social_card("https://www.tiktok.com/@scout2015/video/6718335390845095173", "", "Scout on TikTok", "",
                          "https://p16-sign.tiktokcdn.com/obj/a.jpeg?x-expires=1792517978")
    assert card["video"] is True and card["image"]


def test_x_posts_saved_before_still_read_their_quoted_post():
    from tests.test_quote_cards import MAKES_SENSE, MAKES_SENSE_URL, PALMER, PALMER_URL
    old = (MAKES_SENSE + '<div class="x-quoted" data-url="' + PALMER_URL + '" data-link="https://t.co/qDpOwsUzpY" '
           'data-next-url="" data-next-link="">' + PALMER + "</div>")
    assert qc.social_card(MAKES_SENSE_URL, old, "", "")["inner"]["name"] == "Tyler Palmer"


@pytest.mark.parametrize("url, ends", [
    ("https://scontent.cdninstagram.com/v/a.jpg?stp=x&oe=6AD7A75A&_nc_sid=1", 0x6AD7A75A),
    ("https://p16-sign.tiktokcdn.com/obj/a.jpeg?x-expires=1792517978&x-signature=abc", 1792517978),
    ("https://pbs.twimg.com/media/a.jpg?format=jpg&name=large", None),
    ("https://cdn.example/a.jpg?oe=nothex!", None),
    ("", None),
])
def test_which_picture_addresses_expire(url, ends):
    assert se.picture_expires(url) == ends


def test_only_pictures_about_to_expire_are_read_again_soonest_first():
    now = 1_792_000_000
    rows = [(1, "https://www.instagram.com/p/a/", f"https://x.example/a.jpg?oe={now + 3600:08X}"),
            (2, "https://www.instagram.com/p/b/", f"https://x.example/b.jpg?oe={now + 7 * 86400:08X}"),
            (3, "https://www.tiktok.com/@a/video/1", f"https://x.example/c.jpg?x-expires={now + 60}"),
            (4, "https://x.com/a/status/1", "https://pbs.twimg.com/media/a.jpg"),
            (5, "", f"https://x.example/e.jpg?oe={now:08X}")]
    assert picture_refresh.due(rows, now) == [(3, "https://www.tiktok.com/@a/video/1"), (1, "https://www.instagram.com/p/a/")]
    # a post read again less than an hour ago waits (a site can hand out the same address until nearer the end)
    assert picture_refresh.due(rows, now, {3: now - 600}) == [(1, "https://www.instagram.com/p/a/")]
    assert picture_refresh.due(rows, now, {3: now - 3600}) == [(3, "https://www.tiktok.com/@a/video/1"), (1, "https://www.instagram.com/p/a/")]


class _DB:
    """The two tables the refresh reads, on SQLite."""
    def __init__(self):
        self.conn = sqlite3.connect(":memory:")
        self.conn.executescript("""
            CREATE TABLE submissions (id INTEGER PRIMARY KEY, deleted_utc INTEGER, is_banned BOOLEAN, created_utc INTEGER);
            CREATE TABLE submissions_aux (id INTEGER PRIMARY KEY, url TEXT, preview_image_url TEXT, embed_url TEXT);""")

    def execute(self, statement, params=None):
        return self.conn.execute(str(statement), params or {})

    def commit(self):
        self.conn.commit()


def test_the_refresh_saves_the_new_picture_or_drops_one_that_cannot_be_read():
    now = 1_792_000_000
    db = _DB()
    expiring = f"https://x.example/a.jpg?oe={now + 3600:08X}"
    for post_id, url in ((1, "https://www.instagram.com/p/a/"), (2, "https://www.instagram.com/p/b/")):
        db.conn.execute("INSERT INTO submissions VALUES (?, 0, 0, ?)", (post_id, now - 86400))
        db.conn.execute("INSERT INTO submissions_aux VALUES (?, ?, ?, 'old')", (post_id, url, expiring))
    answers = {"https://www.instagram.com/p/a/": ("widget", "<blockquote>new</blockquote>", "https://x.example/new.jpg", ("t", "")),
               "https://www.instagram.com/p/b/": (None, None, None, None)}
    picture_refresh._tried.clear()
    assert picture_refresh.refresh(db, now, answers.__getitem__) == 2
    assert picture_refresh.refresh(db, now + 60, answers.__getitem__) == 0        # not again within the hour
    rows = dict((r[0], r[1:]) for r in db.conn.execute("SELECT id, preview_image_url, embed_url FROM submissions_aux"))
    assert rows[1] == ("https://x.example/new.jpg", "<blockquote>new</blockquote>")
    assert rows[2] == ("", "old")


def test_the_sites_are_read_as_the_plan_says():
    embed = (ROOT / "ruqqus" / "helpers" / "embed.py").read_text(encoding="utf-8")
    gap = NL * 3
    instagram = embed.split("def instagram_oembed(")[1].split(gap)[0]
    # an honest name, never another company's crawler's, and no redirect followed (a login page)
    assert '_HONEST_AGENT = "Mozilla/5.0 (compatible; RuqqusBot/1.0; link previews)"' in embed
    assert 'headers={"User-Agent": _HONEST_AGENT}' in instagram and "allow_redirects=False" in instagram
    assert "facebookexternalhit" not in embed
    assert 'requests.get(f"https://www.instagram.com{path}"' in instagram       # the post's own page on its own host
    bluesky = embed.split("def bluesky_oembed(")[1].split(gap)[0]
    assert '"https://public.api.bsky.app/xrpc/app.bsky.feed.getPostThread"' in bluesky
    assert 'return _oembed("https://embed.bsky.app/oembed"' in bluesky          # what is left when the API is quiet
    mastodon = embed.split("def mastodon_embed(")[1].split(gap)[0]
    assert "social_embeds.mastodon_status_snippet(status, host, src)" in mastodon and "requests.get" not in mastodon
    recomputes = (ROOT / "scripts" / "recomputes.py").read_text(encoding="utf-8")
    assert "picture_refresh.refresh(db, now, known_provider_embed)" in recomputes
    partial = (ROOT / "ruqqus" / "templates" / "partials" / "quote_card.html").read_text(encoding="utf-8")
    assert "{{ 'fas fa-eye-slash' if q.sensitive else 'far fa-image' }}" in partial


def test_a_mastodon_quotes_re_line_is_left_out():
    content = ('<p class="quote-inline">RE: <a href="https://hachyderm.io/@evacide/117434963703286230">hachyderm.io/@evacide/'
               '117434963703286230</a></p><p>It is unsurprising</p>')
    assert se.mastodon_text(content) == "It is unsurprising"
