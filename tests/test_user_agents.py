"""helpers/user_agents.py: a User-Agent header as one of a few known labels, never the raw text."""
import pytest

from ruqqus.helpers import user_agents

CHROME_WINDOWS = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) "
                  "Chrome/126.0.0.0 Safari/537.36")
EDGE_WINDOWS = CHROME_WINDOWS + " Edg/126.0.0.0"
FIREFOX_LINUX = "Mozilla/5.0 (X11; Linux x86_64; rv:127.0) Gecko/20100101 Firefox/127.0"
SAFARI_MAC = ("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605.1.15 (KHTML, like Gecko) "
              "Version/17.5 Safari/605.1.15")
SAFARI_IPHONE = ("Mozilla/5.0 (iPhone; CPU iPhone OS 17_5 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) "
                 "Version/17.5 Mobile/15E148 Safari/604.1")
CHROME_ANDROID = ("Mozilla/5.0 (Linux; Android 14; Pixel 8) AppleWebKit/537.36 (KHTML, like Gecko) "
                  "Chrome/126.0.0.0 Mobile Safari/537.36")
CHROME_IOS = ("Mozilla/5.0 (iPhone; CPU iPhone OS 17_5 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) "
              "CriOS/126.0.6478.153 Mobile/15E148 Safari/604.1")
SAMSUNG = ("Mozilla/5.0 (Linux; Android 14; SM-S918B) AppleWebKit/537.36 (KHTML, like Gecko) "
           "SamsungBrowser/25.0 Chrome/121.0.0.0 Mobile Safari/537.36")
OPERA = CHROME_WINDOWS + " OPR/111.0.0.0"
IPAD = ("Mozilla/5.0 (iPad; CPU OS 17_5 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) "
        "Version/17.5 Mobile/15E148 Safari/604.1")
CHROMEOS = "Mozilla/5.0 (X11; CrOS x86_64 14541.0.0) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36"


@pytest.mark.parametrize("agent,label", [
    (CHROME_WINDOWS, "Chrome on Windows"),
    (EDGE_WINDOWS, "Edge on Windows"),
    (FIREFOX_LINUX, "Firefox on Linux"),
    (SAFARI_MAC, "Safari on macOS"),
    (SAFARI_IPHONE, "Safari on iOS"),
    (CHROME_ANDROID, "Chrome on Android"),
    (CHROME_IOS, "Chrome on iOS"),
    (SAMSUNG, "Samsung Internet on Android"),
    (OPERA, "Opera on Windows"),
    (IPAD, "Safari on iPadOS"),
    (CHROMEOS, "Chrome on ChromeOS"),
    ("curl/8.5.0", "An app or script"),
    ("python-requests/2.31.0", "An app or script"),
    ("", "Unknown browser"),
    (None, "Unknown browser"),
    ("Mozilla/5.0 (Android 9) SomethingNew/1.0", "Unknown browser on Android"),
    ("some entirely made up agent", "Unknown browser"),
])
def test_known_browsers_have_a_short_label(agent, label):
    assert user_agents.describe(agent) == label


def test_chrome_is_not_taken_for_safari_nor_the_other_way_round():
    assert user_agents.parse(CHROME_WINDOWS)[0] == "Chrome"
    assert user_agents.parse(SAFARI_MAC)[0] == "Safari"


def test_the_label_is_never_the_raw_text():
    hostile = "<script>alert(1)</script> Chrome/1 " + "x" * 500
    label = user_agents.describe(hostile)
    assert "<" not in label and "script" not in label.lower() and len(label) < 40


def test_what_is_stored_has_no_control_characters_and_is_cut_short():
    assert user_agents.stored("Chrome\r\nX-Injected: 1\x00") == "ChromeX-Injected: 1"
    assert len(user_agents.stored("a" * 1000)) == user_agents.STORED_LENGTH
    assert user_agents.stored("   ") is None and user_agents.stored(None) is None
