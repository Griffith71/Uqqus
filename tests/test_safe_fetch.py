"""Fetching an address a member typed (ruqqus/helpers/safe_fetch.py): it must never be a
way to make the site call its own network."""
import socket

import pytest

from ruqqus.helpers import safe_fetch as sf


def refused(url, **kw):
    with pytest.raises(sf.FetchError) as err:
        sf.check_url(url, allow_local=False, **kw)
    return err.value.message


# --- which addresses are public --------------------------------------------------------

@pytest.mark.parametrize("address", [
    "127.0.0.1", "127.8.9.10", "0.0.0.0", "10.0.0.5", "172.16.0.1", "172.31.255.255", "192.168.1.1",
    "169.254.169.254",                # the cloud metadata address
    "100.64.0.1",                     # carrier NAT
    "224.0.0.1", "255.255.255.255", "198.18.0.1", "192.0.2.1", "240.0.0.1",
    "::1", "::", "fe80::1", "fc00::1", "fd12:3456::1", "ff02::1",
    "::ffff:127.0.0.1", "::ffff:10.0.0.1", "::ffff:169.254.169.254",     # an IPv4 address written as IPv6
    "2002:7f00:1::", "64:ff9b::7f00:1", "2001:0:4136:e378:8000:63bf:3fff:fdd2",   # IPv4 carried inside IPv6
    "not an address", "", "999.1.1.1",
])
def test_addresses_that_are_not_on_the_public_internet(address):
    assert not sf.is_public(address)


@pytest.mark.parametrize("address", ["93.184.216.34", "8.8.8.8", "1.1.1.1", "2606:4700:4700::1111", "::ffff:8.8.8.8"])
def test_public_addresses(address):
    assert sf.is_public(address)


# --- the address as written ------------------------------------------------------------

def test_a_plain_https_address_is_taken_apart():
    assert sf.check_url("https://Feeds.Example.org/top?kind=new", allow_local=False) == ("https", "feeds.example.org", 443, "/top?kind=new")
    assert sf.check_url("https://feeds.example.org", allow_local=False)[3] == "/"
    assert sf.check_url("https://feeds.example.org:443/x#part", allow_local=False) == ("https", "feeds.example.org", 443, "/x")


@pytest.mark.parametrize("url, words", [
    ("http://feeds.example.org/top", "https://"),
    ("ftp://feeds.example.org/top", "https://"),
    ("file:///etc/passwd", "https://"),
    ("gopher://feeds.example.org/", "https://"),
    ("//feeds.example.org/top", "https://"),
    ("feeds.example.org/top", "https://"),
    ("https://user:secret@feeds.example.org/", "user name or password"),
    ("https://user@feeds.example.org/", "user name or password"),
    ("https://feeds.example.org:8443/", "usual https port"),
    ("https://feeds.example.org:22/", "usual https port"),
    ("https://127.0.0.1/", "public server name"),
    ("https://169.254.169.254/latest/meta-data/", "public server name"),
    ("https://[::1]/", "public server name"),
    ("https://93.184.216.34/", "public server name"),      # even a public address: a name is asked for
    ("https://localhost/", "public server name"),
    ("https://2130706433/", "public server name"),         # 127.0.0.1 as one number
    ("https://redis/", "public server name"),              # a name on the site's own network
    ("https://printer.local/", "public server name"),
    ("https://db.internal/", "public server name"),
    ("https:///path", "no server name"),
    ("https://feeds.example.org/a b", "not a usable address"),
    ("https://feeds.example.org/\nHost: evil", "not a usable address"),
    ("https://feeds.example.org/" + "a" * 400, "not a usable address"),
    ("https://feeds.example.org:port/", "not a usable address"),
    ("", "not a usable address"), (None, "not a usable address"), (5, "not a usable address"),
])
def test_addresses_that_are_refused_as_written(url, words):
    assert words in refused(url)


# --- where a name leads ----------------------------------------------------------------

def answers(*addresses):
    def lookup(host, port):
        return [(socket.AF_INET, socket.SOCK_STREAM, 6, "", (a, port)) for a in addresses]
    return lookup


def test_a_name_must_lead_only_to_public_addresses(monkeypatch):
    monkeypatch.setattr(sf, "_lookup", answers("93.184.216.34"))
    assert sf.vetted_address("feeds.example.org", 443, allow_local=False) == "93.184.216.34"
    assert sf.check("https://feeds.example.org/top", allow_local=False) == "feeds.example.org"

    for private in ("10.0.0.5", "127.0.0.1", "169.254.169.254", "::1"):
        monkeypatch.setattr(sf, "_lookup", answers(private))
        with pytest.raises(sf.FetchError) as err:
            sf.check("https://feeds.example.org/top", allow_local=False)
        assert "public internet" in err.value.message, private

    # one private address among public ones is enough to refuse the name
    monkeypatch.setattr(sf, "_lookup", answers("93.184.216.34", "10.0.0.5"))
    with pytest.raises(sf.FetchError):
        sf.vetted_address("feeds.example.org", 443, allow_local=False)


def test_a_name_that_does_not_exist_is_said_plainly(monkeypatch):
    def lookup(host, port):
        raise socket.gaierror("no such host")
    monkeypatch.setattr(sf, "_lookup", lookup)
    with pytest.raises(sf.FetchError) as err:
        sf.check("https://nowhere.example.org/", allow_local=False)
    assert "could not be found" in err.value.message
    monkeypatch.setattr(sf, "_lookup", answers())
    with pytest.raises(sf.FetchError):
        sf.vetted_address("nowhere.example.org", 443, allow_local=False)


