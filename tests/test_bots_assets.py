"""Bot accounts cross the schema, the model, login, the post, comment, vote, tip and chat routes, the guild limit, the
creator fund, the profile, Settings > Bots, the admins' queue and the runner. These checks keep them agreeing; the
rules are tests/test_bots.py, the SQL tests/test_bot_store.py, the runner tests/test_run_bots.py."""
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def read(*parts):
    return ROOT.joinpath(*parts).read_text(encoding="utf-8")


def func(source, name, nxt="\n\n\n"):
    return source.split(f"def {name}(")[1].split(nxt)[0]


def head(source, name):
    return source.split(f"def {name}(")[0].rsplit("\n\n\n", 1)[1]


ROUTES = read("ruqqus", "routes", "bot_accounts.py")
POSTS = read("ruqqus", "routes", "posts.py")
USER = read("ruqqus", "classes", "user.py")
MIGRATION = read("scripts", "migrations", "2026-10-15_bots.sql")
SCHEMA = read("schema.sql")


# --- storage -------------------------------------------------------------------------------------------------

def test_the_migration_the_schema_and_the_model_agree():
    for column in ("is_bot boolean DEFAULT false NOT NULL", "bot_owner_id integer"):
        assert column in MIGRATION and column in SCHEMA
    assert "is_bot = Column(Boolean, default=False)" in USER and "bot_owner_id = Column(Integer, default=None)" in USER
    for table in ("bots", "bot_sources", "bot_seen"):
        assert f"CREATE TABLE IF NOT EXISTS {table} (" in MIGRATION and f"CREATE TABLE public.{table} (" in SCHEMA
    for column in re.findall(r"^    ([a-z_]+) ", MIGRATION.split("CREATE TABLE IF NOT EXISTS bots (")[1].split(");")[0], re.M):
        assert column in read("ruqqus", "helpers", "bot_store.py"), column


# --- nobody logs into a bot, and a bot never comments, votes, chats, takes tips or leads a guild ---------------

def test_login_refuses_a_bot_before_any_password_is_checked():
    login = func(read("ruqqus", "routes", "login.py"), "login_post", "\n# ")
    assert login.index("if account.is_bot:") < login.index("account.verifyPass(")
    assert 'UNUSABLE_PASSWORD = "!"' in read("ruqqus", "helpers", "bots.py")
    assert "passhash=bots.UNUSABLE_PASSWORD" in func(ROUTES, "settings_bots_apply")


def test_comments_votes_tips_and_chat_refuse_a_bot_account_whatever_it_sends():
    assert 'request.headers.get("X-User-Type", "").lower() == "bot" or v.is_bot:' in read("ruqqus", "routes", "comments.py")
    assert read("ruqqus", "routes", "votes.py").count('=="bot" or v.is_bot:') == 2
    paypal = read("ruqqus", "routes", "paypal.py")
    assert "if post.author.is_bot:" in func(paypal, "gift_post_pid", "\n@app.route")
    assert "if comment.author.is_bot:" in func(paypal, "gift_comment_pid", "\n@app.route")
    assert read("ruqqus", "templates", "partials", "post_actions.html").count("not author_of(p, v).is_bot") == 2
    assert "if target.is_bot or v.is_bot:" in func(read("ruqqus", "routes", "chat.py"), "chat_start")
    assert "COALESCE(a.is_bot, :no) = :no" in read("ruqqus", "helpers", "creator_fund_store.py")
    assert "if self.is_bot:\n            return False" in func(USER, "can_join_gms", "\n    def ")
    assert 'if self.is_bot:\n            return "person"' in func(USER, "gm_limit_refusal", "\n    @property")


# --- posting -------------------------------------------------------------------------------------------------

def test_every_bot_post_asks_the_bot_rules_and_is_marked_as_a_bot_post():
    submit = POSTS.split("def submit_post(")[1].split("\n@app.get(\"/composer\")")[0]
    assert submit.index("if v.is_bot:") < submit.index("new_post = Submission(")
    assert "bot_error, bot_wait = _bot_refusal(v)" in submit and '"bot_refusal": "wait" if bot_wait else "stop"' in submit
    assert "bot_rules.option_refusal(" in submit
    assert 'is_bot = request.headers.get("X-User-Type","").lower()=="bot" or bool(v.is_bot),' in submit
    assert "bot_store.count_post(g.db, v.id, bot_rules.utc_day())" in submit
    assert submit.index("bot_store.count_post(") > submit.index("g.db.add(new_post)")
    refusal = func(POSTS, "_bot_refusal")
    assert "bot_rules.post_refusal(bot, owner, today)" in refusal


def test_guilds_that_take_no_bots_ask_the_account_too():
    assert 'X-User-Type")=="Bot"' not in POSTS                # the old case-sensitive check let "bot" through
    assert POSTS.count('.lower() == "bot" or v.is_bot') == 1
    assert 'primary.is_bot or v.is_bot' in POSTS and "comment.is_bot or v.is_bot" in read("ruqqus", "routes", "comments.py")


# --- the runner ----------------------------------------------------------------------------------------------

