"""Bookmark folders cross the model, the schema, the routes, two listings, the History tab, six menus and the
page shell. These checks keep the layers agreeing and keep the promises: a folder and a bookmark are the
caller's own, deleting a folder never deletes a bookmark, and moving an item never bookmarks something the
caller may not see."""
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def read(*parts):
    return ROOT.joinpath(*parts).read_text(encoding="utf-8")


ROUTES = read("ruqqus", "routes", "bookmark_folders.py")
USER = read("ruqqus", "classes", "user.py")
HISTORY = read("ruqqus", "routes", "history.py")
HISTORY_PAGE = read("ruqqus", "templates", "history.html")
SCRIPT = read("ruqqus", "assets", "js", "bookmark_folders.js")
MODAL = read("ruqqus", "templates", "partials", "bookmark_folder_modal.html")


def route(name):
    head = ROUTES.split(f"def {name}(")[0].rsplit("@app.", 1)[1]
    return head + "def " + name + "(" + ROUTES.split(f"def {name}(")[1].split("\n@app.")[0]


# --- storage ------------------------------------------------------------------------------------

def test_the_model_the_schema_and_the_migration_have_the_same_columns():
    model = set(re.findall(r"^    (\w+) = Column\(", read("ruqqus", "classes", "bookmark_folder.py"), re.M))
    assert model == {"id", "user_id", "name", "created_utc"}
    for sql, header in ((read("schema.sql"), "CREATE TABLE public.bookmark_folders"),
                        (read("scripts", "migrations", "2026-10-09_bookmark_folders.sql"), "CREATE TABLE IF NOT EXISTS bookmark_folders")):
        block = re.search(rf"{header} \((.*?)\n\);", sql, re.S).group(1)
        columns = {line.split()[0] for line in (x.strip() for x in block.splitlines()) if line and not line.startswith("CONSTRAINT")}
        assert columns == model, header
    assert "from .bookmark_folder import *" in read("ruqqus", "classes", "__init__.py")
    assert "from .bookmark_folders import *" in read("ruqqus", "routes", "__init__.py")


def test_both_bookmark_tables_point_at_a_folder_and_a_deleted_folder_only_unsorts():
    for source in (read("ruqqus", "classes", "submission.py").split("class SaveRelationship")[1].split("class RepostRelationship")[0],
                   read("ruqqus", "classes", "comment.py").split("class CommentSaveRelationship")[1].split("class CommentRepostRelationship")[0]):
        assert 'folder_id = Column(Integer, ForeignKey("bookmark_folders.id", ondelete="SET NULL"), nullable=True)' in source
    schema = read("schema.sql")
    migration = read("scripts", "migrations", "2026-10-09_bookmark_folders.sql")
    for table in ("save_relationship", "comment_save_relationship"):
        assert re.search(rf"CREATE TABLE public\.{table} \([^;]*folder_id integer", schema), table
        assert f"ALTER TABLE ONLY public.{table}\n    ADD CONSTRAINT {table}_folder_fkey FOREIGN KEY (folder_id) REFERENCES public.bookmark_folders(id) ON DELETE SET NULL;" in schema
        assert f"ALTER TABLE {table} ADD COLUMN IF NOT EXISTS folder_id integer;" in migration
        assert f"REFERENCES bookmark_folders(id) ON DELETE SET NULL" in migration
    # a name is unique per member, ignoring case
    assert "CREATE UNIQUE INDEX bookmark_folders_name_key ON public.bookmark_folders USING btree (user_id, lower((name)::text));" in schema
    assert "CREATE UNIQUE INDEX IF NOT EXISTS bookmark_folders_name_key ON bookmark_folders (user_id, lower(name));" in migration


# --- who may do what -----------------------------------------------------------------------------

def test_every_change_is_a_post_that_needs_a_login_and_the_form_key():
    for name in ("bookmark_folders_create", "bookmark_folders_rename", "bookmark_folders_delete", "bookmark_move"):
        head = route(name).split("def ")[0]
        assert 'methods=["POST"]' in head.splitlines()[0] and "@auth_required" in head and "@validate_formkey" in head, name
    assert "@auth_required" in route("bookmark_folders_list").split("def ")[0]


