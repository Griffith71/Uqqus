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


_NO = {"", "0", "false", "off", "no", "none", "null"}


def flag(form, name, default=False):
    """A yes/no form field (content disclosure: paid partnership, made with AI).

    `default` when the field is absent, so an edit that leaves it out keeps
    what is stored. Otherwise true unless every value sent says no. A ticked
    checkbox is sent as "true" and an unticked one not at all, so edit forms
    send an empty hidden field next to the checkbox for "unticked" to arrive."""
    if name not in form:
        return bool(default)
    return any(str(value).strip().lower() not in _NO for value in form.getlist(name))


# --- saved templates (the Template button in the post editor) ----------------

TEMPLATE_NAME_MAX = 60
TEMPLATE_LIMIT = 20   # per user


def clean_template_name(raw):
    """One line, single spaces, 1-60 characters."""
    name = " ".join((raw or "").split())
    if not name:
        raise PostFieldError("Give the template a name.")
    if len(name) > TEMPLATE_NAME_MAX:
        raise PostFieldError(f"{TEMPLATE_NAME_MAX} character limit for template names.")
    return name


def check_template_body(raw):
    """The template text as written (inner formatting kept), within the post body limit."""
    body = (raw or "").strip("\r\n")
    if not body.strip():
        raise PostFieldError("A template needs some text.")
    return check_body(body)
