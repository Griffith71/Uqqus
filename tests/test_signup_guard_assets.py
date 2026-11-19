"""The signup guard in the source: which routes refuse a held account (helpers/quarantine.py) and which must not, the
sign-up form's captcha and hidden field, the migration and schema, the admin pages' levels, and the per-account places
(retention, account closure, the data export)."""
import ast
import re
from pathlib import Path

from ruqqus.helpers import account_cleanup, data_export, retention, signup_review_store

ROOT = Path(__file__).resolve().parent.parent
RUQQUS = ROOT / "ruqqus"


def read(*parts):
    return RUQQUS.joinpath(*parts).read_text(encoding="utf-8")


# every route that acts on someone else, and the action a held account's attempt is counted as
HELD_OUT = {
    ("posts.py", "submit_post"): "post",
    ("posts.py", "edit_post"): "edit_post",
    ("posts.py", "forward_post"): "forward",
    ("posts.py", "repost_post"): "repost",
    ("comments.py", "api_comment"): "comment",
    ("comments.py", "repost_comment"): "repost",
    ("comments.py", "forward_comment"): "forward",
    ("votes.py", "api_vote_post"): "vote",
    ("votes.py", "api_vote_comment"): "vote",
    ("paypal.py", "gift_post_pid"): "tip",
    ("paypal.py", "gift_comment_pid"): "tip",
    ("users.py", "follow_user"): "follow",
    ("boards.py", "subscribe_board"): "join_guild",
    ("chat.py", "chat_start"): "chat",
    ("children_pages.py", "children_new"): "child",
    ("chat_groups.py", "chat_group_create"): "group",
    ("flagging.py", "api_flag_post"): "report",
    ("flagging.py", "api_flag_comment"): "report",
    ("account_reports.py", "report_account"): "report",
    ("log_report_pages.py", "log_report"): "report",
    ("polls.py", "poll_vote"): "poll_vote",
    ("stories.py", "story_create"): "story",
    # --- what a held account may not do beyond the first twenty-two. A suspension used to refuse every one of these through `is_not_banned`; a quarantine
    # (the signup guard's hold, or an automatic check's) stops only what carries the wrapper, so each route that makes or changes something another member
    # reads, or acts on another account, is listed here (the owner, 2026-11-17: a held account reads and changes its own settings and does nothing else).
    ("comments.py", "edit_comment"): "edit_comment",
    ("comments.py", "author_hide_comment"): "moderate",
    ("comments.py", "author_unhide_comment"): "moderate",
    ("guild_wiki.py", "guild_wiki_save"): "wiki",
    ("guild_wiki.py", "guild_wiki_restore"): "wiki",
    ("guild_wiki.py", "guild_wiki_settings"): "wiki",
    ("guild_wiki.py", "guild_wiki_delete"): "wiki",
    ("guild_events.py", "guild_event_create"): "event",
    ("guild_events.py", "guild_event_save"): "event",
    ("guild_events.py", "guild_event_cancel"): "event",
    ("spaces.py", "space_create"): "space",
    ("spaces.py", "space_start"): "space",
    ("spaces.py", "space_message_add"): "space",
    ("spaces.py", "space_update_add"): "space",
    ("spaces.py", "space_summary"): "space",
    ("spaces.py", "space_replay"): "space",
    ("spaces.py", "space_invite"): "space",
    ("spaces.py", "space_cohost"): "space",
    ("spaces.py", "space_remove"): "space",
    ("spaces.py", "space_mute"): "space",
    ("spaces.py", "space_message_remove"): "space",
    ("spaces.py", "space_report"): "report",
    ("spaces.py", "space_message_report"): "report",
    ("live_threads.py", "live_update_add"): "live_update",
    ("live_threads.py", "live_summary"): "live_update",
    ("guild_channels.py", "guild_channel_message"): "channel",
    ("guild_channels.py", "guild_channel_report"): "report",
    ("guild_channels.py", "guild_channel_remove"): "moderate",
    ("guild_channels.py", "guild_channel_pin"): "moderate",
    ("broadcasts.py", "broadcast_message"): "channel",
    ("broadcasts.py", "broadcast_create"): "channel",
    ("broadcasts.py", "broadcast_helpers"): "channel",
    ("broadcasts.py", "broadcast_report"): "report",
    ("chat_reports_pages.py", "chat_report"): "report",
    ("collab.py", "collab_new"): "collab",
    ("collab.py", "collab_send_op"): "collab",
    ("collab.py", "collab_propose"): "collab",
    ("collab.py", "collab_vote"): "collab",
    ("collab.py", "collab_publish"): "collab",
    ("collab.py", "collab_fork"): "collab",
    ("collab.py", "collab_invite_answer"): "collab",
    ("coauthors.py", "coauthor_invite"): "coauthor",
    ("coauthors.py", "coauthor_accept"): "coauthor",
    ("coauthors.py", "coauthor_remove"): "coauthor",
    ("boards.py", "create_board_post"): "guild",
    ("boards.py", "siege_guild"): "guild",
    ("boards.py", "mod_accept_board"): "guild",
    ("curations.py", "curations_create_post"): "curation",
    ("curations.py", "curation_edit_post"): "curation",
    ("curations.py", "curation_fork"): "curation",
    ("stories.py", "highlight_create"): "story",
    ("stories.py", "highlight_rename"): "story",
    ("stories.py", "story_report"): "report",
    ("regions.py", "regions_propose_name"): "region",
    ("regions.py", "regions_vote"): "vote",
    ("circles.py", "circle_create"): "circle",
    ("circles.py", "circle_rename"): "circle",
    ("circles.py", "circle_description"): "circle",
    ("circles.py", "circle_add"): "circle",
    ("circles.py", "circle_subscribe"): "tip",
    ("circles.py", "circle_guild_subscribe"): "tip",
    ("chat_groups.py", "chat_group_add"): "group",
    ("chat_groups.py", "chat_group_remove"): "group",
    ("chat_groups.py", "chat_group_rename"): "group",
    ("chat_groups.py", "chat_group_role"): "group",
    # --- chat, cut off entirely (the owner, 2026-11-19: "hold should cut off chat entirely"): the chat server's session and keys, the chat's own writes, joining a room,
    # a call, outside apps' sign-in is refused in `oidc_pages._refusal`. Reading the chat's lists is in HELD_OUT_READS.
    ("chat.py", "chat_token"): "chat",
    ("chat.py", "chat_store_keys_route"): "chat",
    ("chat.py", "chat_key_set"): "chat",
    ("chat.py", "chat_code_saved"): "chat",
    ("chat.py", "chat_check_password"): "chat",
    ("chat.py", "chat_ready"): "chat",
    ("chat.py", "chat_accept"): "chat",
    ("chat.py", "chat_decline"): "chat",
    ("chat.py", "chat_mark_read"): "chat",
    ("calls.py", "call_join"): "chat",
    ("calls.py", "call_heartbeat"): "chat",
    ("chat_extras_pages.py", "chat_pin"): "chat",
    ("chat_extras_pages.py", "chat_disappear"): "chat",
    ("chat_groups.py", "chat_group_accept"): "chat",
    ("chat_key_reset_pages.py", "chat_key_reset_grant"): "chat",
    ("chat_key_reset_pages.py", "chat_signing_keys"): "chat",
    ("push_pages.py", "push_subscribe"): "chat",
    ("push_pages.py", "chat_group_push"): "chat",
    ("broadcasts.py", "broadcast_join"): "chat",
    ("guild_channels.py", "guild_channel_join"): "chat",
    ("guild_channels.py", "guild_channel_mark_read"): "chat",
    ("guild_channels.py", "guild_channel_mute_for_me"): "chat",
    ("guild_channels.py", "guild_channel_accept_rules"): "chat",
}

