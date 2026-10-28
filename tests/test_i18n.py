"""Interface languages (the owner, 2026-10-24: the language a member sets is the language of the site). The rules
(helpers/i18n.py), the runtime (helpers/i18n_runtime.py: the language of a request, tr and friends, lazy strings in
JSON, the template callables), notices drawn in each reader's language (helpers/alerts.py), and the page shells."""
import json
import os
import time
from pathlib import Path

import pytest
from babel.messages.catalog import Catalog
from babel.messages.pofile import write_po
from flask import Flask, g, jsonify, render_template_string

from ruqqus.helpers import i18n
from ruqqus.helpers import i18n_runtime as rt

ROOT = Path(__file__).resolve().parent.parent


def read(*parts):
    return ROOT.joinpath(*parts).read_text(encoding="utf-8")


# --- the rules --------------------------------------------------------------------------------------------------------

AVAILABLE = frozenset({"en", "es", "ar", "bg", "zh", "no"})


def test_a_member_reads_their_own_first_language():
    assert i18n.resolve(saved=["es", "fr"], accept="ar", available=AVAILABLE) == "es"
    assert i18n.resolve(saved=["mk"], available=AVAILABLE) == "bg"            # Macedonian is the Bulgarian row
    assert i18n.resolve(saved=["fr", "es"], available=AVAILABLE) == "en"      # only "Your language" counts
    assert i18n.resolve(saved=["hr"], accept="es", available=AVAILABLE) == "en"   # not offered: English


def test_a_visitor_gets_their_choice_then_their_browser():
    assert i18n.resolve(cookie="ar", accept="es", available=AVAILABLE) == "ar"
    assert i18n.resolve(cookie="xx", accept="fr-CA,fr;q=0.9,es;q=0.8", available=AVAILABLE) == "es"
    assert i18n.resolve(accept="en-GB,es;q=0.5", available=AVAILABLE) == "en"     # asked for English first
    assert i18n.resolve(accept="nb-NO,nn;q=0.8", available=AVAILABLE) == "no"     # browsers' own codes
    assert i18n.resolve(accept="zh-TW", available=AVAILABLE) == "zh"
    assert i18n.resolve(accept="", available=AVAILABLE) == "en" and i18n.resolve() == "en"


def test_a_forced_language_wins():
    assert i18n.resolve(forced="en", saved=["es"], cookie="ar", accept="ar", available=AVAILABLE) == "en"
    assert i18n.resolve(forced="es", saved=["ar"], available=AVAILABLE) == "es"
    assert i18n.resolve(forced="sv", available=AVAILABLE) == "en"            # no catalogue: English


def test_the_browser_list_knows_nothing_rather_than_english():
    assert i18n.browser_keys("xx-YY, tlh") == []
    assert i18n.browser_keys("de;q=0, fr;q=0.4, es") == ["es", "fr"]


def test_right_to_left_and_the_tags():
    for key in ("ar", "fa", "he", "ur"):
        assert i18n.is_rtl(key) and i18n.direction(key) == "rtl"
    assert i18n.direction("es") == "ltr"
    assert i18n.babel_code("no") == "nb" and i18n.babel_code("zh") == "zh_Hans" and i18n.babel_code("es") == "es"
    assert i18n.html_lang("zh") == "zh-Hans" and i18n.html_lang("es") == "es"
    assert i18n.interface_keys() >= {"en", "es", "ar", "zh", "ja"}            # LibreTranslate's rows


def test_the_pseudo_locale_keeps_placeholders_and_tags():
    out = i18n.pseudo('Hello %(name)s, <a href="/x">100%%</a> &amp; [more](/y)')
    assert "%(name)s" in out and '<a href="/x">' in out and "%%" in out and "&amp;" in out and "](/y)" in out
    assert "Hello" not in out and out.startswith("[") and "·" in out
    assert i18n.pseudo("Hi", rtl=True).startswith("\u202b")
    assert i18n.pseudo("") == "" and i18n.pseudo("  ") == "  "


# --- the runtime ------------------------------------------------------------------------------------------------------

