"""Parent and child accounts, as source (behaviour: tests/test_child_accounts.py and tests/pg/test_child_accounts.py): what each route asks before it acts, that a
parent only ever reaches their own child, that nothing about a birth date is kept, and that every place a parent's family changes (deleting, banning, unbanning,
lifting a lock, closing an account, a shared browser) does what the design says."""
import ast
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
APP = ROOT / "ruqqus"


def read(*parts):
    return ROOT.joinpath(*parts).read_text(encoding="utf-8")


def route_functions(module):
    source = read("ruqqus", "routes", module)
    lines = source.splitlines()
    out = {}
    for node in ast.walk(ast.parse(source)):
        if isinstance(node, ast.FunctionDef) and any(lines[d.lineno - 1].strip().startswith("@app.") for d in node.decorator_list):
            out[node.name] = ([lines[d.lineno - 1].strip() for d in node.decorator_list], "\n".join(lines[node.lineno:node.end_lineno]))
    return out


ROUTES = route_functions("children_pages.py")


def test_the_routes_are_these_and_every_one_is_signed_in_and_held_for_a_limited_qr_sign_in():
    assert set(ROUTES) == {"settings_children", "children_new", "children_controls", "children_answer", "children_stop", "children_password", "children_delete",
                           "children_export", "children_activity", "settings_parent_ask"}
    for name, (decorators, _body) in ROUTES.items():
        assert "@not_qr_limited" in decorators, name
        assert any(d.startswith(("@auth_required", "@is_not_banned")) for d in decorators), name


def test_every_change_is_a_post_with_the_form_key_under_the_sign_in_and_the_qr_rule():
    for name, (decorators, _body) in ROUTES.items():
        if not any(d.startswith("@app.post") for d in decorators):
            continue
        assert "@validate_formkey" in decorators, name
        assert decorators.index("@not_qr_limited") < decorators.index("@validate_formkey"), name
        assert decorators[-1] == "@validate_formkey", name


def test_making_an_account_is_held_for_a_held_account_and_limited():
    decorators, body = ROUTES["children_new"]
    assert '@not_quarantined("child")' in decorators and decorators.index('@not_quarantined("child")') + 1 == decorators.index("@validate_formkey")
    assert any(d.startswith("@limiter.limit(") for d in decorators)
    assert body.index("child_store.lock_parent(g.db, v.id)") < body.index("g.db.refresh(v)") < body.index("parent_view(v)") < body.index("User("), \
        "counted under the parent's lock and from the parent as they are now, before the account exists"
    for piece in ("rules.birth_refusal(", "age_declaration.sub_account_fields(now)", "child_store.link(", "following_visibility=2", 'form.get("terms") != "1"'):
        assert piece in body, piece


def test_a_parent_reaches_only_their_own_child_by_name():
    for name, (_decorators, body) in ROUTES.items():
        if name in ("settings_children", "children_new", "settings_parent_ask"):
            continue
        assert "_child_or_404(v, username)" in body, name
    helper = read("ruqqus", "routes", "children_pages.py").split("def _child_or_404(", 1)[1].split("\ndef ", 1)[0]
    assert "child_store.child_of(g.db, v.id, username, _now())" in helper and "abort(404)" in helper
    store = read("ruqqus", "helpers", "child_store.py")
    assert "WHERE l.parent_id = :p" in store.split("def children_of(", 1)[1].split("\ndef ", 1)[0]


def test_what_is_secret_or_destructive_asks_the_parents_own_password_first():
    for name in ("children_password", "children_delete", "children_export"):
        _decorators, body = ROUTES[name]
        assert "if _wrong_proof(v):" in body, name
        first_change = min(body.index(piece) for piece in ("passhash", "close_account", "data_export.build_for_parent") if piece in body)
        assert body.index("_wrong_proof(") < first_change, name
    proof = read("ruqqus", "routes", "children_pages.py").split("def _wrong_proof(", 1)[1].split("\ndef ", 1)[0]
    assert 'verifyPass(request.form.get("parent_password")' in proof and "v.mfa_secret" in proof and "accept_2fa(" in proof and "mfa_backup_store.spend(" in proof, \
        "a parent with two-factor on gives the code too, as Settings asks for their own"
    assert "negative_balance_cents" in ROUTES["children_delete"][1] and "rules.OWES" in ROUTES["children_delete"][1], "an account that owes money isn't deleted to escape it"
    assert "NEW_PASSWORD" in ROUTES["children_password"][1], "the child is told when their password was set for them"
    assert "data_export.build(" not in ROUTES["children_export"][1], "a parent gets the parent's list of sections, never the whole export"