def test_a_folder_is_only_ever_the_callers_own_and_someone_elses_is_a_404():
    own = ROUTES.split("def _folder_or_404(")[1].split("\n\n\n")[0]
    assert "filter_by(id=folder_id, user_id=v.id)" in own and "abort(404)" in own
    for name in ("bookmark_folders_rename", "bookmark_folders_delete"):
        assert "_folder_or_404(v, folder_id)" in route(name), name
    assert "_folder_or_404(v, folder_id)" in route("bookmark_move")
    assert "filter_by(user_id=v.id)" in ROUTES.split("def _folders(")[1].split("\n\n\n")[0]


def test_deleting_a_folder_never_deletes_a_bookmark():
    body = route("bookmark_folders_delete")
    assert "for model in (SaveRelationship, CommentSaveRelationship):" in body
    assert 'filter_by(user_id=v.id, folder_id=folder.id).update(' in body and '{"folder_id": None}' in body
    assert "g.db.delete(folder)" in body
    assert body.index('{"folder_id": None}') < body.index("g.db.delete(folder)")
    # the only thing deleted is the folder row
    assert body.count("g.db.delete(") == 1


def test_moving_checks_the_item_is_visible_and_bookmarks_only_what_is_live():
    target = ROUTES.split("def _target(")[1].split("\n\n\n")[0]
    assert "post_hidden(item, v)" in target and "comment_hidden(item, v)" in target
    assert "BASE36.match(ident or \"\")" in target and "kind not in (\"post\", \"comment\")" in target
    move = route("bookmark_move")
    assert "if item.is_banned or item.deleted_utc:" in move and "abort(404)" in move
    assert move.index("_target(v, kind") < move.index("_folder_or_404(")
    # an empty folder_id is "unsorted"; anything that is not a plain number is a 404
    assert "folder_id = rules.parse_id(raw)" in move and "if folder_id is None:" in move and "isdigit" not in ROUTES


def test_the_name_rules_come_from_the_helper_and_a_clash_is_a_409():
    assert "rules.clean_name(request.values.get(\"name\"))" in ROUTES
    assert "rules.check_new(name, existing, len(existing))" in route("bookmark_folders_create")
    assert "rules.check_rename(" in route("bookmark_folders_rename")
    assert route("bookmark_folders_create").count("409") >= 2


# --- the listings and the History tab -----------------------------------------------------------------

def test_both_saved_listings_can_be_filtered_by_folder():
    posts = USER.split("def saved_idlist(")[1].split("def saved_comment_idlist(")[0]
    assert "folder=None" in posts.split("\n")[0]
    assert "SaveRelationship.folder_id.is_(None) if folder == 0" in posts and "else SaveRelationship.folder_id == folder" in posts
    comments = USER.split("def saved_comment_idlist(")[1][:2500]
    assert "folder=None" in USER.split("def saved_comment_idlist(")[1].split("\n")[0]
    assert "CommentSaveRelationship.folder_id.is_(None) if folder == 0" in comments
    # the existing rules (word filter, blocks, mutes) still run after the folder filter
    assert posts.index("SaveRelationship.folder_id") < posts.index("filter_posts(posts, self)")
    assert comments.index("CommentSaveRelationship.folder_id") < comments.index("filter_comments(comments, self)")


def test_the_history_tab_reads_the_folder_and_an_unknown_one_is_a_404():
    assert "folder_rules.parse_filter(request.args.get(\"folder\"))" in HISTORY
    assert "if folder is None:\n            abort(404)" in HISTORY
    assert "{folder_rules.ALL: None, folder_rules.UNSORTED: 0}.get(folder_kind, folder_id)" in HISTORY
    assert "filter_by(user_id=v.id)" in HISTORY.split("folders = ")[1].split("\n")[0]
    assert "v.saved_idlist(page=page, folder=saved_folder)" in HISTORY
    assert "v.saved_comment_idlist(page=page, folder=saved_folder)" in HISTORY


