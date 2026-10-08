"""Post insights (ruqqus/helpers/insights.py): the pure rules, without a database."""
import pytest

from ruqqus.helpers import insights as ins

DAY = 86400


# --- days and windows ------------------------------------------------------------------------------

def test_a_timestamp_falls_on_a_utc_day_and_a_day_starts_at_midnight_utc():
    assert ins.day_of(0) == 0 and ins.day_of(DAY - 1) == 0 and ins.day_of(DAY) == 1
    assert ins.day_of(1_760_000_000.9) == 1_760_000_000 // DAY            # fractional time is fine
    assert ins.day_start(ins.day_of(1_760_000_000)) <= 1_760_000_000 < ins.day_start(ins.day_of(1_760_000_000)) + DAY


@pytest.mark.parametrize("raw,expected", [
    (None, 7), ("", 7), ("7", 7), ("28", 28), (28, 28), (" 28 ", 28),
    ("29", 7), ("0", 7), ("-28", 7), ("abc", 7), ("28.0", 7), ("2٨", 7), ("1;28", 7),
])
def test_the_period_is_seven_or_twenty_eight_days(raw, expected):
    assert ins.parse_days(raw) == expected
    assert ins.RANGES == (7, 28) and ins.DEFAULT_DAYS == 7


def test_the_window_is_the_days_ending_today_oldest_first():
    assert ins.window(3, 100) == [98, 99, 100]
    assert len(ins.window(28, 20000)) == 28 and ins.window(28, 20000)[-1] == 20000


def test_the_series_has_every_day_of_the_window_zero_where_nothing_happened():
    rows = ins.series({98: 5, 100: 2, 7: 99}, 3, 100)                      # a day outside the window is left out
    assert rows == [(98, 5), (99, 0), (100, 2)]
    assert ins.series({}, 2, 10) == [(9, 0), (10, 0)]


def test_a_day_is_labelled_in_utc():
    assert ins.label(0) == "1 Jan"
    assert ins.label(ins.day_of(1_760_000_000)) == "9 Oct"                  # 2025-10-09 08:53 UTC


# --- the bars ------------------------------------------------------------------------------------------

def test_the_biggest_bar_is_full_and_a_single_view_is_still_seen():
    assert ins.bar_heights([0, 5, 10]) == [0, 50, 100]
    assert ins.bar_heights([1, 1000]) == [4, 100]                          # never under the floor for something
    assert ins.bar_heights([0, 0, 0]) == [0, 0, 0] and ins.bar_heights([]) == []
    assert ins.bar_heights([3]) == [100]


# --- who is counted ----------------------------------------------------------------------------------------

@pytest.mark.parametrize("agent", [
    "Mozilla/5.0 (compatible; Googlebot/2.1; +http://www.google.com/bot.html)",
    "Mozilla/5.0 (compatible; bingbot/2.0)", "Discordbot/2.0", "TelegramBot (like TwitterBot)", "facebookexternalhit/1.1",
    "Slackbot-LinkExpanding 1.0", "curl/8.4.0", "python-requests/2.31", "Wget/1.21", "Go-http-client/2.0",
    "Mozilla/5.0 HeadlessChrome/120", "WhatsApp/2.23.20", "Twitterbot/1.0", "AhrefsBot/7.0", "Lighthouse", "", "   ", None,
])
def test_robots_link_previews_and_no_user_agent_are_not_views(agent):
    assert ins.is_robot(agent)


@pytest.mark.parametrize("agent", [
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0 Safari/537.36",
    "Mozilla/5.0 (iPhone; CPU iPhone OS 17_1 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.1 Mobile/15E148 Safari/604.1",
    "Mozilla/5.0 (X11; Linux x86_64; rv:121.0) Gecko/20100101 Firefox/121.0",
])
def test_an_ordinary_browser_is_a_view(agent):
    assert not ins.is_robot(agent)


def test_a_prefetch_is_not_a_view():
    assert ins.is_prefetch({"Purpose": "prefetch"}) and ins.is_prefetch({"Sec-Purpose": "prefetch;prerender"})
    assert ins.is_prefetch({"X-Moz": "prefetch"})
    assert not ins.is_prefetch({}) and not ins.is_prefetch({"Purpose": ""})


def test_the_same_viewer_is_told_apart_without_keeping_who_they_are():
    assert ins.viewer_key(7, "1.2.3.4", "agent", "s") == "u7" == ins.viewer_key(7, "9.9.9.9", "other", "t")   # an account: its id
    visitor = ins.viewer_key(None, "1.2.3.4", "agent", "secret")
    assert visitor.startswith("a") and len(visitor) == 21
    assert visitor == ins.viewer_key(None, "1.2.3.4", "agent", "secret")
    assert visitor != ins.viewer_key(None, "1.2.3.5", "agent", "secret")
    assert visitor != ins.viewer_key(None, "1.2.3.4", "agent2", "secret")
    assert visitor != ins.viewer_key(None, "1.2.3.4", "agent", "other secret")        # salted: not a bare hash of the address
    assert "1.2.3.4" not in visitor and "agent" not in visitor
    assert ins.DEBOUNCE_SECONDS == 1800


# --- numbers -----------------------------------------------------------------------------------------------

def test_percent_and_net():
    assert ins.percent(1, 3) == 33 and ins.percent(2, 3) == 67 and ins.percent(5, 0) == 0
    assert ins.net(10, 3) == 7 and ins.net(0, 4) == -4 and ins.net("5", "2") == 3