def test_the_birth_month_and_year_are_checked_and_never_kept():
    for path in (APP / "classes" / "user.py", ROOT / "schema.sql", ROOT / "scripts" / "migrations" / "2026-12-18_parent_accounts.sql"):
        text = path.read_text(encoding="utf-8").lower()
        assert "birth_year" not in text and "birth_month" not in text and "birthdate" not in text and "date_of_birth" not in text, path.name
    page = read("ruqqus", "routes", "children_pages.py")
    assert "birth_year" in page and "birth_year=" not in page, "read from the form for the check; never given to the account"
    assert "isn't kept" in read("ruqqus", "templates", "settings_children.html")


def test_the_controls_route_takes_only_the_fixed_names_and_the_one_special():
    body = ROUTES["children_controls"][1]
    assert 'name == "private_posting"' in body and "child_store.set_control(" in body and "child_store.clear_ask(" in body
    assert "request.args" not in body


def test_a_release_is_refused_where_only_an_age_check_lifts_a_lock():
    body = ROUTES["children_answer"][1]
    assert body.index("age_declaration.is_strict(child_store.asked_country(g.db, child[\"id\"]))") < body.index("child_store.take_ask(") < body.index("release_child(row, _now())"), \
        "judged by where the CHILD asked from, then the one answer that closes the request does the work"
    assert "row.age_under_utc" in body and "UNDER_18_FOUND" in body, "an age check's finding that they are under 18 is not overruled by a parent"
    assert "request_country()" not in body, "the parent's own country decides nothing"
    ask = read("ruqqus", "routes", "children_pages.py").split("def ask_parent(", 1)[1].split("\ndef ", 1)[0]
    assert "age_declaration.is_strict(country)" in ask and "v.age_under_utc" in ask and "v.age_lock != age_declaration.PARENT_LOCK" in ask
    assert "if child_store.ask(g.db, v.id, kind, _now()," in ask, "the parent is told only by the request that opened it"
    stop = ROUTES["children_stop"][1]
    assert "User.age_lock == age_declaration.PARENT_LOCK" in stop and "if not changed:" in stop, "a lock lifted meanwhile is never turned back into one"
    assert stop.index("User.age_lock == age_declaration.PARENT_LOCK") < stop.index("child_store.release(")


def test_the_tables_the_migration_and_the_ledger_agree():
    migration = read("scripts", "migrations", "2026-12-18_parent_accounts.sql")
    schema = read("schema.sql")
    for table in ("account_links", "child_controls", "child_family"):
        assert f"CREATE TABLE IF NOT EXISTS {table} (" in migration and f"CREATE TABLE public.{table} (" in schema
    assert "REFERENCES" not in migration, "no foreign key to users: closing an account deletes the rows (helpers/account_cleanup.py)"
    ledger = re.findall(r"\('(\d{4}-\d{2}-\d{2}_[\w-]+\.sql)'\)", schema.split("INSERT INTO public.schema_migrations", 1)[1])
    assert ledger == sorted(ledger) and "2026-12-18_parent_accounts.sql" in ledger
    for name in ("chat", "spaces", "money", "anonymous_guilds", "posting", "commenting", "ask_private_utc", "ask_release_utc", "ask_release_country", "suspended_until_utc",
                 "parent_id", "made_utc"):
        assert f"    {name} " in migration or f"    {name} integer" in migration, name