# the chat's pages and reads a held account is refused too (no form key: a GET): the chat page and the call window, the locked keys and the lists, previews and search the
# chat client asks for. Every one is under the sign-in wrapper and carries `@not_quarantined("chat")`.
HELD_OUT_READS = {
    ("chat.py", "chat_page"): "chat",
    ("chat.py", "chat_key_get"): "chat",
    ("chat.py", "chat_recovery_key_legacy"): "chat",
    ("chat.py", "chat_conversations"): "chat",
    ("chat.py", "chat_share_targets"): "chat",
    ("chat.py", "chat_post_preview"): "chat",
    ("chat.py", "chat_comment_preview"): "chat",
    ("chat.py", "chat_search"): "chat",
    ("calls.py", "call_window"): "chat",
    ("calls.py", "call_status"): "chat",
    ("calls.py", "calls_history"): "chat",
    ("chat_groups.py", "chat_group_info"): "chat",
    ("chat_groups.py", "chat_people"): "chat",
    ("broadcasts.py", "broadcasts_following"): "chat",
    ("broadcasts.py", "broadcasts_popular"): "chat",
    ("broadcasts.py", "broadcast_info"): "chat",
    ("guild_channels.py", "guild_channels_list"): "chat",
    ("guild_channels.py", "guild_channel_senders"): "chat",
    ("guild_channels.py", "guild_channel_pins"): "chat",
    ("guild_channels.py", "guild_channel_search"): "chat",
}