def _catalogue(root, key, entries, domain="messages", plural=None):
    folder = root / key / "LC_MESSAGES"
    folder.mkdir(parents=True, exist_ok=True)
    catalog = Catalog(locale=i18n.babel_code(key), domain=domain)
    for msgid, msgstr in entries.items():
        catalog.add(msgid, msgstr)
    if plural:
        catalog.add(plural[0], plural[1])
    with (folder / f"{domain}.po").open("wb") as f:
        write_po(f, catalog)


@pytest.fixture
def app(tmp_path, monkeypatch):
    monkeypatch.setattr(rt, "ROOT", tmp_path)
    monkeypatch.delenv("I18N_PSEUDO", raising=False)
    rt.forget()
    _catalogue(tmp_path, "es", {
        "Save": "Guardar",
        "Hello %(name)s": "Hola %(name)s",
        "Welcome to %(site)s": "Bienvenido a %(site)s",
        "Broken %(name)s": "Roto",                       # lost its placeholder: the English is used
        "Your post [%(title)s](%(link)s) was pinned.": "Tu publicación [%(title)s](%(link)s) fue fijada.",
    }, plural=(("%(num)s comment", "%(num)s comments"), ("%(num)s comentario", "%(num)s comentarios")))
    _catalogue(tmp_path, "es", {"Rules for this page": "Reglas de esta página"}, domain="help")
    flask_app = Flask(__name__)
    flask_app.config["SITE_NAME"] = "Laba"
    flask_app.secret_key = "x"
    rt.install(flask_app)
    yield flask_app
    rt.forget()


def test_catalogues_are_found_and_merged(app):
    assert rt.available() == {"en", "es"}
    with app.test_request_context(headers={"Accept-Language": "es"}):
        assert rt.current_locale() == "es"
        assert rt.tr("Save") == "Guardar" and rt.tr("Rules for this page") == "Reglas de esta página"
        assert rt.tr("Hello %(name)s", name="Ana") == "Hola Ana"
        assert rt.tr("Welcome to %(site)s") == "Bienvenido a Laba"
        assert rt.tr("Broken %(name)s", name="x") == "Broken x"           # never a translation that lost a value
        assert rt.tr("Not in the catalogue, 100%%") == "Not in the catalogue, 100%"
        assert rt.trn("%(num)s comment", "%(num)s comments", 1) == "1 comentario"
        assert rt.trn("%(num)s comment", "%(num)s comments", 4) == "4 comentarios"
        assert rt.msg("Save") == "Guardar" and rt.msg("Someone's own words, 100%") == "Someone's own words, 100%"


def test_a_catalogue_written_while_the_worker_runs_is_used(app, monkeypatch):
    # the owner, 2026-10-25: French chosen in Settings > Content showed English, because the worker had listed the
    # catalogues once, before French was translated
    monkeypatch.setattr(rt, "CHECK_SECONDS", 0)
    with app.test_request_context():
        g.v = type("V", (), {"languages": "fr"})()
        assert rt.current_locale() == "en" and rt.tr("Save") == "Save"             # no French catalogue yet
    _catalogue(rt.ROOT, "fr", {"Save": "Enregistrer"})
    with app.test_request_context():
        g.v = type("V", (), {"languages": "fr"})()
        assert rt.current_locale() == "fr" and rt.tr("Save") == "Enregistrer"
    # and a catalogue translated further is read again
    _catalogue(rt.ROOT, "fr", {"Save": "Sauvegarder"})
    future = time.time() + 5
    os.utime(rt.ROOT / "fr" / "LC_MESSAGES" / "messages.po", (future, future))
    with app.test_request_context():
        g.v = type("V", (), {"languages": "fr"})()
        assert rt.tr("Save") == "Sauvegarder"