def test_posting_and_commenting_obey_the_parents_switch_and_the_wrapper_asks_for_the_feature():
    assert 'age_declaration.activity_refusal(v, "posting")' in read("ruqqus", "routes", "posts.py").split("def submit_post(v):", 1)[1].split("\n@app.", 1)[0]
    assert 'age_declaration.activity_refusal(v, "commenting")' in read("ruqqus", "routes", "comments.py")
    assert "age_declaration.feature_refusal(v, feature)" in read("ruqqus", "helpers", "wrappers.py")


def test_a_parents_family_follows_the_parent_into_a_ban_and_back_and_a_deletion():
    user = read("ruqqus", "classes", "user.py")
    ban = user.split("def ban(self", 1)[1].split("    def unban(", 1)[0]
    assert "self._children_follow_ban(days)" in ban
    follow = user.split("def _children_follow_ban(", 1)[1].split("    def unban(", 1)[0]
    assert "child.ban(reason=child_accounts.SUSPENDED_REASON, days=days)" in follow and "child_accounts.close_account(child," in follow
    assert "child_store.set_suspension(" in follow and "child_store.child_ids_safely(" in follow, "the suspension the site gave is remembered on the ledger"
    assert "child.unban_utc == 0 or child.unban_utc >= end" in follow, "a child's own permanent or longer ban is never shortened"
    assert "child_store.set_suspension(g.db, child.id, end, child.unban_utc)" in follow, "a shorter ban of its own is extended and remembered, to come back"
    assert "child.unban_utc = max(end, prior)" in follow, "a second suspension of the parent moves the child's end with it"
    assert "child_store.lock_parent(g.db, self.id, required=False)" in follow, "serialised with a child being made"
    assert follow.index("for child in told:") > follow.index("for child_id in child_store.child_ids_safely"), "notices commit: told after the loop"
    assert "PARENT_SUSPENDED" not in user, "no public reason that says whose child an account is"
    assert "ruqqus.routes" not in follow, "a model never imports a route (tests/test_ad_income_stays_out_assets.py)"
    unban = user.split("    def unban(", 1)[1].split("    @property", 1)[0]
    assert "child_store.suspended_children_safely(g.db, self.id)" in unban and "child.unban_utc == max(until, prior)" in unban and "floor=action_log.STAFF" in unban
    assert "child_store.child_ids" not in unban, "found by the ledger, which outlives a link that ended while the parent was suspended"
    assert "child.unban_utc = prior" in unban, "a shorter ban of the child's own comes back as it was"
    assert "child.ban_reason" not in unban, "found by the marker the site kept, not by a reason a profile prints"
    settings = read("ruqqus", "routes", "settings.py")
    assert "child_accounts.closer = close_own_account" in settings
    close = settings.split("def close_own_account(", 1)[1].split("child_accounts.closer", 1)[0]
    assert "for child_id in child_store.child_ids_safely(g.db, v.id):" in close and "sign_out_browser=False" in close and "if sign_out_browser:" in close
    assert close.index("child_store.lock_parent(g.db, v.id)") < close.index("child_store.child_ids_safely") < close.index("remove_user(v)"), \
        "the parent's lock first (a child being made waits and is then deleted too), then the children, then the parent"


def test_the_suspension_reason_is_staff_only_and_the_cascade_is_logged_for_staff():
    log = read("ruqqus", "helpers", "action_log.py")
    assert '"This account is suspended.": ("", STAFF)' in log
    assert "child_removed_with_parent" in log.split("STAFF_ACTIONS = ", 1)[1].split("\n", 1)[0]
    assert "def record_unban(db, user_id, actor_id, *, reason=\"\", automatic=False, notice_sent=False, floor=PUBLIC)" in log


def test_a_parent_and_its_child_are_not_alts_and_closing_an_account_forgets_the_link():
    login = read("ruqqus", "routes", "login.py").split("def check_for_alts(", 1)[1].split("\n# login post procedure", 1)[0]
    assert "child_store.related_safely(g.db, current_id, past_id)" in login
    cleanup = read("ruqqus", "helpers", "account_cleanup.py")
    assert "DELETE FROM account_links WHERE child_id = :u OR parent_id = :u" in cleanup and "DELETE FROM child_controls WHERE child_id = :u" in cleanup
    assert "child_family" in cleanup, "a closing child leaves the ledger's count; a closing parent's rows go"


