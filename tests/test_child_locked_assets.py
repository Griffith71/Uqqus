"""The child lock, as source (the behaviour is on the real app in tests/pg/test_age_lock.py): every route that a child-locked account
may not use carries `@not_child_locked(<feature>)`, no other route does, the wrapper sits under the sign-in and above the form key, the
routes that start something with another account refuse a locked target, and nothing that decides what a reader sees reads the stored
filter level where it should ask for the effective one."""
import ast
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
ROUTES = ROOT / "ruqqus" / "routes"

# module: {route function: feature}. Chat is the site's chat in all its forms; messaging is the chat session itself (its token and keys): it is chat, and a call
# Space runs on it, so a call is a chat feature (helpers/age_declaration.FEATURE_CONTROL; routes/spaces.py asks "chat" for a call); money is coins, tips, Premium and paid circles; spaces
# are Spaces and their live updates; guilds is starting one. Three routes are open on purpose and ask the lock only for the side that spends: turning Premium's
# auto-renew OFF, leaving the creator fund, and making a free custom circle (a paid circle asks). (Anonymous and Sensitive posting are refused in the post and comment routes
# themselves: they are inputs of a route that stays open.)
GATED = {
    "chat.py": "chat_page chat_token chat_store_keys_route chat_key_get chat_key_set chat_code_saved chat_recovery_key_legacy chat_check_password chat_ready chat_start "
               "chat_conversations chat_share_targets chat_post_preview chat_comment_preview chat_accept chat_decline chat_mark_read chat_search",
    "chat_apps.py": "settings_chat_apps chat_devices chat_device_sign_out",
    "chat_key_reset_pages.py": "chat_key_reset_grant chat_signing_keys",
    "chat_settings_pages.py": "settings_chat",
    "chat_reports_pages.py": "chat_report",
    "chat_extras_pages.py": "chat_pin chat_disappear",
    "push_pages.py": "push_subscribe chat_group_push",
    "chat_groups.py": "chat_group_create chat_group_add chat_group_accept chat_group_info chat_people",
    "calls.py": "call_window call_join",
    "broadcasts.py": "broadcast_create broadcast_join broadcast_message broadcast_info broadcasts_following broadcasts_popular",
    "guild_channels.py": "guild_channel_join guild_channel_message guild_channels_list guild_channel_mark_read guild_channel_senders guild_channel_report "
                         "guild_channel_pins guild_channel_mute_for_me guild_channel_accept_rules guild_channel_search",
    "spaces.py": "space_create space_start space_join space_invite space_cohost space_message_add space_update_add",
    "paypal.py": "shop_buy_coins gift_post_pid gift_comment_pid premium_redeem",
    "circles.py": "circle_price board_circle_price circle_subscribe circle_guild_subscribe",
    "boards.py": "create_board_post",
}
FEATURES = {"chat", "spaces", "money", "guilds", "messaging"}
LISTED = {(module, name) for module, names in GATED.items() for name in names.split()}


def routes():
    found = []
    for path in sorted(ROUTES.glob("*.py")):
        source = path.read_text(encoding="utf-8")
        lines = source.splitlines()
        for node in ast.walk(ast.parse(source)):
            if isinstance(node, ast.FunctionDef) and node.decorator_list:
                decorators = [lines[d.lineno - 1].strip() for d in node.decorator_list]
                if any(d.startswith("@app.") for d in decorators):
                    found.append((path.name, node.name, decorators, "\n".join(lines[node.lineno:node.end_lineno])))
    return found


ALL = routes()


def stack_of(module, name):
    for m, n, decorators, _body in ALL:
        if (m, n) == (module, name):
            return [d.split("#")[0].strip() for d in decorators]
    raise AssertionError((module, name, "no such route"))


def test_every_listed_route_carries_the_wrapper_under_its_sign_in_and_above_its_form_key():
    for module, name in sorted(LISTED):
        stack = stack_of(module, name)
        marks = [d for d in stack if d.startswith("@not_child_locked(")]
        assert len(marks) == 1, (module, name)
        assert re.fullmatch(r'@not_child_locked\("(\w+)"\)', marks[0]) and marks[0].split('"')[1] in FEATURES, (module, name, marks)
        at = stack.index(marks[0])
        assert any(d.startswith(w) for d in stack[:at] for w in ("@auth_required", "@is_not_banned", "@no_sanctions")), (module, name, "under the sign-in")
        if "@validate_formkey" in stack:
            assert at < stack.index("@validate_formkey"), (module, name, "above the form key")


