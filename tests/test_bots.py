"""Bot accounts, the rules (ruqqus/helpers/bots.py)."""
from types import SimpleNamespace as NS

import pytest

from ruqqus.helpers import bots

NOW = 1_800_000_000
OLD = NOW - bots.OWNER_MIN_AGE - 1


def member(created=OLD, **kw):
    return NS(created_utc=created, is_bot=kw.get("is_bot", False), is_deleted=kw.get("is_deleted", False),
              is_suspended=kw.get("is_suspended", False))


# --- who may apply -------------------------------------------------------------------------------------------

def test_a_member_in_good_standing_may_apply_up_to_the_limit():
    assert bots.owner_refusal(member(), 0, NOW) is None
    assert bots.owner_refusal(member(), bots.MAX_BOTS - 1, NOW) is None
    assert "at most" in bots.owner_refusal(member(), bots.MAX_BOTS, NOW)


@pytest.mark.parametrize("who,words", [
    (member(created=NOW - 86400), "30 days"),
    (member(is_suspended=True), "suspended"),
    (member(is_bot=True), "A bot can't run bots"),
    (member(is_deleted=True), "Sign in"),
    (None, "Sign in"),
])
def test_who_may_not_apply(who, words):
    assert words in bots.owner_refusal(who, 0, NOW)


def test_a_bot_name_follows_the_sign_up_rule():
    assert bots.parse_username(" @bbc_tech ") == ("bbc_tech", None)
    for bad in ("ab", "x" * 26, "has space", "dash-name", "", None, "émoji"):
        assert bots.parse_username(bad)[1]


def test_the_purpose_and_guild_names():
    assert bots.parse_purpose("  BBC technology headlines  ")[0] == "BBC technology headlines"
    assert bots.parse_purpose("short")[1]
    assert bots.parse_guild_names("+news, Tech\n+news") == (["news", "Tech"], None)
    assert bots.parse_guild_names("")[0] == []
    assert bots.parse_guild_names("not a/guild")[1]
    assert bots.parse_guild_names(",".join(f"g{i}" for i in range(bots.MAX_FORWARD_GUILDS + 1)))[1]


# --- feed addresses ------------------------------------------------------------------------------------------

def test_a_feed_address_is_https_with_a_host_and_no_credentials():
    assert bots.parse_feed_url(" https://feeds.bbci.co.uk/news/technology/rss.xml ", site_host="ruqqus.example")[0] \
        == "https://feeds.bbci.co.uk/news/technology/rss.xml"
    for bad in ("http://example.org/feed", "ftp://example.org/feed", "https://user:pw@example.org/feed",
                "https:///nohost", "https://example.org/a b", "", "https://example.org:notaport/",
                "https://example.org/" + "x" * bots.URL_MAX):
        assert bots.parse_feed_url(bad, site_host="ruqqus.example")[1], bad


def test_plain_http_only_for_local_testing():
    assert bots.parse_feed_url("http://127.0.0.1:8000/feed.xml", allow_local=True, site_host="")[1] is None


def test_this_site_is_never_a_source():
    for url in ("https://ruqqus.example/feeds/x", "https://www.ruqqus.example/x", "https://RUQQUS.example/"):
        assert bots.parse_feed_url(url, site_host="ruqqus.example")[1] == "A bot can't read this site itself."
    assert bots.parse_feed_url("https://notruqqus.example/x", site_host="ruqqus.example")[1] is None


# --- posting -------------------------------------------------------------------------------------------------

def bot(status=bots.APPROVED):
    return NS(status=status)


def test_only_an_approved_bot_with_an_owner_in_good_standing_posts():
    assert bots.post_refusal(bot(), member(), 0, is_on=True) is None
    assert bots.post_refusal(bot(bots.PENDING), member(), 0, is_on=True) == "This bot has not been approved."
    assert bots.post_refusal(bot(bots.PAUSED), member(), 0, is_on=True) == "This bot is not running."
    assert bots.post_refusal(None, member(), 0, is_on=True)
    assert "owner" in bots.post_refusal(bot(), member(is_suspended=True), 0, is_on=True)
    assert "owner" in bots.post_refusal(bot(), None, 0, is_on=True)
    assert "a day" in bots.post_refusal(bot(), member(), bots.DAILY_CAP, is_on=True)
    assert "switched off" in bots.post_refusal(bot(), member(), 0, is_on=False)


def test_the_switch_reads_the_environment(monkeypatch):
    monkeypatch.setenv("BOTS_ENABLED", "0")
    assert not bots.enabled()
    monkeypatch.setenv("BOTS_ENABLED", "1")
    assert bots.enabled()
    monkeypatch.delenv("BOTS_ENABLED")
    assert bots.enabled()


