"""Server side of the post editor's toolbar: the Write / Preview toggle and the
Template button's saved snippets."""
import time

import mistletoe
from flask import g, jsonify, request

from ruqqus.__main__ import app, limiter
from ruqqus.classes import PostTemplate
from ruqqus.helpers.markdown import CustomRenderer, preprocess
from ruqqus.helpers.post_fields import (
    PostFieldError, TEMPLATE_LIMIT, check_body, check_template_body, clean_template_name)
from ruqqus.helpers.sanitize import sanitize
from ruqqus.helpers.wrappers import auth_required, validate_formkey


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
        body = preprocess(check_body(request.form.get("body", "")))
    except PostFieldError as e:
        return jsonify({"error": str(e)}), 400

    with CustomRenderer() as renderer:
        html = renderer.render(mistletoe.Document(body))

    return jsonify({"html": sanitize(html, linkgen=True)})


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
            return jsonify({"error": f"You can keep {TEMPLATE_LIMIT} templates. Delete one to save another."}), 400
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
        return jsonify({"error": "That template doesn't exist."}), 404

    g.db.delete(template)
    return jsonify({"deleted": tid})
