"""QR sign-in in the source: what a limited sign-in is refused (the list a new route of that kind has to join), that nothing on the
sign-in page holds a code, what the scripts do with a code and a pre-key, the wiring of the session and the pages. The behaviour
is tests/pg/test_qr_login.py and scripts/ui/qr_login.spec.ts."""
import ast
import json
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
RUQQUS = ROOT / "ruqqus"
ROUTES = RUQQUS / "routes"


def read(*parts):
    return ROOT.joinpath(*parts).read_text(encoding="utf-8")


# Everything that outlives "log out all", moves money, reaches a secret, widens who can read what the member shares or runs other people's
# guilds, and that a QR sign-in may not do until its password is given. Whole areas are named, not guessed: chat in all its forms (tokens,
# keys, devices, lists, messages, calls), every coin that moves, apps and their keys, linked storage, bots, a member's circles, follows and
# blocks' undoing, a shared draft's decisions, other accounts on a byline, what prints the email address or the anonymous list. A route that
# already asks for the password is here too: a phished session gets no chance to try it from the victim's cookie.
HELD = {
    "oauth.py": "oauth_authorize_prompt oauth_authorize_post request_api_keys delete_oauth_app edit_oauth_app reroll_oauth_tokens oauth_rescind_app",
    "oidc_pages.py": "oidc_authorize oidc_authorize_post",
    "settings.py": "settings_apps settings_security_post settings_log_out_others settings_profile_post settings_new_feedkey delete_account "
                   "settings_name_change settings_export settings_remove_discord settings_unblock_user settings_blocks_import",
    "static.py": "settings_security press_inquiry",
    "users.py": "follow_user",
    "profile_lists.py": "settings_list_visibility",
    "history.py": "history_anonymous",
    "legal.py": "legal_1 legal_2 legal_final dmca_post counter_dmca_post",
    "collab.py": "collab_propose collab_vote collab_withdraw collab_invite_answer collab_leave collab_publish",
    "coauthors.py": "coauthor_invite coauthor_accept",
    "discord.py": "join_discord discord_redirect",
    "age_declaration_pages.py": "settings_age_declaration",
    "chat.py": "chat_page chat_token chat_key_get chat_key_set chat_recovery_key_legacy chat_check_password chat_ready chat_start "
               "chat_conversations chat_share_targets chat_accept chat_decline chat_mark_read chat_search",
    "chat_apps.py": "settings_chat_apps chat_devices chat_device_sign_out",
    "chat_groups.py": "chat_group_create chat_group_info chat_group_add chat_group_remove chat_group_leave chat_group_accept chat_group_decline "
                      "chat_group_rename chat_group_role chat_group_mark_read chat_people",
    "calls.py": "call_window call_join call_heartbeat call_leave call_decline call_status calls_history",
    "broadcasts.py": "broadcasts_following broadcast_info broadcast_create broadcast_join broadcast_leave broadcast_helpers broadcast_message "
                     "broadcast_mark_read broadcast_report broadcast_delete",
    "guild_channels.py": "guild_channels_list guild_channel_join guild_channel_message guild_channel_mark_read guild_channel_senders "
                         "guild_channel_report guild_channel_remove guild_channel_pin guild_channel_pins guild_channel_mute_for_me "
                         "guild_channel_accept_rules guild_channel_search",
    "spaces.py": "space_start space_join",
    "paypal.py": "shop_buy_coins shop_negative_balance shop_buy_coins_completed gift_post_pid gift_comment_pid premium_redeem premium_auto_renew",
    "circles.py": "circle_subscribe circle_cancel circle_guild_subscribe circle_guild_cancel circle_price circle_create circle_rename circle_delete "
                  "circle_description circle_add circle_remove circle_post_audience",
    "creator_fund.py": "settings_creator_fund_save",
    "bot_accounts.py": "settings_bots_automate settings_bots_apply settings_bot_profile settings_bot_picture settings_bot_picture_remove "
                       "settings_bot_add_source settings_bot_remove_source settings_bot_restart_source settings_bot_pause settings_bot_resume "
                       "settings_bot_delete settings_bot_create_key settings_bot_revoke_key",
    "media.py": "settings_media_google_connect settings_media_google_callback settings_media_service_connect settings_media_service_callback "
                "settings_media_dev_enable settings_media_disconnect media_default settings_media_default settings_media_dailymotion_video "
                "settings_media_google_video",
    "boards.py": "create_board_post mod_accept_board siege_guild all_mod_queue",
    "qr_login_pages.py": "login_qr_info login_qr_approve",
}
LISTED = {(module, name) for module, names in HELD.items() for name in names.split()}

