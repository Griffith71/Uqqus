"""Which address a request comes from (audit X-01). Every rate limit, the login and posting throttles, the guild
limit's network check, the tips self-boost check, creator-fund flags and view counts key on `request.remote_addr`, so
it must be the client's real address, never something the client wrote itself.

The app used `ProxyFix(x_for=3)`: it believed the third address from the right of `X-Forwarded-For`, but at most two
proxies stand in front of it (Cloudflare, then nginx), so that third entry was the client's own value, and a rotating
fake header got past every limit. Now the trust is set by the deployment:

- `TRUSTED_PROXY_HOPS` (default 0): how many proxies in front of the app append to `X-Forwarded-For`. 0 means the
  socket's address is used as it is (the local Docker setup, where gunicorn listens directly). nginx alone: 1.
  Cloudflare then nginx: 2.
- `TRUST_CF_CONNECTING_IP=1`: use Cloudflare's `CF-Connecting-IP` instead. **Only when the origin accepts
  connections from Cloudflare's addresses alone** (the firewall the call relay already needs): otherwise anyone can
  send the header.
"""
import ipaddress
from os import environ


def _valid(value):
    try:
        return str(ipaddress.ip_address((value or "").strip()))
    except ValueError:
        return None


def resolve(environ_, hops=0, trust_cf=False):
    """The client address for one WSGI environ."""
    if trust_cf:
        cf = _valid(environ_.get("HTTP_CF_CONNECTING_IP"))
        if cf:
            return cf
    if hops > 0:
        chain = [part.strip() for part in environ_.get("HTTP_X_FORWARDED_FOR", "").split(",") if part.strip()]
        # each trusted proxy appended the address it saw: the client is `hops` entries from the right
        if len(chain) >= hops:
            found = _valid(chain[-hops])
            if found:
                return found
    return environ_.get("REMOTE_ADDR")


class ClientAddress:
    """WSGI middleware: sets REMOTE_ADDR to the resolved client address (the original stays in
    `ruqqus.socket_addr`)."""

    def __init__(self, app, hops=None, trust_cf=None):
        self.app = app
        self.hops = int(environ.get("TRUSTED_PROXY_HOPS", "0") or 0) if hops is None else hops
        self.trust_cf = (environ.get("TRUST_CF_CONNECTING_IP", "") == "1") if trust_cf is None else trust_cf

    def __call__(self, environ_, start_response):
        environ_["ruqqus.socket_addr"] = environ_.get("REMOTE_ADDR")
        address = resolve(environ_, self.hops, self.trust_cf)
        if address:
            environ_["REMOTE_ADDR"] = address
        if self.hops > 0 or self.trust_cf:
            proto = environ_.get("HTTP_X_FORWARDED_PROTO", "").split(",")[-1].strip()
            if proto in ("http", "https"):
                environ_["wsgi.url_scheme"] = proto
        return self.app(environ_, start_response)
