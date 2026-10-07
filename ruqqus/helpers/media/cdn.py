"""The CDN in front of the site (Cloudflare) keeps a media file that is part of a post for
good. When the post goes away the origin refuses the file at once, but the CDN must be
told to forget its copy too."""
import logging
from os import environ

import gevent
import requests


def absolute(path):
    """The full address of a site-relative media path, as the public sees it."""
    from ruqqus.__main__ import app
    scheme = "https" if app.config.get("FORCE_HTTPS") else "http"
    return f"{scheme}://{app.config['SERVER_NAME']}{path}"


def _purge(urls):
    key, zone = environ.get("CLOUDFLARE_KEY", "").strip(), environ.get("CLOUDFLARE_ZONE", "").strip()
    if not key or not zone:
        return
    try:
        requests.post(f"https://api.cloudflare.com/client/v4/zones/{zone}/purge_cache",
                      headers={"Authorization": f"Bearer {key}", "Content-Type": "application/json"},
                      json={"files": urls}, timeout=10)
    except Exception as e:          # the origin already refuses the file; this only shortens the CDN's memory
        logging.warning("media: could not purge %d address(es) from the CDN: %s", len(urls), e)


def purge(paths):
    """Ask the CDN to drop these media paths. Runs in the background and never raises."""
    urls = [absolute(p) for p in paths if p]
    if urls:
        gevent.spawn(_purge, urls)