def test_a_language_is_offered_only_once_it_is_ready(app, monkeypatch):
    # the owner, 2026-10-25: a half-translated language read as broken; hide a language until it is ready
    monkeypatch.setattr(rt, "CHECK_SECONDS", 0)
    half = {f"Label {i}": (f"Étiquette {i}" if i < 10 else "") for i in range(20)}
    _catalogue(rt.ROOT, "fr", half)
    assert "fr" not in rt.available() and "fr" not in dict(rt.interface_choices())
    with app.test_request_context():
        g.v = type("V", (), {"languages": "fr"})()
        assert rt.current_locale() == "en"
    _catalogue(rt.ROOT, "fr", {**{k: k.replace("Label", "Étiquette") for k in half}, "Label 19": ""})    # 95%
    future = time.time() + 5
    os.utime(rt.ROOT / "fr" / "LC_MESSAGES" / "messages.po", (future, future))
    assert "fr" in rt.available()
    with app.test_request_context():
        g.v = type("V", (), {"languages": "fr"})()
        assert rt.current_locale() == "fr" and rt.tr("Label 3") == "Étiquette 3"


def test_the_share_translated_counts_entries_and_plurals_but_not_the_header():
    po = (
        'msgid ""\nmsgstr ""\n"Project-Id-Version: x\\n"\n\n'
        '#, machine\nmsgid "Save"\nmsgstr "Guardar"\n\n'
        'msgid "Hello"\nmsgstr ""\n\n'
        'msgid ""\n"Long "\n"text"\nmsgstr ""\n"Texto "\n"largo"\n\n'
        'msgid "%(num)s comment"\nmsgid_plural "%(num)s comments"\nmsgstr[0] "%(num)s comentario"\n'
        'msgstr[1] "%(num)s comentarios"\n\n'
        'msgid "%(num)s day"\nmsgid_plural "%(num)s days"\nmsgstr[0] ""\nmsgstr[1] ""\n'
    )
    assert rt.translated_share(po) == 3 / 5


def test_the_request_language_is_worked_out_once(app):
    with app.test_request_context(headers={"Accept-Language": "es"}):
        g.v = type("V", (), {"languages": "ar,es"})()                    # Arabic has no catalogue here: English
        assert rt.current_locale() == "en"
    with app.test_request_context(headers={"Accept-Language": "fr"}):
        g.v = type("V", (), {"languages": ""})()
        assert rt.current_locale() == "en"
    with app.test_request_context(headers={"Cookie": "ui_lang=es", "Accept-Language": "fr"}):
        assert rt.current_locale() == "es"


def test_the_public_api_stays_english(app):
    with app.test_request_context("/api/v1/post/abc", headers={"Accept-Language": "es"}):
        assert rt.current_locale() == "en" and rt.tr("Save") == "Save"
    with app.test_request_context("/api/v2/submissions", headers={"Accept-Language": "es", "Authorization": "Bearer x"}):
        assert rt.current_locale() == "en"
    # the site's own scripts read /api/v2 with the session, and /api/vue: those follow the reader
    with app.test_request_context("/api/v2/submissions", headers={"Accept-Language": "es"}):
        assert rt.current_locale() == "es"
    with app.test_request_context("/api/vue/submit", headers={"Accept-Language": "es"}):
        assert rt.current_locale() == "es"


def test_an_in_process_request_names_its_reader(app):
    with app.test_request_context("/api/vue/submit", environ_base={rt.ENVIRON_KEY: "es"}):
        assert rt.current_locale() == "es"
    with app.test_request_context("/api/vue/submit", environ_base={rt.ENVIRON_KEY: "en"},
                                  headers={"Accept-Language": "es"}):
        assert rt.current_locale() == "en"


def test_a_forced_language_for_jobs_and_emails(app):
    assert rt.current_locale() == "en"                                  # no request: English
    with rt.force_locale("es"):
        assert rt.current_locale() == "es" and rt.tr("Save") == "Guardar"
        with rt.force_locale("en"):
            assert rt.tr("Save") == "Save"
        assert rt.tr("Save") == "Guardar"
    assert rt.current_locale() == "en"
    member = type("M", (), {"languages": "es,fr"})()
    assert rt.locale_for_user(member) == "es"
    assert rt.locale_for_user(type("M", (), {"languages": ""})()) == "en"


def test_a_forced_language_is_passed_into_a_greenlet_explicitly(app):
    gevent = pytest.importorskip("gevent")
    seen = []

    def job(key):
        with rt.force_locale(key):
            seen.append(rt.tr("Save"))
    with rt.force_locale("es"):
        gevent.spawn(job, rt.current_locale()).join()
    assert seen == ["Guardar"]


