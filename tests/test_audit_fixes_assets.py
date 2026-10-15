"""Fixes from the audit pass of 2026-10-09 (BACKEND_AUDIT_FINDINGS.md). Each check pins one fix so the bug
can't come back unnoticed; the live behaviour was confirmed by crawling the Docker site before and after."""
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def read(*parts):
    return ROOT.joinpath("ruqqus", *parts).read_text(encoding="utf-8")


def func(source, name):
    """The body of a top-level function, up to the next top-level statement."""
    body = source.split(f"\ndef {name}(")[1]
    return re.split(r"\n(?=@|def |class )", body, maxsplit=1)[0]


# --- crashes on normal input ---------------------------------------------------------------------------------

def test_no_undefined_names_left_from_the_audit():
    assert "jsonfy(" not in read("routes", "paypal.py")
    body = func(read("routes", "front.py"), "notifications_posts")
    assert "for x in listing" not in body and "x.json for x in posts" in body
    assert "user.username" not in read("classes", "userblock.py")
    nuke = func(read("routes", "admin_api.py"), "admin_csam_nuke")
    assert "requests.get(url)" not in nuke and "g.db.add(ma)" in nuke and "return redirect(" in nuke
    assert read("routes", "admin.py").startswith("import calendar\n")


def test_mod_edit_rule_loads_one_rule_of_this_guild():
    body = func(read("routes", "boards.py"), "mod_edit_rule")
    assert "filter_by(id=r, board_id=board.id).first()" in body


def test_exile_note_without_a_target_is_text_not_none():
    note = read("classes", "mod_logs.py").split("def note(self):")[1].split("@note.setter")[0]
    exile = note.split('if self.kind=="exile_user":')[1].split('elif self.kind=="chatban_user"')[0]
    assert 'return self._note or ""' in exile


def test_password_reset_checks_the_account_before_using_it():
    src = read("routes", "login.py")
    for name in ("get_reset", "post_reset"):
        body = func(src, name)
        assert body.index("if not user:") < body.index("user.login_nonce"), name


def test_sticky_404s_on_a_missing_post():
    body = func(read("routes", "admin_api.py"), "api_sticky_post")
    assert body.index("abort(404)") < body.index("post.stickied")


def test_promo_code_without_a_minimum_does_not_crash():
    src = read("classes", "paypal.py")
    assert "cents >= (self.flat_cents_min or 0)" in src
    text = src.split("def promo_text(self):")[1].split("class ")[0]
    assert text.count("text=") >= 4  # every branch sets the text before it is extended


def test_giphy_takes_its_path_and_a_missing_limit():
    src = read("routes", "giphy.py")
    assert "def giphy(path=None):" in src and 'int(request.args.get("limit", ""))' not in src
    assert "params=params" in src  # the search term is url-encoded by requests


def test_bad_query_values_are_4xx_not_500():
    assert 'coins.isascii() and coins.isdigit()' in func(read("routes", "paypal.py"), "shop_get_price")
    assert "abort(400)" in func(read("routes", "discord.py"), "discord_redirect")
    assert "abort(404)" in func(read("routes", "front.py"), "subcat")
    assert 'request.args.get("board", "")' in func(read("routes", "admin.py"), "admin_siege_count")


def test_a_forward_whose_original_is_gone_still_renders():
    src = read("classes", "submission.py")
    for needle in ("self.reposts.is_image", "self.reposts.meta_title", "self.reposts.meta_description",
                   "self.reposts.preview_image_url"):
        line = next(x for x in src.splitlines() if needle in x)
        assert "self.is_repost and self.reposts" in line, needle


def test_admin_pages_built_on_the_profile_template_drop_its_account_modal():
    for name in ("flagged_posts.html", "image_posts.html"):
        assert "{% block aboutAccountModal %}{% endblock %}" in read("templates", "admin", name), name


def test_inpage_routes_fall_back_to_html_for_error_pages():
    assert "result.get('inpage') or result['html']" in read("helpers", "wrappers.py")


def test_error_pages_render_before_the_db_session_exists():
    body = func(read("routes", "front.py"), "inject_sidebar_filter_data")
    assert "getattr(g, 'db', None) is not None" in body


def test_comment_embed_answers_removed_comments_without_their_text():
    body = func(read("routes", "comments.py"), "embed_comment_cid")
    assert "return {'html'" not in body  # no dict without @api
    removed = read("templates", "embeds", "comment_removed.html")
    assert "c.body" not in removed and "{<title>" not in removed
    assert removed.count("{% block") == removed.count("{% endblock %}")


# --- auth and permissions ------------------------------------------------------------------------------------

