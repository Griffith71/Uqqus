"""Interface languages at run time (the rules are helpers/i18n.py; the conventions docs/i18n.md).

* **The language of a request** (`current_locale`): worked out once per request (helpers/i18n.resolve) from, in
  order, a forced one (`force_locale` for jobs and emails; `environ["ruqqus.ui_locale"]` for in-process requests made
  for someone; English on the public API: /api/v1, and /api/v2 called with a token), the member's own first language
  (`users.languages`, read from the session when no wrapper has set `g.v`: error pages, embeds), the visitor's
  `ui_lang` cookie, `Accept-Language`, English. Outside a request (a job), English unless forced.
* **Python**: `tr("Text with %(name)s", name=x)`, `trn(singular, plural, n)` (`%(num)s` is n), `trp(context, text)`,
  `tr_lazy(...)` for module constants (worked out each time it is used: never cached, never stored), `N_("...")` to
  mark a string drawn later with `msg(stored)` (exact match, else the text as stored). Every string is a format
  string: a literal percent sign is `%%`, and `%(site)s` is the site's name. Never `_` in Python: the code has many
  throwaway `_` variables. Anything stored or cached stays English; only what is shown is translated.
* **Templates**: `{{ _("...") }}`, `{{ ngettext(...) }}`, `{{ pgettext(...) }}`, `{% trans %}`, `{{ msg(x) }}`
  (helpers/i18n_jinja.py installs them); the same `%` and `%(site)s` rules.
* **Catalogues**: ruqqus/translations/<key>/LC_MESSAGES/{messages,help}.po (`.mo` when compiled and newer), merged,
  loaded the first time a language is used in a worker; fuzzy entries are skipped.
"""
import json
import os
import re
import time
from contextlib import contextmanager
from contextvars import ContextVar
from io import BytesIO
from pathlib import Path

from babel.support import LazyProxy, NullTranslations, Translations
from flask import g, has_request_context, request
from flask.json.provider import DefaultJSONProvider
from markupsafe import Markup

from ruqqus.helpers import i18n

ROOT = Path(__file__).resolve().parent.parent / "translations"
DOMAINS = ("messages", "help")
ENVIRON_KEY = "ruqqus.ui_locale"

_forced = ContextVar("ui_locale_forced", default=None)
# per worker, and read again when the files change (a catalogue written by the translation run, a deploy that kept the
# worker): key -> (translations, the files' stamp, when the stamp was last looked at)
_loaded = {}
_available = {}
_ready = {}             # key -> (messages.po's stamp, ready or not)
CHECK_SECONDS = 2.0     # how often a worker looks at the catalogue files' times again
# a language is offered once this share of its interface is translated (the owner, 2026-10-25: hide a language until it
# is ready; a half-translated one read as broken)
READY_SHARE = 0.95


# --- catalogues -------------------------------------------------------------------------------------------------------

def pseudo_enabled():
    return os.environ.get("I18N_PSEUDO") == "1"


class _Pseudo(NullTranslations):
    """The pseudo-locales: every string accented, longer and bracketed (helpers/i18n.pseudo)."""

    def __init__(self, rtl):
        super().__init__()
        self.rtl = rtl

    def gettext(self, message):
        return i18n.pseudo(message, self.rtl)

    def ngettext(self, singular, plural, n):
        return i18n.pseudo(singular if n == 1 else plural, self.rtl)

    def pgettext(self, context, message):
        return self.gettext(message)

    def npgettext(self, context, singular, plural, n):
        return self.ngettext(singular, plural, n)


def _domain(key, domain):
    folder = ROOT / key / "LC_MESSAGES"
    po, mo = folder / f"{domain}.po", folder / f"{domain}.mo"
    if mo.exists() and (not po.exists() or mo.stat().st_mtime >= po.stat().st_mtime):
        with mo.open("rb") as f:
            return Translations(f, domain=domain)
    if not po.exists():
        return None
    from babel.messages.mofile import write_mo
    from babel.messages.pofile import read_po
    with po.open("rb") as f:
        catalog = read_po(f, locale=i18n.babel_code(key), domain=domain)
    buffer = BytesIO()
    write_mo(buffer, catalog, use_fuzzy=False)
    buffer.seek(0)
    return Translations(buffer, domain=domain)