def test_lazy_strings_follow_the_request_and_json(app):
    constant = rt.tr_lazy("Save")
    with app.test_request_context(headers={"Accept-Language": "es"}):
        assert str(constant) == "Guardar"
        assert json.loads(jsonify({"error": constant}).get_data()) == {"error": "Guardar"}
    with app.test_request_context(headers={"Accept-Language": "en"}):
        assert str(constant) == "Save"                                   # never cached in the first language


def test_templates_translate_escape_and_format(app):
    with app.test_request_context(headers={"Accept-Language": "es"}):
        out = render_template_string(
            '{{ _("Save") }}|{{ _("Hello %(name)s", name=who) }}|{{ ngettext("%(num)s comment", "%(num)s comments", 3) }}'
            '|{% trans %}Save{% endtrans %}|{{ _("Welcome to %(site)s") }}|{{ msg_text("Save") }}',
            who="<b>Ana</b>")
        assert out == "Guardar|Hola &lt;b&gt;Ana&lt;/b&gt;|3 comentarios|Guardar|Bienvenido a Laba|Guardar"
        assert render_template_string('<html{{ x }} lang="{{ ui_lang() }}" dir="{{ ui_dir() }}">', x="") \
            == '<html lang="es" dir="ltr">'


def test_pseudo_locales_only_when_switched_on(app, monkeypatch):
    with app.test_request_context(headers={"Cookie": "ui_lang=en-XA"}):
        assert rt.current_locale() == "en"
    monkeypatch.setenv("I18N_PSEUDO", "1")
    rt.forget()
    with app.test_request_context(headers={"Cookie": "ui_lang=ar-XB"}):
        assert rt.current_locale() == "ar-XB" and render_template_string("{{ ui_dir() }}") == "rtl"
        assert rt.tr("Save %(n)s", n=2).startswith("\u202b[Šàṽé 2")


def test_a_notice_is_read_in_each_readers_language(app):
    import mistletoe
    values = json.dumps({"title": "Hi", "link": "/post/1", "site": "Laba"})
    aux = type("Aux", (), {"notice_msgid": "Your post [%(title)s](%(link)s) was pinned.", "notice_params": values})()
    notice = type("C", (), {"id": 991, "comment_aux": aux, "body_html": "<p>English as sent</p>"})()
    plain = type("C", (), {"id": 992, "comment_aux": type("A", (), {"notice_msgid": None})(), "body_html": "<p>x</p>"})()
    rt._NOTICES.clear()
    with app.test_request_context(headers={"Accept-Language": "en"}):
        assert rt.notice_html(notice, render=mistletoe.markdown) == "<p>English as sent</p>"
    with app.test_request_context(headers={"Accept-Language": "es"}):
        assert rt.notice_html(plain, render=mistletoe.markdown) == "<p>x</p>"
        html = rt.notice_html(notice, render=mistletoe.markdown)
        assert "Tu publicación" in html and 'href="/post/1"' in html
    alerts = read("ruqqus", "helpers", "alerts.py")
    assert "def notify(user, message, **params):" in alerts and "def notify_many(user_ids, message, **params):" in alerts
    assert "notice_msgid=notice[0] if notice else None" in alerts


# --- the shells and the source -----------------------------------------------------------------------------------------

def test_every_shell_loads_the_scripts_table_first():
    shells = ("default", "media_connected", "login", "call", "submit", "authforms", "login_2fa", "settings",
              "sign_up", "docs_default", "space", "embeds/embed_default", "help/about", "help/donate",
              "guild/guild_settings")
    for name in shells:
        page = read("ruqqus", "templates", f"{name}.html")
        head = page.split("<head>")[1]
        assert head.lstrip().startswith('{% from "partials/i18n_boot.html" import i18n_boot %}{{ i18n_boot() }}'), name
    boot = read("ruqqus", "templates", "partials", "i18n_boot.html")
    assert "asset_url('js/i18n.js')" in boot and "i18n_script(key)" in boot


