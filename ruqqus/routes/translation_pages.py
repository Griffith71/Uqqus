"""The Translate button (rules in helpers/translation.py): the route that translates a post or a comment for a member,
the template helpers that decide where the button shows, and the member's languages in Settings > Content.

Named translation_pages.py, not translation.py: `from ruqqus.routes import *` would put a module named like the helper
over it (the star-import trap in CLAUDE.md)."""
import time

import bleach
from flask import abort, g, jsonify, redirect, request

from ruqqus.helpers import languages, translation, translation_store, translators
from ruqqus.helpers.get import get_comment, get_post
from ruqqus.helpers.i18n_runtime import tr
from ruqqus.helpers.post_excerpt import SHOWS_TEXT, excerpt
from ruqqus.helpers.sanitize import sanitize
from ruqqus.helpers.visibility import comment_hidden, post_hidden
from ruqqus.helpers.wrappers import auth_required, validate_formkey
from ruqqus.__main__ import app, limiter, member_limit_key


def _is_id(raw):
    """A base36 id (what get_post and get_comment decode), nothing else."""
    return bool(raw) and len(raw) <= 10 and raw.isascii() and raw.isalnum() and raw == raw.lower()


def reader_languages(v):
    """(languages, chosen) for this reader: their own choice, or what their browser asks for (once per request)."""
    if "_reader_languages" not in g.__dict__:
        g._reader_languages = translation.reader_languages(getattr(v, "languages", "") if v else "",
                                                           request.headers.get("Accept-Language", ""))
    return g._reader_languages


def _pairs():
    """LibreTranslate's pairs, once per request."""
    if "_translation_pairs" not in g.__dict__:
        g._translation_pairs = translators.libre_pairs()
    return g._translation_pairs


def translate_offered(item, v):
    """Draw "Translate" under `item` (a post or a comment) for this reader: it isn't in their languages and
    LibreTranslate can translate it (translation.offered asks the same `engines` the route uses), and it is readable at
    all (translation.readable, which the route asks too)."""
    if not v or not translators.available() or not translation.readable(item):
        return False
    return translation.offered(getattr(item, "language_code", None), reader_languages(v)[0], _pairs())


class _LanguageChoices:
    """The rows a member chooses their languages from, named in the reader's interface language (helpers/i18n.py:
    English is exactly the row label, in the same order); other languages sort by their own names."""

    def __iter__(self):
        from ruqqus.helpers import i18n
        from ruqqus.helpers.i18n_runtime import current_locale
        key = current_locale()
        rows = [(row["value"], i18n.language_name(row["value"], key) or row["label"])
                for row in languages.catalogue() if row["value"] != languages.UNKNOWN]
        if key != i18n.ENGLISH:
            rows.sort(key=lambda row: row[1].casefold())
        return iter(rows)


app.jinja_env.globals.update(
    translate_offered=translate_offered,
    my_languages=lambda v: reader_languages(v),
    translation_services=lambda: {"libre": bool(translators.libre_url())},
    # the rows a member chooses their languages from (helpers/languages.py: alike languages share one)
    language_choices=_LanguageChoices(),
    # can anything translate into this row now? (Settings > Content: a hint under the first language)
    translatable_into=lambda key: any(target == key for _, target in translators.libre_pairs()),
)


def _readable_post(pid, v):
    """The post, if this member may read it (get_post's guards, Circle posts included: a 404)."""
    post = get_post(pid, v=v, graceful=True)
    if post is None or not translation.readable(post) or post.in_private_guild_for(v) or post_hidden(post, v):
        abort(404)
    return post


def _clean_title(text):
    # a title is plain text: whatever comes back is reduced to it and escaped
    return bleach.clean(bleach.clean(text or "", tags=[], strip=True))


@app.route("/api/translate/<kind>/<item_id>", methods=["POST"])
@limiter.limit("10/minute;60/hour", key_func=member_limit_key)
@auth_required
@validate_formkey
def translate_item(kind, item_id, v):
    if kind not in ("post", "comment") or not _is_id(item_id):
        abort(404)
    if kind == "post":
        item = _readable_post(item_id, v)
    else:
        item = get_comment(item_id, v=v, graceful=True)
        if item is None or not translation.readable(item) or comment_hidden(item, v):
            abort(404)
        _readable_post(item.post.base36id, v)

    reader = reader_languages(v)[0]
    source = languages.key_of(item.language_code)
    if not source or source == languages.UNKNOWN:
        return jsonify({"error": tr("We couldn't tell which language this is in.")}), 400
    target = translation.target_for(reader)
    if translation.same_language(source, target):
        return jsonify({"error": tr("This is already in your language.")}), 400

    title, body = translation.texts(item)
    now = int(time.time())
    choice = translation.route(source, target, _pairs())
    if not choice.engine:
        return jsonify({"error": choice.reason}), 409

    digest = translation.source_hash(title, body)
    found = translation_store.kept(g.db, kind, item.id, target, choice.engine, digest)
    if found is None:
        texts = [t for t in (title, body) if t]
        try:
            out, translated_from = translators.translate(choice.engine, texts, source, target, fmt="html")
        except translators.TranslatorDown:
            return jsonify({"error": tr("Translation isn't available right now. Try again in a little while.")}), 503
        out_title = _clean_title(out.pop(0)) if title else ""
        out_body = sanitize(out.pop(0)) if body else ""
        translation_store.keep(g.db, kind, item.id, target, choice.engine, digest, translated_from, out_title,
                               out_body, now)
        g.db.commit()
        found = (out_title, out_body, translated_from)

    response = jsonify({
        "title": found[0],
        "html": found[1],
        # the card's first lines, as plain text (helpers/post_excerpt.py), for a post's feed card
        "snippet": excerpt(found[1], shows=SHOWS_TEXT)[0] if kind == "post" and found[1] else "",
        "engine": choice.engine,
        "engine_label": translation.ENGINE_LABELS[choice.engine],
        "from": languages.label(found[2] or source) or languages.label(source),
        "to": languages.label(target),
        "to_code": target,
    })
    response.headers["Cache-Control"] = "private, no-store"
    return response


@app.route("/settings/languages", methods=["POST"])
@auth_required
@validate_formkey
def settings_languages(v):
    """A member's own languages (Settings > Content): the Translate button shows under what isn't in one of them."""
    codes = translation.parse_languages(",".join(request.form.getlist("languages")) or request.form.get("codes", ""))
    v.languages = ",".join(codes)
    g.db.add(v)
    g.db.commit()
    return redirect("/settings/content#languages")