def test_no_other_route_carries_it():
    carried = {(m, n) for m, n, decorators, _b in ALL if any(d.startswith("@not_child_locked(") for d in decorators)}
    assert carried == LISTED


def test_the_wrapper_asks_the_one_rule_and_answers_a_page_or_a_403():
    wrappers = (ROOT / "ruqqus" / "helpers" / "wrappers.py").read_text(encoding="utf-8")
    body = wrappers.split("def not_child_locked(feature):", 1)[1].split("\n\nQR_LIMITED_REFUSAL", 1)[0]
    assert "age_declaration.feature_refusal(v, feature)" in body
    assert '"age_locked": True' in body and "403" in body and 'link="/settings/content"' in body


def test_anonymous_and_sensitive_are_refused_in_the_routes_that_take_them():
    posts = (ROUTES / "posts.py").read_text(encoding="utf-8")
    submit = posts.split("def submit_post(v):", 1)[1].split("\n@app.", 1)[0]
    assert 'age_declaration.post_refusal(v, flag(request.form, "sensitive"), flag(request.form, "anonymous"))' in submit
    assert submit.index("age_declaration.post_refusal(") < submit.index("new_post = Submission(")
    assert "age_declaration.post_refusal(v, True, False)" in posts.split("def apply_post_edit(", 1)[1].split("\ndef ", 1)[0]
    assert "age_declaration.post_refusal(v, True, False)" in posts.split("def toggle_post_sensitive(", 1)[1].split("\n@app.", 1)[0]
    comments = (ROUTES / "comments.py").read_text(encoding="utf-8")
    assert 'age_declaration.post_refusal(v, False, flag(request.form, "anonymous"))' in comments
    settings = (ROUTES / "settings.py").read_text(encoding="utf-8")
    account = settings.split("def settings_sensitive_account(v):", 1)[1].split("\n@app.", 1)[0]
    assert "age_declaration.locked_sensitive(v, wanted)" in account


def test_starting_something_with_a_locked_account_is_refused_without_saying_why():
    assert 'age_declaration.target_refusal(target, "chat")' in (ROUTES / "chat.py").read_text(encoding="utf-8")
    paypal = (ROUTES / "paypal.py").read_text(encoding="utf-8")
    assert paypal.count('age_declaration.target_refusal(u, "money")') == 2
    groups = (ROOT / "ruqqus" / "helpers" / "chat_groups.py").read_text(encoding="utf-8")
    assert 'age_declaration.target_refusal(target, "chat")' in groups.split("def add_refusal(", 1)[1].split("\ndef ", 1)[0]


def test_a_locked_account_is_not_suggested_and_is_found_only_by_its_exact_name():
    suggestions = (ROOT / "ruqqus" / "helpers" / "suggestions.py").read_text(encoding="utf-8")
    assert "age_declaration.is_locked(u)" in suggestions.split("def user_eligible(", 1)[1].split("\ndef ", 1)[0]
    search = (ROUTES / "search.py").read_text(encoding="utf-8")
    assert "User.age_lock.is_(None), User.username.ilike(term, escape=LIKE_ESCAPE)" in search
    assert "can_make_guild" in (ROOT / "ruqqus" / "classes" / "user.py").read_text(encoding="utf-8")
    assert 'and not age_declaration.feature_refusal(self, "guilds")' in (ROOT / "ruqqus" / "classes" / "user.py").read_text(encoding="utf-8")


def test_ads_follow_the_stored_lock_not_the_country():
    ads = (ROOT / "ruqqus" / "helpers" / "ads.py").read_text(encoding="utf-8")
    allows = ads.split("def viewer_allows(", 1)[1].split("\ndef ", 1)[0]
    assert "stored_level(viewer) < CHILD and not age_declaration.is_locked(viewer)" in allows
    assert "viewer_level" not in ads, "a visitor in a strict country keeps the ads they had"


def test_a_page_that_shows_a_level_shows_the_effective_one():
    """Settings, the chat client's boot and the filtered words page ask for the level the reader really browses at."""
    assert "effective_level(v)" in (ROOT / "ruqqus" / "templates" / "settings_filters.html").read_text(encoding="utf-8")
    chat = (ROOT / "ruqqus" / "templates" / "chat.html").read_text(encoding="utf-8")
    assert "v.filter_level" not in chat and "effective_level(v)" in chat
    assert "viewer_level(v)" in (ROOT / "ruqqus" / "helpers" / "filtered_words.py").read_text(encoding="utf-8")