# a route whose address is in one of these areas has to be on the list, or here with its reason
AREAS = ("/api/chat/", "/settings/chat_apps", "/api/calls/", "/call/", "/api/broadcasts", "/api/guilds/channels/", "/api/guilds/<guildname>/channels",
         "/oauth/authorize", "/oidc/authorize", "/shop/", "/gift_", "/settings/premium/", "/settings/bots/", "/settings/media/", "/api/media/default",
         "/settings/security", "/settings/export", "/settings/delete_account", "/settings/name_change", "/settings/log_out_all_others", "/discord",
         "/api/circles", "/legal")
AREA_EXCEPTIONS = {
    "chat_unread_count": "the navbar's number, on every page: a count, nothing it counts",
    "chat_post_preview": "a post, by the reader's own rules; only the chat client asks",
    "shop_get_price": "no sign-in at all",
    "paypal_webhook_handler": "PayPal's, checked by its signature; no session",
    "settings_bot": "the page of forms, each of them refused; a key is only ever shown as it is made",
    "shop_coin_balance": "a number the navbar already shows",
    "circle_choices_for": "reads the member's own circles for the sheet; adding is refused",
}
# what a route's own body doing marks it as this kind of route
MARKS = ("verifyPass", "passhash", "accept_2fa", "mfa_backup_store", "end_other_sessions", "ClientAuth(", "OauthApp(", "coins.spend", "coins.move", "coins.give", "coins_store.",
         "redeem_week", "circle_store.subscribe(", "circle_store.subscribe_guild(", "circle_store.cancel", "data_export.", "close_account", "login_as(", "recovery_key",
         "chat_devices.sign_out", "delete_device", "ModRelationship(", "Follow(user_id", "invite_names(")
MARK_EXCEPTIONS = {
    "login_post": "the sign-in itself", "post_reset": "signed out, by the emailed token", "oauth_grant": "an app's own token request",
    "oauth_release_auth": "a token ending itself", "oauth_kill_auth": "a token ending itself", "login_qr_upgrade": "what ends the limit",
    "sign_up_post": "signed out", "mfa_qr": "draws a picture of what it is given", "activate": "by the emailed token",
    "submit_post": "HELD_IN_PART: a co-author named in the composer",
}

# refused for one part of what a route does, the rest of it being everyday: what marks that part, and the refusal
HELD_IN_PART = {
    ("post_drafts.py", "save_draft"): 'v.qr_limited and (request.form.get("publish_utc")',
    ("media.py", "media_upload_begin"): 'kind == "video" and v.qr_limited',
    ("settings.py", "settings_block_user"): "v.qr_limited and user and circle_store.paid_between(",
    ("posts.py", "submit_post"): "coauthor_names and v.qr_limited",
    ("curations.py", "curation_edit_post"): "if v.qr_limited:",
}


