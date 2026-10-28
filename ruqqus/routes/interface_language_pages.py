"""Choosing the interface language from the footer (helpers/i18n.py; the owner, 2026-10-24: a visitor gets their
browser's language, and anyone can pick another). Named interface_language_pages.py, not i18n.py: the routes' star
import would put a module named like the helper over it (the star-import trap in CLAUDE.md).

`GET /interface_language?lang=<key>&next=<path>`: a link, so it works without script. For a visitor it is the
`ui_lang` cookie; for a member it is their own "Your language" (moved to the front of `users.languages`, Settings >
Content), which every request reads first. A request another site sends changes nothing (helpers/same_origin.py), and
`next` only ever leads to a path on this site (helpers/redirects.same_site)."""
from os import environ

from flask import g, redirect, request

from ruqqus.helpers import i18n, same_origin, translation
from ruqqus.helpers.redirects import same_site
from ruqqus.helpers.wrappers import auth_desired
from ruqqus.__main__ import app


def moved_to_front(saved, key):
    """A member's languages (as saved: "es,fr") with `key` first, once, keeping the rest in order."""
    keys = translation.parse_languages(saved)
    return [key] + [k for k in keys if k != key]


@app.route("/interface_language", methods=["GET"])
@auth_desired
def interface_language(v=None):
    back = redirect(same_site(request.args.get("next", "/")))
    back.headers["Cache-Control"] = "no-store"
    key = request.args.get("lang", "")
    if key not in i18n.interface_keys():
        return back
    if same_origin.from_another_site(request.headers.get("Origin"), request.headers.get("Sec-Fetch-Site"),
                                     request.host, (app.config.get("SERVER_NAME") or "", environ.get("ONION_NAME", ""))):
        return back
    if v:
        v.languages = ",".join(moved_to_front(v.languages, key)[:translation.MAX_LANGUAGES])
        g.db.add(v)
        g.db.commit()
    back.set_cookie(i18n.COOKIE, key, max_age=i18n.COOKIE_AGE, samesite="Lax",
                    secure=bool(app.config.get("FORCE_HTTPS")), httponly=True)
    # it sets a cookie: never kept by a cache, whatever auth_desired adds for a visitor
    back.headers["Cache-Control"] = "no-store"
    return back
