"""The Preview overlay's card (the owner, 2026-10-30): `POST /api/post_preview` and templates/partials/post_preview.html draw the post as
its own page does, for the person writing it. What these tests keep, without a database: the route's guards, that the text is
rendered by the one helper the plain Preview uses, that the only HTML the card trusts is what the server made, that it names the
author the way every other place does (anonymity.py), and that it uses only messages the catalogues already have (a new one would
lower every language's share until it was translated). The real app is tried in tests/pg/test_post_preview.py and the look in
scripts/ui/post_rail.spec.ts."""
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent / "ruqqus"
ROUTE = (ROOT / "routes" / "post_editor.py").read_text(encoding="utf-8")
CARD = (ROOT / "templates" / "partials" / "post_preview.html").read_text(encoding="utf-8")
MESSAGES = (ROOT / "translations" / "fr" / "LC_MESSAGES" / "messages.po").read_text(encoding="utf-8")


def route(name):
    return ROUTE.split(f"def {name}(", 1)[1].split("\n\n\n", 1)[0]


def test_the_route_is_guarded_like_the_plain_preview():
    for path in ("/api/preview", "/api/post_preview"):
        head = ROUTE.split(f'@app.post("{path}")', 1)[1].split("\ndef ", 1)[0]
        assert '@limiter.limit("60/minute")' in head and "@auth_required" in head and "@validate_formkey" in head, path


def test_the_text_is_rendered_by_the_one_helper_both_previews_use():
    helper = route("render_body")
    assert "preprocess(check_body(raw))" in helper and "CustomRenderer(breaks=True)" in helper and "sanitize(html, linkgen=True)" in helper
    assert "render_body(request.form.get(\"body\", \"\"))" in route("preview_markdown")
    assert "render_body(form.get(\"body\", \"\"))" in route("post_preview")
    assert ROUTE.count("CustomRenderer(") == 1          # the line-break rule (tests/test_markdown_breaks_assets.py) has one place to be wrong in


def test_the_title_and_link_are_checked_as_a_post_s_are_and_nothing_is_saved():
    body = route("post_preview")
    assert "clean_title(form.get(\"title\"))" in body and "normalize_url(form.get(\"url\"))" in body
    assert "except PostFieldError as e:" in body and "return jsonify({\"error\": str(e)}), 400" in body
    for flag in ("anonymous", "paid_partnership", "made_with_ai", "sensitive"):
        assert f'flag(form, "{flag}")' in body, flag
    assert "g.db.add" not in body and "commit" not in body and "Submission(" not in body


def test_the_card_trusts_only_what_the_server_made():
    safe = re.findall(r"\{\{\s*([^}]*?)\s*\|\s*safe\s*\}\}", CARD)
    assert sorted(safe) == ["author.title.rendered", "p.body_html", "p.title"], safe       # the user's chip, the sanitized text, the cleaned title
    assert "<script" not in CARD and "onclick" not in CARD and "href=\"{{" not in CARD         # drawn, not used: no link, no handler
    assert CARD.count("aria-hidden=\"true\"") == 2                                           # the action row and the vote column


def test_the_card_names_the_author_the_way_every_other_place_does():
    assert "{% set author = author_of(p, v) %}" in CARD
    for never in ("v.username", "v.shown_name", "p.author", "v.profile_url"):
        assert never not in CARD, never
    assert "author.profile_url" in CARD and "author.shown_name" in CARD and "handle_tag(author)" in CARD
    assert "disclosure_row(p, v)" in CARD


def test_the_card_uses_only_messages_the_catalogues_already_have():
    used = set(re.findall(r"_\(\s*\"([^\"]+)\"", CARD))
    assert used == {"just now", "by %(author)s", "text post", "sensitive content"}, used
    for msgid in used:
        assert f'msgid "{msgid}"' in MESSAGES, msgid


def test_the_card_has_the_post_pages_own_classes_and_nesting():
    # what the post page draws (templates/submission.html): the same classes in the same order, so the stylesheets lay it out the same
    order = [CARD.index(x) for x in ('class="card border-0 post-preview-card"', "d-flex flex-row-reverse flex-nowrap justify-content-end",
                                     'class="card-block w-100 my-md-auto"', 'class="post-meta text-left mb-md-2"', "card-title post-title",
                                     'class="post-body mt-3"', 'class="post-text"', 'class="post-actions mt-2 flex-grow-1"', 'class="voting mb-auto"')]
    assert order == sorted(order)
    page = (ROOT / "templates" / "submission.html").read_text(encoding="utf-8")
    for needed in ('d-flex flex-row-reverse flex-nowrap justify-content-end', 'class="card-block w-100 my-md-auto"', 'card-title post-title',
                   'class="post-body mt-3"', 'class="post-text"', 'class="voting mb-auto"'):
        assert needed in page, needed
    assert 'id="post-body"' not in CARD and 'id="voting"' not in CARD            # ids stay unique on the composer's page
