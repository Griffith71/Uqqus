"""May another site's video player load before the reader presses play? The rules in helpers/consent.py, and the browser's copy of
how the cookie reads in assets/js/consent.js, run under node on the same cases (CLAUDE.md "Video consent")."""
import json
import shutil
import subprocess
from pathlib import Path

import pytest

from ruqqus.helpers import consent

SCRIPT = Path(__file__).resolve().parent.parent / "ruqqus" / "assets" / "js" / "consent.js"
NOW = 1_800_000_000


def cookie(choice="yes", date=NOW, version=consent.VERSION):
    return "%s.%s.%s" % (version, choice, date)


# --- who is asked --------------------------------------------------------------------------------------------------------

@pytest.mark.parametrize("country", ["GB", "IE", "DE", "FR", "NO", "IS", "LI", "CH", "RE", "GF", "AX", "JE", "GI"])
def test_the_uk_the_eea_and_switzerland_are_asked(country):
    assert consent.needs_asking(country) is True


@pytest.mark.parametrize("country", ["US", "CA", "BR", "JP", "AU", "IN", "ZA", "TR", "UA"])
def test_everywhere_else_is_not(country):
    assert consent.needs_asking(country) is False


@pytest.mark.parametrize("country", [None, "", "T1", "XX", "ZZ", "EU", "AP", "A1", "us", "gb", "USA", "U", "G B", "GB\n", "<b", 7, b"US"])
def test_a_country_that_is_not_known_means_ask(country):
    assert consent.needs_asking(country) is True


def test_the_eu_is_all_there():
    eu = "AT BE BG HR CY CZ DK EE FI FR DE GR HU IE IT LV LT LU MT NL PL PT RO SK SI ES SE".split()
    assert len(eu) == 27 and set(eu) <= consent.REGIONS


# --- the cookie ----------------------------------------------------------------------------------------------------------

def test_a_choice_is_read_back():
    assert consent.read_cookie(cookie("yes"), NOW) == "yes"
    assert consent.read_cookie(cookie("no"), NOW) == "no"
    assert consent.read_cookie(cookie("yes", NOW - 86400), NOW) == "yes"


def test_a_choice_lasts_about_six_months():
    assert 180 * 86400 <= consent.REASK_SECONDS <= 186 * 86400
    assert consent.read_cookie(cookie("no", NOW - consent.REASK_SECONDS + 1), NOW) == "no"
    assert consent.read_cookie(cookie("no", NOW - consent.REASK_SECONDS), NOW) is None


def test_a_browser_clock_a_little_ahead_is_kept_and_a_date_far_ahead_is_not():
    assert consent.read_cookie(cookie("yes", NOW + 3600), NOW) == "yes"
    assert consent.read_cookie(cookie("yes", NOW + consent.CLOCK_SLACK + 1), NOW) is None


@pytest.mark.parametrize("value", [None, "", "yes", "1.yes", "1.maybe.1800000000", "0.yes.1800000000", "2.yes.1800000000", "x.yes.1800000000",
                                   "1.yes.abc", "1.YES.1800000000", "1.yes.1800000000.1", "1.yes.1800000000\n", " 1.yes.1800000000",
                                   "1.yes.-5", "99999.yes.1800000000", "1.yes.123456789012", 5])
def test_anything_else_is_no_answer(value):
    assert consent.read_cookie(value, NOW) is None


# --- the cookie the server sets ------------------------------------------------------------------------------------------

def test_the_server_writes_what_is_read_back():
    assert consent.cookie_value("yes", NOW) == "%d.yes.%d" % (consent.VERSION, NOW)
    assert consent.cookie_value("no", NOW + 0.9) == "%d.no.%d" % (consent.VERSION, NOW)         # whole seconds, its own clock
    for choice in ("yes", "no"):
        assert consent.read_cookie(consent.cookie_value(choice, NOW), NOW) == choice
        assert consent.state("GB", consent.cookie_value(choice, NOW), NOW) == choice


@pytest.mark.parametrize("sent", [None, "", " yes", "yes ", "YES", "No", "maybe", "ask", "1", "1.yes.1800000000", "yes; Domain=evil.example", "yes\r\nSet-Cookie: session_ruqqus=x",
                                  "yes\n", "session_ruqqus=x", "yes;", b"yes", ["yes"], ("no",), True, 1])
