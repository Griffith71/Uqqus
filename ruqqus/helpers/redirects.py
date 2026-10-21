"""Where a browser may be sent from a value it brought along (`?redirect=`, a form's `redirect`): only a path on this
site (bug hunt 2026-10, H5: signing in used to send people to any address, a phishing aid). Pure, no Flask."""
from urllib.parse import urlsplit


def same_site(target, default="/"):
    """`target` when it is a path on this site ("/settings", "/@a/post/1?x=y#z"), else `default`. Refused: anything
    with a scheme or a host, "//host" and "/\\host" (browsers read both as another site), backslashes, whitespace or
    control characters anywhere (they let a value pass as one thing here and another in a browser or a header)."""
    if not isinstance(target, str) or not target.startswith("/") or target.startswith(("//", "/\\")):
        return default
    if any(c == "\\" or c.isspace() or ord(c) < 32 or ord(c) == 127 for c in target):
        return default
    parts = urlsplit(target)
    if parts.scheme or parts.netloc:
        return default
    return target