def test_a_bot_post_is_public_signed_and_its_own():
    assert bots.option_refusal(False, False, False) is None
    assert "anonymously" in bots.option_refusal(True, False, False)
    assert "public" in bots.option_refusal(False, True, False)
    assert "co-authors" in bots.option_refusal(False, False, True)


def test_each_bot_posts_from_its_own_loopback_address():
    addresses = {bots.loopback_for(i) for i in range(1, 70000)}
    assert len(addresses) == 69999
    for address in addresses:
        parts = [int(x) for x in address.split(".")]
        assert parts[0] == 127 and 1 <= parts[3] <= 254
    assert "127.0.0.1" not in addresses            # the drafts publisher's address


def test_reading_backs_off_after_failures_and_never_waits_more_than_a_day():
    assert bots.next_check(NOW, 30, 0) == NOW + 1800
    assert bots.next_check(NOW, 1, 0) == NOW + bots.FLOOR_MINUTES * 60          # never under the floor
    assert bots.next_check(NOW, 30, 2) == NOW + 1800 * 4
    assert bots.next_check(NOW, 30, 50) == NOW + bots.BACKOFF_MAX
    assert bots.parse_check_minutes("1") == bots.FLOOR_MINUTES and bots.parse_check_minutes("x") == bots.FLOOR_MINUTES


def test_an_item_key_prefers_the_guid_and_is_stable():
    assert bots.item_key("abc", "https://a/1") == bots.item_key("abc", "https://b/2")
    assert bots.item_key("", "https://a/1") == bots.item_key(None, " https://a/1 ")
    assert len(bots.item_key("x", "y")) == 64


def test_a_title_is_plain_text_on_one_line_and_cut_to_the_limit():
    assert bots.clean_title("  <b>Chips</b> &amp; phones\n\tnews ") == "Chips & phones news"
    assert bots.clean_title("&lt;script&gt;alert(1)&lt;/script&gt; Hi") == "alert(1) Hi"
    long = bots.clean_title("word " * 200)
    assert len(long) == bots.TITLE_MAX and long.endswith("…")
    assert bots.clean_title(None) == ""


# --- the two kinds: site bots (free, the site's) and member bots (their member's) --------------------------------

def test_a_site_bot_reads_a_feed_and_a_member_bot_may_not():
    assert bots.parse_kind("site") == bots.SITE and bots.parse_kind("member") == bots.MEMBER
    assert bots.parse_kind("admin") is None and bots.parse_kind(None) is None
    assert "at least one feed" in bots.kind_refusal(bots.SITE, 0)
    assert bots.kind_refusal(bots.SITE, 1) is None
    assert bots.kind_refusal(bots.MEMBER, 0) is None             # it may post only with its member's key
    assert bots.kind_refusal(None, 3)


def proposal(kind, status=bots.PENDING, owner=None, proposer=1):
    return NS(kind=kind, status=status, owner_id=owner, proposed_by_id=proposer)


def test_who_may_change_a_bot():
    mine = proposal(bots.MEMBER, bots.APPROVED, owner=1)
    assert bots.may_manage(mine, 1, False) and not bots.may_manage(mine, 2, False)
    assert bots.may_manage(mine, 2, True)                          # an admin, any bot
    site = proposal(bots.SITE)
    assert bots.may_manage(site, 1, False)                         # its proposer, while it waits
    assert not bots.may_manage(site, 2, False)
    for status in (bots.APPROVED, bots.PAUSED):                    # then it is the site's
        assert not bots.may_manage(proposal(bots.SITE, status), 1, False)
        assert bots.may_manage(proposal(bots.SITE, status), 2, True)
    assert not bots.may_manage(proposal(bots.SITE, bots.REJECTED), 2, True)
    assert not bots.may_manage(None, 1, True) and not bots.may_manage(site, None, False)


def test_who_is_told_about_a_bot():
    assert bots.responsible_id(proposal(bots.MEMBER, owner=5, proposer=5)) == 5
    assert bots.responsible_id(proposal(bots.SITE, proposer=7)) == 7
    assert bots.responsible_id(None) is None


def test_admins_propose_without_the_age_or_the_count():
    admin = member(created=NOW - 60)
    admin.admin_level = 3
    assert bots.is_admin(admin) and not bots.is_admin(member()) and not bots.is_admin(None)
    assert bots.owner_refusal(admin, bots.MAX_BOTS + 5, NOW) is None
    admin.is_suspended = True
    assert "suspended" in bots.owner_refusal(admin, 0, NOW)


def test_a_site_bot_posts_with_nobody_behind_it():
    site = NS(status=bots.APPROVED, kind=bots.SITE)
    assert bots.post_refusal(site, None, 0, is_on=True) is None
    assert "a day" in bots.post_refusal(site, None, bots.DAILY_CAP, is_on=True)
    assert "owner" in bots.post_refusal(NS(status=bots.APPROVED, kind=bots.MEMBER), None, 0, is_on=True)


