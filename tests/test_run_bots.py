"""What the bot runner does with a due source (ruqqus/helpers/bot_runner.py), with stand-ins for the fetch, the
submit route and the notification, on the real SQL (SQLite)."""
import re
from pathlib import Path

from sqlalchemy import text

from ruqqus.helpers import bot_runner as br
from ruqqus.helpers import bot_store as bs
from ruqqus.helpers import bots, quarantine
from ruqqus.helpers.feed_fetch import Answer
from ruqqus.helpers.safe_fetch import FetchError

from tests.test_bot_store import make, NOW


def feed(*numbers):
    """An RSS feed with items n (newest first), item n published n hours after a fixed time."""
    items = "".join(f"<item><title>Item {n}</title><link>https://news.example/{n}</link><guid>g{n}</guid>"
                    f"<pubDate>{_date(n)}</pubDate></item>" for n in sorted(numbers, reverse=True))
    return f"<rss><channel><title>News</title>{items}</channel></rss>".encode()


def _date(n):
    import email.utils
    return email.utils.formatdate(NOW + n * 3600, usegmt=True)


class World:
    def __init__(self, guilds=("news",)):
        self.db = make()
        bs.add_bot(self.db, 10, 1, "bbc_tech", "Headlines", list(guilds), NOW)
        self.source = bs.add_source(self.db, 10, "https://feeds.example.org/a.xml", NOW)
        bs.review_bot(self.db, 10, bots.APPROVED, 99, "", NOW)
        self.body = feed(1)
        self.fail = None
        self.posted, self.told, self.answers, self.calls = [], [], [], []

    def fetch(self, url, etag, last_modified):
        if self.fail:
            raise FetchError(self.fail)
        if self.body is None:
            return Answer(False, b"", "", etag, last_modified, url)
        return Answer(True, self.body, "application/rss+xml", '"e"', "", url)

    def submit(self, bot, item, guilds):
        self.calls.append((item.title, list(guilds)))
        if self.answers:
            return self.answers.pop(0)
        self.posted.append(item.title)
        bs.count_post(self.db, bot.user_id, bots.utc_day(NOW))
        return br.outcome("posted", post_id=100 + len(self.posted))

    def notify(self, user_id, message, **values):
        self.told.append((user_id, message % values))      # alerts.notify: an English message and its values

    def run(self, owner_ok=True, now=NOW):
        return br.run_source(self.db, self.source, now, self.fetch, self.submit, self.notify, lambda _: owner_ok)


def test_the_first_read_posts_only_the_newest_items_oldest_first_and_skips_the_backlog():
    w = World()
    w.body = feed(1, 2, 3, 4, 5, 6)
    assert w.run().endswith("3 posted")
    assert w.posted == ["Item 4", "Item 5", "Item 6"]
    assert w.calls[0][1] == ["news"]                          # forwarded to the bot's guilds
    seen = w.db.execute(text("SELECT COUNT(*) FROM bot_seen")).scalar()
    assert seen == 6                                          # the backlog is seen, never posted
    src = bs.source(w.db, w.source)
    assert src.read_once and src.posted_count == 3 and src.title == "News" and src.etag == '"e"'


def test_later_reads_post_what_is_new_and_nothing_twice():
    w = World()
    w.body = feed(1)
    w.run()
    w.posted.clear()
    w.run()
    assert w.posted == []
    w.body = feed(1, 2, 3, 4, 5)
    w.run()
    assert w.posted == ["Item 2", "Item 3", "Item 4"]         # at most PER_RUN, oldest first
    w.posted.clear()
    w.run()
    assert w.posted == ["Item 5"]


def test_an_unchanged_feed_posts_nothing():
    w = World()
    w.run()
    w.body = None
    assert w.run() == "unchanged"


def test_a_duplicate_is_seen_a_wait_is_tried_again_later():
    w = World()
    w.run()
    w.body = feed(1, 2, 3)
    w.answers = [br.outcome("duplicate"), br.outcome("wait", "Slow down.")]
    w.run()
    assert bs.seen_keys(w.db, w.source, [bots.item_key("g2", ""), bots.item_key("g3", "")]) == {bots.item_key("g2", "")}
    assert bs.source(w.db, w.source).last_error == "Slow down."
    w.run()
    assert w.posted == ["Item 1", "Item 3"]


