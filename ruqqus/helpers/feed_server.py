"""A curation ranked by an outside feed server (as Bluesky's custom feeds).

The curation names a server; the site asks it which posts to show:

    GET <address>?limit=100[&cursor=<what the last answer gave>]
    -> {"posts": ["<post id>", ...], "cursor": "<opaque, optional>"}

A post id is the one in a post's address (/post/<id>/...). What this module keeps to:

* the server only ORDERS. Its ids go through the same rules as every feed before a
  single post is shown (routes/curations.py), so it can never surface a post the
  viewer may not see;
* nothing about the viewer is sent: no id, IP address, cookie or language. One list
  for everyone, fetched by the site (helpers/safe_fetch.py), kept for `CACHE_SECONDS`
  so a page view does not become a request the server can time;
* a server that fails `FAILS_BEFORE_PAUSE` times in a row is left alone for
  `PAUSE_SECONDS`, and its last good list is shown meanwhile;
* one host gets at most `HOST_BUDGET` requests a minute from the whole site, so a
  curation cannot be used to hammer somebody.

The state lives in Redis (`ruqqus.__main__.r`); `ids_for` takes the store and the fetch
function as arguments so the rules can be tested without either.
"""
import hashlib
import json
import os
import re
import time

from ruqqus.helpers import safe_fetch

LIMIT = 100                  # posts asked for per request
MAX_IDS = 500                # posts kept per curation
MAX_REQUESTS = 5             # requests one page view may cause
CACHE_SECONDS = 120          # how long a list is used before asking again
KEEP_SECONDS = 3600          # how long the last good list is kept for when the server is down
FAILS_BEFORE_PAUSE = 3
PAUSE_SECONDS = 600
HOST_BUDGET = 30             # requests a minute to one host
CURSOR_CHARS = 200

OFF = "Outside feed servers are switched off on this site."
PAUSED = "This curation's feed server has not been answering. Showing its last answer."
PAUSED_EMPTY = "This curation's feed server has not been answering."
BUSY = "This curation's feed server has been asked too often. Try again in a minute."

_POST_ID = re.compile(r"^[0-9a-z]{1,10}$")
_ALPHABET = "0123456789abcdefghijklmnopqrstuvwxyz"


class BadAnswer(safe_fetch.FetchError):
    """The server answered, but not with a list of posts."""


def enabled():
    return os.environ.get("FEED_SERVERS_ENABLED", "1") != "0"


# --- the protocol (pure) ---------------------------------------------------------------

def request_params(cursor=None):
    """Everything that is sent. Nothing here depends on who is looking."""
    params = {"limit": LIMIT}
    if cursor:
        params["cursor"] = cursor
    return params


def parse_answer(data):
    """(post ids as numbers in the server's order, the cursor for more or None)."""
    posts = data.get("posts") if isinstance(data, dict) else None
    if not isinstance(posts, list):
        raise BadAnswer("The feed server's answer has no list of posts.")
    if len(posts) > LIMIT:
        raise BadAnswer(f"The feed server answered with more than {LIMIT} posts at once.")
    ids = []
    for item in posts:
        if not isinstance(item, str) or not _POST_ID.match(item):
            raise BadAnswer("The feed server's answer holds something that is not a post id.")
        number = int(item, 36)
        if number not in ids:
            ids.append(number)
    cursor = data.get("cursor")
    if cursor is not None and (not isinstance(cursor, str) or len(cursor) > CURSOR_CHARS or not cursor.isprintable()):
        raise BadAnswer("The feed server's cursor is not usable.")
    return ids, (cursor or None)


def keep_order(ids, visible):
    """The server's order, with only the posts the viewer may see."""
    visible = set(visible)
    return [i for i in ids if i in visible]


def post_id(number):
    """A post's id as the protocol writes it (base 36, as in its address)."""
    number, out = int(number), ""
    while number:
        number, digit = divmod(number, 36)
        out = _ALPHABET[digit] + out
    return out or "0"


# --- the state -------------------------------------------------------------------------