def test_every_link_of_the_bookmarked_tab_keeps_the_folder():
    # the continuous feed follows the Next link, so it has to carry the folder too
    keep = "{% if active_tab == 'bookmarked' and folder_query %}&folder={{ folder_query }}{% endif %}"
    assert HISTORY_PAGE.count(keep) == 5          # All / Posts / Comments, Prev, Next
    assert 'id="bookmark-folders"' in HISTORY_PAGE and "{% if active_tab == 'bookmarked' %}" in HISTORY_PAGE
    assert "{{ f.name }}" in HISTORY_PAGE and "| safe" not in HISTORY_PAGE.split('id="bookmark-folders"')[1].split("{% endif %}\n\n")[0]


# --- the sheet and the menus -------------------------------------------------------------------------------

def test_move_to_folder_is_only_offered_on_the_bookmarked_tab():
    # Bookmarks are sorted from History, not from the menus of every post and comment: each of the six
    # menu items only draws when the History route sets organise_bookmarks.
    for name, spots in (("submission_listing.html", 2), ("submission.html", 2), ("comments.html", 2)):
        html = read("ruqqus", "templates", name)
        assert html.count("bookmark-folder-item") == spots, name
        assert html.count("organise_bookmarks") == spots, name
        assert html.count("Move to folder") == spots and "Add to folder" not in html, name
        assert html.count('data-kind="post"') + html.count('data-kind="comment"') == spots, name
    assert read("ruqqus", "templates", "comments.html").count('data-kind="comment"') == 2
    bookmarked = HISTORY.split('def history_bookmarked(')[1].split("def history_viewed(")[0]
    assert bookmarked.count("organise_bookmarks=True") == 1
    assert "organise_bookmarks" not in HISTORY.split("def history_viewed(")[1]
    assert "To sort a bookmark, open its &hellip; menu and choose <strong>Move to folder</strong>." in HISTORY_PAGE


def test_the_sheet_and_the_script_are_only_on_the_bookmarked_tab_and_not_in_a_panel_frame():
    shell = read("ruqqus", "templates", "default.html")
    assert "{% if v and organise_bookmarks and not request.args.get('embed') %}{% include \"partials/bookmark_folder_modal.html\" %}{% endif %}" in shell
    assert "{% if v and organise_bookmarks and not request.args.get('embed') %}<script src=\"/assets/js/bookmark_folders.js?v=2\"></script>{% endif %}" in shell


def test_every_element_the_script_uses_exists_and_names_are_drawn_as_text():
    ids = set(re.findall(r"getElementById\('([\w-]+)'\)", SCRIPT))
    sheet = {"bookmarkFolderModal", "bookmark-folder-list", "bookmark-folder-form", "bookmark-folder-input",
             "bookmark-folder-create", "bookmark-folder-status"}
    row = {"bookmark-folders", "folder-new", "folder-rename", "folder-delete"}
    assert sheet <= ids and row <= ids
    assert not [i for i in sheet if f'id="{i}"' not in MODAL and i != "bookmarkFolderModal"]
    assert 'id="bookmarkFolderModal"' in MODAL
    assert not [i for i in row if f'id="{i}"' not in HISTORY_PAGE]
    assert "innerHTML" not in SCRIPT and "name.textContent = label;" in SCRIPT
    # the icon ids the page draws for an item, toggled once it is filed
    assert "['', 'mobile-', 'modal-']" in SCRIPT and "'bookmark-' + part + id" in SCRIPT and "'unbookmark-' + part + id" in SCRIPT


def test_both_stylesheets_style_the_row_and_the_sheet():
    for sheet in ("main.scss", "main_dark.scss"):
        css = read("ruqqus", "assets", "style", sheet)
        for selector in (".folder-chip {", ".folder-chip.active,", ".folder-chip-new {", ".bookmark-folder-list {",
                         ".bookmark-folder-row {", ".bookmark-folder-row.active {"):
            assert selector in css, (sheet, selector)