def routes():
    """(module, name, address list, decorators) of every route."""
    found = []
    for path in sorted(ROUTES.glob("*.py")):
        source = path.read_text(encoding="utf-8")
        lines = source.splitlines()
        for node in ast.walk(ast.parse(source)):
            if not isinstance(node, ast.FunctionDef) or not node.decorator_list:
                continue
            decorators = [lines[d.lineno - 1].strip() for d in node.decorator_list]
            addresses = [m.group(1) for d in decorators for m in [re.match(r"""@app\.\w+\(\s*["']([^"']+)""", d)] if m]
            if addresses:
                found.append((path.name, node.name, addresses, decorators, "\n".join(lines[node.lineno:node.end_lineno])))
    return found


ALL_ROUTES = routes()


def stack_of(module, name):
    for found_module, found_name, _addresses, decorators, _body in ALL_ROUTES:
        if (found_module, found_name) == (module, name):
            return [d.split("#")[0].strip() for d in decorators]
    raise AssertionError((module, name, "no such route"))


def test_every_listed_route_refuses_a_limited_sign_in_under_its_sign_in_above_its_form_key():
    for module, name in sorted(LISTED):
        stack = stack_of(module, name)
        assert "@not_qr_limited" in stack, (module, name)
        at = stack.index("@not_qr_limited")
        assert any(d == w or d.startswith(w) for d in stack[:at] for w in ("@auth_required", "@is_not_banned", "@auth_desired")), (module, name, "under the sign-in")
        if "@validate_formkey" in stack:
            assert at < stack.index("@validate_formkey"), (module, name, "above the form key")


def test_nothing_else_carries_it_without_being_listed():
    carried = {(m, n) for m, n, _a, decorators, _b in ALL_ROUTES if "@not_qr_limited" in decorators}
    assert carried == LISTED


def test_a_route_in_one_of_those_areas_has_to_be_named():
    for module, name, addresses, decorators, _body in ALL_ROUTES:
        if (module, name) in LISTED or name in AREA_EXCEPTIONS or any("admin_level_required" in d or "is_guildmaster" in d for d in decorators):
            continue
        for address in addresses:
            assert not address.startswith(AREAS), (module, name, address, "add it to HELD, or say why not")


def test_a_route_doing_that_kind_of_thing_has_to_be_named():
    for module, name, _addresses, decorators, body in ALL_ROUTES:
        if (module, name) in LISTED or name in MARK_EXCEPTIONS or any("admin_level_required" in d or "is_guildmaster" in d for d in decorators):
            continue
        for mark in MARKS:
            assert mark not in body, (module, name, mark, "add it to HELD, or say why not")


def test_what_is_refused_in_part():
    bodies = {(m, n): body for m, n, _a, _d, body in ALL_ROUTES}
    for where, mark in HELD_IN_PART.items():
        assert mark in bodies[where], where
        assert "qr_limited_answer(v)" in bodies[where].split(mark, 1)[1].split("\n\n", 1)[0], where


def test_a_template_that_prints_the_email_address_keeps_it_from_a_limited_sign_in():
    """Whoever holds a relayed code shouldn't get the address to write to next. Each one asks, or only a refused route draws it."""
    asks = {"help/contact.html", "help/press.html", "help/dmca.html", "help/counter_dmca.html"}
    behind_a_refused_route = {"settings_security.html": "settings_security", "2fa_modal.html": "settings_security", "legal/legal.html": "legal_1",
                              "help/dmca_email.md": "dmca_post", "admin/ncmec_report.xml": "an admin's page"}
    found = set()
    templates = RUQQUS / "templates"
    for path in templates.rglob("*"):
        if not path.is_file() or path.parts[len(templates.parts)] == "email":
            continue
        source = path.read_text(encoding="utf-8", errors="ignore")
        if "v.email" in source:
            name = path.relative_to(templates).as_posix()
            found.add(name)
            if name in asks:
                assert source.index("{% if v and v.qr_limited %}") < source.index("v.email"), name
    assert found == asks | set(behind_a_refused_route)


