"""`POST /consent/video`: the server sets the reader's answer to the video question (helpers/consent.py, CLAUDE.md "Video consent").
Named consent_pages.py, not consent.py: the routes' star import would put a module named like the helper over it.

WebKit keeps a cookie written by document.cookie for seven days at most, so assets/js/consent.js writes the cookie itself (the page
works at once, and if this fails) and then sends the answer here to have it replaced by one a response set. It stores nothing:
no row, no session cookie, no question to the database (`__main__.before_request` lets this path through before it opens one, so
the site's own ban table isn't asked here: the answer only ever reaches the browser that sent it). The answer travels in the body,
so it is in no address and no access log. A visitor has no form key, so another site's request is refused by what the browser says
about where it comes from (helpers/same_origin.py). A refusal has no body, a limited answer included (`routes/errors.py` `error_429`),
since consent.js says nothing when this fails, and `__main__.after_request` makes every answer on this path no-store."""
import time
from os import environ

from flask import make_response, request

from ruqqus.__main__ import app, limiter
from ruqqus.helpers import consent as consent_rules
from ruqqus.helpers import same_origin

MAX_BODY = 64                      # `choice=yes` is ten bytes: nothing longer has to be read at all


@app.post(consent_rules.ANSWER_PATH)
@limiter.limit("30/minute")
def video_consent_answer():
    if same_origin.from_another_site(request.headers.get("Origin"), request.headers.get("Sec-Fetch-Site"), request.host,
                                     (app.config.get("SERVER_NAME") or "", environ.get("ONION_NAME", ""))):
        return "", 403
    size = request.content_length
    if size is None or size > MAX_BODY:
        return "", 400
    sent = request.form.getlist("choice")
    value = consent_rules.cookie_value(sent[0] if len(sent) == 1 else None, time.time())
    if value is None:
        return "", 400
    resp = make_response("", 204)
    consent_rules.set_answer(resp, value, request.is_secure, request.headers.get("Origin"))
    return resp