def test_a_bot_picture_is_one_of_the_usual_formats():
    assert set(bots.IMAGE_FORMATS) == {"PNG", "JPEG", "GIF", "WEBP"}             # never SVG
    assert bots.IMAGE_MAX_BYTES == 8 * 1024 * 1024


# --- links and dates of feed items --------------------------------------------------------------------------------

def test_a_link_loses_its_tracking_parameters_and_keeps_the_rest():
    bbc = "https://www.bbc.co.uk/news/articles/c8wy9veedgygo?at_medium=RSS&at_campaign=rss"
    assert bots.clean_link(bbc) == "https://www.bbc.co.uk/news/articles/c8wy9veedgygo"
    assert bots.clean_link("https://example.org/a?id=7&utm_source=x&UTM_Medium=y&page=2") == "https://example.org/a?id=7&page=2"
    assert bots.clean_link("https://www.youtube.com/watch?v=abc&si=share1&fbclid=z") == "https://www.youtube.com/watch?v=abc&si=share1"
    assert bots.clean_link("https://github.com/o/r/blob/x?ref=main") == "https://github.com/o/r/blob/x?ref=main"
    assert bots.clean_link("https://example.org/a?gclid=1#part-2") == "https://example.org/a#part-2"
    assert bots.clean_link("https://example.org/plain") == "https://example.org/plain"
    assert bots.clean_link("https://example.org/a?flag&utm_id=3") == "https://example.org/a?flag"


def test_an_item_dated_more_than_two_days_back_is_stale():
    assert not bots.is_stale(NOW - bots.STALE_AFTER + 60, NOW)
    assert bots.is_stale(NOW - bots.STALE_AFTER - 60, NOW)
    assert bots.is_stale(NOW - 40 * 86400, NOW)                  # a promo the feed keeps
    assert not bots.is_stale(None, NOW) and not bots.is_stale(0, NOW)   # undated: never stale
    assert not bots.is_stale(NOW + 3600, NOW)                    # a date ahead of the clock


# --- how often a feed is read: as often as it safely can be ------------------------------------------------------

def test_what_a_feed_asks_for():
    assert bots.feed_asks("15") == 15                                    # the BBC's <ttl>
    assert bots.feed_asks(None, "hourly", "2") == 30                      # sy:updatePeriod / sy:updateFrequency
    assert bots.feed_asks(None, "Daily") == 1440
    assert bots.feed_asks("15", "hourly", "1") == 60                      # the longer of the two
    assert bots.feed_asks("", "", "") == 0 and bots.feed_asks("soon") == 0 and bots.feed_asks("-5") == 0
    assert bots.feed_asks(None, "yearly") == bots.ASK_MAX_MINUTES         # never left alone more than a day


def test_what_its_server_asks_for():
    assert bots.server_asks(2) == 1                                       # the BBC's max-age=2 rounds up to a minute
    assert bots.server_asks(900) == 15 and bots.server_asks(0) == 0 and bots.server_asks(None) == 0
    assert bots.server_asks(10 ** 9) == bots.ASK_MAX_MINUTES


def test_the_shortest_allowed_is_the_floor_or_the_ask_whichever_is_longer():
    assert bots.next_interval(0, 30, got_new=True) == bots.FLOOR_MINUTES
    assert bots.next_interval(1, 30, got_new=True) == bots.FLOOR_MINUTES   # a tiny max-age can't go under the floor
    assert bots.next_interval(15, 5, got_new=True) == 15                  # the BBC case
    assert bots.next_interval(0, 0, got_new=False) == bots.FLOOR_MINUTES  # a first read


def test_a_quiet_feed_is_read_less_often_and_comes_back_when_it_has_news():
    minutes, seen = bots.FLOOR_MINUTES, []
    for _ in range(12):
        minutes = bots.next_interval(0, minutes, got_new=False)
        seen.append(minutes)
    assert seen[:4] == [8, 12, 18, 27] and seen[-1] == bots.QUIET_MAX_MINUTES and max(seen) == bots.QUIET_MAX_MINUTES
    assert bots.next_interval(0, minutes, got_new=True) == bots.FLOOR_MINUTES
    assert bots.next_interval(15, 15, got_new=False) == 23                # stretched from what the feed asks
    assert bots.next_interval(120, 120, got_new=False) == 120             # an ask longer than the quiet cap is kept


