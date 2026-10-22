"""Reading a page or a picture a member linked (a link post's title, preview text and picture), without letting the
address reach anything it should not (bug hunt 2026-10, H1 and H2: `/submit/title` and `thumbs.py` called plain
`requests` on any address a member typed, redirects followed, no size limit, so the site could be made to read its
own network and hand back what it found).

Every hop goes through helpers/safe_fetch.py's guard, as a bot's feed does (helpers/feed_fetch.py): `check_url`
(https, the usual port, a public name, no user name or password) and `vetted_address` (every address the name has
must be public), and the connection goes to the address that was checked (`_Pinned`). On top of that:

* an http:// address is read as https:// (as `thumbs.expand_url` always did for pictures): the guard is about where
  a request goes, and a page on the open internet almost always answers on https too;
* redirects are followed, at most MAX_HOPS, each one judged again from scratch;
* the body is read only for the kinds of answer the caller wants (`read`), and never more than `max_bytes`: a page
  cut short is still worth reading for its title, a picture cut short is not (`Page.complete`);
* one time limit for the whole exchange.

Nothing about any member is sent: no cookies, no referrer.
"""
import http.client
import ssl
import time
from urllib.parse import quote, urljoin, urlsplit, urlunsplit

from ruqqus.helpers import safe_fetch
from ruqqus.helpers.safe_fetch import FetchError

try:
    import gevent
except ImportError:          # the unit tests run without it
    gevent = None

TIMEOUT = 10.0
MAX_HOPS = 3
PAGE_BYTES = 2 * 1024 * 1024
IMAGE_BYTES = 8 * 1024 * 1024
REDIRECTS = (301, 302, 303, 307, 308)
MAX_URL = 2048               # article and picture addresses run longer than a feed server's
# what the title suggestion and the link preview always sent (many pages answer a browser only); kept as it was
BROWSER_AGENT = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) "
                 "Chrome/89.0.4389.72 Safari/537.36")


class Page:
    """What an address answered. `headers` is the one header the embed detection reads (`embed.detect_video_embed`)."""

    def __init__(self, status, content_type, body, url, complete):
        self.status, self.content_type, self.body, self.url, self.complete = status, content_type, body, url, complete

    @property
    def headers(self):
        return {"Content-Type": self.content_type}


def as_https(url):
    """An http:// address as https:// on the usual port; anything else unchanged (the guard judges it)."""
    if not isinstance(url, str):
        return url
    try:
        parts = urlsplit(url.strip())
    except ValueError:
        return url
    if parts.scheme.lower() == "http" and parts.hostname and parts.port in (None, 80):
        host = parts.hostname if ":" not in parts.hostname else f"[{parts.hostname}]"
        return urlunsplit(("https", host, parts.path, parts.query, parts.fragment))
    return url


def _wire(host, path):
    """The host and path as they go on the wire: a name in another script as IDNA, a path's other characters
    percent-encoded (requests did both; http.client sends ASCII only). FetchError when it can't be written."""
    try:
        wire_host = host.encode("idna").decode("ascii") if not host.isascii() else host
    except UnicodeError:
        raise FetchError("That is not a usable address.")
    return wire_host, quote(path, safe="/%:@!$&'()*+,;=?~-._")


def _location(response):
    """Where a redirect points. Headers arrive as latin-1; a server that wrote UTF-8 there is read as UTF-8."""
    value = response.getheader("Location", "") or ""
    try:
        return value.encode("latin-1").decode("utf-8")
    except UnicodeError:
        return value


def _once(url, read, user_agent, allow_local, deadline, max_bytes):
    """One request. ("redirect", location) or ("answer", Page)."""
    scheme, host, port, path = safe_fetch.check_url(url, allow_local, MAX_URL)
    host, path = _wire(host, path)
    address = safe_fetch.vetted_address(host, port, allow_local)
    context = ssl.create_default_context() if scheme == "https" else None
    connection = safe_fetch._Pinned(host, port, address, max(0.5, deadline - time.monotonic()), context)
    slow = FetchError("The page did not answer in time.")
    try:
        connection.request("GET", path, headers={"User-Agent": user_agent, "Accept": "*/*", "Connection": "close"})
        response = connection.getresponse()
        if response.status in REDIRECTS:
            return "redirect", _location(response)
        content_type = (response.getheader("Content-Type", "") or "")[:200]
        body, complete = b"", True
        if response.status == 200 and any(content_type.lower().startswith(kind) for kind in read):
            while len(body) <= max_bytes:
                if time.monotonic() > deadline:
                    raise slow
                chunk = response.read1(65536)
                if not chunk:
                    break
                body += chunk
            if len(body) > max_bytes:
                body, complete = body[:max_bytes], False
        else:
            complete = False
        return "answer", Page(response.status, content_type, body, url, complete)
    except FetchError:
        raise
    except ssl.SSLError:
        raise FetchError("The page's certificate could not be checked.")
    except (OSError, http.client.HTTPException):
        raise slow
    except (UnicodeError, ValueError):
        # a header or address http.client can't write or read: no answer, never a crash of the caller
        raise FetchError("That is not a usable address.")
    finally:
        connection.close()


def get(url, read=("text/html",), max_bytes=PAGE_BYTES, user_agent=None, timeout=TIMEOUT, allow_local=None):
    """GET a page or a picture: a Page (any status), or FetchError for a refused address at any hop, too many
    redirects or no answer in time. Its body is read only when the answer is a 200 whose type starts with one of
    `read`, and only up to `max_bytes`."""
    allow_local = safe_fetch.local_allowed() if allow_local is None else allow_local
    user_agent = user_agent or safe_fetch.USER_AGENT
    deadline = time.monotonic() + timeout
    guard = gevent.Timeout(timeout, FetchError("The page did not answer in time.")) if gevent is not None else None
    if guard is not None:
        guard.start()
    try:
        url = url if allow_local else as_https(url)
        for hop in range(MAX_HOPS + 1):
            kind, value = _once(url, read, user_agent, allow_local, deadline, max_bytes)
            if kind == "answer":
                return value
            if hop == MAX_HOPS or not value:
                break
            url = urljoin(url, value.strip())
            url = url if allow_local else as_https(url)
        raise FetchError("The page sends it somewhere else too many times.")
    finally:
        if guard is not None:
            guard.close()