def test_the_password_form_is_only_a_link_in_a_frame():
    page = read("ruqqus", "templates", "login_qr_upgrade.html")
    assert page.index("{% if in_frame %}\n    {#") < page.index("data-chat-prekey>") and "{% else %}" in page.split('id="qr-upgrade-link"', 1)[1].split("data-chat-prekey>", 1)[0]
    assert 'target="_top"' in page.split('id="qr-upgrade-link"', 1)[1].split(">", 1)[0] and '<base target="_top">' in page
    wrappers = read("ruqqus", "helpers", "wrappers.py")
    assert 'in_frame = request.args.get("embed") == "1"' in wrappers and "_outside_the_frame(back)" in wrappers


def test_the_exceptions_are_real_routes():
    names = {name for _m, name, _a, _d, _b in ALL_ROUTES}
    assert set(AREA_EXCEPTIONS) <= names
    assert set(MARK_EXCEPTIONS) - {"activate"} <= names, "activate is in ruqqus/mail/mail.py"


def test_admin_and_guildmaster_powers_ask_for_the_password_in_their_wrappers():
    wrappers = read("ruqqus", "helpers", "wrappers.py")
    admin = wrappers.split("def admin_level_required(", 1)[1].split("\ndef ", 1)[0]
    assert admin.index('getattr(v, "qr_limited", False) and v.qr_held_level >= x') < admin.index("v.admin_level < x")
    guild = wrappers.split("def is_guildmaster(", 1)[1].split("\ndef ", 1)[0]
    assert guild.index('getattr(v, "qr_limited", False) and board.leads(v)') < guild.index("m=board.has_mod(v)")


def test_a_limited_sign_in_reads_as_no_admin_and_no_guildmaster_everywhere_else():
    wrappers = read("ruqqus", "helpers", "wrappers.py")
    assert 'set_committed_value(user, "admin_level", 0)' in wrappers, "a committed value, so nothing is ever saved"
    assert '@event.listens_for(User, "refresh")' in wrappers, "a commit expires the object and the reload would bring the real level back"
    boards = read("ruqqus", "classes", "boards.py")
    has_mod = boards.split("def has_mod(self, user, perm=None):", 1)[1].split("def leads(", 1)[0]
    assert 'getattr(user, "qr_limited", False)' in has_mod and "return False" in has_mod


def test_only_reading_a_guild_asks_leads():
    boards = read("ruqqus", "classes", "boards.py")
    for name in ("can_forward", "can_comment", "can_view"):
        assert "self.leads(" in boards.split(f"def {name}(self, user)", 1)[1].split("\n    def ", 1)[0], name


def test_the_session_treats_a_qr_sign_in_as_over_at_its_end_in_both_places():
    wrappers = read("ruqqus", "helpers", "wrappers.py")
    assert "if not uid or qr_login.session_over(flask_session, time.time()):" in wrappers
    cookie = read("ruqqus", "helpers", "session_cookie.py")
    assert "qr_login.EXPIRES_KEY, qr_login.LIMITED_KEY" in cookie.split("SIGN_IN_KEYS =", 1)[1].split("\n", 1)[0]
    assert 'session.get("user_id") and not qr_login.session_over(session, now)' in cookie


def test_only_the_sign_in_and_the_password_write_the_limit():
    for path in (RUQQUS / "routes").glob("*.py"):
        source = path.read_text(encoding="utf-8")
        assert ("begin_limited" in source) == (path.name == "login.py"), path.name
        assert ("end_limited" in source) == (path.name in ("login.py", "qr_login_pages.py")), path.name
    assert not re.search(r"\[[^\]]*(qr_expires_utc|EXPIRES_KEY)[^\]]*\]\s*=", read("ruqqus", "routes", "qr_login_pages.py")), "never refreshed"


