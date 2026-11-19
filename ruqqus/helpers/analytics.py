"""Page-view analytics from a host the site's owner runs (Plausible or a compatible script), off unless set. Pure, no
Flask. Replaces the old Ruqqus line that named plausible.ruqqus.com (bug hunt 2026-10, H7; it had long been commented
out, but a script on a domain this site doesn't own must never come back). The privacy page names the service while it
is on.

Only Plausible's manual script is accepted (its file name has a `manual` part: `script.manual.js`,
`script.manual.outbound-links.js`): the ordinary script reports each page's full address by itself, and an address
such as /@alice/post/<id> names an account. With the manual script the page reports only its kind (`page_kind` in
helpers/jinja2.py, the built-in counter's own list: helpers/traffic.py ENDPOINT_KINDS), never its address (the
owner, 2026-11-19)."""
import re
from urllib.parse import urlsplit

_DOMAIN = re.compile(r"^[a-z0-9](?:[a-z0-9-]{0,62}[a-z0-9])?(?:\.[a-z0-9](?:[a-z0-9-]{0,62}[a-z0-9])?)+$")


def script_src(value):
    """The script's address when it is a plain https address with a host and no user name or password, else None
    (off). A value that isn't one is ignored rather than drawn, so a typo can't put something odd in every page."""
    value = (value or "").strip()
    if not value or any(c.isspace() or c in "\"'<>\\" for c in value):
        return None
    parts = urlsplit(value)
    if parts.scheme != "https" or not parts.hostname or parts.username or parts.password:
        return None
    if not is_manual(parts.path):
        return None
    return value


def is_manual(path):
    """Is this Plausible's manual script, which sends nothing by itself? `script.manual.js` and its combinations."""
    return "manual" in (path or "").rsplit("/", 1)[-1].lower().split(".")[1:-1]


def site_domain(value, server_name):
    """The name the analytics records page views under (Plausible's data-domain): the value given, else the site's
    own address. Only a host name (lowercase letters, digits, dots and hyphens); anything else falls back."""
    for candidate in (value, server_name):
        candidate = (candidate or "").strip().lower().split(":")[0]
        if _DOMAIN.match(candidate):
            return candidate
    return None


def host_of(src):
    """The script's host, for the privacy page to name; None when analytics is off."""
    return urlsplit(src).hostname if src else None


def settings(environ, server_name):
    """(src, domain) for the layout, or (None, None) when analytics is off."""
    src = script_src(environ.get("PLAUSIBLE_SRC"))
    domain = site_domain(environ.get("PLAUSIBLE_DOMAIN"), server_name) if src else None
    if not src or not domain:
        return None, None
    return src, domain
