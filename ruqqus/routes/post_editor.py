"""Server side of the post editor's toolbar: the Write / Preview toggle, the Preview overlay's card and the
Template button's saved snippets."""
import time
from types import SimpleNamespace
from urllib.parse import urlparse

import mistletoe
from flask import g, jsonify, render_template, request

from ruqqus.__main__ import app, limiter
from ruqqus.classes import PostTemplate
from ruqqus.helpers.i18n_runtime import tr, trn
from ruqqus.helpers.markdown import CustomRenderer, preprocess
from ruqqus.helpers.post_fields import (
    PostFieldError, TEMPLATE_LIMIT, check_body, check_template_body, clean_template_name, clean_title, flag, normalize_url)
from ruqqus.helpers.sanitize import sanitize
from ruqqus.helpers.wrappers import auth_required, validate_formkey


def render_body(raw):
    """A post's text as it is stored: checked, preprocessed, rendered with a single Enter as a line break (post text, as
    the post and comment routes do), then sanitized. PostFieldError when it is over the limit."""
    body = preprocess(check_body(raw))
    with CustomRenderer(breaks=True) as renderer:
        html = renderer.render(mistletoe.Document(body))
    return sanitize(html, linkgen=True)


@app.post("/api/preview")
@limiter.limit("60/minute")
@auth_required
@validate_formkey
def preview_markdown(v):
    """Render post text the way it will look once posted. Nothing is saved.

Required form data:
* `body` - Raw text, up to the post body limit.
"""
    try:
        html = render_body(request.form.get("body", ""))
    except PostFieldError as e:
        return jsonify({"error": str(e)}), 400

    return jsonify({"html": html})


@app.post("/api/post_preview")
@limiter.limit("60/minute")
@auth_required
@validate_formkey
def post_preview(v):
    """The post as its own page draws it, for the composers' Preview overlay (assets/js/post_editor.js). Nothing is saved
and nothing is sent anywhere: the card is built from what the composer holds, for the person writing it.

Form data (all optional):
* `title`, `body`, `url` - checked as a post's are.
* `anonymous`, `paid_partnership`, `made_with_ai`, `sensitive` - the flags.

Answers `{"html": <the card>}` (templates/partials/post_preview.html), or `{"error": ...}` with a 400.
"""
    form = request.form
    try:
        title = clean_title(form.get("title")) if (form.get("title") or "").strip() else ""
        url = normalize_url(form.get("url"))
        body_html = render_body(form.get("body", ""))
    except PostFieldError as e:
        return jsonify({"error": str(e)}), 400

    domain = urlparse(url).netloc if url else ""
    if domain.startswith("www."):
        domain = domain[4:]
    # a stand-in for the post, so the page's own helpers (author_of, anon_hidden, the disclosure badges) read it: the author is
    # the person writing it, who is told who wrote their own anonymous post
    post = SimpleNamespace(
        author=v, author_id=v.id, is_anonymous=flag(form, "anonymous"),
        paid_partnership=flag(form, "paid_partnership"), made_with_ai=flag(form, "made_with_ai"),
        is_sensitive=flag(form, "sensitive"), title=title, url=url, domain=domain, body_html=body_html)
    return jsonify({"html": render_template("partials/post_preview.html", v=v, p=post)})


@app.get("/api/post_templates")
@auth_required
def list_post_templates(v):
    """Your saved post templates, newest first."""
    rows = g.db.query(PostTemplate).filter_by(user_id=v.id).order_by(
        PostTemplate.created_utc.desc(), PostTemplate.id.desc()).all()
    return jsonify({"templates": [row.json for row in rows], "limit": TEMPLATE_LIMIT})


@app.post("/api/post_templates")
@limiter.limit("30/minute")
@auth_required
@validate_formkey
def create_post_template(v):
    """Save text as a template you can insert into later posts. Saving under a
name you already use replaces that template.

Required form data:
* `name` - Up to 60 characters.
* `body` - The text, up to the post body limit.
"""
    try:
        name = clean_template_name(request.form.get("name"))
        body = check_template_body(request.form.get("body"))
    except PostFieldError as e:
        return jsonify({"error": str(e)}), 400

    mine = g.db.query(PostTemplate).filter_by(user_id=v.id).all()
    existing = next((t for t in mine if t.name.lower() == name.lower()), None)

    if existing:
        existing.name = name
        existing.body = body
        g.db.add(existing)
        saved = existing
    else:
        if len(mine) >= TEMPLATE_LIMIT:
            return jsonify({"error": trn("You can keep %(num)s template. Delete one to save another.",
                                          "You can keep %(num)s templates. Delete one to save another.",
                                          TEMPLATE_LIMIT)}), 400
        saved = PostTemplate(user_id=v.id, name=name, body=body, created_utc=int(time.time()))
        g.db.add(saved)

    g.db.flush()
    return jsonify(saved.json)


@app.post("/api/post_templates/<int:tid>/delete")
@auth_required
@validate_formkey
def delete_post_template(tid, v):
    """Delete one of your saved post templates."""
    template = g.db.query(PostTemplate).filter_by(id=tid, user_id=v.id).first()
    if not template:
        return jsonify({"error": tr("That template doesn't exist.")}), 404

    g.db.delete(template)
    return jsonify({"deleted": tid})