def test_the_connection_goes_to_the_address_that_was_checked(monkeypatch):
    # the name is looked up once; whatever it would answer later is never asked
    looked_up, connected = [], []

    def lookup(host, port):
        looked_up.append(host)
        return [(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("93.184.216.34", port))]

    def connect(address, timeout=None, *a, **kw):
        connected.append(address)
        raise OSError("stop here")

    monkeypatch.setattr(sf, "_lookup", lookup)
    monkeypatch.setattr(sf.socket, "create_connection", connect)
    with pytest.raises(sf.FetchError) as err:
        sf.get_json("https://feeds.example.org/top", {"limit": 100}, allow_local=False)
    assert connected == [("93.184.216.34", 443)] and looked_up == ["feeds.example.org"]
    assert "did not answer" in err.value.message


def test_a_refused_address_is_never_connected_to(monkeypatch):
    def connect(*a, **kw):
        raise AssertionError("must not connect")
    monkeypatch.setattr(sf.socket, "create_connection", connect)
    monkeypatch.setattr(sf, "_lookup", answers("10.0.0.5"))
    for url in ("https://feeds.example.org/", "https://127.0.0.1/", "http://feeds.example.org/", "https://169.254.169.254/"):
        with pytest.raises(sf.FetchError):
            sf.get_json(url, allow_local=False)


# --- the local switch ------------------------------------------------------------------

def test_local_addresses_need_the_switch_and_the_switch_is_off_by_default(monkeypatch):
    monkeypatch.delenv("FEED_SERVER_ALLOW_LOCAL", raising=False)
    assert not sf.local_allowed()
    with pytest.raises(sf.FetchError):
        sf.check_url("http://127.0.0.1:8099/feed")
    monkeypatch.setenv("FEED_SERVER_ALLOW_LOCAL", "1")
    assert sf.local_allowed()
    assert sf.check_url("http://127.0.0.1:8099/feed") == ("http", "127.0.0.1", 8099, "/feed")
    monkeypatch.setattr(sf, "_lookup", answers("127.0.0.1"))
    assert sf.vetted_address("127.0.0.1", 8099) == "127.0.0.1"
    for value in ("0", "true", "yes", ""):
        monkeypatch.setenv("FEED_SERVER_ALLOW_LOCAL", value)
        assert not sf.local_allowed(), value
    # even with the switch, only http and https
    with pytest.raises(sf.FetchError):
        sf.check_url("file:///etc/passwd", allow_local=True)


# --- the exchange ----------------------------------------------------------------------

def serve(body, status=200, headers=()):
    """A one-request server on this machine; returns its address."""
    import threading

    listener = socket.socket()
    listener.bind(("127.0.0.1", 0))
    listener.listen(1)
    port = listener.getsockname()[1]
    seen = {}

    def run():
        conn, _ = listener.accept()
        seen["request"] = conn.recv(65536).decode("latin-1")
        head = f"HTTP/1.1 {status} X\r\nContent-Length: {len(body)}\r\n" + "".join(f"{k}: {v}\r\n" for k, v in headers) + "\r\n"
        conn.sendall(head.encode() + body)
        conn.close()
        listener.close()

    threading.Thread(target=run, daemon=True).start()
    return f"http://127.0.0.1:{port}/feed?kind=top", seen


def test_a_json_object_comes_back_and_only_what_a_request_needs_is_sent():
    url, seen = serve(b'{"posts": ["abc"], "cursor": "next"}')
    assert sf.get_json(url, {"limit": 100, "cursor": "a b"}, allow_local=True) == {"posts": ["abc"], "cursor": "next"}
    request = seen["request"]
    assert request.startswith("GET /feed?kind=top&limit=100&cursor=a+b HTTP/1.1")
    sent = {line.split(":")[0].lower() for line in request.split("\r\n")[1:] if line}
    assert sent == {"host", "accept-encoding", "accept", "user-agent", "connection"}
    assert "cookie" not in request.lower() and "x-forwarded" not in request.lower()


@pytest.mark.parametrize("body, status, headers, words", [
    (b"<html>hello</html>", 200, (), "not JSON"),
    (b'["abc"]', 200, (), "not a JSON object"),
    (b'"abc"', 200, (), "not a JSON object"),
    (b"{}", 500, (), "an error (500)"),
    (b"{}", 404, (), "an error (404)"),
    (b"", 302, (("Location", "http://169.254.169.254/"),), "redirect"),
    (b"", 301, (("Location", "https://feeds.example.org/"),), "redirect"),
    (b'{"posts": ["' + b"a" * (70 * 1024) + b'"]}', 200, (), "too large"),
], ids=["a web page", "a list", "a string", "500", "404", "a redirect inwards", "a redirect", "70 KB"])
def test_answers_that_are_refused(body, status, headers, words):
    url, _ = serve(body, status, headers)
    with pytest.raises(sf.FetchError) as err:
        sf.get_json(url, allow_local=True)
    assert words in err.value.message


def test_a_server_that_says_nothing_is_given_up_on_quickly():
    import time

    listener = socket.socket()
    listener.bind(("127.0.0.1", 0))
    listener.listen(1)
    started = time.monotonic()
    with pytest.raises(sf.FetchError) as err:
        sf.get_json(f"http://127.0.0.1:{listener.getsockname()[1]}/", allow_local=True, timeout=0.4)
    listener.close()
    assert time.monotonic() - started < 3 and "did not answer" in err.value.message
