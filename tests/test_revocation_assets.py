"""The tripwires of the one credential event (helpers/account_sessions.py `revoke`) and the durable chat sign-outs
(helpers/chat_signouts.py): nothing else raises an account's nonce, nothing else deletes a chat device, every credential event goes
through it with the right sparing, the interim of build steps 2 and 3 is over, and a suspension changes nothing
(the owner's decision 3). The behaviour is tests/test_chat_signouts.py (SQLite) and tests/pg/test_chat_signout_routes.py (the app)."""
import ast
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
RUQQUS = ROOT / "ruqqus"


def read(*parts):
    return ROOT.joinpath(*parts).read_text(encoding="utf-8")


def python_files():
    for base in (RUQQUS, ROOT / "scripts"):
        for path in base.rglob("*.py"):
            if "node_modules" not in path.parts:
                yield path


def body_of(source, name):
    tree = ast.parse(source)
    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef) and node.name == name:
            return "\n".join(source.splitlines()[node.lineno - 1:node.end_lineno])
    raise AssertionError(f"no function {name}")


def decorators_of(source, name):
    lines = source.splitlines()
    for node in ast.walk(ast.parse(source)):
        if isinstance(node, ast.FunctionDef) and node.name == name:
            return [lines[d.lineno - 1].strip().split("  #")[0] for d in node.decorator_list]
    raise AssertionError(name)


# --- one place raises the nonce, one place deletes devices ------------------------------------------------------------------

def test_nothing_but_revoke_writes_an_accounts_nonce():
    """Item writes of a session's copy (`flask_session["login_nonce"] = …`) don't count, nor reads; the column itself is excepted."""
    write = re.compile(r"\b(\w+)\.login_nonce\s*(\+=|=(?!=))")
    found = []
    for path in python_files():
        for number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
            for match in write.finditer(line):
                if match.group(1) not in ("session", "flask_session"):
                    found.append((path.relative_to(ROOT).as_posix(), match.group(0)))
    assert found == [("ruqqus/helpers/account_sessions.py", "user.login_nonce =")], found
    sessions = read("ruqqus", "helpers", "account_sessions.py")
    assert "def end_other_sessions" not in sessions and "def _end_other_sessions(user, session=None):" in sessions
    assert "_end_other_sessions(user, session)" in body_of(sessions, "revoke")


def test_nothing_else_deletes_a_chat_device():
    found = sorted(path.relative_to(ROOT).as_posix() for path in python_files()
                   if ".delete_device(" in path.read_text(encoding="utf-8") and path.name != "matrix_client.py")
    assert found == ["ruqqus/helpers/chat_signouts.py", "ruqqus/routes/chat_apps.py"]
    apps = read("ruqqus", "routes", "chat_apps.py")
    assert apps.count(".delete_device(") == 1 and ".delete_device(" in body_of(apps, "chat_device_sign_out")
    assert "def sign_out(" not in read("ruqqus", "helpers", "chat_devices.py")


def test_revoke_queues_in_its_transaction_and_runs_nothing_itself():
    sessions = read("ruqqus", "helpers", "account_sessions.py")
    body = body_of(sessions, "revoke")
    assert "return chat_signouts.this_sign_in(db, user.id, session_id)" in body, "a callable: found by queue under the mint lock"
    assert 'chat_signouts.queue(db, user.id, reason, "all", spared=spared, now_ms=now_ms)' in body
    signouts = read("ruqqus", "helpers", "chat_signouts.py")
    queue = body_of(signouts, "queue")
    assert "matrix" not in queue.split('"""', 2)[2], "no Synapse call inside queue: run_for_user, after the commit"
    assert "lock_account(db, user_id)" in queue and queue.index("lock_account(db, user_id)") < queue.index("_now_ms()")
    assert queue.index("lock_account(db, user_id)") < queue.index("spared = spared()")
    assert "with db.begin_nested():" in queue and "missing_table(e)" in queue
    assert "g.after_commit.append(functools.partial(run_for_user, current_app._get_current_object(), int(user_id)))" in signouts
    assert "pg_advisory_xact_lock(:k, :u)" in signouts and "MINT_LOCK = 7003" in signouts
    one = body_of(signouts, "run_one")
    assert one.index("matrix.list_devices(mxid)") < one.index("FROM chat_site_devices"), "site rows are read after the list"
    assert "FOR UPDATE SKIP LOCKED" in one and "SET LOCAL lock_timeout = '{int(WAIT_LOCK_SECONDS)}s'" in one
    assert "\nWAIT_LOCK_SECONDS = 15 " in signouts