# Every other route a signed-in account can POST to, and why a held account may still use it: what it does is its own (a bookmark, a draft, a setting kept
# apart from Settings, its own deletion), a way out (an appeal, Log out, Send feedback, a legal form), reading (a mark as read, a preview, a translation, a
# join of a room to read it, ending its own part). **Chat is not here any more**: a hold cuts it off entirely (HELD_OUT and HELD_OUT_READS), and a new hold signs the
# account's chat devices out of the chat server (`auto_quarantine._cut_off_chat`). A new route that acts on others or writes what others read is NOT added here: it carries the wrapper above.
OPEN_TO_HELD = {
    "boards.py": "unsubscribe_board toggle_guild_bell",                                            # leaving, and its own bell
    "bookmark_folders.py": "bookmark_folders_create bookmark_folders_rename bookmark_folders_delete bookmark_move",
    "broadcasts.py": "broadcast_leave broadcast_mark_read broadcast_delete",                       # leaving, and deleting one's own
    "calls.py": "call_leave call_decline",                                                         # ending one's own part (the heartbeat that keeps a slot is held)
    "chat_apps.py": "chat_device_sign_out",                                                        # signing one's own device out is security, not chat
    "chat_groups.py": "chat_group_leave chat_group_decline chat_group_mark_read",                  # leaving or refusing: ending one's own part
    "circles.py": "circle_delete circle_price circle_remove circle_cancel circle_post_audience circle_guild_cancel",   # its own, and ending what it pays
    "coauthors.py": "coauthor_hide_name coauthor_decline coauthor_leave",                          # taking its own name off
    "collab.py": "collab_options collab_presence collab_withdraw collab_leave",
    "comments.py": "delete_comment save_comment unsave_comment toggle_comment_sensitive unrepost_comment",
    "curations.py": "curation_delete curation_add_guild curation_remove_guild curation_add_user curation_remove_user user_add_to_curation guild_add_to_curation "
                    "curation_set_region_filter curation_set_language_filter curation_set_category_filter curation_set_algorithm curation_preview_algorithm "
                    "curation_follow curation_follow_inline curation_unfollow_inline curation_unfollow",       # its own feeds, and following a feed
    "guild_events.py": "guild_event_rsvp",
    "guild_flair.py": "post_flair_set",                                                             # a guildmaster's (`has_mod` says no to a held account) or the forwarder's own copy
    "history_mass_pages.py": "history_mass",
    "legal.py": "legal_2 legal_final dmca_post counter_dmca_post",
    "live_threads.py": "live_toggle live_update_strike live_update_delete",                         # ending or correcting its own
    "log_appeal_pages.py": "log_appeal_post guild_appeal_decide",                                  # the way out of a hold; a guild's decision asks `has_mod`
    "login.py": "logout",
    "media.py": "media_default media_upload_begin media_upload_complete media_dev_upload",         # the member's own storage
    "oauth.py": "oauth_authorize_post request_api_keys delete_oauth_app edit_oauth_app reroll_oauth_tokens oauth_rescind_app oauth_release_auth oauth_kill_auth",
    "oidc_pages.py": "oidc_authorize_post",                                                        # an outside chat app: refused inside (`_refusal`), tested in tests/pg/test_held_chat.py
    "paypal.py": "shop_buy_coins shop_negative_balance",                                            # paying the site, not anyone
    "post_commenting.py": "post_comment_permission",                                               # its own post
    "post_drafts.py": "save_draft unschedule_draft delete_draft",                                  # a draft is private; a scheduled one is published through the held `submit_post`
    "post_editor.py": "preview_markdown post_preview create_post_template delete_post_template",
    "post_feedback.py": "post_fewer_like_this",
    "post_follows.py": "follow_post unfollow_post",
    "posts.py": "delete_post_pid toggle_post_sensitive save_post unsave_post unrepost_post",
    "push_pages.py": "push_unsubscribe",                                                           # taking a browser off
    "qr_login_pages.py": "login_qr_info login_qr_approve login_qr_deny login_qr_upgrade",
    "quarantine_pages.py": "quarantine_appeal_post",                                                 # the way OUT of an automatic hold: the account asks for a review
    "site_feedback.py": "send_feedback",                                                           # the way to tell the staff
    "spaces.py": "space_join space_heartbeat space_leave space_hand space_accept space_listen space_video space_end space_remind space_message_delete space_slow",   # join and heartbeat refuse a held account inside, for a call only (a board stays readable)
    "static.py": "press_inquiry",
    "stories.py": "story_view story_delete highlight_delete",
    "translation_pages.py": "translate_item",
    "users.py": "unfollow_user api_agree_tos toggle_user_bell",
}


