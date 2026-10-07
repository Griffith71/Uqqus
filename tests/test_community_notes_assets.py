"""Community notes cross the model, the schema, the flag menu, the admin pages and three templates.
These checks keep the layers agreeing and keep the promises: a request is not a flag, only admins write
notes, a note never names an admin, and a post's note is the same on every copy."""
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def read(*parts):
    return ROOT.joinpath(*parts).read_text(encoding="utf-8")


ADMIN = read("ruqqus", "routes", "community_notes.py")
FLAGGING = read("ruqqus", "routes", "flagging.py")


def model_columns(name):
    body = read("ruqqus", "classes", "community_notes.py").split(f"class {name}(")[1].split("\nclass ")[0]
    return set(re.findall(r"^    (\w+) = Column\(", body, re.M))


def table_columns(sql, header):
    block = re.search(rf"{header} \((.*?)\n\);", sql, re.S).group(1)
    return {line.split()[0] for line in (x.strip() for x in block.splitlines()) if line and not line.startswith("CONSTRAINT")}


def test_the_models_the_schema_and_the_migration_have_the_same_columns():
    schema, migration = read("schema.sql"), read("scripts", "migrations", "2026-10-08_community_notes.sql")
    for model, table in (("NoteRequest", "note_requests"), ("CommunityNote", "community_notes")):
        columns = model_columns(model)
        assert columns and table_columns(schema, f"CREATE TABLE public.{table}") == columns, table
        assert table_columns(migration, f"CREATE TABLE IF NOT EXISTS {table}") == columns, table
    for sql in (schema, migration):
        for rule in ("note_requests_one_target CHECK ((post_id IS NULL) <> (comment_id IS NULL))",
                     "community_notes_one_target CHECK ((post_id IS NULL) <> (comment_id IS NULL))",
                     "note_requests_post_user_index", "note_requests_comment_user_index",
                     "community_notes_post_live_index", "community_notes_comment_live_index"):
            assert rule in sql, rule
        assert "WHERE post_id IS NOT NULL AND removed_utc = 0" in sql      # one live note per target
    assert "from .community_notes import *" in read("ruqqus", "classes", "__init__.py")
    assert "from .community_notes import *" in read("ruqqus", "routes", "__init__.py")


def test_a_request_is_not_a_flag():
    note = FLAGGING.split('elif kind == "note":')[1].split("    else:")[0]
    assert "NoteRequest(" in note and "Flag(" not in note and "Report(" not in note
    comment = FLAGGING.split("def api_flag_comment(")[1]
    assert 'if kind == "note":' in comment and "NoteRequest(comment_id=comment.id" in comment
    assert "CommentFlag(" in comment.split('if kind != "admin":')[1]        # a policy flag is unchanged


def test_both_flag_routes_need_the_form_key_and_refuse_what_they_do_not_know():
    for route in ("/api/flag/post/<pid>", "/api/flag/comment/<cid>"):
        head = FLAGGING.split(f'@app.route("{route}", methods=["POST"])')[1].split("def ")[0]
        assert "@is_not_banned" in head and "@validate_formkey" in head, route
    assert 'return "", 422' in FLAGGING.split("def api_flag_post(")[1].split("def _note_refusal")[0]
    assert 'if kind != "admin":\n        return "", 422' in FLAGGING


def test_a_request_is_refused_on_your_own_post_twice_and_past_the_daily_limit():
    refusal = FLAGGING.split("def _note_refusal(")[1].split('@app.route("/api/flag/comment')[0]
    for rule in ("author_id == v.id", "mine.filter(target).first()", "community_notes.DAILY_REQUESTS", "409", "429"):
        assert rule in refusal, rule
    # a copy's request counts for the post it was copied from
    assert "post_id=community_notes.primary_post_id(post)" in FLAGGING