def test_the_sign_in_page_holds_no_code_and_its_panel_is_outside_the_form_that_writes_a_pre_key():
    page = read("ruqqus", "templates", "login.html")
    form = page.split('data-chat-prekey>', 1)[1].split("</form>", 1)[0]
    assert "qr-" not in form and page.index("</form>") < page.index('id="qr-login"')
    assert "{% if qr_login_offered() %}" in page.split('id="qr-login"', 1)[0].rsplit("</form>", 1)[1]
    assert "image" not in page.split('id="qr-image"', 1)[1].split(">", 1)[0].replace('role="img"', "") and 'src=' not in page.split('id="qr-image"', 1)[1].split(">", 1)[0]
    route = read("ruqqus", "routes", "login.py").split("def login_get(", 1)[1].split("def check_for_alts", 1)[0]
    assert "qr" not in route, "a press starts it: nothing on opening the page"


def test_neither_page_carries_analytics_or_an_ad_script():
    for template in ("login.html", "login_qr_approve.html", "login_qr_upgrade.html"):
        source = read("ruqqus", "templates", template)
        for word in ("analytics", "ad_slot", "ads_script", "ad_script", "adsbygoogle", "plausible"):
            assert word not in source, (template, word)


def test_the_upgrade_form_writes_a_pre_key_so_its_route_holds_the_ads_back():
    assert 'data-chat-prekey' in read("ruqqus", "templates", "login_qr_upgrade.html")
    assert "ads_rules.hold_after_password(flask_session, now)" in read("ruqqus", "routes", "qr_login_pages.py").split("def login_qr_upgrade(v):", 1)[1]


def test_the_script_removes_a_pre_key_on_success_and_never_relays_one():
    script = read("ruqqus", "assets", "js", "qr_login.js")
    finish = script.split("'/login/qr/finish'", 1)[1].split("catch", 1)[0]
    assert "forgetPrekey();" in finish
    assert "try { sessionStorage.removeItem('uqqus_chat_prekey'); }" in script and "try { localStorage.removeItem('uqqus_chat_prekey'); }" in script
    assert "getItem" not in script, "never read, so never relayed"


def test_the_code_comes_out_of_the_address_at_once_and_is_never_sent_to_the_status_route():
    script = read("ruqqus", "assets", "js", "qr_login.js")
    assert "history.replaceState(null, '', location.pathname)" in script
    assert script.index("history.replaceState") < script.index("'/login/qr/info'")
    assert "fetch('/login/qr/status', { credentials: 'same-origin' })" in script
    assert "innerHTML" not in script and "insertAdjacentHTML" not in script
    assert "location.search" not in script


def test_the_phone_believes_only_the_routes_own_answer():
    script = read("ruqqus", "assets", "js", "qr_login.js")
    assert "redirect: 'manual'" in script.split("function post(", 1)[1].split("}\n", 2)[1] + script.split("function post(", 1)[1], "an ended sign-in's redirect must not read as success"
    assert "answer.type === 'opaqueredirect'" in script
    assert "got.status === 200 && got.data && got.data.ok === true) return show(screen, 'approved')" in script
    assert "got.status === 200 && got.data && got.data.ok === true) return show(screen, 'denied')" in script
    assert ".disabled = " not in script, "a disabled button loses the focus the view after it should get"


def test_the_computer_asks_once_more_before_a_code_counts_as_over():
    script = read("ruqqus", "assets", "js", "qr_login.js")
    poll = script.split("var poll = function () {", 1)[1].split("fetch('/login/qr/status'", 1)[0]
    assert "ended(" not in poll, "the question comes first"
    assert "if (Date.now() >= endsAt) return ended('expired');" in script.split("var again = function", 1)[1]


def test_the_script_polls_as_the_plan_says():
    script = read("ruqqus", "assets", "js", "qr_login.js")
    assert "window.QR_POLL_MS || 3000" in script and "window.QR_SLOW_MS || 15000" in script and "MAX_429 = 3" in script
    assert "if (document.hidden) return;" in script and "visibilitychange" in script


