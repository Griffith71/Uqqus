"""Which embedded players get the strict sandbox (stdlib only, so it can be tested without the app).

A named video platform's player (KNOWN_PROVIDER_HANDLERS in helpers/embed.py, embed type "iframe") is trusted:
it is drawn without a sandbox. An iframe src scraped from an arbitrary page's og:video tag is not
("iframe_untrusted", sandbox="allow-scripts"), and most real players cannot load inside that sandbox.

When the submitted page is itself on one of those platforms and its og:video points back at that same
platform over https, it is the platform's own player, as trusted as the platform's oEmbed answer. That
answer can fail or time out at submit time, which used to leave such a post with a player that never
played (an Odysee post of 2026-10-04; repaired by scripts/migrations/2026-10-10_trusted_provider_embeds.sql).
"""
from urllib.parse import urlparse


def _on(hostname, domain):
    hostname = (hostname or "").lower()
    return hostname == domain or hostname.endswith("." + domain)


def same_provider_player(page_url, src, domains):
    """True when `page_url` and the https `src` are both on one of `domains` (the same one)."""
    page, frame = urlparse(page_url or ""), urlparse(src or "")
    if frame.scheme != "https" or page.scheme not in ("http", "https"):
        return False
    return any(_on(page.hostname, d) and _on(frame.hostname, d) for d in domains)