def _stamp(key):
    """The modification times of a key's catalogue files (0 for a missing one): what its loaded copy is kept on."""
    folder = ROOT / key / "LC_MESSAGES"
    stamp = []
    for domain in DOMAINS:
        for suffix in (".po", ".mo"):
            try:
                stamp.append((folder / f"{domain}{suffix}").stat().st_mtime)
            except OSError:
                stamp.append(0)
    return tuple(stamp)


def translations(key):
    """The translations for an interface key (English and unknown keys: none, so the English is shown)."""
    now = time.monotonic()
    entry = _loaded.get(key)
    if entry is not None and now - entry[2] < CHECK_SECONDS:
        return entry[0]
    stamp = _stamp(key) if key in i18n.interface_keys() else None
    if entry is not None and entry[1] == stamp and key in available():
        _loaded[key] = (entry[0], stamp, now)
        return entry[0]
    found = _load(key)
    if entry is not None:
        _NOTICES.clear()                       # notices drawn with the old catalogue
    _loaded[key] = (found, stamp, now)
    return found


def _load(key):
    if key == i18n.PSEUDO and pseudo_enabled():
        found = _Pseudo(rtl=False)
    elif key == i18n.PSEUDO_RTL and pseudo_enabled():
        found = _Pseudo(rtl=True)
    elif key == i18n.ENGLISH or key not in available():
        found = NullTranslations()
    else:
        found = None
        for domain in DOMAINS:
            part = _domain(key, domain)
            if part is None:
                continue
            found = part if found is None else found.merge(part)
        found = found or NullTranslations()
    return found


def translated_share(text):
    """The share of a .po file's entries that have a translation (the header is not an entry). A quick scan of the
    text rather than a parse: every worker asks it of every catalogue that changed."""
    total = done = 0
    for block in text.split("\n\n"):
        lines = [line for line in block.splitlines() if line and not line.startswith("#")]
        if not lines or not lines[0].startswith("msgid "):
            continue
        part, msgid, translated = None, [], False
        for line in lines:
            if line.startswith("msgid ") or line.startswith("msgid_plural "):
                part = "id"
                msgid.append(line.split(" ", 1)[1])
            elif line.startswith("msgstr"):
                part = "str"
                translated = translated or line.split(" ", 1)[1] != '""'
            elif part == "id":
                msgid.append(line)
            elif part == "str":
                translated = translated or line != '""'
        if msgid == ['""']:
            continue                                   # the header
        total += 1
        done += translated
    return done / total if total else 0.0


def _is_ready(key):
    """Has this language's interface (its messages catalogue) at least READY_SHARE translated? A compiled .mo alone
    (no .po beside it) counts as ready: it was made from a finished catalogue."""
    folder = ROOT / key / "LC_MESSAGES"
    po = folder / "messages.po"
    try:
        stamp = po.stat().st_mtime
    except OSError:
        return (folder / "messages.mo").exists()
    cached = _ready.get(key)
    if cached is not None and cached[0] == stamp:
        return cached[1]
    ready = translated_share(po.read_text(encoding="utf-8")) >= READY_SHARE
    _ready[key] = (stamp, ready)
    return ready


def available():
    """The interface keys whose catalogue is ready (READY_SHARE translated), English among them (and the pseudo-locales
    while they are on); looked for again every CHECK_SECONDS, so a catalogue finished while the worker runs is used."""
    now = time.monotonic()
    if "keys" not in _available or now - _available["checked"] >= CHECK_SECONDS:
        keys = {i18n.ENGLISH}
        if ROOT.exists():
            for key in i18n.interface_keys():
                if _is_ready(key):
                    keys.add(key)
        _available["keys"] = frozenset(keys)
        _available["checked"] = now
    keys = _available["keys"]
    return keys | {i18n.PSEUDO, i18n.PSEUDO_RTL} if pseudo_enabled() else keys


