"""Was a form sent from another site? For the forms a visitor fills in before they have a form key, signing in above
all (bug hunt 2026-10, M8: another site could sign a visitor into the attacker's account, whose posts, chats and
uploads they then make as if their own). Pure, no Flask.

A browser says where a POST comes from: `Origin` on every cross-site form submission (all current browsers), and
`Sec-Fetch-Site`. A request that names another site, or the opaque origin "null" (a sandboxed frame, a data: page:
what an attacker would use to hide where it comes from), is refused. A request with neither header did not come from a
browser on another site (an app, a script, a test), so it is not this attack and is let through."""
from urllib.parse import urlsplit


def from_another_site(origin, fetch_site, host, site_names=()):
    """True when the browser says the request comes from somewhere other than this site: `host` (the Host it was sent
    to) or one of `site_names` (the names it is configured with, SERVER_NAME and the onion name: a proxy that passes on
    the name only, as nginx's `$host` does, drops a port the browser's Origin keeps). The scheme is not compared:
    behind a TLS proxy the site itself may be reached over http."""
    if (fetch_site or "").strip().lower() == "cross-site":
        return True
    origin = (origin or "").strip()
    if not origin:
        return False
    if origin.lower() == "null":
        return True
    try:
        parts = urlsplit(origin)
        netloc = parts.netloc.lower()
    except ValueError:
        return True
    if parts.scheme not in ("http", "https") or not netloc:
        return True
    ours = {n.strip().lower() for n in (host, *site_names) if n and n.strip()}
    return netloc not in ours
