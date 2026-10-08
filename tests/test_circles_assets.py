"""Circles cross the model, the schema, the routes, the settings pages, the profile, the block route and a supervisord job.
These checks keep the layers agreeing and keep the promises: only the owner changes their own Circle, coins move in
exactly two statements, a block ends a Circle, and no page draws names or prices as HTML."""
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def read(*parts):
    return ROOT.joinpath(*parts).read_text(encoding="utf-8")


ROUTES = read("ruqqus", "routes", "circles.py")
STORE = read("ruqqus", "helpers", "circle_store.py")
MODEL = read("ruqqus", "classes", "circle.py")
SCHEMA = read("schema.sql")
MIGRATION = read("scripts", "migrations", "2026-10-10_circles.sql")
TABLES = {"Circle": "circles", "CircleMember": "circle_members", "CirclePayment": "circle_payments"}


def model_columns(cls):
    body = MODEL.split(f"class {cls}(")[1].split("\nclass ")[0]
    return set(re.findall(r"^    (\w+) = Column\(", body, re.M))


def route(name):
    head = ROUTES.split(f"def {name}(")[0].rsplit("\n\n\n", 1)[1]
    return head + "def " + name + "(" + ROUTES.split(f"def {name}(")[1].split("\n\n\n")[0]


# --- storage ------------------------------------------------------------------------------------------

def test_the_model_the_schema_and_the_migration_have_the_same_columns():
    for sql, header in ((SCHEMA, "CREATE TABLE public."), (MIGRATION, "CREATE TABLE IF NOT EXISTS ")):
        for cls, table in TABLES.items():
            block = re.search(rf"{header}{table} \((.*?)\n\);", sql, re.S).group(1)
            names = {line.split()[0] for line in (x.strip() for x in block.splitlines()) if line and not line.startswith("CONSTRAINT")}
            assert names == model_columns(cls), (header, table)
    assert "from .circle import *" in read("ruqqus", "classes", "__init__.py")
    assert "from .circles import *" in read("ruqqus", "routes", "__init__.py")


def test_the_rules_the_database_enforces_are_the_same_in_schema_and_migration():
    for sql in (SCHEMA, MIGRATION):
        assert "CONSTRAINT circles_one_owner CHECK ((user_id IS NOT NULL) <> (board_id IS NOT NULL))" in sql
        assert "CONSTRAINT circles_price_range CHECK (price_coins >= 0 AND price_coins <= 100)" in sql
        assert "CONSTRAINT circle_members_pair_key UNIQUE (circle_id, user_id)" in sql
        assert "CONSTRAINT circle_members_tier CHECK (tier IN ('friend', 'subscriber'))" in sql
        assert "CONSTRAINT circle_members_status CHECK (status IN ('active', 'ended'))" in sql
        assert "price_coins integer DEFAULT 0 NOT NULL" in sql
    assert "circles_user_key ON public.circles USING btree (user_id) WHERE (user_id IS NOT NULL)" in SCHEMA
    assert "circles_board_key ON public.circles USING btree (board_id) WHERE (board_id IS NOT NULL)" in SCHEMA
    assert "CREATE UNIQUE INDEX IF NOT EXISTS circles_user_key ON circles (user_id) WHERE user_id IS NOT NULL;" in MIGRATION
    assert "PRICE_MAX = 100" in read("ruqqus", "helpers", "circles.py")


# --- who may change what ------------------------------------------------------------------------------

def test_every_action_needs_a_login_and_the_form_key():
    for name in ("settings_circle_price", "circle_friend_add", "circle_friend_remove", "circle_subscribe", "circle_cancel"):
        head = route(name).split("def ")[0]
        assert "@validate_formkey" in head, name
        assert "@is_not_banned" in head or "@auth_required" in head, name
        assert '@app.route(' in head and 'methods=["POST"]' in head, name
    for name in ("settings_circle", "settings_circles"):
        assert "@auth_required" in route(name).split("def ")[0], name


def test_money_moving_routes_refuse_a_negative_balance_account():
    assert '@no_negative_balance("toast")' in route("circle_subscribe").split("def ")[0]


def test_the_owner_is_always_the_signed_in_member_never_a_form_field():
    # a Circle is changed through v.id; the only account named in a request is the OTHER party
    assert re.search(r"request\.values\.get\(\"(owner|owner_id|circle|circle_id|user_id)\"", ROUTES) is None
    assert "circle_store.set_price(g.db, v.id, price)" in route("settings_circle_price")
    assert "circle_store.add_friend(g.db, v.id, target.id)" in route("circle_friend_add")
    assert "circle_store.remove_friend(g.db, v.id, target.id)" in route("circle_friend_remove")
    assert "circle_store.subscribe(g.db, owner.id, v.id)" in route("circle_subscribe")
    assert "circle_store.cancel(g.db, owner.id, v.id)" in route("circle_cancel")