# --- every credential event, and what it spares ----------------------------------------------------------------------------

EVENTS = {
    ("routes", "settings.py"): ['account_sessions.revoke(g.db, v, "password", session=flask_session, spare_this_sign_in=True)',
                                'account_sessions.revoke(g.db, v, "mfa", session=flask_session, spare_this_sign_in=True)',
                                'account_sessions.revoke(g.db, v, "others", session=flask_session, spare_this_sign_in=True)',
                                'account_sessions.revoke(g.db, v, "deleted")'],
    ("routes", "login.py"): ['account_sessions.revoke(g.db, account, "mfa_recovery")', 'account_sessions.revoke(g.db, user, "reset")'],
    ("routes", "children_pages.py"): ['account_sessions.revoke(g.db, row, "child_password")', 'chat_signouts.queue(g.db, child_id, reason)',
                                      '_sign_out_of_chat(child["id"], "child_chat_off")', '_sign_out_of_chat(child["id"], "child_stopped")'],
    ("routes", "age_declaration_pages.py"): ['chat_signouts.queue(g.db, v.id, "child_locked")', 'chat_signouts.queue(g.db, target.id, "child_locked")'],
}


def test_every_credential_event_goes_through_revoke_and_only_the_three_spare_this_sign_in():
    for (folder, name), calls in EVENTS.items():
        source = read("ruqqus", folder, name)
        for call in calls:
            assert call in source, (name, call)
    spared = [line.strip() for path in python_files() for line in path.read_text(encoding="utf-8").splitlines()
              if "spare_this_sign_in=True" in line and "def revoke" not in line]
    assert sorted(spared) == sorted(['account_sessions.revoke(g.db, v, "password", session=flask_session, spare_this_sign_in=True)',
                                     'account_sessions.revoke(g.db, v, "mfa", session=flask_session, spare_this_sign_in=True)',
                                     'account_sessions.revoke(g.db, v, "mfa", session=flask_session, spare_this_sign_in=True)',
                                     'account_sessions.revoke(g.db, v, "others", session=flask_session, spare_this_sign_in=True)']), spared
    settings = read("ruqqus", "routes", "settings.py")
    for name in ("settings_security_post", "settings_log_out_others"):
        assert "account_sessions.revoke(" in body_of(settings, name), name
    login = read("ruqqus", "routes", "login.py")
    two_factor = login[login.index("is_recovery = (not is_2fa"):login.index("# set session and user id")]
    assert two_factor.index('account_sessions.revoke(g.db, account, "mfa_recovery")') < two_factor.index("complete_sign_in(account")


def test_a_newly_locked_account_is_signed_out_only_when_it_became_locked():
    pages = read("ruqqus", "routes", "age_declaration_pages.py")
    for name in ("settings_age_declaration", "admin_age_declaration"):
        assert "age_declaration.newly_locked(was_locked, age_declaration.is_locked(" in body_of(pages, name), name


def test_deleting_an_account_queues_before_its_rows_go_and_never_ends_another_accounts_cookie():
    settings = read("ruqqus", "routes", "settings.py")
    close = body_of(settings, "close_own_account")
    assert close.index('account_sessions.revoke(g.db, v, "deleted")') < close.index("account_cleanup.close_account(")
    assert "v.login_nonce" not in close
    ended = close.index("sign_in_ends.end(")
    assert close.rindex("if sign_out_browser:", 0, ended) > close.index("account_cleanup.close_account(")
    # both pages that say the account is gone name its chat account, read before it closed
    assert 'render_template("account_deleted.html", v=None, deleted_mxid=deleted_mxid, deleted_children=deleted_children,' in body_of(settings, "delete_account")
    delete = body_of(settings, "delete_account")
    assert delete.index("deleted_mxid, deleted_children = chat_devices.closing_with(g.db, v.id)") < delete.index("close_own_account(")
    callback = body_of(read("ruqqus", "routes", "age_declaration_pages.py"), "settings_age_assurance_callback")
    assert callback.index("deleted_mxid, deleted_children = chat_devices.closing_with(g.db, v.id)") < callback.index("close_own_account(")
    assert "deleted_mxid=deleted_mxid, deleted_children=deleted_children" in callback
    assert "forgetChatDevices({{ deleted_mxid | tojson }}, {{ (deleted_children or []) | tojson }})" in read("ruqqus", "templates", "account_deleted.html")
    # a child its parent deletes: the parent's page forgets the child's chat, read before it closed (a family shares a browser)
    children = body_of(read("ruqqus", "routes", "children_pages.py"), "children_delete")
    assert children.index("mxid = chat_devices.mxid_of(g.db, row.id)") < children.index("rules.close_account(row") < children.index("forget_chat=mxid")
    assert "forgetChildChatDevices([{{ forget_chat | tojson }}])" in read("ruqqus", "templates", "settings_children.html")
    closing = body_of(read("ruqqus", "helpers", "chat_devices.py"), "closing_with")
    assert "child_store.child_ids_safely(db, user_id)" in closing


