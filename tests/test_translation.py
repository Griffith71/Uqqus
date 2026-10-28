"""The Translate button (the owner's choices, 2026-10-23 and -24): a button only, never automatic; the site's own
LibreTranslate, its only translator (Google was removed); each text translated once per language.
helpers/translation.py (rules), helpers/translation_store.py (SQL, on SQLite here), helpers/translators.py (the
service), routes/translation_pages.py, partials/translate_button.html, assets/js/translate.js."""
import ast
import re
from pathlib import Path

import pytest
from sqlalchemy import create_engine, text

from ruqqus.helpers import translation as tr
from ruqqus.helpers import translation_store as store
from ruqqus.helpers.languages import UNASKED

ROOT = Path(__file__).resolve().parent.parent


def read(*parts):
    return ROOT.joinpath(*parts).read_text(encoding="utf-8")


# --- a reader's languages -------------------------------------------------------------------------------------------

def test_a_members_languages_are_known_codes_in_order():
    assert tr.parse_languages("en, fr,xx,en,ja") == ["en", "fr", "ja"]
    assert tr.parse_languages("") == [] and tr.parse_languages(None) == []


def test_until_they_choose_the_browser_says():
    assert tr.from_accept_language("fr-CA,fr;q=0.9,en;q=0.8") == ["fr", "en"]
    assert tr.from_accept_language("en;q=0.2, de") == ["de", "en"]
    assert tr.from_accept_language("") == ["en"] and tr.from_accept_language("xx-YY") == ["en"]
    assert tr.reader_languages("ja", "fr") == (["ja"], True)
    assert tr.reader_languages("", "fr") == (["fr"], False)


def test_the_button_shows_only_under_another_language_that_something_can_translate():
    pairs = frozenset({("es", "en"), ("zh", "en"), ("en", "es")})
    assert tr.offered("es", ["en"], pairs)
    assert not tr.offered("en", ["en", "fr"], pairs)
    assert not tr.offered(None, ["en"], pairs) and not tr.offered("xx", ["en"], pairs)
    assert not tr.offered("yue", ["zh"], pairs)          # Cantonese is in the Chinese row: a Chinese reader reads it
    assert tr.offered("yue", ["en"], pairs)              # ...and LibreTranslate translates it as Chinese
    assert not tr.offered("sv", ["en"], pairs)           # Swedish isn't loaded here: no dead button
    assert not tr.offered("wa", ["en"], pairs)           # the site doesn't offer Walloon
    assert not tr.offered("hr", ["en"], pairs)           # nor Croatian since LibreTranslate is the only service


def test_the_button_and_the_route_always_agree():
    pairs = frozenset({("es", "en"), ("zh", "en"), ("en", "es"), ("ja", "en")})
    for source in ("es", "yue", "ja", "sv", "mk", "wa", "hr", None, "en"):
        shown = tr.offered(source, ["en"], pairs)
        routed = tr.route(source, "en", pairs).engine
        assert shown == bool(routed), source


# --- the service ----------------------------------------------------------------------------------------------------

PAIRS = frozenset({("es", "en"), ("en", "es"), ("ja", "en"), ("zh", "en")})


def test_libretranslate_when_it_has_the_pair_and_nothing_else():
    # the owner (2026-10-24): LibreTranslate is the site's only translator; Google was removed
    assert tr.route("es", "en", PAIRS).engine == tr.LIBRE
    assert tr.route("yue", "en", PAIRS).engine == tr.LIBRE
    assert tr.route("sw", "en", PAIRS).reason == tr.CANT                     # a pair it hasn't loaded
    assert tr.route("es", "en", frozenset()).reason == tr.CANT               # LibreTranslate down
    assert not hasattr(tr, "GOOGLE") and tr.ENGINE_LABELS == {tr.LIBRE: "LibreTranslate"}