def decorators_of(source, name):
    match = re.search(rf"((?:@[^\n]+\n)+)def {name}\(", source)
    assert match, name
    return match.group(1).strip().split("\n")


def test_every_route_that_acts_on_others_refuses_a_held_account():
    for (module, name), action in HELD_OUT.items():
        stack = decorators_of(read("routes", module), name)
        assert f'@not_quarantined("{action}")' in stack, (module, name)
        at = stack.index(f'@not_quarantined("{action}")')
        assert stack[at + 1] == "@validate_formkey", (module, name, "right above the form key")
        assert any(d in stack[:at] for d in ("@auth_required", "@is_not_banned")), (module, name, "under the sign-in")


def test_the_chats_pages_and_reads_refuse_a_held_account_too():
    for (module, name), action in HELD_OUT_READS.items():
        stack = decorators_of(read("routes", module), name)
        assert f'@not_quarantined("{action}")' in stack, (module, name)
        assert any(d in stack[:stack.index(f'@not_quarantined("{action}")')] for d in ("@auth_required", "@is_not_banned")), (module, name, "under the sign-in")
        assert not set(HELD_OUT_READS) & set(HELD_OUT)


def test_a_call_space_refuses_a_held_account_inside_its_join_and_heartbeat():
    """A call Space runs on the chat session: its join (a Matrix account, a room, a roster slot) and the heartbeat that keeps the slot refuse a held account, for a call only."""
    spaces = read("routes", "spaces.py")
    for name, marker in (("space_join", "_call_only(space)"), ("space_heartbeat", "space.kind != space_rules.CALL")):
        body = spaces.split(f"def {name}(", 1)[1].split("\n\n\n", 1)[0]
        assert 'quarantine.refusal(v, "chat", r)' in body, name
        assert body.index(marker) < body.index('quarantine.refusal(v, "chat", r)'), (name, "after the call-only check: a board is left alone")


def test_every_chat_route_carrying_the_child_lock_carries_the_hold_too():
    """A held account has no chat: a route guarded for chat or messaging by `@not_child_locked` (the same feature) that lacks `@not_quarantined(...)` is a way in."""
    for path in sorted((RUQQUS / "routes").glob("*.py")):
        if path.name == "chat_apps.py":
            continue                                        # signing one's own device out, and the page that lists it: security, not chat
        for stack, name in re.findall(r'((?:@[^\n]+\n)+)def (\w+)\(', path.read_text(encoding="utf-8")):
            if re.search(r'@not_child_locked\("(chat|messaging)"\)', stack) and not re.search(r'\("/settings', stack):    # a held account reads and changes its own settings
                assert "@not_quarantined(" in stack, (path.name, name)      # `chat`, or the more particular `group` or `channel` a write already had


