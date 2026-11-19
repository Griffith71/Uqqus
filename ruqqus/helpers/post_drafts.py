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
from . import circles
from . import polls as poll_rules
from .i18n_runtime import N_, tr, trn
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
        raise PostFieldError(trn("%(num)s character limit for titles.", "%(num)s character limit for titles.",
                                 TITLE_MAX))

    url = (form.get("url") or "").strip()
    if len(url) > URL_MAX:
        raise PostFieldError(trn("%(num)s character limit for URLs.", "%(num)s character limit for URLs.", URL_MAX))

    body = check_body(form.get("body"))

    seen, guilds = set(), []
    for name in form.getlist("forward_guilds"):
        name = name.strip().lstrip("+")
        if not name or name.lower() in seen:
            continue
        if len(name) > GUILD_NAME_MAX:
            raise PostFieldError(tr("That guild name is too long."))
        seen.add(name.lower())
        guilds.append(name)
    if len(guilds) > FORWARD_GUILDS_MAX:
        raise PostFieldError(trn("Forward to at most %(num)s guild.", "Forward to at most %(num)s guilds.",
                                 FORWARD_GUILDS_MAX))

    raw_permission = form.get("comment_permission", "")
    permission = cperm.EVERYONE if str(raw_permission).strip() == "" else cperm.parse(raw_permission)
    if permission is None:
        raise PostFieldError(tr("Choose who can comment."))

    # which circles are the author's own is decided when the post is made (circle_store.resolve in submit_post)
    audience, circle_tokens, audience_error = circles.parse_audience(form.get("audience"), form.getlist("circles"))
    if audience_error:
        raise PostFieldError(audience_error)

    # a draft may hold an unfinished poll (one option); scheduling it needs a whole one (require_poll)
    try:
        poll_options = poll_rules.clean_options(form.getlist("poll_option"), complete=False)
        poll_hours = poll_rules.clean_hours(form.get("poll_hours")) if poll_options else poll_rules.DEFAULT_HOURS
    except poll_rules.PollError as error:
        raise PostFieldError(error.message)

    return {
        "title": title,
        "url": url,
        "body": body,
        "forward_guilds": guilds,
        "options": {
            "poll_options": poll_options,
            "poll_hours": poll_hours,
            "comment_permission": permission,
            "post_to_profile": flag(form, "post_to_profile", True),      # "where to post": the profile, as well as the guilds
            "audience": audience,
            "circles": list(circle_tokens),
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
        raise PostFieldError(tr("Pick a date and time."))
    if when < now + MIN_LEAD_SECONDS:
        raise PostFieldError(tr("Pick a time at least 5 minutes from now."))
    if when > now + MAX_LEAD_SECONDS:
        raise PostFieldError(tr("You can schedule a post up to a year ahead."))
    return when


def require_title(fields):
    if not fields["title"]:
        raise PostFieldError(tr("A scheduled post needs a title."))


def require_poll(fields):
    """A scheduled post is published as it is: a poll in it must be a whole one."""
    try:
        poll_rules.clean_options(fields["options"].get("poll_options"), complete=True)
    except poll_rules.PollError as error:
        raise PostFieldError(error.message)


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
        "post_to_profile": "true" if options.get("post_to_profile", True) else "false",
        "formkey": formkey,
    }
    if options.get("audience"):
        data["audience"] = str(options["audience"])
        data["circles"] = [str(c) for c in options.get("circles") or []]
    for name in ("sensitive", "paid_partnership", "made_with_ai", "anonymous"):
        if options.get(name):
            data[name] = "true"
    if options.get("poll_options"):
        # the length counts from the moment of publishing
        data["poll_option"] = list(options["poll_options"])
        data["poll_hours"] = str(options.get("poll_hours") or poll_rules.DEFAULT_HOURS)
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


# Stored in English as the draft's error (drawn with msg) and put in the author's notice after "could not be
# published:" (failure_message).
# Translators: why a scheduled post could not be published. Each completes the sentence "Your scheduled post could
# not be published: ...". "Post" is the name of the button.
STATUS_REASONS = {
    400: N_("something in it isn't accepted any more (a link, a guild or an option). Open it and press Post to see "
            "what."),
    401: N_("you were signed out."),
    403: N_("your account couldn't post it at that moment (it is on hold or suspended, or the rules of a guild it was forwarded to). "
            "Open it and press Post to see what."),
    404: N_("a guild or post it named no longer exists."),
    409: N_("it clashed with something already posted."),
    413: N_("it is too large."),
    429: N_("you were posting too fast, and it couldn't wait any longer."),
}
SERVER_PROBLEM = N_("the site had a problem. Try posting it yourself or scheduling it again.")
REFUSED = N_("it was refused.")


def status_reason(status):
    """What a refusal with no message of its own means, in words (audit P-02: members were told "403 Forbidden")."""
    if status in STATUS_REASONS:
        return STATUS_REASONS[status]
    if status >= 500:
        return SERVER_PROBLEM
    return REFUSED


# Translators: a notification. %(title)s is the post's title; %(reason)s says why, as a sentence starting in lowercase.
_FAILED = N_("Your scheduled post \"%(title)s\" could not be published: %(reason)s\n\n"
             "It is saved in your drafts, so you can fix it and post it or schedule it again.")
# Translators: a notification about a post with no title. %(reason)s says why, as a sentence starting in lowercase.
_FAILED_UNTITLED = N_("Your scheduled post \"Untitled\" could not be published: %(reason)s\n\n"
                      "It is saved in your drafts, so you can fix it and post it or schedule it again.")


def failure_message(title, reason):
    """(message, values) of the notification for a scheduled post that could not be published, for alerts.notify:
    stored in English with its values, read in the author's own language."""
    if title:
        return _FAILED, {"title": title, "reason": reason}
    return _FAILED_UNTITLED, {"reason": reason}


def failure_notice(title, reason):
    """The notification text for a scheduled post that could not be published, as it is stored (English)."""
    message, values = failure_message(title, reason)
    return message % values


__all__ = [
    "DRAFT_LIMIT", "MIN_LEAD_SECONDS", "MAX_LEAD_SECONDS", "MAX_ATTEMPTS", "KEEP_PUBLISHED_SECONDS",
    "STALE_PUBLISHING_SECONDS", "STATUSES", "EDITABLE", "BODY_MAX",
    "STATUS_REASONS", "status_reason", "clean_fields", "is_empty", "schedule_time", "require_title", "publish_form",
    "cooldown_seconds", "retry_at", "failure_message", "failure_notice", "PostFieldError",
]