def test_nothing_else_makes_a_cookie(sent):
    assert consent.cookie_value(sent, NOW) is None


# --- what <html> says ----------------------------------------------------------------------------------------------------

def test_someone_who_has_to_be_asked_and_has_not_answered_is_asked():
    assert consent.state("GB", None, NOW) == consent.ASK
    assert consent.state(None, None, NOW) == consent.ASK
    assert consent.state("T1", "rubbish", NOW) == consent.ASK
    assert consent.state("DE", cookie("yes", NOW - consent.REASK_SECONDS), NOW) == consent.ASK    # six months on: asked again


def test_anyone_else_is_not_named_at_all():
    assert consent.state("US", None, NOW) == ""
    assert consent.state("US", "rubbish", NOW) == ""


def test_an_answer_counts_wherever_the_reader_is_now():
    for country in ("GB", "US", None, "T1"):
        assert consent.state(country, cookie("yes"), NOW) == consent.YES
        assert consent.state(country, cookie("no"), NOW) == consent.NO


def test_a_no_that_lapsed_is_asked_again_wherever_the_reader_is():
    lapsed = cookie("no", NOW - consent.REASK_SECONDS)
    for country in ("US", "GB", None):
        assert consent.state(country, lapsed, NOW) == consent.ASK, country
    assert consent.state("US", cookie("no", NOW, 0), NOW) == consent.ASK        # an older version of the question: the same "no"


def test_a_yes_that_lapsed_outside_the_regions_is_nothing():
    assert consent.state("US", cookie("yes", NOW - consent.REASK_SECONDS), NOW) == ""
    assert consent.state("US", cookie("yes", NOW, 0), NOW) == ""


def test_the_cookie_outlives_the_answer_or_a_lapsed_no_could_never_be_read():
    # a browser deletes a cookie at its max-age, so one that ended with the answer would reach the server as no answer at all
    assert consent.KEEP_SECONDS >= consent.REASK_SECONDS + 180 * 86400
    assert consent.KEEP_SECONDS <= 400 * 86400                                  # Chrome's most: asking for more is not more


def test_only_the_three_words_are_ever_drawn():
    # the value goes into the page's <html>: nothing from the header or the cookie's text gets there
    for country in ('"><script>', "GB", "US"):
        for value in ('1.yes.1800000000"><script>', "1.no.1800000000", None):
            assert consent.state(country, value, NOW) in ("ask", "yes", "no", "")


# --- who a player belongs to ---------------------------------------------------------------------------------------------

@pytest.mark.parametrize("address,name", [
    ("https://www.youtube-nocookie.com/embed/abc", "YouTube"), ("https://youtube.com/embed/abc", "YouTube"), ("https://player.vimeo.com/video/1", "Vimeo"),
    ("https://www.dailymotion.com/embed/video/x1", "Dailymotion"), ("https://player.twitch.tv/?channel=x", "Twitch"),
    ("https://open.spotify.com/embed/track/1", "Spotify"), ("//www.tiktok.com/player/v1/1", "TikTok"),
    ("https://cdn.example.org/clip.mp4", "cdn.example.org"), ("https://www.example.org/clip.mp4", "example.org"),
])
def test_a_player_is_named_for_its_host(address, name):
    assert consent.player_site(address) == name


@pytest.mark.parametrize("address", ["https://youtube.com.evil.example/embed/x", "https://notyoutube.com/embed/x", "https://evil.example/youtube.com/x",
                                     "https://youtube.com@evil.example/x"])
def test_only_the_real_host_counts(address):
    assert consent.player_site(address) not in ("YouTube",)


@pytest.mark.parametrize("address", [None, "", "/media/1/x.mp4", "javascript:alert(1)"])
def test_an_address_with_no_host_names_nothing(address):
    assert consent.player_site(address) == ""


# --- the browser reads the cookie the same way -----------------------------------------------------------------------------

node = pytest.mark.skipif(shutil.which("node") is None, reason="node is not installed")