def test_safe_compare_is_constant_time_and_never_matches_a_missing_code():
    body = func(read("helpers", "security.py"), "safe_compare")
    assert "hmac.compare_digest" in body and "sleep" not in body and "x==y" not in body
    assert "if not x or not y:" in body


def test_2fa_retry_hash_uses_the_timestamp_it_sends():
    assert "+{time}+2fachallenge" not in read("routes", "login.py")


def test_comment_ban_needs_the_same_admin_level_as_post_ban():
    src = read("routes", "admin_api.py")
    for name in ("api_ban_comment", "api_unban_comment", "ban_post", "unban_post"):
        head = src.split(f"\ndef {name}(")[0].rsplit("\n\n\n", 1)[1]
        assert "@admin_level_required(3)" in head, name


def test_guildmaster_permissions_missing_from_the_model_deny_instead_of_raising():
    # perm_chat is commented out of the model, so a raw lookup raised KeyError for "chat"
    for name in ("boards.py", "comment.py", "submission.py"):
        assert '__dict__[f"perm_{perm}"]' not in read("classes", name), name


def test_comment_api_checks_the_comments_own_post():
    body = func(read("routes", "comments.py"), "post_pid_comment_cid")
    assert "get_post(base36encode(comment.parent_submission), v=v)" in body
    assert "v.admin_level < 3" not in body  # crashed for visitors


# --- privacy and visibility ----------------------------------------------------------------------------------

def test_every_way_of_reading_or_acting_on_a_post_by_id_asks_about_private_guilds():
    """Submission.in_private_guild_for is the post page's rule (rendered_page); these routes skipped it."""
    sites = {
        ("routes", "posts.py"): ("post_base36id_no_comments", "post_base36id_with_comments", "post_history",
                                 "forward_post", "repost_post", "save_post", "embed_post_pid"),
        ("routes", "comments.py"): ("post_pid_comment_cid", "comment_history", "save_comment", "repost_comment",
                                    "forward_comment", "embed_comment_cid"),
        ("routes", "votes.py"): ("api_vote_post", "api_vote_comment"),
        ("routes", "paypal.py"): ("gift_post_pid", "gift_comment_pid"),
    }
    for path, names in sites.items():
        src = read(*path)
        for name in names:
            assert "in_private_guild_for(" in func(src, name), name


def test_history_shows_no_text_an_admin_removed_or_purged():
    vis = read("helpers", "visibility.py")
    body = func(vis, "history_text_shown")
    assert "purged_utc" in body and "is_banned" in body and "admin_level >= 3" in body
    page = read("templates", "content_history.html")
    assert "{% if not show_text %}" in page
    for path, name in ((("routes", "posts.py"), "post_history"), (("routes", "comments.py"), "comment_history")):
        assert "show_text=history_text_shown(" in func(read(*path), name), name


def test_random_lands_only_on_what_anyone_may_open():
    src = read("routes", "front.py")
    post = func(src, "random_post")
    for needle in ("Board.is_private==False", "Submission.post_public==True", "Submission.audience==0", "abort(404)"):
        assert needle in post, needle
    comment = func(src, "random_comment")
    for needle in ("Comment.deleted_utc == 0", "Submission.post_public == True", "Board.is_private == False",
                   "abort(404)"):
        assert needle in comment, needle
    user = func(src, "random_user")
    assert "is_deleted=False" in user and "filter_users(x, v)" in user and "abort(404)" in user


def test_a_block_never_drops_an_anonymous_item_from_a_list():
    # dropping it would tell the viewer which anonymous items the blocked account wrote
    pat = re.compile(r"(?<!or_\()\b(Submission|Comment)\.author_id\.notin_\((blocking|blocked)\)")
    for path in (("routes", "boards.py"), ("routes", "curations.py"), ("routes", "front.py"), ("routes", "search.py"),
                 ("classes", "boards.py"), ("classes", "user.py")):
        for line in read(*path).splitlines():
            if line.lstrip().startswith("#"):
                continue
            assert not pat.search(line) or "is_anonymous" in line, (path, line.strip())


def test_profile_meta_counts_leave_anonymous_items_out():
    page = read("templates", "userpage.html")
    assert "u.post_count" not in page and "u.comment_count" not in page
    assert page.count("u.public_post_count }} Posts") == 2


def test_bots_cannot_comment():
    body = func(read("routes", "comments.py"), "api_comment")
    head = body.split('parent_fullname = request.form.get("parent_fullname")')[0]
    assert 'X-User-Type' in head and "Bots can't comment." in head
    assert "Bots may not vote or comment." in read("templates", "help", "rules.html")