def test_lifting_a_sub_accounts_lock_ends_the_link_by_every_road():
    pages = read("ruqqus", "routes", "age_declaration_pages.py")
    callback = pages.split("def settings_age_assurance_callback(v):", 1)[1].split("\n@app.", 1)[0]
    assert "child_store.release(g.db, v.id) if was_sub" in callback and "tell_parent_released(" in callback
    admin = pages.split("def admin_age_declaration(v):", 1)[1]
    assert "child_store.release(g.db, target.id) if was_sub" in admin and "tell_parent_released(" in admin
    declare = pages.split("def settings_age_declaration(v):", 1)[1].split("\ndef ", 1)[0]
    assert "ask_parent(v, \"release\", back)" in declare, "a tick from a sub-account asks its parent: it doesn't lift the lock"
    assert "release_child" in read("ruqqus", "routes", "children_pages.py")


def test_the_pages_are_reachable_and_say_so():
    settings = read("ruqqus", "templates", "settings.html")
    assert settings.count("{% if parent_tab(v) %}") == 2 and settings.count('href="/settings/children"') == 2
    security = read("ruqqus", "templates", "settings_security.html")
    assert "delete-children-warning" in security and security.count("children_count(v)") >= 2
    assert ".children_pages import" in read("ruqqus", "routes", "__init__.py") or "from .children_pages import *" in read("ruqqus", "routes", "__init__.py")
    content = read("ruqqus", "templates", "settings_filters.html")
    assert "child_view(v)" in content and "/settings/parent/ask" in content and 'id="parent-controls"' in content


def test_the_child_is_told_what_a_parent_can_see_and_that_chats_are_not_among_it():
    welcome = read("ruqqus", "routes", "children_pages.py").split("WELCOME = N_(", 1)[1].split(")\nASKED_PRIVATE", 1)[0]
    assert "never shows them what your chats say" in welcome and "anyone who knows it can sign in as you and read your chats" in welcome
    assert "never shows them what your chats say" in read("ruqqus", "templates", "settings_filters.html")
    assert "never shows you what their chats say" in read("ruqqus", "templates", "settings_children.html")
    for what in ("that your account was reported", "recent sign-ins"):
        assert what in welcome.replace("\n", " ") + read("ruqqus", "templates", "settings_filters.html"), what


def test_a_parents_switch_covers_every_way_of_posting_not_only_the_composer():
    """Posting off means no new post by any road: the composer, a story, a forward, a repost of a post or a comment, and publishing a shared draft."""
    for module, function in (("posts.py", "forward_post"), ("posts.py", "repost_post"), ("comments.py", "forward_comment"), ("comments.py", "repost_comment"),
                             ("stories.py", "story_create"), ("collab.py", "collab_publish")):
        body = read("ruqqus", "routes", module).split(f"def {function}(", 1)[1].split("\n@app.", 1)[0]
        assert 'age_declaration.activity_refusal(v, "posting")' in body, function


def test_a_locked_account_is_not_paid_in_coins_by_the_routes_that_pay_someone():
    circles = read("ruqqus", "routes", "circles.py")
    assert 'age_declaration.target_refusal(owner, "money")' in circles.split("def circle_subscribe(", 1)[1].split("\n@app.", 1)[0]
    guild = circles.split("def circle_guild_subscribe(", 1)[1].split("\n@app.", 1)[0]
    assert 'age_declaration.target_refusal(founder, "money")' in guild
    price = circles.split("def board_circle_price(", 1)[0].rsplit("@app.route", 1)[1]
    assert '@not_child_locked("money")' in price
    tips = read("ruqqus", "routes", "paypal.py")
    assert 'age_declaration.target_refusal(receiver, "money")' in tips, "the coins of a bot's post go to the member who runs it"


def test_an_outside_chat_app_needs_chat_to_be_on():
    body = read("ruqqus", "routes", "oidc_pages.py")
    assert body.count("oidc.account_refusal(") == 1 and body.count("_refusal(") >= 4
    assert 'age_declaration.feature_refusal(user, "chat")' in body.split("def _refusal(", 1)[1].split("\ndef ", 1)[0]