def test_plain_log_out_deletes_no_device():
    logout = body_of(read("ruqqus", "routes", "login.py"), "logout")
    assert "sign_in_ends.end(" in logout
    assert "chat_signouts" not in logout and "account_sessions" not in logout and "delete_device" not in logout


# --- THE INTERIM IS OVER (build step 4) -------------------------------------------------------------------------------------

def test_the_interim_is_gone_and_nothing_writes_its_word():
    """Build step 4 shipped the client that keeps its stores, and with it every `all` sign-out reaches the site's own browsers
    (decision 2's sparing applies). run_one still reads the word, for a row queued before."""
    for path in python_files():
        src = path.read_text(encoding="utf-8")
        assert "KEEP_SITE_BROWSERS" not in src and "interim_spared" not in src, path.name
    signouts = read("ruqqus", "helpers", "chat_signouts.py")
    queue = body_of(signouts, "queue")
    assert "spared = tuple(d for d in spared if d and d != SITE)" in queue, "the word is never written, even when asked"
    assert "if SITE in keep:" in body_of(signouts, "run_one")
    revoke = body_of(read("ruqqus", "helpers", "account_sessions.py"), "revoke")
    assert "SITE" not in revoke and '"site"' not in revoke
    assert "chat_signouts.this_sign_in(" in revoke, "decision 2: at most one chat and one call row of this sign-in, found on the server"


# --- decision 3: a suspension changes nothing ---------------------------------------------------------------------------------

def test_a_suspension_or_ban_signs_no_device_out():
    user = read("ruqqus", "classes", "user.py")
    for name in ("ban", "unban"):
        body = body_of(user, name)
        assert "chat_signouts" not in body and "account_sessions" not in body and "delete_device" not in body, name
    oidc = read("ruqqus", "routes", "oidc_pages.py")
    token = body_of(oidc, "oidc_token")
    assert token.index('_refusal(user, _chat_ready(record["user_id"]))') < token.index("chat_signouts.flush_for_oidc(g.db, matrix, user.id)")
    assert '_token_error("temporarily_unavailable", 503)' in token
    assert "is_suspended" in read("ruqqus", "helpers", "oidc_provider.py"), "/oidc/token keeps refusing a suspended account, as it did"


# --- the routes ---------------------------------------------------------------------------------------------------------------

