"""Drafts and scheduling cross the model, the schema, the scheduler config, the
composer template and its script. These checks keep the layers agreeing."""
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def read(*parts):
    return ROOT.joinpath(*parts).read_text(encoding="utf-8")


def schema_columns(sql, header):
    m = re.search(rf"{header} \((.*?)\n\);", sql, re.S)
    assert m, header
    cols = set()
    for line in m.group(1).splitlines():
        line = line.strip()
        if line and not line.startswith(("--", "CONSTRAINT")):
            cols.add(line.split()[0])
    return cols


def model_columns():
    return set(re.findall(r"^    (\w+) = Column\(", read("ruqqus", "classes", "post_draft.py"), re.M))


def test_the_model_schema_and_migration_have_the_same_columns():
    model = model_columns()
    assert "status" in model and "publish_utc" in model
    assert schema_columns(read("schema.sql"), "CREATE TABLE public.post_drafts") == model
    assert schema_columns(read("scripts", "migrations", "2026-10-06_post_drafts.sql"), "CREATE TABLE IF NOT EXISTS post_drafts") == model


def test_the_scheduler_runs_under_supervisord_and_can_import_the_app():
    conf = read("supervisord.conf")
    block = conf[conf.index("[program:ruqqusscheduler]"):]
    assert "scripts/publish_scheduled.py" in block
    assert "autostart=true" in block and "autorestart=true" in block
    assert 'PYTHONPATH="/opt/ruqqus/service"' in block     # a script's own folder is not the app's root


def test_the_scheduler_submits_through_the_real_route():
    src = read("scripts", "publish_scheduled.py")
    assert '"/api/vue/submit"' in src           # session auth, JSON answers, same rules as the Post button
    assert "FOR UPDATE SKIP LOCKED" in src      # more than one scheduler is safe
    assert "retry_at" in src and "failure_notice" in src


def test_every_element_the_script_uses_exists_in_the_composer():
    js, page = read("ruqqus", "assets", "js", "post_drafts.js"), read("ruqqus", "templates", "submit.html")
    ids = set(re.findall(r"\bel\('([\w-]+)'\)", js)) | set(re.findall(r"getElementById\('([\w-]+)'\)", js))
    missing = sorted(i for i in ids if f'id="{i}"' not in page)
    assert not missing, f"post_drafts.js uses ids that submit.html does not have: {missing}"


def test_the_script_sends_the_fields_the_server_reads():
    js = read("ruqqus", "assets", "js", "post_drafts.js")
    sent = set(re.findall(r"data\.append\('(\w+)'", js))
    assert sent >= {"formkey", "title", "url", "body", "forward_guilds", "comment_permission",
                    "sensitive", "draft_id", "publish_utc"}
    # the two disclosure boxes are sent from one loop over their names
    assert "['paid_partnership', 'made_with_ai'].forEach" in js
    server = read("ruqqus", "helpers", "post_drafts.py")
    for name in ("title", "url", "body", "forward_guilds", "comment_permission", "paid_partnership", "made_with_ai", "sensitive"):
        assert f'"{name}"' in server, name


def test_the_composer_and_the_post_route_know_about_drafts():
    page, posts = read("ruqqus", "templates", "submit.html"), read("ruqqus", "routes", "posts.py")
    assert 'name="draft_id"' in page and "post_drafts.js" in page
    assert "_discard_draft(v, request.form.get(\"draft_id\"))" in posts     # posting from a draft uses it up
    assert "PostDraft.user_id == v.id" in posts                             # only your own draft opens