def forget():
    """Read the catalogues again (tests, and scripts/i18n_catalogs.py after writing them)."""
    _loaded.clear()
    _available.clear()
    _ready.clear()
    _NOTICES.clear()


# --- the language of a request ----------------------------------------------------------------------------------------

def _public_api():
    path = request.path
    return path.startswith("/api/v1") or (path.startswith("/api/v2") and bool(request.headers.get("Authorization")))


def _saved_languages():
    """The member's own languages: the viewer a wrapper set, else the session's account (error pages, embeds)."""
    v = getattr(g, "v", None)
    if v is not None:
        return [c for c in (getattr(v, "languages", "") or "").split(",") if c]
    from flask import session
    uid = session.get("user_id") if session else None
    db = getattr(g, "db", None)
    if not uid or db is None:
        return []
    from ruqqus.classes.user import User
    saved = db.query(User.languages).filter(User.id == uid, User.is_deleted.is_(False)).scalar()
    return [c for c in (saved or "").split(",") if c]


def _request_locale():
    available_keys = available()
    environ_key = request.environ.get(ENVIRON_KEY)
    if environ_key:
        return i18n.resolve(forced=environ_key, available=available_keys)
    if _public_api():
        return i18n.ENGLISH
    return i18n.resolve(saved=_saved_languages(), cookie=request.cookies.get(i18n.COOKIE),
                        accept=request.headers.get("Accept-Language", ""), available=available_keys)


def current_locale():
    """The interface language now: forced (a job, an email), else the request's, else English."""
    forced = _forced.get()
    if forced:
        return forced
    if not has_request_context():
        return i18n.ENGLISH
    key = g.get("_ui_locale")
    if key is None:
        key = _request_locale()
        g._ui_locale = key
    return key


def locale_for_user(user):
    """The language to write to `user` in (an email, a job's notice): their own first language with a catalogue, else
    English. A member who hasn't chosen gets the current request's language when there is one."""
    saved = [c for c in (getattr(user, "languages", "") or "").split(",") if c]
    if saved:
        return i18n.resolve(saved=saved, available=available())
    return current_locale() if has_request_context() else i18n.ENGLISH


@contextmanager
def force_locale(key):
    """Everything translated inside is in `key` (an email to someone, a notice drawn for a reader). A greenlet
    started inside doesn't inherit it: pass the key along and force it again there."""
    token = _forced.set(i18n.resolve(forced=key, available=available()) if key else None)
    try:
        yield
    finally:
        _forced.reset(token)


# --- translating ------------------------------------------------------------------------------------------------------

def site_name():
    try:
        from flask import current_app
        return current_app.config.get("SITE_NAME") or os.environ.get("SITE_NAME", "Laba")
    except RuntimeError:
        return os.environ.get("SITE_NAME", "Laba")


_PLACEHOLDER = re.compile(r"%\((\w+)\)[sd]")


def _same_values(text, original, optional=()):
    """A translation names exactly the values its English does (one that lost or invented one is not used); a plural
    form may leave out the number (`optional`: "un comentario")."""
    if text is original:
        return True
    have, want = sorted(_PLACEHOLDER.findall(text)), sorted(_PLACEHOLDER.findall(original))
    if have == want:
        return True
    return [n for n in have if n not in optional] == [n for n in want if n not in optional] and \
        all(n in want for n in have)


def _format(text, original, params, optional=()):
    params.setdefault("site", site_name())
    if not _same_values(text, original, optional):
        text = original
    try:
        return text % params
    except (KeyError, ValueError, TypeError):
        try:
            return original % params                      # a translation that can't be filled in: the English
        except (KeyError, ValueError, TypeError):
            return original


def _now():
    return translations(current_locale())


def tr(message, **params):
    return _format(_now().gettext(message), message, params)


def trn(singular, plural, n, **params):
    params.setdefault("num", n)
    return _format(_now().ngettext(singular, plural, n), singular if n == 1 else plural, params, ("num",))