def test_the_scripts_tr_formats_like_python(tmp_path):
    import subprocess
    script = read("ruqqus", "assets", "js", "i18n.js")
    probe = tmp_path / "probe.js"
    probe.write_text(
        'global.window = {I18N: {lang: "es", intl: "es", dir: "ltr", pseudo: false}};\n' + script +
        '\nwindow.I18N.messages = {"Hello %(name)s": "Hola %(name)s", "%(num)s post\\u0000%(num)s posts": '
        '["%(num)s publicación", "%(num)s publicaciones"]};\nwindow.I18N.plural = function (n) { return n == 1 ? 0 : 1; };\n'
        'console.log(JSON.stringify([window.tr("Hello %(name)s", {name: "Ana"}), window.tr("Untranslated, 100%%"), '
        'window.trn("%(num)s post", "%(num)s posts", 1), window.trn("%(num)s post", "%(num)s posts", 7)]));\n', encoding="utf-8")
    try:
        out = subprocess.run(["node", str(probe)], capture_output=True, text=True, timeout=30)
    except FileNotFoundError:
        pytest.skip("node is not installed")
    assert json.loads(out.stdout) == ["Hola Ana", "Untranslated, 100%", "1 publicación", "7 publicaciones"]


def test_no_template_assigns_to_underscore():
    # `_` is gettext in templates: {% set _ = ... %} would hide it for the rest of the template
    for path in (ROOT / "ruqqus" / "templates").rglob("*.html"):
        text = path.read_text(encoding="utf-8")
        assert "set _ =" not in text and "set _=" not in text, path.name


def test_the_app_installs_it_and_varies_by_language():
    main = read("ruqqus", "__main__.py")
    assert "i18n_runtime.install(app)" in main
    assert 'response.vary.add("Accept-Language")' in main


def test_languages_are_named_in_the_readers_language_and_in_their_own():
    # from CLDR, never typed (helpers/i18n.py): English pages keep languages.label exactly
    assert i18n.language_name("mk", "en") == "Bulgarian" and i18n.language_name("ms", "en") == "Indonesian / Malay"
    assert i18n.language_name("es", "de") == "Spanisch" and i18n.language_name("mk", "es") == "Búlgaro"
    assert i18n.language_name("ms", "es") == "Indonesio / Malayo"
    assert i18n.language_name("hr", "es") is None and i18n.language_name(None, "es") is None    # not offered
    assert i18n.own_name("es") == "Español" and i18n.own_name("ja") == "日本語"
    assert i18n.own_name("id") == "Bahasa Indonesia / Bahasa Melayu" and i18n.own_name("no") == "Norsk"
    for key in i18n.interface_keys():
        assert i18n.own_name(key) and i18n.language_name(key, "fr"), key


def test_a_trans_block_names_the_site_without_being_told(app):
    # {% trans %}…{{ site }}…{% endtrans %} gets the template's own `site`: undefined unless set, so it is filled in
    with app.test_request_context(headers={"Accept-Language": "en"}):
        assert render_template_string("{% trans %}Welcome to {{ site }}{% endtrans %}") == "Welcome to Laba"
        assert render_template_string("{% if msg %}notice{% endif %}") == ""     # `msg` is a page's own notice


# --- choosing the language, right to left ------------------------------------------------------------------------------

def test_the_footer_switch_moves_the_choice_to_the_front_and_is_refused_from_another_site():
    source = read("ruqqus", "routes", "interface_language_pages.py")
    assert "same_origin.from_another_site(" in source and "same_site(request.args.get(\"next\", \"/\"))" in source
    assert 'back.headers["Cache-Control"] = "no-store"' in source and "httponly=True" in source
    assert "key not in i18n.interface_keys()" in source
    assert "from .interface_language_pages import *" in read("ruqqus", "routes", "__init__.py")
    switcher = read("ruqqus", "templates", "partials", "language_switcher.html")
    assert "interface_choices()" in switcher and "request.full_path | urlencode" in switcher
    for footer in ("footer.html", "footer_bottom.html", "footer_article.html"):
        assert "language_switcher()" in read("ruqqus", "templates", footer), footer


