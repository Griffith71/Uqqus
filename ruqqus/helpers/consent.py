"""May another site's video player load before the reader presses play? (CLAUDE.md "Video consent"; the owner, 2026-10-30.)
The rules, with no I/O. Readers in the UK, the EEA and Switzerland, and anyone whose country isn't known, are asked first;
everyone else keeps videos that start as they scroll into view. The answer is the `video_consent` cookie: the reader's own
browser writes it, and then the site writes it again from a request that carries only the answer (routes/consent_pages.py),
because WebKit keeps a cookie a script wrote for seven days at most (assets/js/consent.js holds the same reading of it, checked
against these by tests/test_consent.py). Nothing about a visitor is stored on the server, which also makes it the weakest
kind of proof that consent was given."""
import re

COOKIE = "video_consent"
ANSWER_PATH = "/consent/video"     # where the browser sends the answer so that the server can set the same cookie
VERSION = 1                        # raise it when what the banner says changes: everyone is asked again
REASK_SECONDS = 183 * 86400        # about six months, then the reader is asked again
KEEP_SECONDS = 400 * 86400         # how long the browser keeps the cookie (Chrome's most): longer than the answer counts, or a "no" that lapsed would look like no answer at all
CLOCK_SLACK = 7 * 86400            # a date the browser's own clock wrote: a little ahead of ours is not a forgery

ASK, YES, NO = "ask", "yes", "no"

# Google's list for its own consent message (so that the two prompts agree): the EU, Iceland, Liechtenstein, Norway, the UK
# and Switzerland. Plus the places a country header names apart from them that the same law reaches: the EU's outermost
# regions and Åland, and the Crown dependencies and Gibraltar.
REGIONS = frozenset((
    "AT", "BE", "BG", "HR", "CY", "CZ", "DK", "EE", "FI", "FR", "DE", "GR", "HU", "IE", "IT", "LV", "LT", "LU", "MT", "NL",
    "PL", "PT", "RO", "SK", "SI", "ES", "SE",
    "IS", "LI", "NO",
    "GB", "UK",
    "CH",
    "AX", "GF", "GP", "MF", "MQ", "RE", "YT",
    "GG", "GI", "IM", "JE",
))

# two letters that don't name a country: Cloudflare's "unknown", the ISO block for it, and the old lookup services' continents
UNKNOWN = frozenset(("XX", "ZZ", "EU", "AP"))

_COUNTRY = re.compile(r"[A-Z]{2}\Z")
_COOKIE = re.compile(r"(\d{1,4})\.(yes|no)\.(\d{1,11})\Z")


def needs_asking(country):
    """Should a reader in this country be asked? Yes for the regions and for anything that isn't plainly a country: no header
    (the local site, a deployment with no such network), Tor (`T1`), `XX`, or a header in any other shape."""
    if not isinstance(country, str) or not _COUNTRY.match(country) or country in UNKNOWN:
        return True
    return country in REGIONS


def cookie_value(choice, now):
    """The cookie's text for a choice of exactly YES or NO, dated `now`; None for anything else, so nothing a request sent
    but those two words reaches the text: it can't add an attribute or name another cookie."""
    if choice not in (YES, NO):
        return None
    return "%d.%s.%d" % (VERSION, choice, int(now))


def set_answer(response, value, is_secure, origin):
    """Put the answer on a response: the same cookie the script writes, so each replaces the other. No Domain, so it is this host's
    alone, and readable by the script, which reads it on every page and may write over it. Secure when the page that asked is on
    https, the script's own rule: behind a proxy that says nothing about the scheme, the browser's Origin still does."""
    response.set_cookie(COOKIE, value, max_age=KEEP_SECONDS, path="/", samesite="Lax", httponly=False,
                        secure=is_secure or (origin or "").lower().startswith("https://"))


def read_cookie(value, now):
    """The reader's standing choice (YES or NO) from the cookie's text, or None: no cookie, not ours, an older version of the
    question, older than REASK_SECONDS, or dated too far ahead."""
    found = _COOKIE.match(value) if isinstance(value, str) else None
    if not found or int(found.group(1)) != VERSION:
        return None
    age = now - int(found.group(3))
    if age >= REASK_SECONDS or age < -CLOCK_SLACK:
        return None
    return found.group(2)


def said_no_before(value):
    """Is this a "no" that has stopped counting (six months on, or an older version of the question)? It is still the reader's
    last word: wherever they are, it ends in the question and never in players that load by themselves."""
    found = _COOKIE.match(value) if isinstance(value, str) else None
    return bool(found) and found.group(2) == NO


def state(country, cookie, now):
    """What <html data-video-consent> says: YES or NO for a choice the reader made, wherever they are now (the Cookie choices
    link is everywhere, and a reader who said no meant it); ASK for a reader who has to be asked and hasn't answered, and for
    anyone, anywhere, whose "no" has lapsed; "" for everyone else, whose videos behave as they always did (a "yes" that lapsed
    outside the regions included)."""
    choice = read_cookie(cookie, now)
    if choice:
        return choice
    return ASK if needs_asking(country) or said_no_before(cookie) else ""


# the name to show for a player's address. The hosts are the ones helpers/embed.py and helpers/social_embeds.py make players
# from; anything else (a direct video file, an old player) is shown as its host.
_SITES = (
    ("youtube.com", "YouTube"), ("youtube-nocookie.com", "YouTube"), ("vimeo.com", "Vimeo"),
    ("dailymotion.com", "Dailymotion"), ("twitch.tv", "Twitch"), ("rumble.com", "Rumble"), ("odysee.com", "Odysee"),
    ("bitchute.com", "BitChute"), ("bilibili.com", "Bilibili"), ("streamable.com", "Streamable"), ("loom.com", "Loom"),
    ("ted.com", "TED"), ("facebook.com", "Facebook"), ("instagram.com", "Instagram"), ("tiktok.com", "TikTok"),
    ("kick.com", "Kick"), ("spotify.com", "Spotify"), ("soundcloud.com", "SoundCloud"), ("apple.com", "Apple"),
    ("bandcamp.com", "Bandcamp"), ("mixcloud.com", "Mixcloud"), ("deezer.com", "Deezer"),
)
_HOST = re.compile(r"(?:https?:)?//(?:[^/?#@]*@)?([^/?#:]+)", re.I)


def player_site(address):
    """Who a player at this address belongs to, in words: "YouTube", or the address's host. An address with no host: ""."""
    found = _HOST.match(address or "")
    if not found:
        return ""
    host = found.group(1).lower()
    for domain, name in _SITES:
        if host == domain or host.endswith("." + domain):
            return name
    return host[4:] if host.startswith("www.") else host
