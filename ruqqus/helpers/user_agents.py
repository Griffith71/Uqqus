"""A browser's User-Agent text as a short label ("Chrome on Windows"), for the sign-in history and the new-sign-in email.

No library: the label comes from a short fixed list, so what is shown is never the raw header (which is attacker-controlled
text) but one of a few known words. What is stored is the header cleaned and cut to `STORED_LENGTH` (`stored`), erased
after 90 days (helpers/retention.py); the label is worked out when it is shown.
"""
import re

STORED_LENGTH = 200

# order matters: Edge, Opera and the others say "Chrome" too, and Chrome says "Safari"
_BROWSERS = (
    ("Edge", ("edg/", "edga/", "edgios/", "edge/")),
    ("Opera", ("opr/", "opera")),
    ("Samsung Internet", ("samsungbrowser",)),
    ("DuckDuckGo", ("duckduckgo/", "ddg/")),
    ("Firefox", ("firefox/", "fxios/")),
    ("Chrome", ("chrome/", "crios/", "chromium/")),
    ("Safari", ("version/",)),                       # only a real Safari has "Version/x Safari/y" without Chrome
)
_TOOLS = ("curl/", "wget/", "python-requests", "python-urllib", "okhttp", "go-http-client", "node-fetch", "axios/",
          "java/", "postmanruntime", "libwww", "httpie", "insomnia")
_SYSTEMS = (
    ("iOS", ("iphone", "ipod")),
    ("iPadOS", ("ipad",)),
    ("Android", ("android",)),
    ("ChromeOS", ("cros ",)),
    ("Windows", ("windows",)),
    ("macOS", ("macintosh", "mac os x")),
    ("Linux", ("linux", "x11")),
)

_CONTROL = re.compile(r"[\x00-\x1f\x7f]")


def stored(user_agent):
    """What the sign-in event keeps: the header with control characters removed and cut short, or None."""
    text = _CONTROL.sub("", str(user_agent or "")).strip()[:STORED_LENGTH]
    return text or None


def parse(user_agent):
    """(browser, system), each a known word or None. A script or an app is ("An app or script", None)."""
    text = str(user_agent or "").lower()
    if not text:
        return None, None
    if any(tool in text for tool in _TOOLS):
        return "An app or script", None
    browser = None
    for name, needles in _BROWSERS:
        if any(needle in text for needle in needles):
            if name == "Safari" and ("safari/" not in text or "chrome" in text):
                continue
            browser = name
            break
    system = None
    for name, needles in _SYSTEMS:
        if any(needle in text for needle in needles):
            system = name
            break
    return browser, system


def describe(user_agent):
    """"Chrome on Windows", "Firefox", "Unknown browser on Android", "Unknown browser"."""
    browser, system = parse(user_agent)
    if browser and system:
        return f"{browser} on {system}"
    if browser:
        return browser
    if system:
        return f"Unknown browser on {system}"
    return "Unknown browser"