class MemoryStore:
    """The same few operations as RedisStore, in this process. Used by the tests, and
    when the site has no Redis (the lists are then per worker, which is only slower)."""

    def __init__(self):
        self.data = {}

    def _live(self, key, now=None):
        entry = self.data.get(key)
        if entry and entry[1] is not None and entry[1] <= (now or time.time()):
            del self.data[key]
            return None
        return entry

    def get(self, key):
        entry = self._live(key)
        return entry[0] if entry else None

    def set(self, key, value, seconds):
        self.data[key] = (value, time.time() + seconds)

    def add(self, key, seconds):
        """Count one more under `key` (which expires `seconds` after its first count)."""
        entry = self._live(key)
        count = (int(entry[0]) if entry else 0) + 1
        self.data[key] = (str(count), entry[1] if entry else time.time() + seconds)
        return count

    def delete(self, key):
        self.data.pop(key, None)

    def claim(self, key, seconds):
        """True for the one caller that gets to do the work for the next `seconds`."""
        if self._live(key):
            return False
        self.set(key, "1", seconds)
        return True


class RedisStore:
    def __init__(self, redis):
        self.redis = redis

    def get(self, key):
        return self.redis.get(key)

    def set(self, key, value, seconds):
        self.redis.set(key, value, ex=int(seconds))

    def add(self, key, seconds):
        count = self.redis.incr(key)
        if count == 1:
            self.redis.expire(key, int(seconds))
        return count

    def delete(self, key):
        self.redis.delete(key)

    def claim(self, key, seconds):
        return bool(self.redis.set(key, "1", nx=True, ex=int(seconds)))


_memory = MemoryStore()


def default_store():
    from ruqqus.__main__ import r
    return RedisStore(r) if r is not None else _memory


def _keys(curation_id, url):
    mark = hashlib.sha1(url.encode("utf-8")).hexdigest()[:12]      # a new address starts afresh
    base = f"feedserver:{int(curation_id)}:{mark}"
    return base + ":list", base + ":fails", base + ":lock"


def _host_key(host, now):
    return f"feedserver:host:{host}:{int(now) // 60}"


def _load(store, key):
    try:
        state = json.loads(store.get(key) or "null")
    except ValueError:
        state = None
    if not isinstance(state, dict) or not isinstance(state.get("ids"), list):
        return {"ids": [], "cursor": None, "done": False, "at": 0}
    return state


def ids_for(curation_id, url, need, store=None, fetch=None, now=None):
    """(post ids in the server's order, a notice for the page or None).

    At least `need` ids when the server has that many (never more than MAX_IDS). Asks
    the server only when the kept list is older than CACHE_SECONDS or too short."""
    store = default_store() if store is None else store
    fetch = safe_fetch.get_json if fetch is None else fetch
    now = time.time() if now is None else now
    need = min(int(need), MAX_IDS)
    list_key, fails_key, lock_key = _keys(curation_id, url)

    state = _load(store, list_key)
    fresh = now - state["at"] < CACHE_SECONDS
    if fresh and (len(state["ids"]) >= need or state["done"]):
        return state["ids"], None
    if int(store.get(fails_key) or 0) >= FAILS_BEFORE_PAUSE:
        return state["ids"], (PAUSED if state["ids"] else PAUSED_EMPTY)
    if not store.claim(lock_key, 10):
        return state["ids"], None          # another page view is already asking

    try:
        host = safe_fetch.check_url(url)[1]
        # an old list is thrown away only once the server has answered again
        work = state if fresh else {"ids": [], "cursor": None, "done": False, "at": now}
        requests = 0
        while len(work["ids"]) < need and not work["done"] and requests < MAX_REQUESTS:
            if store.add(_host_key(host, now), 120) > HOST_BUDGET:
                # the site's own restraint, not the server failing: it does not count against it
                return (work["ids"] or state["ids"]), BUSY
            requests += 1
            ids, cursor = parse_answer(fetch(url, request_params(work["cursor"])))
            new = [i for i in ids if i not in work["ids"]]
            work["ids"] = (work["ids"] + new)[:MAX_IDS]
            work["cursor"] = cursor
            work["done"] = not cursor or not new or len(work["ids"]) >= MAX_IDS
        store.set(list_key, json.dumps(work), KEEP_SECONDS)
        store.delete(fails_key)
        return work["ids"], None
    except safe_fetch.FetchError as error:
        store.add(fails_key, PAUSE_SECONDS)
        if state["ids"]:
            return state["ids"], f"{error.message} Showing its last answer."
        return [], error.message
    finally:
        store.delete(lock_key)


def try_server(url, fetch=None):
    """Ask the server once, now, for the owner's preview: its first ids, or FetchError."""
    fetch = safe_fetch.get_json if fetch is None else fetch
    ids, _ = parse_answer(fetch(url, request_params()))
    return ids


def forget(curation_id, url, store=None):
    """Drop what is kept for a curation's server (its address changed or was removed)."""
    store = default_store() if store is None else store
    for key in _keys(curation_id, url):
        store.delete(key)