def test_an_edit_makes_a_new_translation():
    assert tr.source_hash("Title", "<p>a</p>") != tr.source_hash("Title", "<p>b</p>")
    assert tr.source_hash("ab", "c") != tr.source_hash("a", "bc")          # the parts are kept apart


# --- the SQL --------------------------------------------------------------------------------------------------------

@pytest.fixture
def db():
    engine = create_engine("sqlite://")
    with engine.connect() as conn:
        conn.execute(text("CREATE TABLE translations (id INTEGER PRIMARY KEY, kind TEXT, item_id INTEGER, source_hash TEXT,"
                          " source_lang TEXT, target TEXT, engine TEXT, title TEXT, text_html TEXT, created_utc INTEGER)"))
        conn.execute(text("CREATE UNIQUE INDEX translations_key ON translations (kind, item_id, target, engine, source_hash)"))
        yield conn


def test_a_translation_is_kept_once_and_found_again(db):
    assert store.kept(db, "post", 7, "en", tr.LIBRE, "h1") is None
    store.keep(db, "post", 7, "en", tr.LIBRE, "h1", "es", "Title", "<p>Hi</p>", 1000)
    store.keep(db, "post", 7, "en", tr.LIBRE, "h1", "es", "Other", "<p>Other</p>", 1001)     # two at once: the first
    assert store.kept(db, "post", 7, "en", tr.LIBRE, "h1") == ("Title", "<p>Hi</p>", "es")
    assert store.kept(db, "post", 7, "en", tr.LIBRE, "h2") is None                            # edited since
    assert store.kept(db, "post", 7, "fr", tr.LIBRE, "h1") is None


# --- the route and the page -----------------------------------------------------------------------------------------

def test_the_route_guards_what_it_translates():
    route = read("ruqqus", "routes", "translation_pages.py")
    handler = route.split("def translate_item(")[1].split("\n@app.route")[0]
    assert "@auth_required" in route and "@validate_formkey" in route
    assert '@limiter.limit("10/minute;60/hour", key_func=member_limit_key)' in route
    # the item's own visibility first: get_post's guards (a Circle post is a 404), private guilds, the word filter
    readable = route.split("def _readable_post(")[1].split("\ndef ")[0]
    assert "get_post(pid, v=v, graceful=True)" in readable and "in_private_guild_for(v)" in readable
    assert "post_hidden(post, v)" in readable and "comment_hidden(item, v)" in handler
    # what comes back is sanitized before it is kept or drawn
    assert "out_body = sanitize(out.pop(0))" in handler and "_clean_title(out.pop(0))" in handler
    # nothing about the reader or the author goes to a service
    services = read("ruqqus", "helpers", "translators.py")
    for leak in ("author", "username", "v.id", "user_id"):
        assert leak not in services.split('"""', 2)[2], leak


def test_the_button_is_never_pressed_for_the_member():
    partial = read("ruqqus", "templates", "partials", "translate_button.html")
    assert "{% if translate_offered(item, v) %}" in partial
    js = read("ruqqus", "assets", "js", "translate.js")
    assert ".innerHTML" not in js and "DOMParser" in js
    assert 'document.addEventListener("click"' in js and "DOMContentLoaded" not in js      # only on a press
    for name, kind in (("submission_listing.html", "post"), ("submission.html", "post"), ("comments.html", "comment")):
        assert f'translate_row("{kind}"' in read("ruqqus", "templates", name), name


def test_libretranslate_is_opt_in_and_unreachable_from_outside():
    compose = read("docker-compose.yml")
    service = compose.split("\n  libretranslate:\n")[1].split("\n\n")[0]
    assert 'profiles: ["translation"]' in service and "ports:" not in service
    assert "image: libretranslate/libretranslate:v" in service
    assert "LIBRETRANSLATE_URL=${LIBRETRANSLATE_URL:-}" in compose
    assert "GOOGLE_TRANSLATE" not in compose and "GOOGLE_TRANSLATE" not in read(".env.example")     # removed