def test_banned_guilds_leave_the_feeds():
    front = read("routes", "front.py")
    assert "posts = posts.filter(Board.is_banned == False)" in func(front, "frontlist")
    assert "Board.is_banned == False" in func(front, "comment_idlist")
    user = read("classes", "user.py").split("def for_you_idlist(")[1].split("\n    def ")[0]
    assert "Board.is_banned == False" in user
    assert "Board.is_banned == True" in func(read("routes", "curations.py"), "_viewable")
    assert "board_banned.html" in func(read("routes", "boards.py"), "board_comments")


def test_removed_and_deleted_comments_leave_general_lists_for_admins_too():
    # owner's rule (2026-10-09): they belong on the logs
    front = func(read("routes", "front.py"), "comment_idlist")
    guild = read("classes", "boards.py").split("def comment_idlist(")[1].split("\n    def ")[0]
    for body in (front, guild):
        assert "admin_level >= 3:\n        comments = comments.filter_by(is_banned=False)" not in body
        assert "comments = comments.filter_by(is_banned=False).filter(Comment.deleted_utc == 0)" in body


def test_small_data_source_fixes():
    get = read("helpers", "get.py")
    assert "x._is_subscribed = items[6] or 0" in get
    assert get.count("invite_rescinded=False") >= 4  # get_post, get_posts, get_comment, get_comments
    assert 'posts = posts.filter_by(is_pinned=False)' in read("classes", "boards.py")
    search = read("routes", "search.py")
    assert "select(Submission).filter_by(user_id=v.id, is_active=True)" not in search
    # url: is LIKE text with its own wildcards escaped (the search operators moved to helpers/search_filters.py)
    assert 'SubmissionAux.url.ilike(pattern, escape="\\\\")' in read("helpers", "search_filters.py")
    assert "func.lower(Board.name) != PROFILE_BOARD_NAME" in search
    assert "func.lower(Board.name) != PROFILE_BOARD_NAME" in func(read("routes", "front.py"), "guild_ids")


def test_a_private_guilds_mod_log_is_for_those_who_can_view_it():
    src = read("routes", "boards.py")
    for name in ("board_mod_log", "mod_log_item"):
        assert "board.can_view(v)" in func(src, name), name


def test_profile_json_has_the_profile_pages_gates():
    users = read("routes", "users.py")
    gates = func(users, "profile_json_refusal")
    for needle in ("u.reserved", "u.is_private", "u.is_blocking", "u.is_blocked"):
        assert needle in gates, needle
    assert "profile_json_refusal(user, v)" in func(users, "u_username_info")
    api = read("routes", "api", "json_api.py")
    assert "profile_json_refusal(user, v)" in api and "board_hidden(guild, v)" in api


def test_mentions_tell_only_people_who_may_see_the_post():
    body = func(read("routes", "comments.py"), "api_comment")
    assert "parent_post.in_private_guild_for(user)" in body and "circle_guard.eligible_ids(" in body


def test_error_pages_answer_json_with_their_status_on_the_api():
    errors = read("routes", "errors.py")
    assert "@api()" not in errors and errors.count("@api(error_handler=True)") == 13
    assert 'request.path.startswith(("/api/v1/", "/api/v2/"))' in func(errors, "error_401")


def test_the_api_comment_url_reaches_its_handler():
    assert "def comment_cid_api_redirect" not in read("routes", "comments.py")


# --- front end -----------------------------------------------------------------------------------------------

JS = read("assets", "js", "all_js.js")


def js_func(name):
    return JS.split(f"function {name}(")[1].split("\nfunction ")[0]


def test_the_delete_post_buttons_hold_one_handler():
    # a listener added per opening stayed after Cancel, so confirming deleted every post opened before too
    body = js_func("delete_postModal")
    assert 'addEventListener("click", delete_post)' not in JS
    assert 'getElementById("deletePostButton").onclick = delete_post;' in body
    assert 'getElementById("deletePostButton-mobile").onclick = delete_post;' in body


def test_guild_switches_show_a_refusal_and_go_back():
    body = js_func("mod_post")
    assert "var button=id ? document.getElementById(id) : null;" in body and "if (!button) return;" in body
    assert "item.checked = !item.checked;" in body
    assert "JSON.parse(xhr.response)[\"error\"] || message" in body  # inside a try


def test_requests_read_error_pages_and_login_redirects_without_throwing():
    for name in ("xhrData", "xhrLoggedOut", "xhrError", "xhrOk"):
        assert f"function {name}(" in JS, name
    for name in ("post_toast", "post_toast_with_reason"):
        body = js_func(name)
        assert "JSON.parse" not in body and "xhrError(xhr, data)" in body, name
    for name in ("post", "post_with_reason", "post_response"):
        assert "xhrOk(xhr)" in js_func(name), name
    assert 'else if (xhr && !xhrOk(xhr)) {' in JS.split("flagAnswer = function")[1].split("};")[0]
    assert "alert(errortext)" not in JS


