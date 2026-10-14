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
    assert bots.next_check(NOW, 1, 0) == NOW + bots.CHECK_MINUTES_MIN * 60
    assert bots.next_check(NOW, 30, 2) == NOW + 1800 * 4
    assert bots.next_check(NOW, 30, 50) == NOW + bots.BACKOFF_MAX
    assert bots.parse_check_minutes("5") == bots.CHECK_MINUTES_MIN and bots.parse_check_minutes("x") == 30


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