def test_the_token_route_keeps_its_wrappers_with_no_suspension_check_and_never_logs_an_answer():
    chat = read("ruqqus", "routes", "chat.py")
    for route, path, limit in (("chat_token", "/api/chat/token", '"60/minute;600/hour"'), ("chat_store_keys_route", "/api/chat/store_keys", '"20/minute"')):
        assert decorators_of(chat, route) == [f'@app.route("{path}", methods=["POST"])', "@auth_required", "@not_qr_limited",
                                              '@not_child_locked("messaging")', "@validate_formkey",
                                              f"@limiter.limit({limit}, key_func=member_limit_key)"], route
        body = body_of(chat, route)
        assert body.index("chat_signouts.tables_ready(g.db)") < body.index("chat_tokens."), "no Matrix call or key before the tables are known"
        assert "suspend" not in body and "is_banned" not in body, "decision 3: a suspension changes nothing"
        assert "chat_store_keys.secret_of(app.config)" in body
    body = body_of(chat, "chat_token")
    assert body.index("chat_signouts.tables_ready(g.db)") < body.index("_ensure_provisioned(v)"), "no Matrix call before the tables are known"
    assert "_ensure_provisioned" not in body_of(chat, "chat_store_keys_route"), "asking for keys makes no chat account"
    assert "CHAT_EXPIRING_TOKENS" not in chat and 'report_seconds=app.config["CHAT_TOKEN_REPORT_SECONDS"]' in body
    tokens = read("ruqqus", "helpers", "chat_tokens.py")
    assert "print(" not in tokens and "logging" not in tokens and "logger" not in tokens
    answer = body_of(tokens, "answer")
    assert "refresh_token" not in answer and '"refresh' not in answer, "a refresh token is never in an answer"
    mint = body_of(tokens, "mint")
    assert mint.index("chat_signouts.lock_account(db, user_id)") < mint.index("INSERT INTO chat_site_devices") < mint.index("db.commit()\n                device_id = candidate") < mint.index("self.matrix.login_as(self.mxid, device_id)")
    assert "AND revoked_utc = 0" in mint.split("self.matrix.login_as(", 1)[1]
    # the account read again under the lock, before the row (a deletion or chat switched off the request waited for), and a failed mint
    # leaves the row to queue, which takes the lock first: never a row lock before the lock
    assert mint.index("chat_signouts.lock_account(db, user_id)") < mint.index("_refusal(db, user_id, self.nonce)") < mint.index("INSERT INTO chat_site_devices")
    assert "revoke_rows" not in mint
    # every renewal tries /refresh first, and every login goes through the list check: never login_as on a device Synapse no longer lists
    token_for = body_of(tokens, "token_for")
    assert token_for.index("self.matrix.refresh(") < token_for.index("self.matrix.list_devices(self.mxid)") < token_for.index("self.matrix.login_as(self.mxid, device_id)")
    # a key-only start only for a row that holds a refresh token: a device made before tokens expired is re-tokened at its next start
    assert 'req.mode == "boot" and req.want == "key" and row[3]:' in body_of(tokens, "run")
    assert "expiring" not in tokens
    assert tokens.count("matrix.login_as(") == 2 and tokens.count("matrix.refresh(") == 1
    assert 'nonce=flask_session.get("login_nonce", 0)' in body
    client = read("ruqqus", "helpers", "matrix_client.py")
    for name in ("login_as", "list_devices", "delete_device", "refresh"):
        assert f'with _answering("{name}"):' in body_of(client, name), "a Synapse that doesn't answer is a MatrixError too"
    login = body_of(client, "login_as")
    assert 'body["refresh_token"] = True' in login and "if refresh" not in login and "refresh:" not in login.split('"""')[0], \
        "every site login asks for a refresh token: every site token expires"
    assert "print(" not in body_of(client, "refresh") and "headers=" not in body_of(client, "refresh"), "no answer logged, no appservice token sent"


def test_the_chat_token_settings_are_plumbed_and_the_report_setting_is_never_set():
    main = read("ruqqus", "__main__.py")
    for name in ("CHAT_STORE_SECRET", "CHAT_TOKEN_REPORT_SECONDS"):
        assert f'app.config["{name}"]' in main and f'environ.get("{name}"' in main
        assert f"- {name}=${{{name}:-" in read("docker-compose.yml")
    assert '- CHAT_TOKEN_REPORT_SECONDS=${CHAT_TOKEN_REPORT_SECONDS:-}' in read("docker-compose.yml")
    example = read(".env.example")
    assert "CHAT_TOKEN_REPORT_SECONDS" not in example, "dev and specs only: never in a file a live site copies"
    # build step 5: every site token expires, so there is no switch for it anywhere (login_as always asks for a refresh token)
    for where in (main, read("docker-compose.yml"), example, read("ruqqus", "routes", "chat.py"), read("ruqqus", "helpers", "chat_tokens.py"),
                  read("scripts", "gen_dev_env.py")):
        assert "CHAT_EXPIRING_TOKENS" not in where
    assert "\n#CHAT_STORE_SECRET=" in example and "\nCHAT_STORE_SECRET=" not in example
    assert '"CHAT_STORE_SECRET": lambda: secrets.token_hex(32)' in read("scripts", "gen_dev_env.py")