def test_the_guild_mod_queue_has_prev_and_next():
    page = read("templates", "guild", "reported_posts.html")
    assert "{% block pagenav %}" not in page
    content = page.split("{% block content %}")[1]
    assert "next_exists" in content and ">Next</a>" in content


def test_guild_buttons_send_the_name_the_route_needs():
    assert "approve_from_guild('{{ b.name }}')" in read("templates", "guild", "contributors.html")
    assert "exile_from_guild('{{ b.name }}')" in read("templates", "guild", "chatbans.html")


def test_admin_pages_only_point_at_routes_that_exist():
    assert "/admin/ncmec" not in read("templates", "admin", "ncmec.html")
    for name in ("apps.html", "app_data.html"):
        assert "/admin/app/reject/" not in read("templates", "admin", name), name


def test_admin_answers_keep_their_status():
    body = read("helpers", "wrappers.py").split("def admin_level_required(")[1].split("\ndef ")[0]
    assert "len(response) in (2, 3) and isinstance(response[1], int)" in body


# --- docs and config -----------------------------------------------------------------------------------------

def test_delete_dialogs_say_the_text_stays_in_the_public_log():
    # owner's rule (2026-10-09): author deletes stay public, with their text, in the Deletion log
    for name in ("delete_post_modal.html", "delete_comment_modal.html"):
        page = read("templates", name)
        assert "removed everywhere" not in page and 'href="/log/deleted"' in page, name
    privacy = read("templates", "help", "privacy.html")
    assert "### Deleting and editing" in privacy and "(/log/deleted)" in privacy


def test_local_secrets_stay_out_of_the_image():
    ignore = (ROOT / ".dockerignore").read_text(encoding="utf-8").splitlines()
    for entry in (".env", ".venv", "web/", "synapse/ruqqus-appservice.yaml"):
        assert entry in ignore, entry
    assert "!.env.example" in ignore


# === pass of 2026-10-10 ======================================================================================

# --- A: visibility and permissions ---------------------------------------------------------------------------

def test_blocks_count_both_ways_in_every_feed_and_spare_anonymous_items():
    helper = read("helpers", "blocks.py")
    assert "UserBlock.user_id == v.id" in helper and "UserBlock.target_id == v.id" in helper
    assert "model.is_anonymous.is_(True)" in helper and "coauthored_with(model, ids)" in helper
    for path in (("routes", "front.py"), ("routes", "search.py"), ("routes", "curations.py"), ("routes", "boards.py"),
                 ("classes", "boards.py"), ("classes", "user.py"), ("helpers", "trending_store.py")):
        src = read(*path)
        assert "hide_blocked(" in src, path
        assert "#Submission.author_id.notin_(blocked)" not in src and "#    Submission.author_id.notin_(blocked)" not in src, path


def test_mutes_also_hide_posts_coauthored_with_a_muted_account():
    muting = read("helpers", "muting.py")
    # the rule lives in exclude_authors (a curation's leave-out list uses it too); a member's mutes feed it their ids
    assert "coauthored_with(model, ids)" in muting and "exclude_authors(query, muted_ids(v), model)" in muting


def test_notifications_leave_out_blocked_accounts_and_counts_agree():
    user = read("classes", "user.py")
    listing = user.split("def notification_commentlisting(")[1].split("\n    def ")[0]
    assert "hide_blocked(hide_muted(notifications, self, Comment), self, Comment)" in listing
    count = user.split("def mentions_count(")[1].split("\n    @property")[0]
    assert "hide_blocked(filter_comments(hide_muted(" in count
    assert "c._is_blocked = False" not in func(read("routes", "front.py"), "notifications")


def test_pins_and_stickies_follow_the_viewers_filters():
    assert "hide_blocked(hide_muted(filter_posts(stickies, v), v, Submission), v, Submission)" in read("routes", "boards.py")
    front = read("routes", "front.py")
    assert "filter_by(stickied=True).first()" not in front and front.count("sticky = viewer_sticky(v)") == 4


def test_json_hides_what_the_page_hides():
    sub = read("classes", "submission.py")
    assert "elif self.hidden_by_guild and not self._guild_hide_lifted_for(anonymity.current_viewer()):" in sub
    assert 'data["comment_count"] = self.comment_count' in sub
    com = read("classes", "comment.py")
    assert "elif self._text_hidden_for(anonymity.current_viewer()):" in com
    assert "base36encode(self.parent_comment_id)," not in com   # was a one-element tuple in the API
    assert "self.bio_for(anonymity.current_viewer())" in read("classes", "user.py")
    assert "self.description_for(anonymity.current_viewer())" in read("classes", "boards.py")


