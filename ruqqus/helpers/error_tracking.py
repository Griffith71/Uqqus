"""Error tracking (Sentry, or a self-hosted GlitchTip: the same protocol), off unless `SENTRY_DSN` is set.

What is sent is kept to what is needed to fix a crash: the exception, its stack (file, function and line, **never local
variables**), the route and the release. Everything that could name a person is dropped before it leaves the server
(`scrub`): request bodies, cookies, query strings, almost every header, the user, the client address, the server name,
the text of a database error after its "[SQL: ...]" marker (SQLAlchemy puts the query's *parameters* there: emails,
usernames, text people wrote), and any email address or IPv4 address left in a message. The privacy page says the same
(templates/help/privacy.html). `scrub` is pure, so it is tested without the SDK (tests/test_error_tracking.py).
"""
import re

_EMAIL = re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}")
_IPV4 = re.compile(r"\b(?:\d{1,3}\.){3}\d{1,3}\b")
_SQL_MARK = re.compile(r"\n?\[(?:SQL|parameters):")


def clean_text(text):
    """A message with nothing personal left in it: cut at a database error's SQL, then no emails or addresses."""
    text = str(text or "")
    cut = _SQL_MARK.search(text)
    if cut:
        text = text[:cut.start()] + " [query removed]"
    return _IPV4.sub("[address]", _EMAIL.sub("[email]", text))


def _clean_request(request):
    out = {"method": request.get("method"), "headers": {}}
    url = str(request.get("url") or "")
    out["url"] = url.split("?", 1)[0]                                            # the path, never the query
    for name, value in (request.get("headers") or {}).items():
        if str(name).lower() in ("user-agent", "content-type", "accept"):
            out["headers"][name] = clean_text(value)
    return out


def scrub(event, hint=None):
    """`before_send`: the event with everything personal removed (always returned, never dropped)."""
    if "request" in event:
        event["request"] = _clean_request(event["request"])
    for key in ("user", "server_name", "contexts_user", "extra"):
        event.pop(key, None)
    event.setdefault("tags", {})
    for exc in (event.get("exception") or {}).get("values", []) or []:
        exc["value"] = clean_text(exc.get("value"))
        for frame in (exc.get("stacktrace") or {}).get("frames", []) or []:
            frame.pop("vars", None)                                              # never local variables
            frame.pop("pre_context", None)
            frame.pop("post_context", None)
            frame.pop("context_line", None)
    if event.get("message"):
        event["message"] = clean_text(event["message"])
    logentry = event.get("logentry")
    if logentry:
        logentry["message"] = clean_text(logentry.get("message"))
        logentry.pop("params", None)
    for crumb in (event.get("breadcrumbs") or {}).get("values", []) or []:
        crumb["message"] = clean_text(crumb.get("message"))
        crumb.pop("data", None)
    return event


def scrub_breadcrumb(crumb, hint=None):
    """`before_breadcrumb`: a breadcrumb is a short note of what happened just before; drop what could be personal."""
    crumb["message"] = clean_text(crumb.get("message"))
    crumb.pop("data", None)
    if crumb.get("category") in ("httplib", "http", "console"):
        return None
    return crumb


def init(environ, version):
    """Starts error tracking when `SENTRY_DSN` is set. Returns whether it did. Call before the Flask app is made."""
    dsn = (environ.get("SENTRY_DSN") or "").strip()
    if not dsn:
        return False
    import sentry_sdk
    sentry_sdk.init(
        dsn=dsn,
        release=environ.get("SENTRY_RELEASE") or version,
        environment=environ.get("SENTRY_ENVIRONMENT") or ("development" if environ.get("FLASK_DEBUG") else "production"),
        send_default_pii=False,
        include_local_variables=False,
        max_request_body_size="never",
        traces_sample_rate=0,
        server_name="",
        before_send=scrub,
        before_breadcrumb=scrub_breadcrumb,
    )
    return True
