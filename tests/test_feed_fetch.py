"""Fetching a bot's feed (ruqqus/helpers/feed_fetch.py): redirects judged again at every hop, conditional requests,
the size cap, and never this site or a private address. Real local sockets, as tests/test_safe_fetch.py uses."""
import socket
import threading

import pytest

from ruqqus.helpers import feed_fetch as ff
from ruqqus.helpers import safe_fetch as sf


def serve(*answers):
    """A server on this machine that answers one request per entry: (status, headers, body). Returns its base
    address and the requests it got."""
    listener = socket.socket()
    listener.bind(("127.0.0.1", 0))
    listener.listen(len(answers))
    port = listener.getsockname()[1]
    seen = []

    def run():
        for status, headers, body in answers:
            conn, _ = listener.accept()
            seen.append(conn.recv(65536).decode("latin-1"))
            head = f"HTTP/1.1 {status} X\r\nContent-Length: {len(body)}\r\n" + \
                "".join(f"{k}: {v}\r\n" for k, v in headers) + "\r\n"
            conn.sendall(head.encode() + body)
            conn.close()
        listener.close()

    threading.Thread(target=run, daemon=True).start()
    return f"http://127.0.0.1:{port}", seen


FEED = b"<rss><channel><title>T</title><item><title>A</title><link>https://e.org/a</link></item></channel></rss>"


def test_a_feed_comes_back_with_its_validators_and_nothing_about_anyone_is_sent():
    base, seen = serve((200, [("Content-Type", "application/rss+xml"), ("ETag", '"v1"'),
                              ("Last-Modified", "Tue, 14 Oct 2026 10:00:00 GMT")], FEED))
    answer = ff.fetch(base + "/feed.xml", allow_local=True)
    assert answer.changed and answer.body == FEED and answer.content_type == "application/rss+xml"
    assert answer.etag == '"v1"' and answer.last_modified.startswith("Tue")
    request = seen[0]
    assert request.startswith("GET /feed.xml HTTP/1.1")
    assert "cookie" not in request.lower() and "referer" not in request.lower() and "feed reader" in request


def test_an_unchanged_feed_is_a_304_and_costs_no_body():
    base, seen = serve((304, [], b""))
    answer = ff.fetch(base + "/feed.xml", etag='"v1"', last_modified="Tue, 14 Oct 2026 10:00:00 GMT", allow_local=True)
    assert not answer.changed and answer.body == b"" and answer.etag == '"v1"'
    assert 'If-None-Match: "v1"' in seen[0] and "If-Modified-Since: Tue" in seen[0]


def test_a_redirect_is_followed_and_every_hop_is_judged_again(monkeypatch):
    base, seen = serve((301, [("Location", "/moved.xml")], b""), (200, [], FEED))
    judged = []
    real = sf.check_url
    monkeypatch.setattr(sf, "check_url", lambda url, allow_local=None: judged.append(url) or real(url, allow_local))
    answer = ff.fetch(base + "/feed.xml", allow_local=True)
    assert answer.body == FEED and answer.url == base + "/moved.xml"
    assert judged == [base + "/feed.xml", base + "/moved.xml"]
    assert seen[1].startswith("GET /moved.xml")


def test_too_many_redirects_stop():
    base, _ = serve(*[(302, [("Location", "/again")], b"")] * (ff.MAX_HOPS + 1))
    with pytest.raises(sf.FetchError) as err:
        ff.fetch(base + "/feed.xml", allow_local=True)
    assert "too many times" in err.value.message


def test_an_error_page_and_a_huge_answer_are_refused():
    base, _ = serve((500, [], b"oops"))
    with pytest.raises(sf.FetchError):
        ff.fetch(base + "/", allow_local=True)
    base, _ = serve((200, [], b"x" * 5000))
    with pytest.raises(sf.FetchError) as err:
        ff.fetch(base + "/", allow_local=True, max_bytes=1000)
    assert "too large" in err.value.message


def test_a_private_address_is_refused_before_anything_is_sent():
    for url in ("https://127.0.0.1/feed", "https://169.254.169.254/latest/meta-data", "http://example.org/feed",
                "https://localhost/feed"):
        with pytest.raises(sf.FetchError):
            ff.fetch(url, allow_local=False)


def test_this_site_is_refused_at_any_hop(monkeypatch):
    monkeypatch.setenv("SERVER_NAME", "127.0.0.1")
    with pytest.raises(sf.FetchError) as err:
        ff.fetch("http://127.0.0.1:9/feed", allow_local=True)
    assert "this site" in err.value.message


def test_safe_fetch_itself_still_follows_no_redirects():
    source = (sf.__file__)
    text = open(source, encoding="utf-8").read()
    assert "allow_redirects" not in text and "follow" not in text.split('"""', 2)[2].replace("not followed", "")