def test_confirm_is_never_focused_so_enter_signs_nobody_in():
    script = read("ruqqus", "assets", "js", "qr_login.js")
    assert "heading.focus()" in script and "'qr-confirm').focus" not in script


def test_not_me_is_primary_and_focused_on_the_phone():
    page = read("ruqqus", "templates", "login_qr_approve.html")
    assert 'class="btn btn-primary w-100" id="qr-deny"' in page and 'class="btn btn-outline-primary w-100 mt-3" id="qr-approve-button"' in page
    assert page.index('id="qr-deny"') < page.index('id="qr-approve-form"')
    assert "deny.focus();" in read("ruqqus", "assets", "js", "qr_login.js")
    assert "Only continue if you started this yourself on a screen in front of you." in page


def test_the_legacy_recovery_code_is_never_asked():
    pages = read("ruqqus", "routes", "qr_login_pages.py")
    assert "mfa_removal_code" not in pages and "legacy_code_allowed" not in pages
    answer = pages.split("def _answer_is_right(", 1)[1].split("\n\n\n", 1)[0]
    assert "v.accept_2fa(" in answer and "mfa_backup_store.spend(" in answer and "v.verifyPass(answer)" in answer


def test_every_redis_step_of_an_approval_goes_through_one_delete():
    pages = read("ruqqus", "routes", "qr_login_pages.py")
    approve = pages.split("def login_qr_approve(v):", 1)[1].split("@app.route", 1)[0]
    assert approve.index("r.delete(qr_login.wait_key(digest)) != 1") < approve.index("r.set(qr_login.ok_key(digest)")
    deny = pages.split("def login_qr_deny(v):", 1)[1].split("@app.route", 1)[0]
    assert deny.index("r.delete(qr_login.wait_key(digest)) == 1") < deny.index("r.set(qr_login.no_key(digest)")
    finish = pages.split("def login_qr_finish():", 1)[1].split("def _tell_the_member", 1)[0]
    assert "qr_login.take(r, qr_login.ok_key(digest))" in finish
    assert finish.index("qr_login.take(") < finish.index("complete_sign_in(account, sign_in_history.METHOD_QR)")


def test_the_guards_of_each_route():
    pages = read("ruqqus", "routes", "qr_login_pages.py")
    def stack(name):
        return re.search(rf"((?:@[^\n]+\n)+)def {name}\(", pages).group(1)
    assert '@limiter.limit("10/minute")' in stack("login_qr_start")
    # by the code, never by the address alone: one address may hold many computers, and nobody on it can use up another's Confirm
    for name, limit in (("login_qr_status", "120/minute"), ("login_qr_finish", "6/minute")):
        assert f'@limiter.limit("{limit}", key_func=_key_of_this_code, exempt_when=_has_no_code)' in stack(name)
        assert "exempt_when=_has_code)" in stack(name)
    assert "throttle_check_ip" not in stack("login_qr_finish"), "heat is per address: anyone on it could stop someone else's Confirm"
    assert "PER_CODE_PATHS" in read("ruqqus", "routes", "errors.py").split("def error_429", 1)[1].split("count_429s", 1)[0], "before anything is counted"
    assert '@limiter.limit("30/minute")' in stack("login_qr_info") and "@validate_formkey" in stack("login_qr_info")
    approve = stack("login_qr_approve")
    assert '@limiter.limit("6/minute")' in approve and "@auth_required" in approve and "@throttle_check_ip('qr_approve')" in approve
    assert "@validate_formkey" in stack("login_qr_deny")
    for name in ("login_qr_start", "login_qr_finish", "login_qr_cancel"):
        body = pages.split(f"def {name}(", 1)[1].split("\n\n\n", 1)[0]
        assert "_refuse_another_site()" in body, name
    assert "g.skip_session_cookie = True" in pages.split("def login_qr_status(", 1)[1].split("\n\n\n", 1)[0]
    assert 'request.path.startswith("/login/qr/")' in read("ruqqus", "__main__.py"), "assigned no-store, whichever code answered"