def test_circle_guilds_are_not_listed_to_outsiders():
    assert "filter_by(is_banned=False, is_circle=False)" in func(read("routes", "front.py"), "guild_ids")
    assert "or_(Board.is_circle == False, Board.id.in_(member_of))" in read("routes", "search.py")
    assert "guild.is_circle and not guild.can_view(v)" in read("routes", "api", "json_api.py")


def test_follow_respects_blocks_and_account_state_and_a_block_ends_follows():
    users = read("routes", "users.py")
    follow = func(users, "follow_user")
    assert "v.any_block_exists(target)" in follow and "target.is_nofollow" in follow and "target.is_private" in follow
    assert "You can't follow this account." in follow
    assert "@validate_formkey\ndef follow_user(" in users and "@validate_formkey\ndef unfollow_user(" in users
    block = func(read("routes", "settings.py"), "settings_block_user")
    assert "g.db.query(Follow).filter(or_(" in block and 'f"You can\'t block @{user.username}."' in block
    page = read("templates", "userpage.html")
    assert page.count("not u.is_private and not u.is_nofollow") >= 2   # desktop and phone


def test_token_requests_skip_the_form_key():
    body = read("helpers", "wrappers.py").split("def validate_formkey(")[1].split("\ndef ")[0]
    assert 'not getattr(v, "client", None)' in body


def test_an_exile_never_names_the_author_of_an_anonymous_item():
    body = func(read("routes", "boards.py"), "mod_ban_bid_user")
    assert "if item.is_anonymous:" in body and "item = None" in body
    assert "elif item.author_id != user.id:" in body


def test_story_gates_match_the_profile_and_highlight_names_are_filtered():
    src = read("routes", "stories.py")
    closed = func(src, "_profile_closed")
    assert "owner.is_suspended" in closed and "owner.is_private" in closed and "user_hidden(owner, v)" in closed
    assert "(owner.is_banned and not owner.unban_utc)" not in src
    assert src.count("_title_for(") >= 3


def test_rss_and_guild_filters():
    feeds = read("routes", "feeds.py")
    assert "not p.hidden_by_guild" in feeds and "[deleted account]" in feeds and "if not username and key" not in feeds
    for path in (("routes", "search.py"), ("routes", "boards.py")):
        assert "BoardBlock.board_id).filter_by(user_id=v.id)" in read(*path), path
    assert "BoardBlock" in func(read("routes", "front.py"), "comment_idlist")


# --- B: crashes, wrong results, consistency ------------------------------------------------------------------

def test_page_numbers_are_whole_numbers_from_one_everywhere():
    helper = func(read("helpers", "wrappers.py"), "page_number")
    assert "max(int(raw), 1) if raw.isascii() and raw.isdigit() else 1" in helper
    pat = re.compile(r'''int\(request\.args\.get\(\s*["']page["']''')
    for name in ("admin.py", "boards.py", "front.py", "history.py", "users.py", "oauth.py", "curations.py",
                 "search.py", "feeds.py"):
        live = "\n".join(x for x in read("routes", name).splitlines() if not x.lstrip().startswith("#"))
        assert not pat.search(live), name


def test_admin_queries_mean_what_they_say():
    api = read("routes", "admin_api.py")
    assert "User.reserved is not None" not in api and "User.reserved.isnot(None)" in api
    assert 'domain.replace("_",r"\\_")' not in api
    admin = read("routes", "admin.py")
    assert "Submission.flags.any()" in admin and "Comment.flags.any()" in admin
    assert "contains_eager(Submission.flags)" not in admin


def test_guild_moderation_routes():
    src = read("routes", "boards.py")
    assert "isn't a guildmaster of" in func(src, "board_mod_perms_change")
    remove = func(src, "mod_remove_username")
    assert "use Step down" in remove and "board.mods_count == 0 and not board.is_circle" in remove
    banner = func(src, "mod_settings_toggle_banner")
    assert "g.db.add(board)" in banner and 'kind="update_appearance"' in banner
    rule = func(src, "mod_add_rule")
    assert "Rules(board_id=board.id" in rule and "Add one rule at a time." in rule


def test_content_rules():
    posts = read("routes", "posts.py")
    assert "# return {\"html\": lambda: (render_template(\"submit.html\"," not in posts
    assert "Links to that site aren't allowed." in func(posts, "submit_post")
    comments = read("routes", "comments.py")
    assert "if c.deleted_utc > 0:\n        abort(404)" in func(comments, "delete_comment")
    assert "[0:10000]" not in comments and comments.count("len(body") >= 2 and "COMMENT_MAX = 10000" in comments
    for name in ("submission.html", "comments.html"):
        page = read("templates", name)
        assert page.count('class="comment-box') == page.count('maxlength="10000"'), name


