"""The client address every rate limit keys on (helpers/client_address.py, audit X-01): a client's own
X-Forwarded-For entries are never believed beyond the deployment's real proxies."""
from ruqqus.helpers.client_address import ClientAddress, resolve


def env(xff="", remote="10.0.0.9", cf=None):
    e = {"REMOTE_ADDR": remote, "HTTP_X_FORWARDED_FOR": xff}
    if cf is not None:
        e["HTTP_CF_CONNECTING_IP"] = cf
    return e


def test_no_trusted_proxy_uses_the_socket():
    assert resolve(env("203.0.113.5, 10.0.0.1, 10.0.0.2"), hops=0) == "10.0.0.9"


def test_trusted_hops_count_from_the_right_so_a_forged_prefix_is_ignored():
    # client forged "1.2.3.4"; Cloudflare appended the real client 198.51.100.7; nginx appended Cloudflare's address
    forged = env("1.2.3.4, 198.51.100.7, 172.70.1.1")
    assert resolve(forged, hops=2) == "198.51.100.7"
    assert resolve(env("198.51.100.7"), hops=1) == "198.51.100.7"
    assert resolve(env("x, y"), hops=1) == "10.0.0.9"          # junk: fall back to the socket
    assert resolve(env(""), hops=2) == "10.0.0.9"              # fewer entries than proxies: the socket


def test_cloudflare_header_only_when_trusted():
    assert resolve(env(cf="198.51.100.7"), trust_cf=False) == "10.0.0.9"
    assert resolve(env(cf="198.51.100.7"), trust_cf=True) == "198.51.100.7"
    assert resolve(env(cf="not an address"), trust_cf=True) == "10.0.0.9"


def test_middleware_sets_remote_addr():
    seen = {}

    def app(environ, start_response):
        seen.update(environ)
        return []

    ClientAddress(app, hops=1, trust_cf=False)(env("203.0.113.5"), None)
    assert seen["REMOTE_ADDR"] == "203.0.113.5" and seen["ruqqus.socket_addr"] == "10.0.0.9"
    ClientAddress(app, hops=0, trust_cf=False)(env("203.0.113.5"), None)
    assert seen["REMOTE_ADDR"] == "10.0.0.9"