def test_the_refusal_never_names_the_switch():
    pages = read("ruqqus", "routes", "qr_login_pages.py")
    words = pages.split('CANT_USE = N_("', 1)[1].split('"', 1)[0]
    assert "turn" not in words.lower() and "off" not in words.lower() and "setting" not in words.lower()
    assert words in read("ruqqus", "templates", "login_qr_approve.html")
    assert pages.count("msg(CANT_USE)") == 1


def test_a_signed_out_phone_has_no_way_back_to_the_page():
    page = read("ruqqus", "templates", "login_qr_approve.html")
    signed_out = page.split('{% if state == "signed_out" %}', 1)[1].split("{% elif", 1)[0]
    assert "Sign in on this phone first, then scan a new code." in signed_out
    assert 'href="/login"' in signed_out and "redirect" not in signed_out.split("#}", 1)[1]


def test_the_new_strings_are_marked_and_the_styles_are_shared_tokens():
    for style in ("main.scss", "main_dark.scss"):
        assert '@import "{ruqquspath}/assets/style/_qr_login";' in read("ruqqus", "assets", "style", style)
    scss = read("ruqqus", "assets", "style", "_qr_login.scss")
    assert not re.search(r"#[0-9a-fA-F]{3,8}\b", scss), "tokens only (the code's own paper is the word white)"
    assert not re.search(r"\b(left|right)\b", scss), "logical properties: nothing to mirror"
    assert "fa-qrcode" in read("ruqqus", "templates", "login.html")
    free = set(read("tests", "data", "fa_free_6_4_0_names.txt").split())
    assert "qrcode" in free or "fa-qrcode" in free


def test_scripts_are_loaded_through_asset_url():
    assert "{{ asset_url('js/qr_login.js') }}" in read("ruqqus", "templates", "login.html")


def test_the_three_bundles_handle_the_refusal():
    token = read("ruqqus", "assets", "chat_src", "src", "token.js")
    assert token.index("refused.need_password") < token.index("forgetDevice(storageKey);\n  if (otherIsSomeoneElses)"), "nothing is cleared before the site says yes"
    assert "e instanceof PasswordNeeded" in read("ruqqus", "assets", "chat_src", "src", "index.js")
    assert "e instanceof PasswordNeeded" in read("ruqqus", "assets", "chat_src", "src", "call.js")
    assert "boot.qrLimited" in read("ruqqus", "assets", "chat_src", "src", "space.js")
    assert '"qrLimited": v.qr_limited' in read("ruqqus", "routes", "spaces.py")
    assert "space-password-first" in read("ruqqus", "templates", "space.html")


def test_the_feed_address_is_kept_from_a_limited_sign_in():
    page = read("ruqqus", "templates", "settings_profile.html")
    assert page.index("{% if v.qr_limited %}") < page.index('id="rss_feed"')


def test_the_privacy_page_says_what_is_kept():
    page = read("ruqqus", "templates", "help", "privacy.html")
    for word in ("`qr_pending`", "`qr_expires_utc`", "`qr_limited`", "for two minutes", "That record holds no address.", "which account approved it"):
        assert word in page, word
    assert "No address is stored" not in page, "the limits on pressing the button and approving a code are counted under the address, in Redis"