def test_a_guild_that_refuses_gets_the_post_on_the_profile_only():
    w = World()
    w.answers = [br.outcome("guild", "403 Not Authorized - +news disallows bots from forwarding!")]
    w.run()
    assert w.calls == [("Item 1", ["news"]), ("Item 1", [])]
    assert w.posted == ["Item 1"] and "Posted without forwarding" in bs.source(w.db, w.source).last_error


def test_an_answer_that_the_bot_cant_post_pauses_it_and_tells_the_owner():
    w = World()
    w.answers = [br.outcome("stop", "An automatic check put the account on hold.")]
    assert w.run().startswith("stopped")
    assert bs.bot_of(w.db, 10).status == bots.PAUSED
    assert w.told and w.told[0][0] == 1 and "paused" in w.told[0][1]
    assert w.run() == "skipped"                                # a paused bot reads nothing


def test_failures_back_off_and_then_pause_the_source():
    w = World()
    w.fail = "The feed did not answer in time."
    for _ in range(bots.FAILS_BEFORE_PAUSE - 1):
        assert w.run() == "failed"
    src = bs.source(w.db, w.source)
    assert src.fail_count == bots.FAILS_BEFORE_PAUSE - 1 and src.next_check_utc > NOW + 3600
    assert w.run() == "paused"
    assert bs.source(w.db, w.source).status == bots.PAUSED and "stopped reading" in w.told[0][1]


def test_a_feed_that_is_not_a_feed_counts_as_a_failure():
    w = World()
    w.body = b"<html>nope</html>"
    assert w.run() == "failed" and bs.source(w.db, w.source).last_error


def test_an_owner_who_cant_run_it_stops_it_without_a_failure():
    w = World()
    assert w.run(owner_ok=False) == "owner"
    assert w.posted == [] and bs.source(w.db, w.source).fail_count == 0


def test_a_held_owner_skips_the_source_and_every_item_waits_for_the_hold_to_be_lifted():
    w = World()
    w.body = feed(1, 2, 3)
    assert w.run(owner_ok=False) == "owner"
    assert w.calls == [] and w.posted == [], "nothing was submitted"
    assert w.db.execute(text("SELECT COUNT(*) FROM bot_seen")).scalar() == 0, "no item was marked seen, so none is lost"
    src = bs.source(w.db, w.source)
    assert not src.read_once and src.fail_count == 0 and src.next_check_utc == NOW + 3600
    assert src.last_error == "The bot's owner can't run it right now."
    assert w.told == [], "the owner is not told or paused for a hold"
    assert bs.bot_of(w.db, 10).status == bots.APPROVED
    # the hold is lifted: the same feed is read as a first read and its newest items are posted
    assert w.run(owner_ok=True, now=NOW + 3600).endswith("3 posted")
    assert w.posted == ["Item 1", "Item 2", "Item 3"]


def test_a_refusal_that_comes_of_a_hold_that_landed_meanwhile_keeps_the_item_and_pauses_nothing():
    w = World()
    w.run()
    w.body = feed(1, 2)
    w.answers = [br.classify(403, "", {"error": quarantine.HELD_AUTOMATIC}, False)]
    assert w.run().endswith("0 posted")
    assert bs.seen_keys(w.db, w.source, [bots.item_key("g2", "")]) == set(), "not marked seen: it is tried again"
    assert bs.source(w.db, w.source).last_error == quarantine.HELD_AUTOMATIC
    assert bs.bot_of(w.db, 10).status == bots.APPROVED and w.told == []
    w.run()
    assert w.posted == ["Item 1", "Item 2"]


def test_the_runners_owner_check_asks_for_a_hold_too():
    source = (Path(__file__).resolve().parent.parent / "scripts" / "run_bots.py").read_text(encoding="utf-8")
    body = re.search(r"def owner_ok\(owner_id\):.*?\n\n\n", source, re.S).group(0)
    assert "owner.is_quarantined" in body and "owner.is_suspended" in body and "owner.is_deleted" in body