def test_the_privacy_page_names_the_service_only_while_it_is_on():
    page = read("ruqqus", "templates", "help", "privacy.html")
    assert "{% if translation_services().libre %}\n<h2 id=\"translation\">" in page
    section = page.split('<h2 id="translation">')[1].split("{% endif %}\n\n")[0]
    assert "LibreTranslate" in section and "Google" not in section


def test_old_translations_are_pruned():
    retention = read("ruqqus", "helpers", "retention.py")
    assert '"DELETE FROM translations WHERE created_utc < :translations"' in retention


# --- the services, with requests faked ------------------------------------------------------------------------------

class _Answer:
    def __init__(self, status, payload):
        self.status_code, self._payload = status, payload

    def raise_for_status(self):
        if self.status_code >= 400:
            raise RuntimeError(self.status_code)

    def json(self):
        return self._payload


def _fresh_services(monkeypatch):
    from ruqqus.helpers import translators as tx
    monkeypatch.setenv("LIBRETRANSLATE_URL", "http://lt:5000")
    monkeypatch.setitem(tx._languages, "at", 0)
    monkeypatch.setitem(tx._languages, "good_at", 0)
    monkeypatch.setitem(tx._detect, "paused_until", 0)
    return tx


def test_libretranslates_codes_become_rows(monkeypatch):
    tx = _fresh_services(monkeypatch)
    listed = [{"code": "en", "targets": ["en", "zh-Hans", "nb"]}, {"code": "zh-Hans", "targets": ["en"]},
              {"code": "nb", "targets": ["en"]}]
    monkeypatch.setattr(tx.requests, "get", lambda *a, **k: _Answer(200, listed))
    pairs, codes = tx.libre_languages(now=1000)
    assert ("zh", "en") in pairs and ("en", "zh") in pairs and ("no", "en") in pairs     # Chinese reaches it now
    assert codes["zh"] == "zh-Hans" and codes["no"] == "nb"                             # and is sent its own code
    assert tr.offered("yue", ["en"], pairs)


def test_the_last_good_language_list_outlives_a_short_outage(monkeypatch):
    tx = _fresh_services(monkeypatch)
    monkeypatch.setattr(tx.requests, "get", lambda *a, **k: _Answer(200, [{"code": "es", "targets": ["en"]},
                                                                          {"code": "en", "targets": ["es"]}]))
    assert ("es", "en") in tx.libre_pairs(now=1000)

    def down(*a, **k):
        raise OSError("down")
    monkeypatch.setattr(tx.requests, "get", down)
    assert ("es", "en") in tx.libre_pairs(now=1000 + tx.LIST_TTL + 1)              # kept
    assert tx.libre_pairs(now=1000 + tx.LIST_KEEP + tx.LIST_TTL + 60) == frozenset()  # then nothing: no dead buttons


def test_a_second_opinion_pauses_only_when_libretranslate_is_down(monkeypatch):
    tx = _fresh_services(monkeypatch)
    calls = []

    def refuses(*a, **k):
        calls.append(1)
        return _Answer(400, {"error": "q is required"})
    monkeypatch.setattr(tx.requests, "post", refuses)
    assert tx.libre_detect("", now=1) is None and calls == []                # nothing to ask about: not asked
    assert tx.libre_detect("hola", now=1) is None and tx._detect["paused_until"] == 0   # a refusal isn't an outage

    def times_out(*a, **k):
        raise TimeoutError()
    monkeypatch.setattr(tx.requests, "post", times_out)
    # couldn't ask is not the same as unsure (helpers/languages.py decide keeps a code the text already has)
    assert tx.libre_detect("hola", now=10) is UNASKED and tx._detect["paused_until"] == 10 + tx.DETECT_PAUSE
    monkeypatch.setattr(tx.requests, "post", lambda *a, **k: _Answer(200, [{"language": "zh-Hans", "confidence": 90}]))
    assert tx.libre_detect("谢谢", now=20) is UNASKED                              # still paused
    monkeypatch.setattr(tx.requests, "post", lambda *a, **k: _Answer(503, {}))
    tx._detect["paused_until"] = 0
    assert tx.libre_detect("hola", now=30) is UNASKED and tx._detect["paused_until"] == 30 + tx.DETECT_PAUSE
    tx._detect["paused_until"] = 0
    monkeypatch.setattr(tx.requests, "post", lambda *a, **k: _Answer(200, [{"language": "zh-Hans", "confidence": 90}]))
    assert tx.libre_detect("谢谢", now=10 + tx.DETECT_PAUSE + 1) == ("zh", 90.0)