def test_the_runner_posts_through_the_real_route_as_the_bot_from_its_own_address():
    runner = read("scripts", "run_bots.py")
    body = func(runner, "submit")
    assert 'client.post("/api/vue/submit"' in body and '"X-User-Type": "Bot"' in body
    assert 'environ_base={"REMOTE_ADDR": bots.loopback_for(user_id)}' in body and '"no_repost": "1"' in body
    assert "submit_post" not in runner.split('"""', 2)[2]     # it never repeats the route's logic
    assert "if not bots.enabled():" in func(runner, "tick")
    conf = read("supervisord.conf")
    assert "[program:ruqqusbots]" in conf and "command=/opt/ruqqus/service/venv/bin/python scripts/run_bots.py" in conf
    assert "BOTS_ENABLED=1" in read(".env.example") and "BOTS_ENABLED=${BOTS_ENABLED:-1}" in read("docker-compose.yml")


def test_feeds_are_only_fetched_through_the_guard_and_parsed_safely():
    fetch = read("ruqqus", "helpers", "feed_fetch.py")
    once = func(fetch, "_once")
    assert once.index("safe_fetch.check_url(") < once.index("safe_fetch.vetted_address(") < once.index("safe_fetch._Pinned(")
    assert "_not_this_site(host)" in once
    for banned in ("import requests", "urllib.request", "urlopen"):
        assert banned not in fetch
    items = read("ruqqus", "helpers", "feed_items.py")
    assert "from defusedxml import ElementTree" in items and "xml.etree" not in items.replace("defusedxml", "")
    assert "defusedxml==" in read("requirements.txt") and "defusedxml>=" in read("requirements-dev.txt")


# --- pages ---------------------------------------------------------------------------------------------------

def test_every_change_is_a_post_with_the_form_key_and_admins_decide():
    for name in re.findall(r"@app\.post\([^)]*\)\n(?:@[^\n]+\n)*def (\w+)\(", ROUTES):
        decorators = head(ROUTES, name)
        assert "@validate_formkey" in decorators, name
        if name.startswith("admin_"):
            assert "@admin_level_required(3)" in decorators, name
        else:
            assert "@auth_required" in decorators or "@is_not_banned" in decorators, name
            if name != "settings_bots_apply":
                assert "account, row = _owned(name, v)" in func(ROUTES, name), name
    assert "@admin_level_required(3)" in head(ROUTES, "admin_bots")
    owned = func(ROUTES, "_owned")
    assert "row.owner_id != v.id" in owned and "abort(404)" in owned


def test_a_member_can_not_resume_what_an_admin_paused():
    assert 'only_own = "" if by_admin else " AND paused_by_admin = :no"' in read("ruqqus", "helpers", "bot_store.py")
    assert "set_running(g.db, account.id, True)" in func(ROUTES, "settings_bot_resume")
    assert "by_admin=True" in func(ROUTES, "admin_bot_pause") and "by_admin=True" in func(ROUTES, "admin_bot_resume")


def test_a_bot_profile_says_who_runs_it_and_is_hidden_until_approved():
    header = read("ruqqus", "templates", "partials", "profile_header.html")
    assert "Automated{% if bot_owner and not bot_owner.is_deleted %} by" in header
    assert "{% if not u.is_bot %}" in header.split('action="/api/chat/start"')[0].rsplit("{% elif v %}", 1)[1]
    assert "if not u.bot_shown_to(v):\n        abort(404)" in func(read("ruqqus", "routes", "users.py"), "u_username")
    shown = func(USER, "bot_shown_to", "\n    def ")
    assert '("approved", "paused")' in shown and "v.id == self.bot_owner_id" in shown


def test_the_settings_nav_the_admin_page_and_the_help_page_link_up():
    nav = read("ruqqus", "templates", "settings.html")
    assert nav.count('href="/settings/bots"') == 2
    assert "/admin/bots" in read("ruqqus", "templates", "admin", "admin_home.html")
    assert "/help/bots" in read("ruqqus", "templates", "settings_bots.html")
    helpage = read("ruqqus", "templates", "help", "bots.html")
    for limit in ("MAX_BOTS", "MAX_SOURCES", "DAILY_CAP", "FIRST_READ", "PER_RUN"):
        assert f"bot_rules.{limit}" in helpage, limit
    assert "bot_rules=bots" in ROUTES
    assert "from .bot_accounts import *" in read("ruqqus", "routes", "__init__.py")


def test_templates_draw_text_escaped():
    for name in ("settings_bots.html", "settings_bot.html", ("admin", "bots.html"), ("help", "bots.html")):
        source = read("ruqqus", "templates", *(name if isinstance(name, tuple) else (name,)))
        assert "| safe" not in source and "|safe" not in source


def test_claude_md_describes_bot_accounts():
    claude = read("CLAUDE.md")
    assert "## Bot accounts (legacy app)" in claude
    section = claude.split("## Bot accounts (legacy app)")[1].split("\n## ")[0]
    for part in ("helpers/bots.py", "bot_store", "run_bots.py", "feed_fetch", "defusedxml", "Automated by @owner",
                 "/admin/bots", "loopback_for", "BOTS_ENABLED"):
        assert part in section, part