def test_the_daily_cap_holds_whatever_the_feed_has():
    w = World()
    day = bots.utc_day(NOW)
    for _ in range(bots.DAILY_CAP):
        bs.count_post(w.db, 10, day)
    w.run()
    assert w.posted == [] and "a day" in bs.source(w.db, w.source).last_error


# --- what the submit route's answer means --------------------------------------------------------------------

def test_classify():
    assert br.classify(200, "", {"id": "x"}, False, post_id=5) == br.outcome("posted", post_id=5)
    assert br.classify(409, "", {"error": "This content has already been posted"}, False).kind == "duplicate"
    assert br.classify(302, "/notifications", {}, False) == br.outcome("stop", "An automatic check put the account on hold.")
    assert br.classify(302, "/help/terms#agreebox", {}, False).kind == "stop"
    assert br.classify(302, "/post/abc/title", {}, False).kind == "duplicate"
    assert br.classify(403, "", {"error": "x", "bot_refusal": "wait"}, False).kind == "wait"
    assert br.classify(403, "", {"error": "x", "bot_refusal": "stop"}, False).kind == "stop"
    assert br.classify(403, "", {"error": "403 - +news disallows bots", "refusal": "guild"}, True).kind == "guild"
    assert br.classify(403, "", {"error": "403 - +news disallows bots", "refusal": "guild"}, False).kind == "refused"
    # read from the field the submit route sets, never from the words (they are in the reader's language)
    assert br.classify(403, "", {"error": "+news: Zugriff verweigert"}, True).kind == "refused"
    assert br.classify(429, "", {"error": "Slow down"}, False).kind == "wait"
    assert br.classify(503, "", {}, False).kind == "wait"
    assert br.classify(400, "", {"error": "banned domain"}, False) == br.outcome("refused", "banned domain")
    assert br.classify(401, "", {}, False).kind == "stop"


def test_the_answer_to_a_held_account_is_a_wait_never_a_refusal_that_burns_the_item():
    # @not_quarantined answers {"error": <the hold's words>, "held": true}: the signup guard's, an automatic check's, and a member bot's held owner
    for words in (quarantine.HELD, quarantine.HELD_AUTOMATIC, bots.OWNER_HELD):
        for forwarded in (False, True):
            assert br.classify(403, "", {"error": words}, forwarded) == br.outcome("wait", words)
    # the marker is what is read first: words in another language (a reader's own) are still a wait
    assert br.classify(403, "", {"error": "Su cuenta est\u00e1 retenida.", "held": True}, False).kind == "wait"
    assert br.classify(403, "", {"error": "Your account is suspended.", "held": False}, False).kind == "refused"
    # a member bot's held owner also says "stop" in the answer; the words win, since a hold can be lifted
    assert br.classify(403, "", {"error": bots.OWNER_HELD, "bot_refusal": "stop"}, False).kind == "wait"
    # any other 403 is what it was: a refusal of this item, or the bot's own stop
    assert br.classify(403, "", {"error": "This bot's owner can't run it right now.", "bot_refusal": "stop"}, False).kind == "stop"
    assert br.classify(403, "", {"error": "Your account is suspended."}, False).kind == "refused"
    assert br.classify(403, "", {}, False).kind == "refused"


# --- a site bot: run by the site, nobody to ask or tell ----------------------------------------------------------

class SiteWorld(World):
    def __init__(self):
        self.db = make()
        bs.add_bot(self.db, 10, None, "bbc_tech", "Headlines", [], NOW, kind=bots.SITE, proposed_by=1)
        self.source = bs.add_source(self.db, 10, "https://feeds.example.org/a.xml", NOW)
        bs.review_bot(self.db, 10, bots.APPROVED, 99, "", NOW)
        self.body = feed(1)
        self.fail = None
        self.posted, self.told, self.answers, self.calls = [], [], [], []


def test_a_site_bot_needs_no_owner_and_tells_nobody():
    w = SiteWorld()
    assert bs.bot_of(w.db, 10).owner_id is None
    assert w.run(owner_ok=False).endswith("1 posted")         # no owner to be in good standing
    w.body = feed(1, 2)
    w.answers = [br.outcome("stop", "Suspended.")]
    w.run()
    assert bs.bot_of(w.db, 10).status == bots.PAUSED and w.told == []