# --- in place -------------------------------------------------------------------------------------------------------

def test_the_translation_replaces_the_text_in_place():
    # the owner (2026-10-24): the parts each template marks are swapped, and "Show original" puts back the same nodes
    card = read("ruqqus", "templates", "submission_listing.html")
    for part in ('class="stretched-link" data-translate-part="title"', 'data-translate-part="snippet"',
                 'id="post-text-{{ p.base36id }}" data-translate-part="body"'):
        assert part in card, part
    page = read("ruqqus", "templates", "submission.html")
    assert page.count('data-translate-part="title"') == 2                          # a link post's and a text post's
    assert page.count('<div class="post-text" data-translate-part="body"') == 2    # plain and behind the blur
    assert '<div id="post-body" class="post-body mt-3">' in page                   # its lang moved onto the text
    comments = read("ruqqus", "templates", "comments.html")
    assert 'class="comment-clamp is-clamped" data-translate-part="body"' in comments
    assert '<div class="comment-body" data-translate-part="body">{{ notice_html(c) | safe }}</div>' in comments
    js = read("ruqqus", "assets", "js", "translate.js")
    assert "new WeakMap()" in js and "createDocumentFragment" in js and "cloneNode" not in js   # moved, not copied
    assert '.card[id^="post-"]' in js and '".comment-main"' in js                  # found from the row, not by id
    assert "window.Translate" in js and "settleCommentClamps(" not in js         # it would remove the toggle
    partial = read("ruqqus", "templates", "partials", "translate_button.html")
    assert 'lang="{{ ui_lang() }}" dir="{{ ui_dir() }}"' in partial and 'class="translate-status" aria-live="polite"' in partial


def test_a_comment_edit_keeps_its_clamp_and_blur_and_resets_the_translation():
    js = read("ruqqus", "assets", "js", "all_js.js")
    edit = js.split("comment_edit=function(id){")[1].split("\n}\n")[0]
    assert ("Translate.reset(scope, {offered: Boolean(answer.translate), kind: 'comment', id: id, after: commentText})"
            in edit)
    assert "querySelector('[data-translate-part=\"body\"]')" in edit and "commentText.innerHTML" not in edit
    route = read("ruqqus", "routes", "comments.py")
    assert '"language_code": c.language_code, "translate": translate_offered(c, v)' in route


# --- the review of 2026-10-24 ---------------------------------------------------------------------------------------

class _Board:
    def __init__(self, banned=False):
        self.is_banned = banned


class _Post:
    def __init__(self, banned=False, deleted=0, purged=0, board=None, profile=False, title="Hola", body="<p>Hola</p>"):
        self.is_banned, self.deleted_utc, self.purged_utc = banned, deleted, purged
        self.board, self.is_profile_post = board or _Board(), profile
        self.title, self.body_html = title, body


class _Comment:
    def __init__(self, post, banned=False, deleted=0, body="<p>Hola</p>"):
        self.parent_submission = post and 1
        self.post, self.is_banned, self.deleted_utc, self.purged_utc = post, banned, deleted, 0
        self.title = object()                     # Comment.title is its author's badge, not text
        self.body_html = body