def js(expression):
    code = "const c = require(%s); console.log(JSON.stringify(%s));" % (json.dumps(str(SCRIPT)), expression)
    out = subprocess.run(["node", "-e", code], capture_output=True, text=True, timeout=60)
    assert out.returncode == 0, out.stderr
    return json.loads(out.stdout)


@node
def test_the_two_copies_have_the_same_numbers():
    assert js("c.VERSION") == consent.VERSION
    assert js("c.REASK_SECONDS") == consent.REASK_SECONDS
    assert js("c.KEEP_SECONDS") == consent.KEEP_SECONDS
    assert js("c.COOKIE") == consent.COOKIE
    assert js("c.ANSWER_PATH") == consent.ANSWER_PATH
    assert js("c.GOOGLE_WAIT_MS") == 5000


@node
@pytest.mark.parametrize("value", [
    cookie("yes"), cookie("no"), cookie("yes", NOW - 86400), cookie("no", NOW - consent.REASK_SECONDS + 1), cookie("no", NOW - consent.REASK_SECONDS),
    cookie("yes", NOW + 3600), cookie("yes", NOW + consent.CLOCK_SLACK + 1), cookie("yes", NOW, 0), cookie("yes", NOW, 2),
    "1.maybe.1800000000", "yes", "1.yes", "x.yes.1800000000", "1.YES.1800000000", "1.yes.abc", "1.yes.1800000000.1",
])
def test_the_browser_gives_the_same_answer(value):
    assert js("c.readChoice(%s, %d)" % (json.dumps("session_ruqqus=abc; video_consent=%s; density=compact" % value), NOW)) == consent.read_cookie(value, NOW), value
    assert js("c.readChoice(%s, %d)" % (json.dumps("video_consent=%s" % value), NOW)) == consent.read_cookie(value, NOW), value


@node
def test_another_cookie_with_a_like_name_is_not_read():
    assert js("c.readChoice('not_video_consent=1.yes.%d', %d)" % (NOW, NOW)) is None
    assert js("c.readChoice('', %d)" % NOW) is None
    assert js("c.readChoice(null, %d)" % NOW) is None


@node
@pytest.mark.parametrize("skew", [0, -30 * 86400, 200 * 86400, -4000 * 86400])
def test_the_text_of_an_answer_doesnt_depend_on_any_clock(skew):
    """What a page the browser kept goes by, since the date in the cookie is usually the server's and the browser's clock may not be able to place it."""
    written = cookie("no", NOW + skew)
    text = js("c.cookieText(%s)" % json.dumps("session_ruqqus=abc; video_consent=%s; density=compact" % written))
    assert text == written
    assert js("c.choiceIn(%s)" % json.dumps(text)) == "no"


@node
@pytest.mark.parametrize("value", ["yes", "1.yes", "x.yes.1800000000", "1.maybe.1800000000", "1.yes.abc", cookie("yes", NOW, consent.VERSION + 1)])
def test_only_this_version_of_the_question_has_a_text(value):
    assert js("c.cookieText(%s)" % json.dumps("video_consent=%s" % value)) is None
    assert js("c.choiceIn(null)") is None


@node
def test_no_cookie_has_no_text():
    assert js("c.cookieText('')") is None and js("c.cookieText(null)") is None
    assert js("c.cookieText('not_video_consent=1.yes.%d')" % NOW) is None


@node
def test_what_the_browser_writes_the_server_reads():
    written = js("c.cookieValue('no', %d.7, false)" % NOW)
    assert consent.read_cookie(written.split(";")[0], NOW) == "no"
    assert "path=/" in written and "max-age=%d" % consent.KEEP_SECONDS in written and "samesite=lax" in written and "secure" not in written
    assert "; secure" in js("c.cookieValue('yes', %d, true)" % NOW)


@node
@pytest.mark.parametrize("choice", ["yes", "no"])
def test_what_the_server_writes_the_browser_reads(choice):
    written = consent.cookie_value(choice, NOW)
    assert js("c.readChoice(%s, %d)" % (json.dumps("session_ruqqus=abc; video_consent=%s; density=compact" % written), NOW)) == choice
    assert js("c.cookieValue('%s', %d, false)" % (choice, NOW)).split(";")[0] == written       # the same text from either side