def test_nothing_else_carries_it_without_being_listed():
    listed = {(m, n) for m, n in HELD_OUT} | set(HELD_OUT_READS)
    for path in (RUQQUS / "routes").glob("*.py"):
        for name in re.findall(r'@not_quarantined\("\w+"\)\n(?:@[^\n]+\n)*def (\w+)\(', path.read_text(encoding="utf-8")):
            assert (path.name, name) in listed, (path.name, name)


def _routes_a_signed_in_account_can_change_things_through():
    """(module, function) of every route that writes (POST, PATCH, PUT or DELETE) behind a sign-in wrapper, leaving out what its own wrapper already refuses a held
    account (`@is_guildmaster`, which asks `refusal`; `@admin_level_required`, whose accounts are never held) and what lives under /settings and /admin (a held
    account reads and changes its own settings, and never reaches the admin pages)."""
    found = set()
    for path in sorted((RUQQUS / "routes").glob("*.py")):
        source = path.read_text(encoding="utf-8")
        lines = source.splitlines()
        for node in ast.walk(ast.parse(source)):
            if not isinstance(node, ast.FunctionDef) or not node.decorator_list:
                continue
            stack = [lines[d.lineno - 1].strip() for d in node.decorator_list]
            routes = [d for d in stack if d.startswith("@app.")]
            if not routes or not any("POST" in d or d.startswith(("@app.post", "@app.patch", "@app.put", "@app.delete")) for d in routes):
                continue
            if not any(d.split("(")[0] in ("@auth_required", "@is_not_banned", "@no_sanctions") for d in stack):
                continue
            if any(d.startswith(("@is_guildmaster", "@admin_level_required")) for d in stack):
                continue
            if re.search(r'\("/(settings|admin)', routes[0]):
                continue
            found.add((path.name, node.name))
    return found


def test_every_route_that_changes_something_decides_whether_a_held_account_may_use_it():
    """A suspension stopped every route through `is_not_banned`; a quarantine stops only what carries the wrapper. So no route may be left undecided: it is in
    HELD_OUT (the wrapper) or in OPEN_TO_HELD (with the reason above it). A new route fails here until it is placed."""
    open_to_held = {(module, name) for module, names in OPEN_TO_HELD.items() for name in names.split()}
    held = set(HELD_OUT)
    found = _routes_a_signed_in_account_can_change_things_through()
    assert not held & open_to_held, sorted(held & open_to_held)
    assert not found - held - open_to_held, f"decide these (the wrapper, or OPEN_TO_HELD with a reason): {sorted(found - held - open_to_held)}"
    # an OPEN entry must still be a route that changes things (a held one under /settings, as `children_new` is, is not found and may stay in HELD_OUT)
    assert not (open_to_held - found), f"not routes any more: {sorted(open_to_held - found)}"


def test_a_guildmasters_powers_are_a_held_accounts_no_more_in_the_one_place_every_route_asks():
    """`@is_guildmaster` refuses a held guildmaster of the guild (saying why), and `Board.has_mod`, which every inline check, helper and template asks, says no
    for a held account as it does for a limited QR sign-in; `Board.leads` is what a held guildmaster may still READ."""
    wrappers = read("helpers", "wrappers.py")
    guild = wrappers.split("def is_guildmaster(", 1)[1].split("\ndef ", 1)[0]
    held = 'getattr(v, "is_quarantined", False) and board.leads(v)'
    assert held in guild and 'refusal(v, "moderate", r)' in guild
    assert guild.index(held) < guild.index("m=board.has_mod(v)"), "asked before has_mod, which would say 'not a guildmaster' for a held one"
    has_mod = read("classes", "boards.py").split("def has_mod(self, user, perm=None):", 1)[1].split("def leads(", 1)[0]
    assert 'getattr(user, "is_quarantined", False)' in has_mod and "return False" in has_mod
    assert "is_quarantined" not in read("classes", "boards.py").split("def leads(", 1)[1].split("def _mod_row(", 1)[0]