def test_the_button_and_the_route_refuse_the_same_items():
    assert tr.readable(_Post()) and tr.readable(_Comment(_Post()))
    for post in (_Post(banned=True), _Post(deleted=1), _Post(purged=1), _Post(board=_Board(banned=True))):
        assert not tr.readable(post) and not tr.readable(_Comment(post))      # nor the comments under it
    assert tr.readable(_Post(board=_Board(banned=True), profile=True))        # a profile post: the page's own rule
    assert not tr.readable(_Comment(_Post(), banned=True)) and not tr.readable(_Comment(_Post(), deleted=1))
    assert not tr.readable(_Comment(None))                                     # a system notice
    route = read("ruqqus", "routes", "translation_pages.py")
    assert route.count("translation.readable(") == 3                         # the button, the post, the comment


def test_a_comment_is_measured_by_its_text_alone():
    # Comment.title is a Title object: len() on it was a 500 under every comment by someone with a title
    assert tr.texts(_Comment(_Post(), body="<p>abc</p>")) == ("", "<p>abc</p>")
    assert tr.texts(_Post(title="T", body="")) == ("T", "")
    route = read("ruqqus", "routes", "translation_pages.py")
    assert "title, body = translation.texts(item)" in route


def test_a_purge_or_obliteration_forgets_its_translations(db):
    store.keep(db, "post", 7, "en", tr.LIBRE, "h1", "es", "T", "<p>Hi</p>", 1000)
    store.keep(db, "post", 8, "en", tr.LIBRE, "h1", "es", "T", "<p>Hi</p>", 1000)
    store.keep(db, "comment", 7, "en", tr.LIBRE, "h1", "es", "", "<p>Hi</p>", 1000)
    store.forget(db, "post", [7])
    store.forget(db, "comment", [])
    assert store.kept(db, "post", 7, "en", tr.LIBRE, "h1") is None
    assert store.kept(db, "post", 8, "en", tr.LIBRE, "h1") and store.kept(db, "comment", 7, "en", tr.LIBRE, "h1")
    admin = read("ruqqus", "routes", "admin_api.py")
    for name in ("def purge_post(", "def purge_comment(", "def admin_obliterate_post(", "def admin_obliterate_comment("):
        assert "translation_store.forget(g.db" in admin.split(name)[1].split("\n@app.route")[0], name


def test_an_anonymous_comment_never_shows_its_authors_title():
    comments = read("ruqqus", "templates", "comments.html")
    assert "{% if c.title and not anon_hidden(c, v) %}{{ c.title.rendered | safe }}{% endif %}" in comments
    assert "{% if c.title %}" not in comments


def test_a_folded_comment_hides_its_translate_row():
    from tests.stylesheets import expanded
    for sheet in ("main.scss", "main_dark.scss"):
        assert ".comment.collapsed .translate-row" in expanded(sheet), sheet


def test_an_edit_settles_the_clamp_once_the_text_is_shown_again():
    js = read("ruqqus", "assets", "js", "all_js.js")
    edit = js.split("comment_edit=function(id){")[1].split("\n}\n")[0]
    assert edit.index("document.getElementById('cancel-edit-'+id).click()") < edit.index("settleCommentClamps(scope)")


def test_a_translation_measures_the_clamp_again_and_gives_it_back():
    js = read("ruqqus", "assets", "js", "translate.js")
    assert "function settle(part)" in js and "function unsettle(part)" in js
    assert "button.hidden = fits" in js and "button.remove()" not in js         # hidden, not removed
    assert len(re.findall(r"(?<!un)settle\(part\);", js)) == 2 and "unsettle(part);" in js   # show, apply; reset


def test_a_redrawn_text_drops_an_answer_still_on_its_way():
    js = read("ruqqus", "assets", "js", "translate.js")
    assert "var asking = new WeakMap()" in js and "asking.delete(row)" in js
    ask = js.split("function ask(row) {")[1].split("\n  }\n")[0]
    assert "if (!current()) return;" in ask