def test_the_form_key_route_is_signed_in_and_never_stored():
    chat = read("ruqqus", "routes", "chat.py")
    stack = decorators_of(chat, "chat_formkey")
    assert stack[0] == '@app.route("/api/chat/formkey", methods=["GET"])' and "@auth_required" in stack
    body = body_of(chat, "chat_formkey")
    assert 'resp.headers["Cache-Control"] = "no-store"' in body and '{"formkey": v.formkey, "mxid": chat_devices.mxid_of(g.db, v.id)}' in body


def test_the_admin_page_shows_the_queue_and_the_missing_migration():
    page = read("ruqqus", "templates", "admin", "chat_health.html")
    assert 'id="chat-signouts-missing"' in page and 'id="chat-signouts-late"' in page and 'id="chat-signouts-pending"' in page
    assert "signouts=chat_signouts.pending_summary(g.db)" in read("ruqqus", "routes", "chat_health_pages.py")
    recomputes = read("scripts", "recomputes.py")
    assert recomputes.index("chat_health.run(db") < recomputes.index("chat_signouts.run_due(db, signout_matrix)")


def test_the_migration_is_mirrored_and_its_rows_kept_and_forgotten_as_planned():
    migration = read("scripts", "migrations", "2026-12-20_chat_sign_outs.sql")
    schema = read("schema.sql")
    for table in ("chat_site_devices", "chat_device_signouts"):
        assert f"CREATE TABLE IF NOT EXISTS {table} (" in migration and f"CREATE TABLE public.{table} (" in schema
    assert "REFERENCES" not in migration and "('2026-12-20_chat_sign_outs.sql')" in schema
    cleanup = read("ruqqus", "helpers", "account_cleanup.py")
    assert "DELETE FROM chat_site_devices WHERE user_id = :u" in cleanup and "chat_device_signouts" not in cleanup.split("STEPS = (", 1)[1].split("\n)", 1)[0].replace("(chat_device_signouts)", "")
    export = read("ruqqus", "helpers", "data_export.py")
    assert '("chat_devices", "SELECT count(*) AS chat_devices' in export and "device_id" not in export.split('("chat_devices"', 1)[1].split("),", 1)[0]
    assert "DELETE FROM chat_device_signouts WHERE done_utc > 0 AND done_utc < :chat_signouts" in read("ruqqus", "helpers", "retention.py")


# --- the browser's side (build step 4b) ---------------------------------------------------------------------------------------

CHAT_SRC = RUQQUS / "assets" / "chat_src" / "src"


def chat_sources():
    return {path.name: path.read_text(encoding="utf-8") for path in CHAT_SRC.rglob("*.js")}


def test_every_chat_client_is_made_by_make_client_and_every_store_opened_with_its_key():
    for name, text in chat_sources().items():
        if name == "keeper.js":
            continue
        assert "sdk.createClient(" not in text and "createClient(" not in text.replace("makeClient(", ""), name
        assert ".initRustCrypto(" not in text or name == "token.js", name
    keeper = read("ruqqus", "assets", "chat_src", "src", "keeper.js")
    make = keeper[keeper.index("export function makeClient("):]
    assert "fetchFn: keeper.fetchFn," in make and "store: new sdk.MemoryStore()," in make
    assert make.index("store: new sdk.MemoryStore(),") < make.index("...extra,")            # a page can't swap the fetch or the store
    assert make.index("fetchFn: keeper.fetchFn,") < make.index("...extra,")
    token = read("ruqqus", "assets", "chat_src", "src", "token.js")
    assert token.count(".initRustCrypto(") == 1
    assert "client.initRustCrypto({ storageKey: session.storeKey, cryptoDatabasePrefix: session.prefix })" in token


def test_nothing_in_the_client_wipes_a_store_but_the_listed_cases():
    for name, text in chat_sources().items():
        assert "clearStores" not in text and "forgetDevice" not in text, name
        if name != "token.js":
            assert "deleteDatabase" not in text, f"{name}: stores are deleted only by token.js, in the plan's 2.4 cases"
    signed_out = read("ruqqus", "assets", "chat_src", "src", "signed_out.js")
    assert "removeItem" not in signed_out and "localStorage" not in signed_out and "from \"matrix-js-sdk\"" not in signed_out
    keeper = read("ruqqus", "assets", "chat_src", "src", "keeper.js")
    assert "removeItem(" not in keeper and "deleteStore" not in keeper and "from \"matrix-js-sdk\"" not in keeper