def test_a_held_account_can_still_reach_settings_and_its_email():
    wrappers = read("helpers", "wrappers.py")
    is_not_banned = wrappers.split("def is_not_banned(", 1)[1].split("\ndef ", 1)[0]
    assert "quarantin" not in is_not_banned
    assert "quarantin" not in wrappers.split("def auth_required(", 1)[1].split("\ndef ", 1)[0]
    mail = read("mail", "mail.py")
    for name in ("api_verify_email", "activate"):
        assert "@not_quarantined" not in "\n".join(decorators_of(mail, name)), name
    for path in (RUQQUS / "routes").glob("settings*.py"):
        assert "@not_quarantined" not in path.read_text(encoding="utf-8"), path.name


def test_verifying_an_email_releases_a_held_account_unless_it_is_a_throwaway_or_an_automatic_check_holds_it():
    activate = read("mail", "mail.py").split("def activate(", 1)[1]
    # only the signup guard's own hold (quarantine_kind 0): a hold an automatic check placed is released by an admin or an appeal, never an email
    assert "user.quarantined_utc and not (user.quarantine_kind or 0) and not signup_signals.disposable(email)" in activate
    assert 'signup_review_store.release(g.db, user.id, "verified"' in activate
    assert "user.quarantined_utc = 0" not in activate, "release is conditional SQL; the object is only read again"


def test_the_form_draws_the_chosen_captcha_and_a_hidden_field():
    form = read("templates", "sign_up.html")
    assert "hcaptcha.com/1/api.js" not in form, "the script comes from helpers/captcha.py"
    assert '<script src="{{ captcha.script }}" async defer></script>' in form
    assert 'class="{{ captcha.widget_class }} mt-3" data-sitekey="{{ captcha.sitekey }}"' in form
    field = re.search(r'<div class="signup-extra" aria-hidden="true">.*?</div>', form, re.S).group(0)
    assert 'name="website"' in field and 'tabindex="-1"' in field and 'autocomplete="off"' in field
    style = read("assets", "style", "_components.scss")
    rule = style.split(".signup-extra {", 1)[1].split("}", 1)[0]
    assert "left: -10000px" in rule and "display: none" not in rule


def test_the_route_asks_the_guard_and_counts_every_outcome():
    login = read("routes", "login.py")
    post = login.split("def sign_up_post(", 1)[1].split("\n@app.route", 1)[0]
    assert "guard = _signup_guard(" in post and "signup_signals.tally(r," in post
    assert post.index("guard = _signup_guard(") < post.index('flask_session.pop("signup_token")')
    assert 'tr("There was a problem. Please try again.")' in post.split("if guard.verdict.action == signup_risk.REFUSE", 1)[1]
    assert "quarantined_utc=signup_utc if held else 0" in post
    assert "signup_review_store.record(" in post and "signup_signals.note_sign_up(" in post
    assert "requests.post" not in post and "x.json()" not in post, "the captcha is helpers/captcha.py's"


def test_an_account_an_automatic_check_holds_counts_as_evasion_at_sign_up_as_a_suspended_one_does():
    login = read("routes", "login.py")
    post = login.split("def sign_up_post(", 1)[1].split("\n@app.route", 1)[0]
    line = next(x for x in post.splitlines() if x.strip().startswith("ban_evade = int("))
    assert "x.is_suspended or x.is_auto_quarantined" in line and "User.id.in_(session_history)" in line
    assert post.index(line) < post.index("guard = _signup_guard(") and "ban_evasion=ban_evade" in login      # the guard's soft sign and the new account's count
    wrappers = read("helpers", "wrappers.py")
    evade = wrappers.split("def check_ban_evade(v):", 1)[1].split("\n\n\n", 1)[0]
    assert 'auto_quarantine.hold(g.db, v, "evasion"' in evade and "quarantine_kind" in evade           # it holds the new account, and leaves an account already held alone


