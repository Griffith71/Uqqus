"""Content disclosure (paid partnership / made with AI) crosses the schema, the
models, the routes, the templates and the comment JS. These checks keep the
layers agreeing on the field names."""
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
FIELDS = ("paid_partnership", "made_with_ai")


def read(*parts):
    return ROOT.joinpath(*parts).read_text(encoding="utf-8")


def _table(schema, name):
    m = re.search(rf"CREATE TABLE public\.{name} \((.*?)\n\);", schema, re.S)
    assert m, name
    return m.group(1)


def test_both_tables_have_both_columns_in_schema_and_migration():
    schema = read("schema.sql")
    migration = read("scripts", "migrations", "2026-10-06_content_disclosure.sql")
    for table in ("submissions", "comments"):
        body = _table(schema, table)
        for field in FIELDS:
            assert f"{field} boolean DEFAULT false NOT NULL" in body, (table, field)
            assert f"ALTER TABLE {table} ADD COLUMN IF NOT EXISTS {field}" in migration, (table, field)


def test_the_models_declare_the_columns():
    for model in ("submission.py", "comment.py"):
        src = read("ruqqus", "classes", model)
        for field in FIELDS:
            assert f"{field} = Column(Boolean" in src, (model, field)


def test_routes_read_the_fields_where_content_is_made_and_edited():
    posts, comments = read("ruqqus", "routes", "posts.py"), read("ruqqus", "routes", "comments.py")
    for field in FIELDS:
        assert posts.count(f'flag(request.form, "{field}")') == 1, f"submit_post does not read {field}"
        assert f'flag(form, "{field}", primary.{field})' in posts, f"edit_post does not read {field}"
        assert f'flag(request.form, "{field}")' in comments, f"api_comment does not read {field}"
        assert f'flag(request.form, "{field}", c.{field})' in comments, f"edit_comment does not read {field}"
        # forwards copy the disclosure
        assert f"{field}=primary.{field}" in posts and f"{field}=comment.{field}" in posts, field


def test_the_forms_and_the_comment_js_use_the_same_names():
    macros = read("ruqqus", "templates", "partials", "post_options.html")
    js = read("ruqqus", "assets", "js", "all_js.js")
    for field in FIELDS:
        assert f'name="{field}"' in macros
        assert f"form.append('{field}'" in js, f"all_js.js does not send {field}"
    # the ids the JS reads are the ones the toggles build: <prefix>-paid / <prefix>-ai
    assert "'comment-disc-'+fullname+'-paid'" in js and "'comment-edit-disc-'+id+'-paid'" in js
    assert 'disclosure_toggles("comment-disc-" ~' in read("ruqqus", "templates", "comments.html")
    assert 'disclosure_toggles("comment-edit-disc-" ~' in read("ruqqus", "templates", "comments.html")
    assert 'disclosure_toggles("comment-disc-" ~' in read("ruqqus", "templates", "submission.html")


def test_the_labels_show_wherever_a_post_or_comment_is_listed():
    for name in ("submission.html", "submission_listing.html"):
        assert "disclosure_row(p, v)" in read("ruqqus", "templates", name), name
    for name in ("comments.html", "embeds/comment.html"):
        assert "disclosure_badges(c, v)" in read("ruqqus", "templates", *name.split("/")), name