def trp(context, message, **params):
    return _format(_now().pgettext(context, message), message, params)


def trnp(context, singular, plural, n, **params):
    params.setdefault("num", n)
    return _format(_now().npgettext(context, singular, plural, n), singular if n == 1 else plural, params, ("num",))


def tr_lazy(message, **params):
    """A module constant: translated each time it is used, in that request's language."""
    return LazyProxy(tr, message, enable_cache=False, **params)


def N_(message):
    """Marks a string for the catalogues without translating it (it is drawn later with msg)."""
    return message


def msg(stored):
    """A string marked with N_ (or one of the site's own English messages that was stored, such as a ban reason the
    site wrote), drawn in the reader's language: the catalogue's entry when it has one, else as stored. Not
    formatted: what was stored is already whole."""
    if not stored or not isinstance(stored, str):
        return stored
    return _now().gettext(stored)


# --- dates and numbers -----------------------------------------------------------------------------------------------

def babel_locale():
    return i18n.babel_code(current_locale())


def date_text(timestamp, english_format, cldr_format="long"):
    """A date (UTC) for the reader: in English exactly `time.strftime(english_format)` as the site always wrote it;
    in another language Babel's date in that language (`cldr_format`: "short", "medium", "long", "full" or a CLDR
    pattern such as "MMMM y")."""
    import time
    if current_locale() == i18n.ENGLISH:
        return time.strftime(english_format, time.gmtime(int(timestamp)))
    from datetime import datetime, timezone
    from babel.dates import format_date
    return format_date(datetime.fromtimestamp(int(timestamp), tz=timezone.utc).date(), format=cldr_format,
                       locale=babel_locale())


def datetime_text(timestamp, english_format, cldr_format="medium"):
    """A date and time (UTC), as date_text."""
    import time
    if current_locale() == i18n.ENGLISH:
        return time.strftime(english_format, time.gmtime(int(timestamp)))
    from datetime import datetime, timezone
    from babel.dates import format_datetime
    return format_datetime(datetime.fromtimestamp(int(timestamp), tz=timezone.utc), format=cldr_format,
                           tzinfo=timezone.utc, locale=babel_locale())


def number_text(n):
    """A whole number with the reader's grouping (English: 1,234 as `{:,}` writes it)."""
    if current_locale() == i18n.ENGLISH:
        return f"{int(n):,}"
    from babel.numbers import format_decimal
    return format_decimal(int(n), locale=babel_locale())


# --- JSON ------------------------------------------------------------------------------------------------------------

class I18nJSONProvider(DefaultJSONProvider):
    """jsonify and |tojson write a lazy string (tr_lazy) as the text it is in this request."""

    @staticmethod
    def default(o):
        if isinstance(o, LazyProxy):
            return str(o)
        return DefaultJSONProvider.default(o)


_NOTICES = {}
_NOTICES_KEEP = 2048


def _render_notice(text):
    from ruqqus.helpers.alerts import _render
    return _render(text)


def notice_html(comment, render=None):
    """A comment's HTML as this reader sees it: a notice made with alerts.notify / notify_many in their interface
    language (rendered with the same markdown and sanitizer as when it was sent, kept per notice and language),
    anything else as stored."""
    aux = getattr(comment, "comment_aux", None)
    msgid = getattr(aux, "notice_msgid", None) if aux is not None else None
    key = current_locale()
    if not msgid or key == i18n.ENGLISH:
        return comment.body_html
    cache_key = (comment.id, key)
    if cache_key in _NOTICES:
        return _NOTICES[cache_key]
    try:
        values = json.loads(aux.notice_params or "{}")
    except ValueError:
        return comment.body_html
    translated = translations(key).gettext(msgid)
    if not _same_values(translated, msgid):
        return comment.body_html
    try:
        text = translated % values
    except (KeyError, ValueError, TypeError):
        return comment.body_html
    html = (render or _render_notice)(text)
    if len(_NOTICES) >= _NOTICES_KEEP:
        _NOTICES.clear()
    _NOTICES[cache_key] = html
    return html