def test_votes_refresh_every_score_and_is_valid_counts_temporary_bans():
    votes = read("routes", "votes.py")
    assert "    post.score_hot = post.rank_hot" in votes and "    comment.score_disputed = comment.rank_fiery" in votes
    assert "return not (self.is_suspended or self.is_deleted)" in read("classes", "user.py")


def test_one_admin_level_for_a_banned_guilds_pages():
    src = read("routes", "boards.py")
    for name in ("board_mod_log", "mod_log_item", "board_about_mods", "board_about_css"):
        body = func(src, name)
        assert "admin_level>=3" in body.replace(" ", ""), name


def test_state_changing_routes_check_the_form_key():
    want = {("routes", "users.py"): ("api_agree_tos", "toggle_user_bell", "follow_user", "unfollow_user"),
            ("routes", "boards.py"): ("mod_distinguish_post", "mod_distinguish_comment", "board_edit_css",
                                      "subscribe_board", "unsubscribe_board", "toggle_guild_bell"),
            ("routes", "admin_api.py"): ("api_sticky_post", "api_ban_comment", "api_unban_comment", "purge_comment",
                                         "admin_distinguish_comment"),
            ("routes", "oauth.py"): ("request_api_keys", "reroll_oauth_tokens")}
    for path, names in want.items():
        src = read(*path)
        for name in names:
            assert f"@validate_formkey\ndef {name}(" in src, name


def test_search_strips_every_key_and_guild_search_answers_json():
    from ruqqus.helpers import search_query as sq
    # every operator is taken out of the free text, a repeated one included; an unknown key stays text
    query = sq.parse("cats from:@a from:@b https://example.com")
    assert [(p.kind, p.value) for p in query.parts] == [("word", "cats"), ("from", "a"), ("from", "b"),
                                                       ("word", "https://example.com")]
    src = read("routes", "search.py")
    assert '@app.get("/api/v2/guilds/<name>/search")' in src and '"api": lambda: jsonify' in func(src, "search_guild")


def test_database_and_jobs():
    schema = (ROOT / "schema.sql").read_text(encoding="utf-8")
    assert "CREATE TABLE public.rules (" in schema and "login_events_ip_idx" in schema
    migration = (ROOT / "scripts" / "migrations" / "2026-10-10_rules_and_login_ip.sql").read_text(encoding="utf-8")
    assert "CREATE TABLE IF NOT EXISTS rules" in migration and "CREATE INDEX IF NOT EXISTS login_events_ip_idx" in migration
    regions = (ROOT / "scripts" / "migrations" / "2026-10-06_regions_reshuffle.sql").read_text(encoding="utf-8")
    assert "IF NOT EXISTS (SELECT 1 FROM regions WHERE code = 'equatorial_nile') THEN" in regions
    conf = (ROOT / "supervisord.conf").read_text(encoding="utf-8")
    job = conf.split("[program:ruqqusrecomputes]")[1].split("[program:")[0]
    assert "autostart=true" in job and 'environment=PYTHONPATH="/opt/ruqqus/service"' in job
    script = (ROOT / "scripts" / "recomputes.py").read_text(encoding="utf-8")
    assert "time.sleep(CYCLE_PAUSE_SECONDS)" in script and "(cycle - 2) % 10 == 0" in script
    assert "actions.delete()" not in script


# --- C: templates and front end ------------------------------------------------------------------------------

def _templates():
    return {str(p.relative_to(ROOT / "ruqqus" / "templates")): p.read_text(encoding="utf-8")
            for p in (ROOT / "ruqqus" / "templates").rglob("*.html")}


def test_no_template_fills_a_block_its_parents_never_declare():
    templates = _templates()
    ext = re.compile(r'{%\s*extends\s+"([^"]+)"\s*%}')
    tok = re.compile(r'{%-?\s*(block\s+(\w+)|endblock(?:\s+\w+)?)\s*-?%}')

    def declared(name, seen=()):
        if name not in templates or name in seen:
            return set()
        src = templates[name]
        out = {m.group(2) for m in tok.finditer(src) if m.group(2)}
        m = ext.search(src)
        return out | (declared(m.group(1).lstrip("/"), seen + (name,)) if m else set())

    for name, src in templates.items():
        m = ext.search(src)
        if not m:
            continue
        avail = declared(m.group(1).lstrip("/"))
        depth, current, start = 0, None, 0
        for t in tok.finditer(src):
            if t.group(2):
                if depth == 0:
                    current, start = t.group(2), t.end()
                depth += 1
            else:
                depth -= 1
                if depth == 0 and current not in avail and src[start:t.start()].strip():
                    raise AssertionError(f"{name}: block {current} is never drawn")


