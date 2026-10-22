"""Was a form sent from another site? (ruqqus/helpers/same_origin.py, bug hunt 2026-10, M8: login CSRF.)"""
import pytest

from ruqqus.helpers.same_origin import from_another_site


@pytest.mark.parametrize("origin, fetch_site, host", [
    ("https://laba.example", None, "laba.example"),
    ("http://laba.example", "same-origin", "laba.example"),          # behind a TLS proxy the scheme may differ
    ("https://LABA.example", None, "laba.example"),
    ("http://localhost:8000", None, "localhost:8000"),
    (None, None, "laba.example"),                                      # no browser on another site: an app, a script
    ("", "none", "laba.example"),                                      # typed in or a bookmark
    (None, "same-site", "laba.example"),
])
def test_the_sites_own_pages_and_non_browsers_may_send_it(origin, fetch_site, host):
    assert not from_another_site(origin, fetch_site, host)


@pytest.mark.parametrize("origin, fetch_site, host", [
    ("https://evil.example", None, "laba.example"),
    ("https://laba.example.evil.example", None, "laba.example"),
    ("https://evil-laba.example", None, "laba.example"),
    ("https://laba.example:8443", None, "laba.example"),
    ("http://localhost:9000", None, "localhost:8000"),
    ("null", None, "laba.example"),                                    # a sandboxed frame or a data: page
    ("NULL", None, "laba.example"),
    ("file://", None, "laba.example"),
    ("chrome-extension://abc", None, "laba.example"),
    (None, "cross-site", "laba.example"),
    ("https://laba.example", "cross-site", "laba.example"),           # the browser's own word wins
    ("https://[bad", None, "laba.example"),
])
def test_another_site_is_refused(origin, fetch_site, host):
    assert from_another_site(origin, fetch_site, host)