def interface_choices():
    """(key, its own name) for every language the interface can be shown in, by name (the footer's switcher)."""
    keys = [k for k in available() if k in i18n.interface_keys()]
    return sorted(((k, i18n.own_name(k)) for k in keys), key=lambda pair: pair[1].casefold())


def rtl_css(url):
    """A stylesheet's address for right-to-left pages: main.css?v=x -> main.rtl.css?v=x (routes/static.py, a guild's
    sheets in routes/boards.py serve them mirrored by helpers/css_flip.py)."""
    return re.sub(r"\.css(?=\?|$)", ".rtl.css", url, count=1)


def script_url(key):
    """The versioned address of the scripts' table for an interface key (assets/i18n/<key>.js), or "" when it has none
    (English, a language whose js catalogue isn't made yet)."""
    if key == i18n.ENGLISH or not (ROOT.parent / "assets" / "i18n" / f"{key}.js").exists():
        return ""
    from ruqqus.helpers.page_assets import asset_url
    return asset_url(f"i18n/{key}.js")


# --- templates --------------------------------------------------------------------------------------------------------

def _jinja_format(context, text, original, variables, optional=()):
    if not _same_values(text, original, optional):
        text = original
    if context.eval_ctx.autoescape:
        text, original = Markup(text), Markup(original)
    # {% trans %}...{{ site }}...{% endtrans %} passes the template's own `site`, undefined unless set: the site's name
    from jinja2 import Undefined
    if isinstance(variables.get("site"), Undefined) or "site" not in variables:
        variables["site"] = site_name()
    try:
        return text % variables
    except (KeyError, ValueError, TypeError):
        try:
            return original % variables
        except (KeyError, ValueError, TypeError):
            return original


def install(app):
    """Puts the interface languages into the app: Jinja's i18n extension (newstyle: every string formatted, escaped
    values, the trimmed policy so re-indenting a template never changes a string), the callables, the JSON provider."""
    from jinja2 import pass_context

    from ruqqus.helpers.i18n_jinja import EXTENSIONS, POLICIES

    @pass_context
    def gettext(context, message, **variables):
        return _jinja_format(context, _now().gettext(message), message, variables)

    @pass_context
    def ngettext(context, singular, plural, num, **variables):
        variables.setdefault("num", num)
        return _jinja_format(context, _now().ngettext(singular, plural, num), singular if num == 1 else plural,
                             variables, ("num",))

    @pass_context
    def pgettext(context, ctx, message, **variables):
        return _jinja_format(context, _now().pgettext(ctx, message), message, variables)

    @pass_context
    def npgettext(context, ctx, singular, plural, num, **variables):
        variables.setdefault("num", num)
        return _jinja_format(context, _now().npgettext(ctx, singular, plural, num),
                             singular if num == 1 else plural, variables, ("num",))

    env = app.jinja_env
    for name in EXTENSIONS:
        env.add_extension(name)
    env.policies.update(POLICIES)
    env.newstyle_gettext = True
    env.globals.update(
        # msg_text, not msg: many templates are given a `msg` of their own (a notice to show)
        _=gettext, gettext=gettext, ngettext=ngettext, pgettext=pgettext, npgettext=npgettext, msg_text=msg,
        ui_locale=current_locale, ui_lang=lambda: i18n.html_lang(current_locale()),
        ui_dir=lambda: i18n.direction(current_locale()), ui_rtl=lambda: i18n.is_rtl(current_locale()),
        i18n_script=script_url, notice_html=notice_html, rtl_css=rtl_css, interface_choices=interface_choices,
        has_interface=lambda key: key in available(),
        own_language_name=lambda key: i18n.own_name(key) if key in i18n.interface_keys() else "English",
        html_lang=i18n.html_lang, language_name_here=lambda code: i18n.language_name(code, current_locale()), date_text=date_text, datetime_text=datetime_text,
        number_text=number_text,
    )
    app.json = I18nJSONProvider(app)
