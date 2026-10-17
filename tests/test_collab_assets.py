"""Shared drafts and collaborative posts wired into the site (routes/collab.py, templates/collab/*, the editor script,
posts.py, coauthors.py). The promises: a draft is its current authors' only, on every page and API call; the live event
carries a revision number and nothing else; ops are stamped with the signed-in user, never one the browser names;
publishing goes through the real submit route and the shared edit function, never a copy; a collaborative post is
never edited, deleted or given co-authors directly; text is drawn escaped; both stylesheets have the tints."""
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def read(*parts):
    return ROOT.joinpath(*parts).read_text(encoding="utf-8")


ROUTES = read("ruqqus", "routes", "collab.py")


def _routes():
    return re.findall(r'@app\.route\("([^"]+)", methods=\["(\w+)"\]\)\n((?:@[^\n]+\n)*)def (\w+)\(([^)]*)\):\n(.*?)(?=\n@app|\ndef |\Z)',
                      ROUTES, re.S)


def test_every_draft_route_checks_the_author_and_every_change_has_the_form_key():
    found = _routes()
    assert len(found) >= 20
    for path, method, decorators, name, _args, body in found:
        if method == "POST":
            assert "@validate_formkey" in decorators or name == "collab_presence", name
        if "<int:doc_id>" in path:
            assert "_doc_or_404(doc_id, v)" in body, name
        if path.startswith("/post/<pid>") or path.startswith("/collab/fork"):
            assert "_readable_collab_post(pid, v)" in body, name
    guard = ROUTES.split("def _doc_or_404(", 1)[1].split("\ndef ", 1)[0]
    assert "store.is_author(g.db, doc_id, v.id)" in guard and "abort(404)" in guard
    topic = ROUTES.split("def _guard_collab_topic(", 1)[1].split("\n\n", 1)[0]
    assert "store.is_author(g.db, int(key), v.id)" in topic
    assert 'live_bus.register_topic("collab", _guard_collab_topic)' in ROUTES


def test_ops_are_the_callers_and_the_live_event_is_a_number():
    send = ROUTES.split("def collab_send_op(", 1)[1].split("\n@app", 1)[0]
    assert "store.commit_op(g.db, doc_id, v.id, field, base, op, now, client_key=client_key)" in send
    assert '"collab.rev", {"revision": revision}' in send
    assert sorted(re.findall(r'"collab\.\w+", (\{[^}]*\})', ROUTES)) == ['{"revision": revision}', "{}"]
    assert ROUTES.count("@limiter.limit(") >= 2


def test_publishing_reuses_the_site_and_collaborative_posts_refuse_direct_changes():
    publish = ROUTES.split("def _publish(", 1)[1].split("\n@app", 1)[0]
    assert '_post_as(publisher, "/api/vue/submit",' in publish and "rules.post_fields(snap.get(\"options\"))" in publish
    assert "apply_post_edit(post, post, publisher" in publish and "media_owner_ids=store.everyone(g.db, doc[0])" in publish
    assert "store.add_publish(" in publish and "_sync_coauthors(" in publish
    posts = read("ruqqus", "routes", "posts.py")
    edit = posts.split("def edit_post(", 1)[1].split("\ndef apply_post_edit(", 1)[0]
    assert "collab_store.open_doc_for_post(g.db, primary.id) is not None" in edit      # only while its draft is open (C-01)
    assert "return apply_post_edit(p, primary, v, request.form, request.files.get(\"file\"))" in edit
    delete = posts.split("def delete_post_pid(", 1)[1].split("\ndef delete_post_as(", 1)[0]
    assert "COLLAB_DELETE_REFUSAL" in delete and "return delete_post_as(post, v)" in delete
    coauthor_routes = read("ruqqus", "routes", "coauthors.py")
    for name in ("coauthor_invite", "coauthor_leave", "coauthor_remove"):
        body = coauthor_routes.split(f"def {name}(", 1)[1].split("\n@app", 1)[0]
        assert "if _collab(post):" in body, name
    assert "media_owner_ids or v.id" in posts


def test_decisions_follow_the_rules_module():
    settle = ROUTES.split("def _settle(", 1)[1].split("\ndef ", 1)[0]
    assert "rules.counted(store.voters(g.db, doc_id)" in settle and "rules.tally(kind, doc_rules, voters" in settle
    assert "store.decide(g.db, proposal[0], outcome, now)" in settle            # decided once, whoever races
    propose = ROUTES.split("def collab_propose(", 1)[1].split("\n@app", 1)[0]
    assert "blocked_with_any=bool(candidate and any(u.any_block_exists(candidate) for u in people.values()))" in propose
    assert "store.vote(g.db, pid, v.id, True, now)" in propose                   # the proposer's yes
    vote = ROUTES.split("def collab_vote(", 1)[1].split("\n@app", 1)[0]
    assert "You don't vote on your own removal." in vote
    assert "settle_open_proposals(db_session)" in read("scripts", "recomputes.py")


def test_templates_draw_text_escaped_and_scripts_never_write_html():
    blame = read("ruqqus", "templates", "collab", "written_by.html")
    assert "{{ piece }}" in blame and "piece | safe" not in blame
    assert 'class="collab-tint-' in blame and 'title="Typed by {{ who.name }}"' in blame
    editor = read("ruqqus", "templates", "collab", "editor.html")
    assert '<div class="post-toolbar" data-target="collab-body"></div>' in editor
    assert "asset_url('js/collab_ot.js')" in editor and "asset_url('js/collab_editor.js')" in editor
    script = read("ruqqus", "assets", "js", "collab_editor.js")
    assert "innerHTML" not in script and "DOMParser" in script and "OT.transform(outstanding.op, op)" in script
    assert "key: outstanding.key" in script                                    # resent ops are applied once
    for name in ("main.scss", "main_dark.scss"):
        css = read("ruqqus", "assets", "style", name)
        assert css.index(".collab-page {") < css.index("// Create post card (templates/submit.html")
        tints = re.search(r"@each \$n, \$bg in \(([^)]*)\)\s*\{\s*\.collab-tint-#\{\$n\}", css)
        assert tints and len(re.findall(r"\d #[0-9a-f]{6}", tints.group(1))) == 8, name


def test_entry_points_and_docs():
    # the way in is the post area (the tabs above the Create a post card), not the account menu
    assert 'composer_modes("post")' in read("ruqqus", "templates", "submit.html")
    modes = read("ruqqus", "templates", "partials", "composer_modes.html")
    assert 'href="/submit"' in modes and 'href="/collab"' in modes
    for name in ("default.html", "header.html"):
        assert "/collab" not in read("ruqqus", "templates", name)
    for name in ("editor.html", "list.html"):
        page = read("ruqqus", "templates", "collab", name)
        assert '{% extends "submit.html" %}' in page and 'composer_modes("together")' in page, name
    assert "from .collab import *" in read("ruqqus", "routes", "__init__.py")
    docs = read("CLAUDE.md")
    assert "## Shared drafts and collaborative posts (legacy app)" in docs and "2026-11-01_collab_posts.sql" in docs