def test_an_edit_that_makes_a_comment_translatable_gets_a_row():
    js = read("ruqqus", "assets", "js", "translate.js")
    made = js.split("function makeRow(kind, id) {")[1].split("\n  }\n")[0]
    partial = read("ruqqus", "templates", "partials", "translate_button.html")
    # the row the script makes is the row the partial draws
    for cls in ("translate-row", "translate-status", "translate-btn", "fas fa-language", "translate-btn-label"):
        assert f'class="{cls}' in partial and f'"{cls}"' in made, cls
    assert '>{{ _("Translate") }}<' in partial and '"Translate"' in made
    assert "translate-google" not in partial + js
    assert 'aria-live", "polite"' in made and 'setAttribute("lang", I18N.lang)' in made and 'setAttribute("dir", I18N.dir)' in made
    assert "options.offered === true && options.kind && options.id && options.after" in js


def test_a_refused_comment_edit_keeps_its_rating_and_language():
    route = read("ruqqus", "routes", "comments.py")
    edit = route.split("def edit_comment(")[1].split("\n@app.route")[0]
    stamp = edit.index("apply_comment_severity(c, body_html)")
    assert edit.index("Too much spam") < stamp and edit.index("Remove the following link") < stamp
    assert edit.index("c.language_code = detect_language(body, keep=c.language_code)") < edit.index("c.body = body")


def test_the_translate_button_keeps_the_keyboards_focus():
    js = read("ruqqus", "assets", "js", "translate.js")
    assert ".disabled = " not in js and 'setAttribute("aria-disabled", "true")' in js
    assert 'if (row.classList.contains("is-busy")) return;' in js


def test_the_backfill_leaves_a_row_edited_meanwhile():
    source = read("scripts", "redetect_languages.py")
    assert "keep=r[1]" in source                              # a code is kept while LibreTranslate is down
    # the script's own statements (it imports the app, so they are read from its text: adjacent string literals)
    sql_post = ast.literal_eval("(" + source.split("_SET_POST = text(")[1].split(")\n")[0] + ")")
    sql_comment = ast.literal_eval("(" + source.split("_SET_COMMENT = text(")[1].split(")\n")[0] + ")")
    engine = create_engine("sqlite://")
    with engine.connect() as conn:
        conn.execute(text("CREATE TABLE submissions (id INTEGER, repost_id INTEGER, language_code TEXT)"))
        conn.execute(text("CREATE TABLE submissions_aux (id INTEGER, title TEXT, body TEXT)"))
        conn.execute(text("CREATE TABLE comments (id INTEGER, language_code TEXT)"))
        conn.execute(text("CREATE TABLE comments_aux (id INTEGER, body TEXT)"))
        conn.execute(text("INSERT INTO submissions VALUES (1, 0, 'en'), (2, 1, 'en'), (3, 0, 'en')"))
        conn.execute(text("INSERT INTO submissions_aux VALUES (1, 'Hola', 'todos'), (2, 'Hola', 'todos'), (3, 'x', NULL)"))
        conn.execute(text("INSERT INTO comments VALUES (5, NULL)"))
        conn.execute(text("INSERT INTO comments_aux VALUES (5, 'nuevo')"))
        conn.execute(text(sql_post), {"code": "es", "id": 1, "title": "Hola", "body": "todos"})
        conn.execute(text(sql_post), {"code": "es", "id": 3, "title": "x", "body": "old body"})    # edited since
        conn.execute(text(sql_comment), {"code": "es", "id": 5, "body": "viejo"})                   # edited since
        rows = dict(conn.execute(text("SELECT id, language_code FROM submissions")).fetchall())
        assert rows == {1: "es", 2: "es", 3: "en"}                     # the copy follows; the edited post is left
        assert conn.execute(text("SELECT language_code FROM comments")).scalar() is None
