"""Fetching an address a member typed, without letting it reach anything it should not.

The site is asked to call a server somebody names (a curation's feed server). Left
unguarded that is a way to make the site call its own network: the database, the cloud
metadata address, a router. So:

* https only, the usual port, no user name or password in the address;
* the name is looked up first and EVERY address it has must be a public one
  (not private, loopback, link-local, carrier NAT, multicast or reserved);
* the connection then goes to the address that was checked, with the name only used
  for the certificate - a second lookup could answer differently;
* redirects are not followed, the wait is short and the answer small.

`FEED_SERVER_ALLOW_LOCAL=1` lifts the address and https rules so a test server on the
local machine can be used. Local testing only, never on a live site.

Stdlib only. Nothing about the viewer is ever sent: the caller passes no cookies or
headers, and this module adds none beyond what a request needs.
"""
import http.client
import ipaddress
import json
import os
import socket
import ssl
import time
from urllib.parse import urlencode, urlsplit

try:
    import gevent
except ImportError:          # the unit tests run without it
    gevent = None

TIMEOUT = 3.0                # seconds for the whole exchange
MAX_BYTES = 64 * 1024
MAX_URL = 300
USER_AGENT = "feed fetcher"

# public-looking IPv6 ranges that carry an IPv4 address inside them
_EMBEDDED_V4 = [ipaddress.ip_network(n) for n in ("2002::/16", "64:ff9b::/96", "64:ff9b:1::/48", "2001::/32")]


class FetchError(Exception):
    """The address is refused or the server did not give a usable answer.
    The message is written for the member who chose the server."""

    def __init__(self, message):
        super().__init__(message)
        self.message = message


def local_allowed():
    return os.environ.get("FEED_SERVER_ALLOW_LOCAL") == "1"


def is_public(address):
    """Is this IP address one on the open internet?"""
    try:
        ip = ipaddress.ip_address(address)
    except ValueError:
        return False
    if ip.version == 6:
        if ip.ipv4_mapped is not None:
            ip = ip.ipv4_mapped
        elif any(ip in net for net in _EMBEDDED_V4):
            return False
    return bool(ip.is_global) and not ip.is_multicast


def check_url(url, allow_local=None):
    """(scheme, host, port, path with query) of an address that may be fetched, or FetchError.
    Only the address itself is judged here; `vetted_address` judges where it leads."""
    allow_local = local_allowed() if allow_local is None else allow_local
    if not isinstance(url, str) or not url.strip() or len(url) > MAX_URL or any(c.isspace() or ord(c) < 32 for c in url.strip()):
        raise FetchError("That is not a usable address.")
    try:
        parts = urlsplit(url.strip())
        port = parts.port
    except ValueError:
        raise FetchError("That is not a usable address.")

    if parts.scheme != "https" and not (allow_local and parts.scheme == "http"):
        raise FetchError("A feed server address must start with https://")
    if parts.username is not None or parts.password is not None:
        raise FetchError("A feed server address cannot hold a user name or password.")
    host = (parts.hostname or "").lower().rstrip(".")
    if not host:
        raise FetchError("That address has no server name.")
    if not allow_local:
        if port not in (None, 443):
            raise FetchError("A feed server must use the usual https port.")
        try:
            ipaddress.ip_address(host)
            literal = True
        except ValueError:
            literal = False
        if literal or "." not in host or host.endswith((".local", ".internal", ".localhost", ".lan", ".home")):
            raise FetchError("A feed server needs a public server name, like feeds.example.org")
    path = parts.path or "/"
    if parts.query:
        path += "?" + parts.query
    return parts.scheme, host, port or (443 if parts.scheme == "https" else 80), path


def _lookup(host, port):
    return socket.getaddrinfo(host, port, type=socket.SOCK_STREAM)


def vetted_address(host, port, allow_local=None):
    """The address to connect to. Every address the name has must be public."""
    allow_local = local_allowed() if allow_local is None else allow_local
    try:
        found = [info[4][0] for info in _lookup(host, port)]
    except (OSError, UnicodeError):
        raise FetchError(f"The name {host} could not be found.")
    if not found:
        raise FetchError(f"The name {host} could not be found.")
    if not allow_local and not all(is_public(address) for address in found):
        raise FetchError("That server is not on the public internet.")
    return found[0]


def check(url, allow_local=None):
    """Everything that can be judged without calling the server: the address and where it
    leads. Used when a member saves a feed server."""
    scheme, host, port, path = check_url(url, allow_local)
    vetted_address(host, port, allow_local)
    return host


class _Pinned(http.client.HTTPConnection):
    """Connects to the address that was checked, whatever the name says by now."""

    def __init__(self, host, port, address, timeout, context=None):
        super().__init__(host, port, timeout=timeout)
        self._address, self._tls = address, context

    def connect(self):
        self.sock = socket.create_connection((self._address, self.port), self.timeout)
        if self._tls is not None:
            self.sock = self._tls.wrap_socket(self.sock, server_hostname=self.host)


def get_json(url, params=None, allow_local=None, timeout=TIMEOUT, max_bytes=MAX_BYTES):
    """GET `url` (with `params` added to its query) and return the JSON object it answers.
    FetchError for anything else: a refused address, no answer in time, a redirect, an
    error page, too much data, or something that is not a JSON object."""
    scheme, host, port, path = check_url(url, allow_local)
    address = vetted_address(host, port, allow_local)
    if params:
        path += ("&" if "?" in path else "?") + urlencode(params)

    slow = FetchError("The feed server took too long to answer.")
    deadline = time.monotonic() + timeout
    context = ssl.create_default_context() if scheme == "https" else None
    connection = _Pinned(host, port, address, timeout, context)
    # each socket step has its own time limit; under gevent (the app) the whole exchange has one
    guard = gevent.Timeout(timeout, slow) if gevent is not None else None
    if guard is not None:
        guard.start()
    try:
        connection.request("GET", path, headers={"Accept": "application/json", "User-Agent": USER_AGENT, "Connection": "close"})
        response = connection.getresponse()
        if 300 <= response.status < 400:
            raise FetchError("The feed server answered with a redirect, which is not followed.")
        if response.status != 200:
            raise FetchError(f"The feed server answered with an error ({response.status}).")
        body = b""
        while len(body) <= max_bytes:
            if time.monotonic() > deadline:
                raise slow
            chunk = response.read1(8192)
            if not chunk:
                break
            body += chunk
        if len(body) > max_bytes:
            raise FetchError("The feed server's answer is too large.")
    except FetchError:
        raise
    except ssl.SSLError:
        raise FetchError("The feed server's certificate could not be checked.")
    except (OSError, http.client.HTTPException):
        raise FetchError("The feed server did not answer in time.")
    finally:
        if guard is not None:
            guard.close()
        connection.close()

    try:
        data = json.loads(body.decode("utf-8"))
    except (ValueError, UnicodeDecodeError):
        raise FetchError("The feed server's answer is not JSON.")
    if not isinstance(data, dict):
        raise FetchError("The feed server's answer is not a JSON object.")
    return data
