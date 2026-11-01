"""How long the site's own cookie (`session_ruqqus`) lasts and what identifies whom in it. Flask writes it signed, not
encrypted, so anyone with the browser can read it; the page that says so is help/privacy.html.

Asked once per request, from `before_request` in `__main__.py`.

* **Signed in** (`user_id`): a year from the last visit, with the random `session_id` the form keys are made from.
* **Never signed in**: no identifier and no expiry, so nothing is set by opening a page. The cookie appears only when
  there is something to remember (a feed filter, the sign-up form's one-time token) and lasts until the browser closes.
* **Signed out**: the sign-in leaves the cookie, with the account's number in `feed_defaults_for` and the feed filters (its saved default
  was copied into them on the first feed it opened, and on a shared browser they aren't the next person's). It stays year-long only while
  it holds `history`, the accounts that signed in on this browser, which `check_for_alts` and the signup guard's `ban_evade` read: a
  session cookie would end that check at the first closed browser. Without `history` it is a visitor's.

An old visitor's cookie (from before this rule) is put right on its next request: `session_id` and `_permanent` go, and
Flask deletes a cookie left empty.

* **A QR sign-in** (helpers/qr_login.py): as signed in, with an absolute end of 12 hours. Past it, it is signed out here, whichever
  page is asked for, so it doesn't stay a year-long cookie with an account's id in it (`history`, which only a typed password adds this
  account to, stays as for any sign-out). Nothing here ever moves that end. `qr_pending`, the digest a visitor's browser is waiting on, makes no cookie year-long and goes
  once it is useless or someone signs in.

`session.permanent = ...` in either direction writes the `_permanent` key, which makes the session non-empty and so a
cookie: a visitor's must never be set, only removed. tests/test_session_cookie.py, tests/pg/test_visitor_cookie.py.
"""

import time

from ruqqus.helpers import qr_login

PERMANENT_KEY = "_permanent"
SIGN_IN_KEYS = ("user_id", "session_id", "login_nonce", "no_ads_until", "feed_defaults_for", "media_connect", qr_login.EXPIRES_KEY, qr_login.LIMITED_KEY)
# what routes/front.py writes for a feed's filters. A visitor's stay with the visitor; a sign-in's go with it (`forget_sign_in`)
FILTER_KEYS = ("selected_regions", "langcodes", "catids", "groupids", "feed_post_types", "feed_filter")


def settle(session, new_id, now=None):
    """Put `session` right for who is asking. `new_id` makes a random session id, called only when one is needed."""
    now = time.time() if now is None else now
    qr_login.settle_pending(session, now)
    if session.get("user_id") and not qr_login.session_over(session, now):
        session.pop(qr_login.PENDING_KEY, None)
        if not session.get("session_id"):
            session["session_id"] = new_id()
        if not session.get(PERMANENT_KEY):
            session.permanent = True
        return

    forget_sign_in(session)
    if PERMANENT_KEY in session and not session.get("history"):
        del session[PERMANENT_KEY]


def forget_sign_in(session):
    """The cookie stops naming an account. `settle` reads no database, so a sign-in ended elsewhere (the nonce: log out everywhere, a
    new password) or an account gone is only found out by `get_logged_in_user`, which calls this: until then the cookie looks signed in
    to `settle`, which would take a QR code's digest out of it on every request, and a limited sign-in's marks would pass to whoever
    signs up next on this browser. The filters go only when a sign-in is what is ending: this also runs for every visitor's request, and
    what a visitor chose is theirs."""
    if "user_id" in session or "feed_defaults_for" in session:
        for key in FILTER_KEYS:
            if key in session:
                del session[key]
    for key in SIGN_IN_KEYS:
        if key in session:
            del session[key]
