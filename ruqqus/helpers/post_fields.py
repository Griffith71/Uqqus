"""Validation rules for the fields of a post an author can edit.

Mirrors what submit_post applies when the post is created (routes/posts.py).
Deliberately free of Flask/database imports so the rules can be unit-tested.
"""
from urllib.parse import urlparse, ParseResult, urlunparse

import bleach

TITLE_MAX = 280
BODY_MAX = 25000
URL_MAX = 2048


class PostFieldError(ValueError):
    """A rejected field value; str(error) is the message shown to the user."""


def clean_title(raw):
    title = (raw or "").strip()
    title = title.replace("\n", "").replace("\r", "").replace("\t", "")
    title = bleach.clean(title)

    if not title:
        raise PostFieldError("A post needs a title.")
    if len(title) > TITLE_MAX:
        raise PostFieldError(f"{TITLE_MAX} character limit for titles.")
    return title


def check_body(raw):
    body = raw or ""
    if len(body) > BODY_MAX:
        raise PostFieldError(f"{BODY_MAX} character limit for text body.")
    return body


def normalize_url(raw):
    """Empty string for "no link"; otherwise the link forced to https."""
    url = (raw or "").strip()
    if not url:
        return ""
    if len(url) > URL_MAX:
        raise PostFieldError(f"{URL_MAX} character limit for URLs.")

    if "://" not in url:
        url = "https://" + url
    parsed = urlparse(url)
    if parsed.scheme not in ("http", "https") or not parsed.netloc or "." not in parsed.netloc:
        raise PostFieldError("That doesn't look like a valid link.")

    return urlunparse(ParseResult(scheme="https",
                                  netloc=parsed.netloc,
                                  path=parsed.path,
                                  params=parsed.params,
                                  query=parsed.query,
                                  fragment=parsed.fragment))