def test_a_payment_flushes_first_refreshes_after_and_only_notifies_on_a_new_subscription():
    body = route("circle_subscribe")
    assert body.index("g.db.flush()") < body.index("circle_store.subscribe(")
    assert body.index("g.db.commit()") < body.index("g.db.refresh(v)")
    assert 'if outcome == "subscribed":' in body and body.count("send_notification(") == 1
    assert "g.db.rollback()" in body.split('if outcome == "refused":')[1].split("g.db.commit()")[0]


def test_an_unknown_or_deleted_account_is_a_404():
    body = ROUTES.split("def _target(")[1].split("\n\n\n")[0]
    assert "user is None or user.is_deleted" in body and "abort(404)" in body


def test_a_block_ends_both_circles():
    block = read("ruqqus", "routes", "settings.py").split("def settings_block_user(")[1].split("\n@app.route")[0]
    assert "circle_store.end_between(g.db, v.id, user.id)" in block
    assert block.index("on_block_created(v, user)") < block.index("circle_store.end_between(")
    # the only place a UserBlock is made
    assert sum(path.read_text(encoding="utf-8").count("UserBlock(") for path in (ROOT / "ruqqus" / "routes").glob("*.py")) == 1


# --- the coins ----------------------------------------------------------------------------------------

def test_coins_move_in_exactly_two_statements_and_the_charge_checks_the_balance_itself():
    statements = re.findall(r'text\(\s*"([^"]*coin_balance[^"]*)"', STORE)
    assert len(statements) == 3                        # the account read and the two updates
    updates = [s for s in statements if s.startswith("UPDATE users")]
    assert updates == ["UPDATE users SET coin_balance = coin_balance - :c WHERE id = :p AND coin_balance >= :c",
                       "UPDATE users SET coin_balance = coin_balance + :c WHERE id = :o"]


def test_the_store_builds_no_sql_from_text_and_commits_only_in_the_renewal_pass():
    assert re.search(r'f"\s*(SELECT|UPDATE|INSERT|DELETE)', STORE) is None
    assert STORE.count(".commit()") == 1 and "db.commit()" in STORE.split("def renew_due(")[1]


def test_renewals_are_at_the_signed_up_price_and_only_a_lapse_is_told():
    renew = STORE.split("def renew_due(")[1]
    assert "circles.renewal(now, row.renews_utc, row.cancelled, balance, row.price_coins)" in renew
    job = read("scripts", "renew_circles.py")
    assert 'if event == "lapsed"' in job and "--once" in job and "circle_store.renew_due(db, now)" in job
    assert "block" not in job.lower().split('"""', 2)[2].replace("a block among them", "")


def test_the_job_runs_as_its_own_supervisord_program():
    conf = read("supervisord.conf")
    assert "[program:ruqquscircles]" in conf and "scripts/renew_circles.py" in conf


# --- the pages ----------------------------------------------------------------------------------------

def test_the_settings_tab_is_in_both_navs_and_the_pages_draw_nothing_as_html():
    nav = read("ruqqus", "templates", "settings.html")
    assert nav.count('href="/settings/circle"') == 2
    for name in ("settings_circle.html", "settings_circles.html", "partials/circle_buttons.html"):
        html = read("ruqqus", "templates", *name.split("/"))
        assert "| safe" not in html, name
        assert "formkey" not in html, name                      # post_toast adds it


def test_the_profile_shows_the_buttons_in_both_blocks_and_the_global_is_registered():
    page = read("ruqqus", "templates", "userpage.html")
    assert '{% from "partials/circle_buttons.html" import circle_buttons %}' in page
    assert "{{ circle_buttons(u, v) }}" in page and "{{ circle_buttons(u, v, small=True) }}" in page
    assert "app.jinja_env.globals.update(circle_status=circle_status)" in ROUTES
    macro = read("ruqqus", "templates", "partials", "circle_buttons.html")
    assert "circle-join" in macro and "circle-friend-add" in macro and "circle-friend-remove" in macro
    # a visitor and the owner of the profile get nothing
    assert "if viewer is None or viewer.id == owner.id:\n        return None" in ROUTES


def test_claude_md_describes_circles():
    doc = read("CLAUDE.md")
    assert "## Circles (legacy app)" in doc
    for needle in ("Close Friends", "coin_balance >= :c", "renews_utc", "signed up at", "ruqquscircles"):
        assert needle in doc, needle