def test_only_admins_write_or_remove_notes_and_every_change_needs_the_form_key():
    for name in ("admin_note_requests", "admin_note_write", "admin_note_dismiss", "admin_note_remove"):
        head = ADMIN.split(f"def {name}(")[0].rsplit("@app.", 1)[1]
        assert "@admin_level_required(3)" in head, name
        if name != "admin_note_requests":
            assert "@validate_formkey" in head and head.startswith("post("), name
    assert "/admin/note_requests" in read("ruqqus", "templates", "admin", "admin_home.html")


def test_a_note_never_names_the_admin_or_the_requesters():
    for name in ("partials/community_note.html",):
        page = read("ruqqus", "templates", *name.split("/"))
        assert ".admin_id" not in page and "author" not in page and "user_id" not in page
        assert "Added by {{ 'SITE_NAME' | app_config }} moderators" in page
    sent = next(line for line in ADMIN.splitlines() if "send_notification(user," in line)
    assert "item.permalink" in sent and "author" not in sent.lower() and "admin" not in sent.lower()
    admin_page = read("ruqqus", "templates", "admin", "note_requests.html")
    assert "user_id" not in admin_page and ".author" not in admin_page


def test_writing_a_note_keeps_the_old_one_and_closes_the_requests():
    write = ADMIN.split("def admin_note_write(")[1].split("@app.post")[0]
    assert '.update({"removed_utc": now})' in write                     # the old note is kept as removed
    assert "g.db.query(NoteRequest).filter(where(NoteRequest)).delete(" in write
    assert write.index("g.db.commit()") < write.index("community_notes.forget()") < write.index("send_notification(")
    assert "clean_body(request.form.get" in write


def test_the_flag_menus_offer_it_and_say_what_happened():
    post, comment = read("ruqqus", "templates", "flag_post_modal.html"), read("ruqqus", "templates", "flag_comment_modal.html")
    assert '<option value="note">' in post and '<option value="note">' in comment
    assert 'id="reportPostAfterTitle"' in post and 'id="reportCommentAfterTitle"' in comment
    assert 'type="button" id="reportCommentButton"' in comment            # it no longer submits the form
    js = read("ruqqus", "assets", "js", "all_js.js")
    assert "flagAnswer(\"reportComment\", kind, xhr.status, \"comment\")" in js
    assert "flagAnswer(\"reportPost\"," in js
    assert 'form.append("report_type", kind)' in js


def test_the_note_is_drawn_under_a_post_a_card_and_a_comment():
    for name, call in (("submission.html", "{{ community_note(p) }}"), ("submission_listing.html", "{{ community_note(p) }}"),
                       ("comments.html", "{{ community_note(c) }}")):
        page = read("ruqqus", "templates", name)
        assert call in page and '{% from "partials/community_note.html" import community_note %}' in page, name
    assert "community_note_of=_note_of" in ADMIN


def test_a_post_and_its_copies_show_the_same_note_and_a_page_costs_nothing_without_one():
    source = read("ruqqus", "helpers", "community_notes.py")
    assert "return post.repost_id or post.id" in source
    assert "key = item.id if comment else primary_post_id(item)" in source
    assert 'if key not in (_live_ids()["comments"] if comment else _live_ids()["posts"]):\n        return None' in source


def test_the_api_carries_the_note_too():
    assert "'community_note': community_notes.text_of(self)," in read("ruqqus", "classes", "submission.py")
    assert "'community_note': community_notes.text_of(self)," in read("ruqqus", "classes", "comment.py")


def test_both_stylesheets_style_the_note():
    for sheet in ("main.scss", "main_dark.scss"):
        css = read("ruqqus", "assets", "style", sheet)
        for cls in (".community-note ", ".community-note-title", ".community-note-body"):
            assert cls in css, (sheet, cls)
    page = read("ruqqus", "templates", "partials", "community_note.html")
    for cls in ("community-note", "community-note-title", "community-note-body", "community-note-by"):
        assert cls in page, cls