def test_every_local_script_and_stylesheet_exists():
    for name, src in _templates().items():
        for path in re.findall(r"asset_url\('([^']+)'\)", src):
            file = ROOT / "ruqqus" / "assets" / path
            assert file.exists() or file.with_suffix(".scss").exists() or path == "js/chat_bundle.js", (name, path)


def test_menus_agree_with_the_server():
    comments = read("templates", "comments.html")
    assert "and is_allowed_to_comment %}" not in comments.split("report_commentModal")[0].rsplit("{% if", 1)[1]
    assert "'[is_deleted]'" not in comments
    block = comments.split("Block user</a>")[0].rsplit("{% if not anon_hidden(c, v) %}", 1)[1]
    assert "{% endif %}" not in block   # Block user sits inside the not-anonymous condition, with Mute
    listing = read("templates", "submission_listing.html")
    assert "and not anon_hidden(p, v) %}\n                <a class=\"dropdown-item\" href=\"javascript:void(0)\" data-toggle=\"modal\" data-target=\"#reportPostModal\"" not in listing
    # the share menus (Send in chat, Forward, Repost) are drawn by partials/post_actions.html for both pages
    actions = read("templates", "partials", "post_actions.html")
    # drawn only for whoever may share the post (helpers/sharing.py); never forwarded or reposted when made for a Circle
    menu = actions.split("{% if may_share(p, v) %}")[1].split("{% else %}")[0]
    assert '{% if v %}<a class="dropdown-item share-chat-item"' in menu
    assert actions.count("{% if v and not p.audience %}") == 1 and menu.index("share-chat-item") < menu.index("{% if v and not p.audience %}")
    for name in ("submission_listing.html", "submission.html"):
        page = read("templates", name)
        assert '{% if v %}<a class="dropdown-item share-chat-item"' not in page, name
        assert page.count("post_actions.counted_actions(") == 2, name
    board = read("templates", "board.html")
    assert board.count('b.has_mod(v, "config")') == 2


def test_moderation_buttons_have_no_duplicate_ids():
    for name in ("submission_listing.html", "submission.html", "comments.html"):
        page = read("templates", name)
        assert 'id="moderate-post-' not in page and 'id="moderate-{{ c.base36id }}"' not in page, name
    js = read("assets", "js", "all_js.js")
    assert "function showModeration(post_id, removed)" in js and "function showCommentModeration(cid, removed)" in js


def test_front_end_glitches():
    js = read("assets", "js", "all_js.js")
    hide = js.split("function onHideButtonListingClick(")[1].split("\n}\n")[0]
    assert "bindPostCards(this)" in hide and "JSON.parse(" not in hide
    toggle = js.split("function toggleSub(){")[1].split("\n}\n")[0]
    assert "if (el) el.classList.toggle('d-none');" in toggle
    assert '{% include "emoji_modal.html" %}' not in read("templates", "comments.html")
    assert read("templates", "default.html").count('{% include "emoji_modal.html" %}') == 1
    for name in ("default.html", "header.html"):
        assert 'fa-shapes fa-fw text-left mr-3" id="dark-switch"' not in read("templates", name), name
    assert "b=board," in func(read("routes", "boards.py"), "mod_hide_bid_pid")


def test_no_legacy_guild_chat_ui_and_no_orphans():
    for name in ("guild/settings.html", "guild/mods.html", "guild/guild_settings.html"):
        page = read("templates", *name.split("/"))
        assert "public_chat" not in page and "perm_chat" not in page and "chatbans" not in page, name
    assert not (ROOT / "ruqqus" / "templates" / "follows.html").exists()
    assert not (ROOT / "ruqqus" / "templates" / "my_info.html").exists()
    assert "def rendered_comment(" not in read("classes", "comment.py")
    assert "admin/user_data.html" not in read("routes", "admin.py")
    errors = func(read("routes", "errors.py"), "error_all_preview")
    assert "TemplateNotFound" in errors
    assert '@admin_level_required(3)\ndef error_all_preview(' in read("routes", "errors.py")


def test_help_and_tooltips_say_what_the_code_does():
    siege = read("templates", "help", "siege.html")
    assert "30 day" not in siege and "at least 7 days old" in siege
    assert "last 7 days" not in read("templates", "userpage.html")


# --- D: docs, tests, CI, dead code ---------------------------------------------------------------------------