def test_the_guild_chat_tab_and_every_channel_route_a_member_uses_ask_the_lock():
    channels = read("ruqqus", "routes", "guild_channels.py")
    assert 'age_declaration.feature_refusal(v, "chat")' in channels.split("def has_chat(", 1)[1].split("\n\n\n", 1)[0]
    assert 'age_declaration.feature_refusal(v, "chat")' in channels.split("def guild_chat(", 1)[1].split("\n\n\n", 1)[0]


def test_being_a_parent_is_checked_every_time_a_child_is_touched_and_the_parents_lock_is_shared():
    helper = read("ruqqus", "routes", "children_pages.py").split("def _child_or_404(", 1)[1].split("\ndef ", 1)[0]
    assert "age_declaration.is_locked(v)" in helper and "v.is_suspended" in helper and "abort(403)" in helper
    assert helper.index("abort(403)") < helper.index("child_store.child_of(")
    store = read("ruqqus", "helpers", "child_store.py")
    assert "LOCK_KEY = 7002" in store and "pg_advisory_xact_lock" in store
    assert "LOCK_KEY" not in read("ruqqus", "routes", "children_pages.py"), "one copy of the key: the store's"


def test_the_messaging_session_is_chat_and_a_call_space_needs_chat():
    age = read("ruqqus", "helpers", "age_declaration.py")
    assert '"messaging": "chat"' in age and "FEATURE_ANY" not in age
    spaces = read("ruqqus", "routes", "spaces.py")
    for function in ("space_start", "space_join", "space_create"):
        body = spaces.split(f"def {function}(", 1)[1].split("\n@app.", 1)[0]
        assert 'age_declaration.feature_refusal(v, "chat")' in body, function
    assert 'activity_refusal(v, "commenting")' in spaces.split("def space_message_add(", 1)[1].split("\n@app.", 1)[0], "a board and a call's chat are the Space's comments"


def test_editing_is_posting_and_commenting():
    assert 'age_declaration.activity_refusal(v, "posting")' in read("ruqqus", "routes", "posts.py").split("def edit_post(", 1)[1].split("\n@app.", 1)[0]
    assert 'age_declaration.activity_refusal(v, "commenting")' in read("ruqqus", "routes", "comments.py").split("def edit_comment(", 1)[1].split("\n@app.", 1)[0]
    for deleting in (read("ruqqus", "routes", "posts.py").split("def delete_post_pid(", 1)[1].split("\n@app.", 1)[0],
                     read("ruqqus", "routes", "comments.py").split("def delete_comment(", 1)[1].split("\n@app.", 1)[0]):
        assert "activity_refusal" not in deleting, "a child can always take its own words down"


def test_a_locked_account_is_not_put_in_a_circle_a_shared_draft_or_a_byline_and_runs_no_bot():
    circles = read("ruqqus", "routes", "circles.py").split("def circle_add(", 1)[1].split("\n@app.", 1)[0]
    assert 'age_declaration.target_refusal(target, "chat")' in circles and "child_store.linked(g.db, v.id, target.id)" in circles, "a parent may add their own child"
    assert 'age_declaration.target_refusal(candidate, "chat")' in read("ruqqus", "helpers", "collab_rules.py").split("def add_refusal(", 1)[1].split("\ndef ", 1)[0]
    assert 'age_declaration.target_refusal(candidate, "chat")' in read("ruqqus", "helpers", "coauthors.py").split("def refusal(", 1)[1].split("\ndef ", 1)[0]
    collab = read("ruqqus", "routes", "collab.py")
    assert 'age_declaration.activity_refusal(v, "posting")' in collab.split("def _may_type(", 1)[1].split("\n\n\n", 1)[0]
    assert "_may_type(v)" in collab.split("def collab_invite_answer(", 1)[1].split("\n@app.", 1)[0]
    assert "_may_type(v)" in collab.split("def collab_send_op(", 1)[1].split("\n@app.", 1)[0]
    accept = read("ruqqus", "routes", "coauthors.py").split("def coauthor_accept(", 1)[1].split("\n@app.", 1)[0]
    assert 'age_declaration.activity_refusal(v, "posting")' in accept and "private_posting.refusal(v)" in accept
    bots = read("ruqqus", "helpers", "bots.py")
    for function in ("owner_refusal", "self_refusal", "post_refusal"):
        assert "_age_locked(owner)" in bots.split(f"def {function}(", 1)[1].split("\ndef ", 1)[0], function
    assert "bots._age_locked(v)" in read("ruqqus", "routes", "bot_accounts.py").split("def settings_bot_create_key(", 1)[1].split("\n@app.", 1)[0]


