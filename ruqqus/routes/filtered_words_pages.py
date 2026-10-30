"""`/filtered_words`: the words the word filter looks for, in full, for signed-in members, so anyone can see what the
filter hides (the owner, 2026-10-29). Visitors are sent to sign in; a member on the Child filter is told the list isn't
shown there. A warning screen comes first and the list only with `?show=1`; no script is needed for either.

The list is the live one (word_filter_store.listed_entries: the table's enabled rows, or the built-in list while the
table is empty), so an admin's change shows at once; grouping is helpers/filtered_words.py. Never cached, never
indexed, no ads. The module is not named `filtered_words.py`: `from ruqqus.routes import *` would put this module in
place of the helper of that name."""
from functools import wraps
from urllib.parse import urlsplit

from flask import g, render_template, request

from ruqqus.helpers import filtered_words as rules
from ruqqus.helpers import i18n, languages
from ruqqus.helpers import word_filter_store
from ruqqus.helpers.i18n_runtime import current_locale
from ruqqus.helpers.redirects import same_site
from ruqqus.helpers.wrappers import auth_required
from ruqqus.__main__ import app

__all__ = ["filtered_words"]     # a star import of the routes takes only the view, never the helpers it names

PATH = "/filtered_words"
NO_STORE = "private, no-store"


def _private_page(f):
    """Assigned after auth_required has answered, so the page carries one Cache-Control (the wrapper adds a bare
    "private" of its own) and is kept out of search engines whatever it shows."""
    @wraps(f)
    def wrapper(*args, **kwargs):
        response = f(*args, **kwargs)
        response.headers["Cache-Control"] = NO_STORE
        response.headers["X-Robots-Tag"] = "noindex, nofollow"
        return response
    return wrapper


def _name_of(locale):
    """A language's name in the reader's language: the site's own row name where it has one, else CLDR's, else the
    English name from the list of languages, else the code."""
    def name(code):
        found = i18n.language_name(code, locale)
        if found:
            return found
        try:
            from babel import Locale
            cldr = Locale.parse(i18n.babel_code(locale)).languages.get(code)
        except (ValueError, TypeError, ImportError):
            cldr = None
        if cldr:
            return cldr[:1].upper() + cldr[1:]
        return languages.LANGUAGE_NAMES.get(code, code)
    return name


def _back():
    """Where "Go back" leads: the page the member came from on this site, else Settings > Content."""
    default = "/settings/content"
    referrer = request.referrer or ""
    parts = urlsplit(referrer)
    if not referrer or parts.netloc != request.host or parts.path == PATH:
        return default
    return same_site(parts.path + (f"?{parts.query}" if parts.query else ""), default=default)


@app.route(PATH, methods=["GET"])
@_private_page
@auth_required
def filtered_words(v):
    if rules.child_filter(v):
        return render_template("filtered_words.html", v=v, state="child")
    if request.args.get("show") != "1":
        return render_template("filtered_words.html", v=v, state="warning", back=_back())
    listed = word_filter_store.listed_entries(g.db)
    grouped = rules.groups(listed, _name_of(current_locale()))
    return render_template("filtered_words.html", v=v, state="list", grouped=grouped,
                           total=sum(group["count"] for group in grouped),
                           source=listed.get("source") or {},
                           credit=rules.has_other_languages(grouped) and bool((listed.get("source") or {}).get("url")))
