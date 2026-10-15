"""Fetching a bot's feed, without letting the address reach anything it should not.

Every request goes through helpers/safe_fetch.py's guard, the same one an outside feed server gets: `check_url`
(https, the usual port, a public name, no user name or password) and `vetted_address` (every address the name has
must be public), then the connection goes to the address that was checked (`_Pinned`). On top of that a feed needs:

* redirects, because feeds move: at most MAX_HOPS, and every hop is judged again from scratch;
* a conditional GET (`If-None-Match` / `If-Modified-Since`), so an unchanged feed costs both sides almost nothing;
* a bigger answer than a feed server's list (MAX_BYTES) and a longer wait (TIMEOUT).

Never this site's own address, at any hop. Nothing about any member is ever sent: no cookies, no referrer.
"""
import email.utils
import http.client
import os
import ssl
import time
from collections import namedtuple
from urllib.parse import urljoin

from ruqqus.helpers import bots, safe_fetch
from ruqqus.helpers.safe_fetch import FetchError

try:
    import gevent
except ImportError:          # the unit tests run without it
    gevent = None

TIMEOUT = 10.0
MAX_BYTES = 2 * 1024 * 1024
MAX_HOPS = 3
ACCEPT = ("application/rss+xml, application/atom+xml, application/feed+json, application/json;q=0.9, "
          "application/xml;q=0.9, text/xml;q=0.9, */*;q=0.5")
REDIRECTS = (301, 302, 303, 307, 308)

# changed: False for a 304 (body empty). url: where the feed was finally read from.
# max_age: seconds the server asks us to keep the answer (Cache-Control max-age, or Expires), 0 when it says nothing
Answer = namedtuple("Answer", "changed body content_type etag last_modified url max_age", defaults=(0,))


def _max_age(response):
    """Seconds from Cache-Control max-age, else from Expires (against the answer's own Date, or our clock)."""
    control = (response.getheader("Cache-Control", "") or "").lower()
    for part in control.split(","):
        name, _, value = part.strip().partition("=")
        if name == "max-age" and value.strip().isdigit():
            return int(value.strip())
    expires = response.getheader("Expires", "") or ""
    if not expires:
        return 0
    try:
        until = email.utils.parsedate_to_datetime(expires).timestamp()
        sent = response.getheader("Date", "")
        start = email.utils.parsedate_to_datetime(sent).timestamp() if sent else time.time()
    except (TypeError, ValueError, IndexError, AttributeError):
        return 0
    return max(0, int(until - start))


def user_agent():
    site = os.environ.get("SITE_NAME", "Ruqqus")
    host = bots.own_host()
    return f"{site} feed reader (+https://{host}/help/bots)" if host else f"{site} feed reader"


def _not_this_site(host):
    site = bots.own_host()
    if site and (host == site or host.endswith("." + site)):
        raise FetchError("A bot can't read this site itself.")


def _once(url, etag, last_modified, allow_local, deadline, max_bytes):
    """One request. ("redirect", location) or ("answer", Answer)."""
    scheme, host, port, path = safe_fetch.check_url(url, allow_local)
    _not_this_site(host)
    address = safe_fetch.vetted_address(host, port, allow_local)

    slow = FetchError("The feed did not answer in time.")
    context = ssl.create_default_context() if scheme == "https" else None
    connection = safe_fetch._Pinned(host, port, address, max(0.5, deadline - time.monotonic()), context)
    headers = {"Accept": ACCEPT, "User-Agent": user_agent(), "Connection": "close"}
    if etag:
        headers["If-None-Match"] = etag
    if last_modified:
        headers["If-Modified-Since"] = last_modified
    try:
        connection.request("GET", path, headers=headers)
        response = connection.getresponse()
        if response.status in REDIRECTS:
            return "redirect", response.getheader("Location", "") or ""
        new_etag = (response.getheader("ETag", "") or "")[:300]
        new_modified = (response.getheader("Last-Modified", "") or "")[:100]
        if response.status == 304:
            return "answer", Answer(False, b"", "", new_etag or etag, new_modified or last_modified, url, _max_age(response))
        if response.status in (429, 503):
            # told to slow down: the runner waits as long as the server asks (bots.retry_wait)
            busy = FetchError("The feed asked us to slow down." if response.status == 429 else "The feed is unavailable for now.")
            busy.retry_after = response.getheader("Retry-After", "") or ""
            raise busy
        if response.status != 200:
            raise FetchError(f"The feed answered with an error ({response.status}).")
        body = b""
        while len(body) <= max_bytes:
            if time.monotonic() > deadline:
                raise slow
            chunk = response.read1(65536)
            if not chunk:
                break
            body += chunk
        if len(body) > max_bytes:
            raise FetchError("The feed's answer is too large.")
        content_type = (response.getheader("Content-Type", "") or "")[:200]
        return "answer", Answer(True, body, content_type, new_etag, new_modified, url, _max_age(response))
    except FetchError:
        raise
    except ssl.SSLError:
        raise FetchError("The feed's certificate could not be checked.")
    except (OSError, http.client.HTTPException):
        raise slow
    finally:
        connection.close()


def fetch(url, etag="", last_modified="", allow_local=None, timeout=TIMEOUT, max_bytes=MAX_BYTES):
    """GET a feed. An Answer, or FetchError for anything else: a refused address at any hop, too many redirects, no
    answer in time, an error page, or too much data."""
    allow_local = safe_fetch.local_allowed() if allow_local is None else allow_local
    deadline = time.monotonic() + timeout
    guard = gevent.Timeout(timeout, FetchError("The feed did not answer in time.")) if gevent is not None else None
    if guard is not None:
        guard.start()
    try:
        for hop in range(MAX_HOPS + 1):
            kind, value = _once(url, etag, last_modified, allow_local, deadline, max_bytes)
            if kind == "answer":
                return value
            if hop == MAX_HOPS or not value:
                raise FetchError("The feed's address sends it somewhere else too many times.")
            url = urljoin(url, value.strip())
            etag = last_modified = ""                 # the conditional headers belong to the first address
        raise FetchError("The feed's address sends it somewhere else too many times.")
    finally:
        if guard is not None:
            guard.close()


def preview(url, allow_local=None, count=5):
    """(feed title, [the newest `count` item titles]) for the apply form and the admins' queue, or FetchError /
    feed_items.FeedError with a message for a person."""
    from ruqqus.helpers import feed_items

    answer = fetch(url, allow_local=allow_local)
    feed = feed_items.parse(answer.body, answer.content_type)
    return feed.title, [item.title for item in feed_items.newest_first(feed.items)[:count]]