def test_switching_money_off_or_stopping_management_stops_what_was_set_up_and_the_jobs_refuse_a_locked_payer():
    pages = read("ruqqus", "routes", "children_pages.py")
    money = pages.split("def _money_off(", 1)[1].split("\ndef ", 1)[0]
    for piece in ("premium_auto_renew = :no", "creator_fund_store.set_opt_in(g.db, child_id, False)", "circle_store.stop_renewals_of(g.db, child_id)"):
        assert piece in money, piece
    assert 'name == "money" and not on' in ROUTES["children_controls"][1] and "_money_off(" in ROUTES["children_controls"][1]
    stop = ROUTES["children_stop"][1]
    assert "_money_off(" in stop and "_sign_out_of_chat(" in stop, "a standalone locked account has no parent to allow money or chat"
    chat_off = pages.split("def _sign_out_of_chat(", 1)[1].split("\ndef ", 1)[0]
    assert "chat_signouts.queue(g.db, child_id, reason)" in chat_off and "chat_devices.sign_out(" not in pages, "the durable queue"
    assert "child_store.money_locked(db, i)" in read("ruqqus", "helpers", "premium.py")
    renew = read("ruqqus", "helpers", "circle_store.py").split("def renew_due(", 1)[1].split("\n# ---", 1)[0]
    assert "child_store.money_locked(db, row.user_id)" in renew and "child_store.money_locked(db, row.owner_id)" in renew


def test_loosening_or_ending_the_parents_authority_asks_for_their_proof_and_the_export_is_the_activity_pages():
    assert "if allow and _wrong_proof(v):" in ROUTES["children_answer"][1]
    stop = ROUTES["children_stop"][1]
    assert stop.index("if _wrong_proof(v):") < stop.index("User.age_lock == age_declaration.PARENT_LOCK")
    template = read("ruqqus", "templates", "settings_children.html")
    assert template.count("{{ proof() }}") == 6      # password, download, delete, stop, and the two answers
    export = read("ruqqus", "helpers", "data_export.py").split("def build_for_parent(", 1)[1].split("\ndef ", 1)[0]
    for piece in ("child_activity.posts(", "child_activity.comments(", "child_activity.votes(", "child_activity.bookmarks(", "child_activity.guilds("):
        assert piece in export, piece
    assert "SECTIONS" not in export, "built from the page's own statements: no word the page leaves out can reach a download"


def test_deleting_a_parent_whose_child_owes_money_is_refused_and_an_unmigrated_database_doesnt_break_deleting_or_settings():
    delete = read("ruqqus", "routes", "settings.py").split("def delete_account(v):", 1)[1].split("\n@app.", 1)[0]
    assert "child_store.owing_children(g.db, v.id)" in delete
    assert "count_children_safely" in read("ruqqus", "helpers", "jinja2.py")


def test_the_suspension_marker_lives_on_the_ledger_in_the_schema_and_the_migration():
    for text in (read("schema.sql"), read("scripts", "migrations", "2026-12-18_parent_accounts.sql")):
        family = text.split("child_family (", 1)[1].split(");", 1)[0]
        assert "suspended_until_utc" in family and "prior_until_utc" in family
    migration = read("scripts", "migrations", "2026-12-18_parent_accounts.sql")
    assert "DROP COLUMN IF EXISTS suspended_until_utc" in migration, "a database that ran the first form"
    schema_controls = read("schema.sql").split("CREATE TABLE public.child_controls (", 1)[1].split(");", 1)[0]
    assert "suspended_until_utc" not in schema_controls
