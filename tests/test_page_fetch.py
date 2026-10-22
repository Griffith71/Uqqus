"""Reading a page or a picture a member linked (ruqqus/helpers/page_fetch.py): safe_fetch's guard at every hop, the
body read only for what the caller wants and never past its limit (bug hunt 2026-10, H1 and H2). Real local sockets,
as tests/test_feed_fetch.py uses."""
import socket

import pytest

from ruqqus.helpers import page_fetch as pf
from ruqqus.helpers import safe_fetch as sf

from test_feed_fetch import serve

PAGE = b"<html><head><title>Hello</title></head><body></body></html>"


def test_an_http_address_is_read_as_https_and_nothing_else_changes():
    assert pf.as_https("http://example.org/a?b=1#c") == "https://example.org/a?b=1#c"
    assert pf.as_https("HTTP://Example.org") == "https://example.org"
    assert pf.as_https("http://example.org:80/x") == "https://example.org/x"
    assert pf.as_https("http://example.org:8080/x") == "http://example.org:8080/x", "left for the guard to refuse"
    assert pf.as_https("https://example.org/x") == "https://example.org/x"
    assert pf.as_https("ftp://example.org/x") == "ftp://example.org/x"


def test_a_page_comes_back_and_nothing_about_anyone_is_sent():
    base, seen = serve((200, [("Content-Type", "text/html; charset=utf-8")], PAGE))
    page = pf.get(base + "/a", user_agent="agent-x", allow_local=True)
    assert page.status == 200 and page.body == PAGE and page.complete
    assert page.headers == {"Content-Type": "text/html; charset=utf-8"}
    request = seen[0].lower()
    assert request.startswith("get /a http/1.1") and "agent-x" in request
    assert "cookie" not in request and "referer" not in request


def test_the_body_is_read_only_for_what_the_caller_wants():
    base, _ = serve((200, [("Content-Type", "video/mp4")], b"\x00" * 5000))
    page = pf.get(base + "/v.mp4", allow_local=True)
    assert page.status == 200 and page.content_type == "video/mp4" and page.body == b"" and not page.complete
    base, _ = serve((404, [("Content-Type", "text/html")], b"gone"))
    page = pf.get(base + "/", allow_local=True)
    assert page.status == 404 and page.body == b""


def test_a_large_answer_is_cut_at_the_limit_and_says_so():
    base, _ = serve((200, [("Content-Type", "text/html")], b"x" * 5000))
    page = pf.get(base + "/", allow_local=True, max_bytes=1000)
    assert len(page.body) == 1000 and not page.complete


def test_a_redirect_is_followed_and_every_hop_is_judged_again(monkeypatch):
    base, seen = serve((302, [("Location", "/moved")], b""), (200, [("Content-Type", "text/html")], PAGE))
    judged = []
    real = sf.check_url
    monkeypatch.setattr(sf, "check_url", lambda url, allow_local=None, max_len=sf.MAX_URL:
                        judged.append(url) or real(url, allow_local, max_len))
    page = pf.get(base + "/a", allow_local=True)
    assert page.body == PAGE and page.url == base + "/moved"
    assert judged == [base + "/a", base + "/moved"]


def test_too_many_redirects_stop():
    base, _ = serve(*[(302, [("Location", "/again")], b"")] * (pf.MAX_HOPS + 1))
    with pytest.raises(sf.FetchError):
        pf.get(base + "/", allow_local=True)


@pytest.mark.parametrize("url", [
    "http://127.0.0.1/", "https://127.0.0.1/", "http://169.254.169.254/latest/meta-data/", "http://[::1]/",
    "http://localhost/", "https://postgres/", "https://redis.internal/", "https://user:pw@example.org/",
    "https://example.org:8443/", "ftp://example.org/", "file:///etc/passwd", "gopher://example.org/",
])
def test_an_address_off_the_public_internet_is_refused_before_anything_is_sent(url):
    with pytest.raises(sf.FetchError):
        pf.get(url, allow_local=False)


def test_a_public_name_that_leads_to_a_private_address_is_refused(monkeypatch):
    connected = []
    monkeypatch.setattr(sf, "_lookup", lambda host, port: [(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("10.0.0.5", port))])
    monkeypatch.setattr(socket, "create_connection", lambda *a, **k: connected.append(a))
    with pytest.raises(sf.FetchError):
        pf.get("http://looks-public.example.org/", allow_local=False)
    assert connected == []


def test_a_long_article_address_is_fine_while_a_feed_servers_limit_stays():
    long_url = "https://example.org/" + "a" * 1500
    assert sf.check_url(long_url, False, pf.MAX_URL)[1] == "example.org"
    with pytest.raises(sf.FetchError):
        sf.check_url(long_url, False)


def test_an_address_with_other_scripts_is_sent_as_the_browser_would():
    assert pf._wire("bücher.example", "/wiki/Café?q=ü") == ("xn--bcher-kva.example", "/wiki/Caf%C3%A9?q=%C3%BC")
    assert pf._wire("example.org", "/a%20b/c?d=1&e=2") == ("example.org", "/a%20b/c?d=1&e=2"), "escapes kept as they are"
    base, seen = serve((200, [("Content-Type", "text/html")], PAGE))
    page = pf.get(base + "/wiki/Café", allow_local=True)
    assert page.status == 200 and seen[0].startswith("GET /wiki/Caf%C3%A9 HTTP/1.1")


def test_a_redirect_written_in_utf_8_is_followed():
    # the stand-in server writes its headers as UTF-8 bytes; http.client reads them back as latin-1
    base, seen = serve((302, [("Location", "/wiki/Café")], b""), (200, [("Content-Type", "text/html")], PAGE))
    page = pf.get(base + "/a", allow_local=True)
    assert page.status == 200 and seen[1].startswith("GET /wiki/Caf%C3%A9 HTTP/1.1")
