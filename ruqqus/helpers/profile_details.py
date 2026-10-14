"""What a member writes about themselves on the profile header (templates/partials/profile_header.html): a job, a
location and a website. Pure rules, no I/O (tests/test_profile_details.py).

* Job and location are one line of plain text (drawn escaped), at most TEXT_MAX characters. The word filter rates
  them with the bio (word_filter_store.apply_user_severity), and they are hidden whenever the bio is
  (User.details_for).
* The website is an http(s) address with a real host name, no user name or password in it, at most WEBSITE_MAX
  characters. It is drawn with rel="nofollow ugc noopener noreferrer" and never as raw HTML; the banned-domain list
  is asked by the route (helpers/filters.filter_comment_html), as for a link in a bio.
"""
import re
from urllib.parse import urlsplit, urlunsplit

TEXT_MAX = 60
WEBSITE_MAX = 200

_SPACES = re.compile(r"\s+")
_HOST = re.compile(r"^(?=.{1,253}$)([a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?\.)+[a-z][a-z0-9-]{0,62}[a-z0-9]$")


class Refused(ValueError):
    """A value that cannot be saved; the message is shown to the member."""


def clean_text(value, label):
    """One line of plain text, spaces collapsed; "" clears it."""
    text = _SPACES.sub(" ", str(value or "")).strip()
    if len(text) > TEXT_MAX:
        raise Refused(f"{label} can be at most {TEXT_MAX} characters.")
    if "<" in text or ">" in text:
        raise Refused(f"{label} can't contain < or >.")
    return text


def clean_website(value):
    """The address to store ("" clears it). A missing scheme means https."""
    raw = str(value or "").strip()
    if not raw:
        return ""
    if len(raw) > WEBSITE_MAX:
        raise Refused(f"The website can be at most {WEBSITE_MAX} characters.")
    if any(c.isspace() for c in raw):
        raise Refused("The website can't contain spaces.")
    if "://" not in raw:
        if ":" in raw.split("/")[0]:      # javascript:..., data:..., mailto:... (a port needs a scheme too)
            raise Refused("The website must start with https:// or http://.")
        raw = "https://" + raw
    try:
        parts = urlsplit(raw)
        port = parts.port
    except ValueError:
        raise Refused("That website address isn't valid.")
    if parts.scheme.lower() not in ("http", "https"):
        raise Refused("The website must start with https:// or http://.")
    if parts.username is not None or parts.password is not None:
        raise Refused("The website can't contain a user name or password.")
    host = (parts.hostname or "").lower().rstrip(".")
    if not _HOST.match(host):
        raise Refused("The website needs a full address, such as example.com.")
    netloc = host if port is None else f"{host}:{port}"
    cleaned = urlunsplit((parts.scheme.lower(), netloc, parts.path, parts.query, ""))
    if len(cleaned) > WEBSITE_MAX:
        raise Refused(f"The website can be at most {WEBSITE_MAX} characters.")
    return cleaned


def website_label(url, limit=40):
    """How the address reads on the profile: no scheme, no "www.", no trailing slash, shortened."""
    if not url:
        return ""
    parts = urlsplit(url)
    host = (parts.hostname or "")
    if host.startswith("www."):
        host = host[4:]
    label = host + (parts.path if parts.path not in ("", "/") else "")
    label = label.rstrip("/")
    return label if len(label) <= limit else label[:limit - 1] + "…"


def rated_text(job, location):
    """What the word filter reads besides the bio: job and location as plain text."""
    return " ".join(x for x in (job, location) if x)