def test_the_privacy_page_says_how_long_the_limits_keep_an_address():
    page = read("ruqqus", "templates", "help", "privacy.html")
    pages = read("ruqqus", "routes", "qr_login_pages.py")
    throttle = read("ruqqus", "helpers", "throttle.py")
    # pressing the button: the limiter's own key, the address for a minute (__main__.limiter_key_func); going over it again and again, an hour
    assert '@limiter.limit("10/minute")\ndef login_qr_start' in pages and 'limiter_key_func():\n    return request.remote_addr' in read("ruqqus", "__main__.py")
    assert 'r.expire(f"ban_ip_{ip}", 3600)' in read("ruqqus", "routes", "errors.py")
    assert "about a minute for pressing the button (an hour if an address keeps going over the limit)" in page
    # approving and the password after: the throttle's key, ten half-lives of an hour
    assert "@throttle_check_ip('qr_approve')" in pages and "@throttle_check_ip('qr_upgrade')" in pages
    assert "BOIL_HALF_LIFE = 3600.0" in throttle and "ttl=int(BOIL_HALF_LIFE * 10)" in throttle
    assert "up to about ten hours for approving a code on a phone or entering your password after a QR sign-in" in page


def test_the_privacy_page_says_when_the_cookies_number_goes():
    # read after the code has run out, for as long as an approval could still be waiting (helpers/qr_login.py)
    from ruqqus.helpers import qr_login
    assert qr_login.PENDING_SECONDS - qr_login.CODE_SECONDS == 60
    page = read("ruqqus", "templates", "help", "privacy.html")
    assert "on the first page you open once the code has been out of time for a minute" in page
    assert "a few minutes after the code runs out" not in page


def test_the_qr_endpoints_are_not_pages_to_the_traffic_counter():
    named = json.loads(read("tests", "data", "traffic_not_pages.json"))["endpoints"]
    ours = [n for _m, n, _a, _d, _b in ALL_ROUTES if n.startswith("login_qr_")]
    assert ours and set(ours) <= set(named)


def _ban_lines(source):
    """(line number, line) of every automatic suspension in a route's source, its alts' too."""
    return [(n, line) for n, line in enumerate(source.splitlines()) if re.search(r"\b(v|alt)\.ban\(", line)]


def test_no_automatic_ban_can_be_reached_from_a_limited_sign_in():
    """A suspension outlives log out everywhere and the 12 hours, and a spam guard's walks over the member's real alts. A new automatic ban in
    a route a limited sign-in can reach has a `qr_limited` just above it (a route that is whole on HELD, such as Settings > Profile, isn't one)."""
    for name in ("posts.py", "comments.py", "profile_edit_pages.py"):
        lines = read("ruqqus", "routes", name).splitlines()
        found = _ban_lines("\n".join(lines))
        assert found, name
        for n, line in found:
            above = "\n".join(lines[max(0, n - 12):n])
            assert "qr_limited" in above, f"{name}:{n + 1}: {line.strip()}"
    media = read("ruqqus", "routes", "media.py")
    assert "if not v.qr_limited:\n                        safety.ban_uploader(" in media
    assert 'if not getattr(v, "qr_limited", False):\n            v.ban_reason=h.ban_reason' in read("ruqqus", "helpers", "aws.py")
    # the child-abuse match bans whoever it is: CLAUDE.md "QR sign-in" names it, and this says when that changes
    assert "qr_limited" not in read("ruqqus", "helpers", "media", "safety.py")


def test_the_sign_in_row_of_a_code_keeps_no_address_or_region_until_the_password():
    sign_in = read("ruqqus", "routes", "login.py").split("def complete_sign_in(")[1]
    assert "ip=None if is_qr else request.remote_addr" in sign_in and "region_code=None if is_qr else resolve_region(cf_country)" in sign_in
    upgrade = read("ruqqus", "routes", "qr_login_pages.py").split("def login_qr_upgrade(")[1]
    assert upgrade.index("LoginEvent.ip.is_(None)") < upgrade.index("qr_login.end_limited(flask_session)")


def test_a_refused_post_says_what_the_route_said_when_it_needs_the_password():
    post = read("ruqqus", "assets", "js", "all_js.js").split("function post(url, callback, errortext, extra")[1].split("function ")[0]
    assert post.index("data.need_password && data.error") < post.index("errortext ||")
    assert "null, giveBack)" in read("ruqqus", "assets", "js", "who_to_follow.js")