# --- stale items: promos a feed keeps and old stories put back ---------------------------------------------------

def _rss(*items):
    body = ""
    for guid, title, when in items:
        date = f"<pubDate>{_date_at(when)}</pubDate>" if when else ""
        body += f"<item><title>{title}</title><link>https://news.example/{guid}</link><guid>{guid}</guid>{date}</item>"
    return f"<rss><channel><title>News</title>{body}</channel></rss>".encode()


def _date_at(when):
    import email.utils
    return email.utils.formatdate(when, usegmt=True)


def test_an_item_dated_long_before_it_was_seen_is_never_posted():
    w = World()
    w.body = _rss(("promo", "Sign up now", NOW - 40 * 86400), ("old", "An old story", NOW - 21 * 86400),
                  ("new", "Today's story", NOW - 3600), ("undated", "No date given", None))
    w.run()
    assert w.posted == ["No date given", "Today's story"]          # oldest first; undated after dated in newest_first
    stale = [bots.item_key(g, "") for g in ("promo", "old")]
    assert bs.seen_keys(w.db, w.source, stale) == set(stale)       # seen, so never looked at again
    # a promo put back later under a new guid is skipped too
    w.posted.clear()
    w.body = _rss(("promo-2", "Sign up now", NOW - 40 * 86400), ("new", "Today's story", NOW - 3600),
                  ("newer", "A newer story", NOW))
    w.run()
    assert w.posted == ["A newer story"]



# --- how often a feed is read --------------------------------------------------------------------------------------

class PacedWorld(World):
    """Answers carry what the feed and its server ask, and a fetch can be refused with Retry-After."""

    def __init__(self):
        super().__init__()
        self.max_age, self.retry_after = 0, None

    def fetch(self, url, etag, last_modified):
        if self.retry_after is not None:
            busy = FetchError("The feed asked us to slow down.")
            busy.retry_after = self.retry_after
            raise busy
        if self.body is None:
            return Answer(False, b"", "", etag, last_modified, url, self.max_age)
        return Answer(True, self.body, "application/rss+xml", '"e"', "", url, self.max_age)


def _ttl(body, minutes):
    return body.replace(b"<channel>", f"<channel><ttl>{minutes}</ttl>".encode())


def _waits(w):
    src = bs.source(w.db, w.source)
    return src.next_check_utc - NOW, src.check_minutes, src.asked_minutes


def test_a_feed_that_asks_for_15_minutes_is_read_every_15():
    w = PacedWorld()
    w.body = _ttl(feed(1), 15)
    w.run()
    assert _waits(w) == (15 * 60, 15, 15)


def test_a_feed_that_asks_nothing_is_read_at_the_floor_and_a_tiny_max_age_cannot_go_under_it():
    w = PacedWorld()
    w.max_age = 2                                                         # the BBC's own header
    w.run()
    assert _waits(w) == (bots.FLOOR_MINUTES * 60, bots.FLOOR_MINUTES, 1)


def test_quiet_reads_stretch_the_wait_and_news_brings_it_back():
    w = PacedWorld()
    w.run()                                                               # item 1 is new
    assert _waits(w)[1] == bots.FLOOR_MINUTES
    w.run()                                                               # nothing new in the same feed
    assert _waits(w)[1] == 8
    w.body = None                                                         # a 304
    w.run()
    assert _waits(w)[1] == 12
    w.body = feed(1, 2)
    w.run()
    assert _waits(w)[1] == bots.FLOOR_MINUTES and "Item 2" in w.posted


def test_a_304_keeps_what_the_feed_asked_last_time():
    w = PacedWorld()
    w.body = _ttl(feed(1), 15)
    w.run()
    w.body = None
    w.run()
    wait, minutes, asked = _waits(w)
    assert asked == 15 and minutes == 23 and wait == 23 * 60              # stretched from 15, never under it


def test_retry_after_is_waited_for_exactly():
    w = PacedWorld()
    w.retry_after = "600"
    assert w.run() == "failed"
    src = bs.source(w.db, w.source)
    assert src.next_check_utc - NOW == 600 and src.fail_count == 1 and "slow down" in src.last_error