def test_the_regional_rule_lives_in_viewer_level_and_nowhere_else():
    visibility = (ROOT / "ruqqus" / "helpers" / "visibility.py").read_text(encoding="utf-8")
    assert visibility.count("current_region_locked(") == 2          # viewer_level, and the notice's wording
    for path in (ROOT / "ruqqus").rglob("*.py"):
        if path.name in ("visibility.py", "age_declaration.py", "jinja2.py", "children_pages.py"):
            continue
        assert "current_region_locked(" not in path.read_text(encoding="utf-8") and "region_locked(" not in path.read_text(encoding="utf-8"), path


def test_the_routes_that_let_an_account_stop_spending_or_make_a_free_circle_ask_only_for_the_spending_side():
    paypal = (ROUTES / "paypal.py").read_text(encoding="utf-8").split("def premium_auto_renew(", 1)[1].split("\n@app.", 1)[0]
    assert 'if wanted else None' in paypal and 'age_declaration.feature_refusal(v, "money")' in paypal
    assert paypal.index('age_declaration.feature_refusal(v, "money")') < paypal.index("v.premium_auto_renew = wanted"), "refused before the flag is touched: the request's commit would keep it"
    fund = (ROUTES / "creator_fund.py").read_text(encoding="utf-8").split("def settings_creator_fund_save(", 1)[1].split("\n@app.", 1)[0]
    assert fund.index('"leave"') < fund.index('age_declaration.feature_refusal(v, "money")'), "leaving comes first and is never refused"
    circle = (ROUTES / "circles.py").read_text(encoding="utf-8").split("def circle_create(", 1)[1].split("\n@app.", 1)[0]
    assert 'if kind == circles.PAID else None' in circle and 'age_declaration.feature_refusal(v, "money")' in circle


# --- slice D5: a child-locked account only reads (the owner, 2026-11-05). Every route that makes something a reader sees asks the one rule. ----------------------------
CREATING = {
    "posts.py": "submit_get submit_post edit_post forward_post repost_post",
    "comments.py": "api_comment edit_comment forward_comment repost_comment",
    "stories.py": "story_create",
    "spaces.py": "spaces_new space_create space_message_add space_update_add space_summary space_replay",
    "live_threads.py": "live_update_add live_summary",
    "post_drafts.py": "save_draft",
    "collab.py": "collab_new collab_publish collab_fork",
    "coauthors.py": "coauthor_accept",
    "guild_wiki.py": "guild_wiki_save",
}


def _function(module, name):
    source = (ROUTES / module).read_text(encoding="utf-8")
    for node in ast.walk(ast.parse(source)):
        if isinstance(node, ast.FunctionDef) and node.name == name:
            return ast.get_source_segment(source, node)
    raise AssertionError((module, name, "no such function"))


def test_every_route_that_makes_something_a_reader_sees_asks_the_one_rule():
    for module, names in CREATING.items():
        for name in names.split():
            assert "age_declaration.content_refusal(" in _function(module, name), (module, name)


def test_the_shared_draft_routes_that_type_or_join_ask_through_may_type():
    collab = (ROUTES / "collab.py").read_text(encoding="utf-8")
    assert "age_declaration.content_refusal(v)" in collab.split("def _may_type(", 1)[1].split("\n\n\n", 1)[0]
    for name in ("collab_send_op", "collab_invite_answer"):
        assert "_may_type(v)" in _function("collab.py", name), name


def test_the_one_rule_refuses_every_locked_account_and_no_other_and_no_route_that_deletes_asks_it():
    rule = (ROOT / "ruqqus" / "helpers" / "age_declaration.py").read_text(encoding="utf-8").split("def content_refusal(v):", 1)[1].split("\ndef ", 1)[0]
    assert "return FEATURE_LOCKED if is_locked(v) else None" in rule, "no parent's switch changes it"
    for module, name in (("posts.py", "delete_post_pid"), ("comments.py", "delete_comment"), ("stories.py", "story_delete")):
        assert "content_refusal" not in _function(module, name), (module, "deleting stays open")


def test_no_template_offers_a_child_account_a_way_to_post_or_comment():
    for template in ("default.html", "header.html"):
        assert "{% if v and not content_locked(v) %}" in (ROOT / "ruqqus" / "templates" / template).read_text(encoding="utf-8"), template
    menu = (ROOT / "ruqqus" / "templates" / "partials" / "account_menu.html").read_text(encoding="utf-8")
    assert "{% if not content_locked(v) %}" in menu and 'href="/submit"' in menu
    for template in ("partials/comment_form.html", "home.html", "board.html"):
        assert "content_locked(v)" in (ROOT / "ruqqus" / "templates" / template).read_text(encoding="utf-8"), template
