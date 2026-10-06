"""Rules for drafts and scheduled posts (the Save draft / Schedule buttons on
Create a post), kept free of Flask and the database so they can be tested.

A draft is the saved part of the composer: title, link, text, the guilds to
forward to and the options. It is never a row in `submissions` (it would show
up in feeds, counts and search). A scheduled draft is published when its time
comes by scripts/publish_scheduled.py, which submits it through the real
submit_post route, so every posting rule applies exactly as if the author
pressed Post.
"""
import re

from . import comment_permission as cperm
from .post_fields import BODY_MAX, TITLE_MAX, URL_MAX, PostFieldError, check_body, flag

DRAFT_LIMIT = 50                       # per user, published ones do not count
MIN_LEAD_SECONDS = 5 * 60              # a scheduled post must be at least this far ahead
MAX_LEAD_SECONDS = 366 * 24 * 60 * 60  # ... and at most about a year
MAX_ATTEMPTS = 8                       # tries while the account is in a posting cooldown
KEEP_PUBLISHED_SECONDS = 7 * 24 * 60 * 60
STALE_PUBLISHING_SECONDS = 10 * 60     # a claim older than this is an interrupted publish
FORWARD_GUILDS_MAX = 20
GUILD_NAME_MAX = 50

# draft: saved, not scheduled; scheduled: waiting for its time; publishing: claimed by
# the scheduler; published: done (kept briefly); failed: could not be published
STATUSES = ("draft", "scheduled", "publishing", "published", "failed")
EDITABLE = ("draft", "scheduled", "failed")


def clean_fields(form):
    """The saved fields of the composer, validated. Empty is allowed (a draft
    can be a few words), a title is only required to schedule."""
    title = (form.get("title") or "").replace("\r", "").replace("\n", "").replace("\t", "").strip()
    if len(title) > TITLE_MAX:
        raise PostFieldError(f"{TITLE_MAX} character limit for titles.")

    url = (form.get("url") or "").strip()
    if len(url) > URL_MAX:
        raise PostFieldError(f"{URL_MAX} character limit for URLs.")

    body = check_body(form.get("body"))

    seen, guilds = set(), []
    for name in form.getlist("forward_guilds"):
        name = name.strip().lstrip("+")
        if not name or name.lower() in seen:
            continue
        if len(name) > GUILD_NAME_MAX:
            raise PostFieldError("That guild name is too long.")
        seen.add(name.lower())
        guilds.append(name)
    if len(guilds) > FORWARD_GUILDS_MAX:
        raise PostFieldError(f"Forward to at most {FORWARD_GUILDS_MAX} guilds.")

    raw_permission = form.get("comment_permission", "")
    permission = cperm.EVERYONE if str(raw_permission).strip() == "" else cperm.parse(raw_permission)
    if permission is None:
        raise PostFieldError("Choose who can comment.")

    return {
        "title": title,
        "url": url,
        "body": body,
        "forward_guilds": guilds,
        "options": {
            "comment_permission": permission,
            "paid_partnership": flag(form, "paid_partnership"),
            "made_with_ai": flag(form, "made_with_ai"),
            "anonymous": flag(form, "anonymous"),
            "sensitive": flag(form, "sensitive"),
        },
    }


def is_empty(fields):
    return not (fields["title"] or fields["url"] or fields["body"])


def schedule_time(raw, now):
    """The publish time (epoch seconds) from the form, inside the allowed window."""
    try:
        when = int(str(raw).strip())
    except ValueError:
        raise PostFieldError("Pick a date and time.")
    if when < now + MIN_LEAD_SECONDS:
        raise PostFieldError("Pick a time at least 5 minutes from now.")
    if when > now + MAX_LEAD_SECONDS:
        raise PostFieldError("You can schedule a post up to a year ahead.")
    return when


def require_title(fields):
    if not fields["title"]:
        raise PostFieldError("A scheduled post needs a title.")


def publish_form(fields, formkey):
    """The form data the publisher posts to submit_post: the same fields the
    composer would have sent. A tick box is only sent when ticked."""
    options = fields["options"]
    data = {
        "title": fields["title"],
        "url": fields["url"],
        "body": fields["body"],
        "forward_guilds": list(fields["forward_guilds"]),
        "comment_permission": str(options.get("comment_permission", cperm.EVERYONE)),
        "formkey": formkey,
    }
    for name in ("sensitive", "paid_partnership", "made_with_ai", "anonymous"):
        if options.get(name):
            data[name] = "true"
    return data


_COOLDOWN = re.compile(r"cooldown active for another (\d+):(\d+)", re.I)


def cooldown_seconds(message):
    """Seconds left of a posting cooldown, read from submit_post's 429 message,
    or None when the message is about something else."""
    m = _COOLDOWN.search(message or "")
    return int(m.group(1)) * 60 + int(m.group(2)) if m else None


def retry_at(now, message, attempts):
    """When to try again after a refusal, or None to give up: only a posting
    cooldown is worth waiting out, and only so many times."""
    wait = cooldown_seconds(message)
    if wait is None or attempts >= MAX_ATTEMPTS:
        return None
    return now + max(60, wait + 5)


def failure_notice(title, reason):
    """The notification text for a scheduled post that could not be published."""
    shown = title if title else "Untitled"
    return (f"Your scheduled post \"{shown}\" could not be published: {reason}\n\n"
            "It is saved in your drafts, so you can fix it and post it or schedule it again.")


__all__ = [
    "DRAFT_LIMIT", "MIN_LEAD_SECONDS", "MAX_LEAD_SECONDS", "MAX_ATTEMPTS", "KEEP_PUBLISHED_SECONDS",
    "STALE_PUBLISHING_SECONDS", "STATUSES", "EDITABLE", "BODY_MAX",
    "clean_fields", "is_empty", "schedule_time", "require_title", "publish_form",
    "cooldown_seconds", "retry_at", "failure_notice", "PostFieldError",
]
