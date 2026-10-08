"""The deletion log crosses the helper, the route, the template, two stylesheets, the schema and the guild mod log.
These checks keep the promises: it is public but leaves out what was not public or was removed by someone else,
everything goes through the word filter and the anonymity rules, and the text is the only thing drawn as HTML."""
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def read(*parts):
    return ROOT.joinpath(*parts).read_text(encoding="utf-8")


ROUTE = read("ruqqus", "routes", "deletion_log.py")
PAGE = read("ruqqus", "templates", "deletion_log.html")
BOARDS = read("ruqqus", "routes", "boards.py")
MODLOG = read("ruqqus", "templates", "guild", "modlog.html")
RULES = read("ruqqus", "helpers", "deletion_log.py")


def func(name):
    return ROUTE.split(f"def {name}(")[1].split("\n\n\n")[0]


def test_the_route_is_public_and_registered():
    head = ROUTE.split("def deletion_log(")[0].rsplit("\n\n\n", 1)[1]
    assert '@app.route("/log/deleted", methods=["GET"])' in head and "@auth_desired" in head
    assert "auth_required" not in head
    assert "from .deletion_log import *" in read("ruqqus", "routes", "__init__.py")


def test_posts_leave_out_what_was_not_public_or_was_removed_by_someone_else():
    body = func("_post_entries")
    for needle in ('H.action == "delete"', "Submission.deleted_utc > 0", "Submission.purged_utc == 0",
                   "Submission.is_banned.is_(False)", "Submission.hidden_by_guild.is_(False)",
                   "Submission.post_public.is_(True)", "Board.is_private.is_(False)", "Board.is_banned.is_(False)",
                   "H.previous_title.isnot(None)", "ObliterationRecord.target_submission_id == H.target_submission_id"):
        assert needle in body, needle


def test_comments_leave_out_the_same_and_judge_the_post_they_were_on():
    body = func("_comment_entries")
    for needle in ('H.action == "delete"', "Comment.deleted_utc > 0", "Comment.purged_utc == 0", "Comment.is_banned.is_(False)",
                   "parent.is_banned.is_(False)", "parent.hidden_by_guild.is_(False)", "parent.post_public.is_(True)",
                   "parent_board.is_private.is_(False)", "parent_board.is_banned.is_(False)",
                   "H.previous_body_html.isnot(None)", "ObliterationRecord.target_comment_id == H.target_comment_id"):
        assert needle in body, needle
    # the aliased parent keeps filter_comments' own subselect on Submission from being correlated away
    assert "parent = aliased(Submission)" in body


def test_every_list_goes_through_the_word_filter_and_hides_anonymous_items_in_a_one_author_list():
    assert "query = filter_posts(query, v)" in func("_post_entries")
    assert "query = filter_comments(query, v)" in func("_comment_entries")
    assert "anonymity.hide_anonymous(Submission, v)" in func("_post_entries")
    assert "anonymity.hide_anonymous(Comment, v)" in func("_comment_entries")
    assert "filter_boards(" in func("_forwarded_to")
    assert "Board.is_private == False" in func("_forwarded_to") or "Board.is_private.is_(False)" in func("_forwarded_to")


def test_a_filter_on_an_unknown_banned_private_or_filtered_guild_or_user_is_one_404():
    guild = func("_guild_filter")
    assert "guild is None or guild.is_banned or guild.is_private or board_hidden(guild, v)" in guild and "abort(404)" in guild
    user = func("_user_filter")
    assert "user is None or user.is_deleted or user_hidden(user, v)" in user and "abort(404)" in user


def test_paging_is_bounded_and_ordered_newest_first():
    for name in ("_post_entries", "_comment_entries"):
        body = func(name)
        assert "order_by(H.id.desc())" in body and "offset(rules.PER_PAGE * (page - 1)).limit(rules.PER_PAGE + 1)" in body
    assert 'rules.parse_page(request.args.get("page"))' in ROUTE and 'rules.parse_tab(request.args.get("type"))' in ROUTE


def test_the_template_names_nobody_it_should_not_and_draws_only_the_stored_text_as_html():
    assert PAGE.count("| safe") == 1 and "{{ media_free(e.previous_body_html) | safe }}" in PAGE
    assert "anon_hidden(item, v)" in PAGE
    assert ".author" not in PAGE and "author_of(" not in PAGE        # the deleter comes from the row, never .author
    assert "[deleted user]" in PAGE


def test_the_log_is_a_paged_list_not_a_feed():
    # infinite_scroll.js engages on #posts / .posts and would hide Prev/Next and lift "post cards" that are not there
    classes = [c for value in re.findall(r'class="([^"]*)"', PAGE) for c in value.split()]
    assert 'id="posts"' not in PAGE and "posts" not in classes
    assert ">Prev</a>" in PAGE and ">Next</a>" in PAGE and 'ul class="pagination' in PAGE


def test_it_is_linked_from_the_footer_the_account_menu_and_the_guild_mod_log():
    assert 'href="/log/deleted"' in read("ruqqus", "templates", "footer.html")
    assert 'href="/log/deleted"' in read("ruqqus", "templates", "default.html")
    assert "/log/deleted?guild={{ b.name | urlencode }}" in MODLOG


def test_the_guild_mod_log_can_be_narrowed_and_keeps_its_filter_between_pages():
    body = BOARDS.split("def board_mod_log(")[1].split("def mod_log_item(")[0]
    assert 'show=log_rules.parse_show(request.args.get("show"))' in body
    assert "actions.filter(ModAction.kind.in_(kinds))" in body and "show=show" in body
    assert 'page=log_rules.parse_page(request.args.get("page"))' in body
    assert MODLOG.count("{% if show and show != 'all' %}&show={{ show }}{% endif %}") == 2


def test_every_kind_a_filter_names_is_a_real_mod_action():
    kinds = set(re.findall(r'^    "([a-z_]+)":\{', read("ruqqus", "classes", "mod_logs.py"), re.M))
    named = set(re.findall(r'"([a-z_]+)"', RULES.split("SHOW = {")[1].split("SHOW_LABELS")[0])) - {"all", "posts", "comments", "exiles"}
    assert named and named <= kinds, sorted(named - kinds)


def test_the_index_is_in_the_schema_and_the_migration():
    assert ("CREATE INDEX content_edit_history_deletes_idx ON public.content_edit_history USING btree (id DESC) "
            "WHERE ((action)::text = 'delete'::text);") in read("schema.sql")
    migration = read("scripts", "migrations", "2026-10-10_deletion_log.sql")
    assert ("CREATE INDEX IF NOT EXISTS content_edit_history_deletes_idx ON content_edit_history USING btree (id DESC) "
            "WHERE action = 'delete';") in migration


def test_both_stylesheets_style_the_chip_and_the_text():
    for sheet in ("main.scss", "main_dark.scss"):
        css = read("ruqqus", "assets", "style", sheet)
        for selector in (".deletion-chip {", ".deletion-text {", ".deletion-text img {"):
            assert selector in css, (sheet, selector)


def test_claude_md_describes_the_log_and_what_it_leaves_out():
    doc = read("CLAUDE.md")
    assert "## Deletion log (legacy app)" in doc
    for needle in ("public copy", "obliterate", "Anonymous", "post_public"):
        assert needle in doc, needle