def test_retry_after_is_obeyed_in_seconds_or_as_a_date_up_to_a_day():
    import email.utils

    assert bots.retry_wait("600", NOW) == 600
    assert bots.retry_wait(email.utils.formatdate(NOW + 120, usegmt=True), NOW) == 120
    assert bots.retry_wait("999999999", NOW) == bots.BACKOFF_MAX
    assert bots.retry_wait(email.utils.formatdate(NOW - 50, usegmt=True), NOW) == 0
    assert bots.retry_wait("", NOW) is None and bots.retry_wait(None, NOW) is None and bots.retry_wait("later", NOW) is None


def test_the_bot_page_says_how_a_feed_is_read():
    assert bots.cadence_label(15, 15) == "Read every 15 minutes (the feed asks for at least 15)"
    assert bots.cadence_label(5, 0) == "Read every 5 minutes"
    assert bots.cadence_label(27, 0) == "Read every 27 minutes (quiet lately)"
    assert bots.cadence_label(5, 0, bots.PENDING) == ""


def test_every_name_and_address_of_this_site_is_refused(monkeypatch):
    # audit B-01: the short domain, the onion name and the origin's addresses too, not only the main name
    monkeypatch.setenv("SERVER_NAME", "laba.example")
    monkeypatch.setenv("SHORT_DOMAIN", "lb.ex")
    monkeypatch.setenv("ONION_NAME", "abc.onion")
    monkeypatch.setenv("ORIGIN_IPS", "203.0.113.7, 2001:db8::1")
    for host in ("laba.example", "www.laba.example", "lb.ex", "abc.onion", "203.0.113.7", "2001:db8::1"):
        assert bots.is_own_host(host), host
    assert not bots.is_own_host("bbc.co.uk")
    assert bots.parse_feed_url("https://lb.ex/feed")[1] == "A bot can't read this site itself."


# --- a member's own account that posts automatically (SELF) ------------------------------------------------------

def own(status=bots.APPROVED, owner=7, kind=bots.SELF):
    return NS(kind=kind, status=status, owner_id=owner, proposed_by_id=owner, user_id=owner, username="ann")


def test_a_members_own_account_is_a_third_kind_that_reads_feeds():
    assert bots.parse_kind("self") == bots.SELF and bots.SELF in bots.KINDS
    assert bots.KIND_LABELS[bots.SELF] == "Own account"
    assert bots.kind_refusal(bots.SELF, 0) and bots.kind_refusal(bots.SELF, 1) is None
    assert bots.is_self(own()) and not bots.is_self(own(kind=bots.MEMBER)) and not bots.is_self(None)


def test_one_per_account_apart_from_the_bots_a_member_runs():
    assert bots.self_refusal(member(), False, NOW) is None
    assert "already" in bots.self_refusal(member(), True, NOW)
    assert bots.self_refusal(member(created=NOW - 86400), False, NOW) ==         "Your account needs to be at least 30 days old to post automatically."
    assert "suspended" in bots.self_refusal(member(is_suspended=True), False, NOW)
    assert "Sign in" in bots.self_refusal(None, False, NOW)
    assert "A bot can't" in bots.self_refusal(member(is_bot=True), False, NOW)


def test_only_the_member_and_admins_manage_their_own_accounts_posting_and_the_member_is_told():
    assert bots.may_manage(own(), 7, False) and bots.may_manage(own(), 1, True)
    assert not bots.may_manage(own(), 8, False) and not bots.may_manage(own(), None, False)
    assert not bots.may_manage(own(bots.REJECTED), 7, False)
    assert bots.responsible_id(own()) == 7
    assert bots.subject(own()) == "Your account's automated posting"
    assert bots.subject(own(kind=bots.MEMBER)) == "Your bot @ann" and bots.subject(own(kind=bots.MEMBER), capital=False) == "your bot @ann"


def test_the_member_must_be_in_good_standing_for_their_feeds_to_post():
    assert bots.post_refusal(own(), member(), 0, is_on=True) is None
    assert "owner" in bots.post_refusal(own(), member(is_suspended=True), 0, is_on=True)
    assert "owner" in bots.post_refusal(own(), member(is_deleted=True), 0, is_on=True)
    assert "a day" in bots.post_refusal(own(), member(), bots.DAILY_CAP, is_on=True)
    assert bots.post_refusal(own(bots.PAUSED), member(), 0, is_on=True) == "This bot is not running."


@pytest.mark.parametrize("account_is_bot, says_bot, row, automated", [
    (True, False, None, True),                        # every post of a bot account
    (True, True, own(kind=bots.MEMBER), True),
    (False, True, own(), True),                       # a header post from an account that posts automatically
    (False, False, own(), False),                     # the person's own posts, made by hand
    (False, True, None, False),                       # a header alone changes nothing: only marked, as before
    (False, True, own(kind=bots.MEMBER), False),      # (never the case: a person's account has no member-bot row)
])
def test_which_posts_are_automated(account_is_bot, says_bot, row, automated):
    assert bots.is_automated(account_is_bot, says_bot, row) is automated