def test_banning_an_account_the_site_holds_closes_its_appeal_and_reinstating_it_does_not_say_it_is_free():
    admin = read("routes", "admin_api.py")
    ban = admin.split("def ban_user(", 1)[1].split("\n@app.route", 1)[0]
    # the closing lives in the one place every permanent ban goes through: `User.ban` (the admin's, an alt's, the signup queue's, the site's own), and the CSAM route's hand ban
    assert "_close_hold_appeal" not in admin and "quarantine_review.close_hold_appeal_for_ban(g.db, user.id, v.id)" in admin and "close_hold_appeal_for_ban(g.db, alt.id, v.id)" in admin
    user_py = read("classes", "user.py")
    ban_method = user_py.split("    def ban(self", 1)[1].split("    def _children_follow_ban(", 1)[0]
    assert "if days <= 0:" in ban_method and "quarantine_review.close_hold_appeal_for_ban(g.db, self.id," in ban_method, "a ban for good only: a suspension for a time leaves the hold and its appeal"
    helper = read("helpers", "quarantine_review.py").split("def close_hold_appeal_for_ban(", 1)[1].split("\n\n\n", 1)[0]
    assert "auto_quarantine.current_entry(db, user_id)" in helper and "close_appeals_for_ban(" in helper and "begin_nested()" in helper
    assert "notify(" not in helper, "the ban's own notice tells the person: no second one"
    unban = admin.split("def unban_user(", 1)[1].split("\n@app.route", 1)[0]
    code = "\n".join(line for line in unban.splitlines() if not line.strip().startswith("#"))
    assert "auto_quarantine" not in code and "release(" not in code, "reinstating never lifts the hold"
    assert "if user.is_quarantined:" in unban and "It is still on hold, so it can't post, comment or act on others for now." in unban


def test_the_migration_is_mirrored_in_the_schema():
    migration = (ROOT / "scripts/migrations/2026-12-04_signup_guard.sql").read_text(encoding="utf-8")
    schema = (ROOT / "schema.sql").read_text(encoding="utf-8")
    assert "ADD COLUMN IF NOT EXISTS quarantined_utc integer DEFAULT 0 NOT NULL" in migration
    assert "    quarantined_utc integer DEFAULT 0 NOT NULL," in schema
    assert "CREATE TABLE IF NOT EXISTS signup_reviews" in migration and "CREATE TABLE public.signup_reviews" in schema
    for index in ("users_quarantined_idx", "signup_reviews_waiting_idx", "signup_reviews_network_idx",
                  "signup_reviews_shape_idx"):
        assert f"CREATE INDEX IF NOT EXISTS {index}" in migration and f"CREATE INDEX {index}" in schema
    assert "('2026-12-04_signup_guard.sql')" in schema
    assert "REFERENCES" not in migration, "no foreign keys to users (the project's rule)"


def test_the_admin_pages_levels():
    pages = read("routes", "signup_guard_pages.py")
    for path, level in (('@app.get("/admin/signups")', 3), ('@app.post("/admin/signups/decide")', 3),
                        ('@app.post("/admin/signups/cluster")', 3), ('@app.post("/admin/signups/settings")', 4)):
        block = pages.split(path, 1)[1].split("\ndef ", 1)[0]
        assert f"@admin_level_required({level})" in block, path
        if "post" in path:
            assert "@validate_formkey" in block, path
    assert "/admin/signups" in read("templates", "admin", "admin_home.html")


def test_retention_closure_and_export():
    assert retention.SIGNUP_REVIEW_DAYS == signup_review_store.KEEP_DAYS
    assert any("signup_reviews" in sql for _, sql in retention.RULES)
    assert any("DELETE FROM signup_reviews WHERE user_id = :u" == sql for _, sql in account_cleanup.STEPS)
    account = dict(data_export.SECTIONS)["account"]
    assert "quarantined_utc" in account
    assert not any("signup_reviews" in sql for _, sql in data_export.SECTIONS), "the score is the admins'"


def test_the_notice_is_drawn_for_a_held_member_only():
    shell = read("templates", "default.html")
    assert ("{% if v and v.is_quarantined and not request.args.get('embed') %}"
            '{% include "partials/held_notice.html" %}{% endif %}') in shell
    notice = read("templates", "partials", "held_notice.html")
    assert 'role="status"' in notice and 'href="/settings/security"' in notice
    rule = read("assets", "style", "_components.scss").split(".held-notice {", 1)[1].split("\n}", 1)[0]
    assert "background: var(--brand-soft)" in rule and "color: var(--text)" in rule, "tokens, for AA in both themes"