def test_docs_say_how_the_stack_really_starts():
    readme = (ROOT / "README.md").read_text(encoding="utf-8")
    assert "ruqqie" not in readme and "python3 scripts/gen_dev_env.py" in readme and "badwords table" not in readme
    compose = (ROOT / "docker-compose.yml").read_text(encoding="utf-8")
    assert compose.startswith("# LOCAL DEVELOPMENT ONLY.") and "SESSION_COOKIE_SECURE" not in compose
    claude = (ROOT / "CLAUDE.md").read_text(encoding="utf-8")
    assert "two `# noqa: F821`" not in claude and "There are no lint suppressions" in claude
    assert "**Taking a vote back (`x=0`) is never checked by this rule**" in claude
    assert "## Background jobs (legacy app)" in claude
    assert "routes/circles.py draws a gate" not in read("helpers", "get.py") + read("classes", "custom_errors.py")


def test_no_lint_suppressions_and_ci_matches_production():
    for path in (ROOT / "ruqqus").rglob("*.py"):
        assert "# noqa" not in path.read_text(encoding="utf-8"), path.name
    ci = (ROOT / ".github" / "workflows" / "ci.yml").read_text(encoding="utf-8")
    assert 'python-version: "3.11"' in ci
    assert "python: 3.11" in (ROOT / "buildspec.yml").read_text(encoding="utf-8")
    dev = (ROOT / "requirements-dev.txt").read_text(encoding="utf-8")
    assert "py3langid==0.4.0" in dev and "libsass==0.23.0" in dev


def test_one_list_for_numbers_shown_in_two_places():
    # All, For You, trending and a curation's whole-site pool no longer leave categories out (they leave sensitive
    # content out instead: helpers/sensitive.py), so the list of kept-off categories is gone, everywhere
    assert "FILTERED_SUBCATS" not in read("helpers", "visibility.py")
    for path in (("classes", "user.py"), ("routes", "front.py"), ("helpers", "trending_store.py"), ("helpers", "curation_feed.py")):
        src = read(*path)
        assert "44, 108" not in src and "FILTERED_SUBCATS" not in src and "HIDDEN_SUBCATS" not in src, path
    options = read("templates", "partials", "post_options.html")
    assert 'maxlength="40"' not in options and "2 to 4 options" not in options


def test_terminology_guard_covers_more():
    guard = (ROOT / "tests" / "test_terminology.py").read_text(encoding="utf-8")
    assert 're.compile(r"\\bpromot(e|ed|es|ing)\\b", re.I)' in guard and "CROSSPOST" in guard
    assert '"assets/js/*.js"' in guard
    assert "def links_to_post(self):" in read("classes", "submission.py")


def test_dead_code_that_was_wrong_is_gone():
    sub = read("classes", "submission.py")
    assert sub.count("def is_archived(self):") == 1
    for name in ("submission.py", "comment.py"):
        body = read("classes", name).split("def is_guildmaster(self, perm=None):")[1].split("\n    @property")[0]
        assert "return output" not in body, name
    com = read("classes", "comment.py")
    assert 'board = association_proxy("post", "board")' not in com
    assert "if not self.is_banned and not self.is_banned:" not in com
    badges = read("classes", "badges.py")
    assert "    property\n" not in badges and "        return {\n            \"name\": self.name," in badges
    assert read("routes", "discord.py").count('@app.route("/guilded"') == 1
    assert "no_ban" not in read("helpers", "wrappers.py")
    get = read("helpers", "get.py")
    assert "blocking = v.blocking.subquery()" not in func(get, "get_comment")   # built and never used there
    assert get.count("lazyload(Comment.comment_aux) if no_text else joinedload(Comment.comment_aux)") == 2
    assert get.count("lazyload(Submission.submission_aux) if no_text else joinedload(Submission.submission_aux)") == 2
    mix = read("classes", "mix_ins.py")
    assert '"%d %b %Y"' not in mix and "app['FORCE_HTTPS']" not in mix


def test_every_template_compiles():
    # a template error (a block defined twice, an unclosed tag) is only seen when the page is opened
    import jinja2
    class AnyName(dict):    # the app's own filters and tests are not loaded here: only the syntax is checked
        def __contains__(self, key):
            return True

        def __missing__(self, key):
            return lambda *a, **k: a[0] if a else None

        def get(self, key, default=None):
            return self[key]

    env = jinja2.Environment(loader=jinja2.FileSystemLoader(str(ROOT / "ruqqus" / "templates")),
                             extensions=["jinja2.ext.do", "jinja2.ext.loopcontrols"])
    env.filters = AnyName(env.filters)
    env.tests = AnyName(env.tests)
    broken = []
    for path in (ROOT / "ruqqus" / "templates").rglob("*.html"):
        name = path.relative_to(ROOT / "ruqqus" / "templates").as_posix()
        try:
            env.parse(path.read_text(encoding="utf-8"))
            env.get_template(name)
        except jinja2.TemplateSyntaxError as e:
            broken.append(f"{name}:{e.lineno}: {e.message}")
    assert not broken, "\n".join(broken)