def test_moving_a_language_to_the_front():
    # the route module imports the app, so its rule is read from its text and run on its own
    import ast
    source = read("ruqqus", "routes", "interface_language_pages.py")
    node = next(n for n in ast.parse(source).body if isinstance(n, ast.FunctionDef) and n.name == "moved_to_front")
    namespace = {"translation": __import__("ruqqus.helpers.translation", fromlist=["x"])}
    exec(compile(ast.Module(body=[node], type_ignores=[]), "moved_to_front", "exec"), namespace)
    assert namespace["moved_to_front"]("fr,es", "es") == ["es", "fr"]
    assert namespace["moved_to_front"]("", "ja") == ["ja"]
    assert namespace["moved_to_front"]("mk,ja", "bg") == ["bg", "ja"]                 # Macedonian is the Bulgarian row


def test_right_to_left_pages_get_the_mirrored_sheets(app):
    assert rt.rtl_css("/assets/style/main.css?v=abc") == "/assets/style/main.rtl.css?v=abc"
    assert rt.rtl_css("/assets/t2_x/dark/12.css?v=1") == "/assets/t2_x/dark/12.rtl.css?v=1"
    theme = read("ruqqus", "templates", "partials", "theme.html")
    assert "{% if ui_rtl() %}{% set light = rtl_css(light) %}{% set dark = rtl_css(dark) %}{% endif %}" in theme
    for subset in ("cyrillic", "cyrillic-ext", "greek", "greek-ext", "vietnamese"):
        assert f"fonts/inter-{subset}.woff2" in theme and (ROOT / "ruqqus" / "assets" / "fonts" /
                                                           f"inter-{subset}.woff2").exists(), subset
    static = read("ruqqus", "routes", "static.py")
    assert '"main.rtl", "main_dark.rtl"' in static and "css_flip.flip(compiled) if rtl else compiled" in static
    boards = read("ruqqus", "routes", "boards.py")
    assert "return css_flip.flip(css) if rtl else css" in boards and 'rtl = x.endswith(".rtl")' in boards
    assert "js/bootstrap_rtl.js" in read("ruqqus", "templates", "bootstrap.html")
    assert "root.dir === 'rtl'" in read("ruqqus", "assets", "js", "nav_align.js")
    assert "css_flip.py" in read("ruqqus", "helpers", "page_assets.py")                 # a new flipper, a new version


def test_your_language_is_the_sites_language_and_sign_up_keeps_it():
    page = read("ruqqus", "templates", "settings_filters.html")
    assert '_("%(site)s is shown in this language, and translations are made into it.")' in page
    assert "has_interface(my_langs[0])" in page and "own_language_name(code)" in page
    # the language picker lists only ready languages, and keeps the member's own choice before it is ready
    assert "{% if has_interface(code) or (my_langs and code == my_langs[0]) %}" in page
    login = read("ruqqus", "routes", "login.py")
    assert 'languages=current_locale() if current_locale() in i18n.interface_keys() and current_locale() != "en" else ""' in login


def test_the_sites_own_topic_and_region_names_are_marked():
    # rows of the database the catalogues can't find in a template: listed in helpers/taxonomy_names.py, drawn with
    # msg_text; the list keeps up with seed-db.sql
    import re as _re
    seed = read("seed-db.sql")
    names = read("ruqqus", "helpers", "taxonomy_names.py")
    for row in _re.findall(r"INSERT INTO public\.(?:categories|subcategories|regions) VALUES \(([^)]*)\)", seed):
        for value in _re.findall(r"'((?:[^']|'')*)'", row):
            value = value.replace("''", "'")
            if value and not value.startswith(("#", "fa-")) and not _re.fullmatch(r"[0-9a-f]{6}|[a-z_]+", value):
                assert f"N_({value!r})" in names, value
    for name, text in _re.findall(r"INSERT INTO public\.badge_defs VALUES \(\d+, '((?:[^']|'')*)', '((?:[^']|'')*)'", seed):
        name, text = name.replace("''", "'"), text.replace("''", "'")
        assert f"N_({name!r})" in names and f"N_({text!r})" in names, name
    for path in (("make_board.html",), ("regions.html",), ("boards.html",), ("partials", "profile_header.html"),
                 ("partials", "profile_about.html"), ("badge.html",)):
        assert "msg_text(" in read("ruqqus", "templates", *path), path
