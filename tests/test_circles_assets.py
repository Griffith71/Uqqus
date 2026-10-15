"""Circles cross the model, the schema, the routes, the settings pages, the profile, the block route and a supervisord job.
These checks keep the layers agreeing and keep the promises: only the owner changes their own circles, coins move in
exactly two statements, a block ends it all, nobody is shown the custom circles they were put in, and no page draws
names or prices as HTML."""
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def read(*parts):
    return ROOT.joinpath(*parts).read_text(encoding="utf-8")


ROUTES = read("ruqqus", "routes", "circles.py")
STORE = read("ruqqus", "helpers", "circle_store.py")
MODEL = read("ruqqus", "classes", "circle.py")
SCHEMA = read("schema.sql")
FIRST = read("scripts", "migrations", "2026-10-10_circles.sql")
MANY = read("scripts", "migrations", "2026-10-20_many_circles.sql")
TABLES = {"Circle": "circles", "CircleMember": "circle_members", "CirclePayment": "circle_payments"}


def model_columns(cls):
    body = MODEL.split(f"class {cls}(")[1].split("\nclass ")[0]
    return set(re.findall(r"^    (\w+) = Column\(", body, re.M))


def table_columns(sql, header, table):
    block = re.search(rf"{header}{table} \((.*?)\n\);", sql, re.S).group(1)
    return {line.split()[0] for line in (x.strip() for x in block.splitlines())
            if line and not line.startswith(("CONSTRAINT", "PRIMARY KEY"))}


def route(name):
    head = ROUTES.split(f"def {name}(")[0].rsplit("\n\n\n", 1)[1]
    return head + "def " + name + "(" + ROUTES.split(f"def {name}(")[1].split("\n\n\n")[0]


OWNER_ACTIONS = ("circle_create", "circle_rename", "circle_delete", "circle_price", "circle_add", "circle_remove")
MEMBER_ACTIONS = ("circle_subscribe", "circle_cancel")


# --- storage ------------------------------------------------------------------------------------------

def test_the_model_the_schema_and_the_migrations_have_the_same_columns():
    for cls, table in TABLES.items():
        assert table_columns(SCHEMA, "CREATE TABLE public.", table) == model_columns(cls), table
    added = set(re.findall(r"ALTER TABLE circles ADD COLUMN IF NOT EXISTS (\w+) ", MANY))
    assert table_columns(FIRST, "CREATE TABLE IF NOT EXISTS ", "circles") | added == model_columns("Circle")
    for table in ("post_circles", "story_circles"):
        assert table_columns(SCHEMA, "CREATE TABLE public.", table) == table_columns(MANY, "CREATE TABLE IF NOT EXISTS ", table)
    assert "from .circle import *" in read("ruqqus", "classes", "__init__.py")
    assert "from .circles import *" in read("ruqqus", "routes", "__init__.py")


def test_the_rules_the_database_enforces_are_the_same_in_schema_and_migration():
    for needle in ("CONSTRAINT circles_one_owner CHECK ((user_id IS NOT NULL) <> (board_id IS NOT NULL))",
                   "CONSTRAINT circles_price_range CHECK (price_coins >= 0 AND price_coins <= 100)",
                   "CONSTRAINT circle_members_pair_key UNIQUE (circle_id, user_id)",
                   "CONSTRAINT circle_members_status CHECK (status IN ('active', 'ended'))"):
        assert needle in SCHEMA and needle in FIRST, needle
    for rule in ("circle_members_tier CHECK (tier IN ('member', 'subscriber'))",
                 "circles_kind CHECK (kind IN ('friends', 'custom', 'paid', 'guild'))",
                 "circles_kind_owner CHECK ((kind = 'guild') = (board_id IS NOT NULL))",
                 "stories_audience CHECK (audience IN (0, 4))"):
        assert rule in SCHEMA and rule in MANY, rule
    for index in ("circles_friends_key ON ", "circles_name_key ON ", "circles_user_idx ON ", "post_circles_circle_idx ON ",
                  "story_circles_circle_idx ON "):
        assert index in SCHEMA and index in MANY, index
    assert "circles_user_key" not in SCHEMA                                   # many circles per account now
    assert "kind character varying(12) NOT NULL," in SCHEMA                  # no default: every insert names its kind
    assert "circle_id integer REFERENCES public.circles(id) ON DELETE SET NULL" in SCHEMA   # payments outlive a circle
    assert "PRICE_MAX = 100" in read("ruqqus", "helpers", "circles.py")


def test_every_insert_into_circles_names_its_kind():
    for statement in re.findall(r"INSERT INTO circles \(([^)]*)\)", STORE):
        assert "kind" in statement, statement


# --- who may change what ------------------------------------------------------------------------------

def test_every_action_needs_a_login_and_the_form_key():
    for name in OWNER_ACTIONS + MEMBER_ACTIONS:
        head = route(name).split("def ")[0]
        assert "@validate_formkey" in head, name
        assert "@is_not_banned" in head or "@auth_required" in head, name
        assert '@app.route(' in head and 'methods=["POST"]' in head, name
    for name in ("settings_circle", "settings_circle_one", "settings_circles", "circle_choices_for"):
        assert "@auth_required" in route(name).split("def ")[0], name


def test_money_moving_routes_refuse_a_negative_balance_account():
    assert '@no_negative_balance("toast")' in route("circle_subscribe").split("def ")[0]


def test_the_owner_is_always_the_signed_in_member_never_a_form_field():
    # an owner's circle is named by the address and checked to be theirs (_mine); a form never names an owner or a circle
    assert re.search(r"request\.values\.get\(\"(owner|owner_id|circle|circle_id|user_id)\"", ROUTES) is None
    assert "row = circle_store.owned(g.db, v.id, cid, kinds=kinds)" in ROUTES.split("def _mine(")[1].split("\n\n\n")[0]
    for name in OWNER_ACTIONS[1:]:
        assert "_mine(v, cid" in route(name), name
    assert "circle_store.create(g.db, v.id, kind, name, price)" in route("circle_create")
    assert "circle_store.rename(g.db, v.id, cid, name)" in route("circle_rename")
    assert "circle_store.delete(g.db, v.id, cid)" in route("circle_delete")
    assert "circle_store.set_price(g.db, v.id, cid, price)" in route("circle_price")
    assert "circle_store.add_member(g.db, v.id, row.id, target.id)" in route("circle_add")
    assert "circle_store.remove_member(g.db, v.id, row.id, target.id)" in route("circle_remove")
    # a member only ever changes their own subscription
    assert "circle_store.subscribe(g.db, row.id, v.id)" in route("circle_subscribe")
    assert "circle_store.cancel(g.db, row.id, v.id)" in route("circle_cancel")


def test_a_paid_circle_needs_premium_and_is_checked_without_spending_a_coin():
    body = route("circle_create")
    assert "v.has_premium_no_renew" in body and "v.has_premium " not in body
    assert "circles.refusal_to_make(kind, count=circle_store.count_kind(g.db, v.id, kind), has_premium=has_premium)" in body


def test_a_payment_flushes_first_refreshes_after_and_only_notifies_on_a_new_subscription():
    body = route("circle_subscribe")
    assert body.index("g.db.flush()") < body.index("circle_store.subscribe(")
    assert body.index("g.db.commit()") < body.index("g.db.refresh(v)")
    assert 'if outcome == "subscribed":' in body and body.count("send_notification(") == 1
    assert "g.db.rollback()" in body.split('if outcome == "refused":')[1].split("g.db.commit()")[0]
    assert "circles.plain_name(row.name)" in body                           # a member-written name in markdown


def test_nobody_is_told_they_were_put_in_a_custom_circle():
    for name in ("circle_add", "circle_remove", "circle_create", "circle_rename"):
        assert "send_notification(" not in route(name), name
    lookup = route("circle_choices_for")
    assert "circle_store.custom_circles_with(g.db, v.id, target.id)" in lookup and "private, no-store" in lookup
    assert "kind = 'paid'" in STORE.split("def memberships(")[1].split("\n\n\n")[0]


def test_an_unknown_or_deleted_account_or_circle_is_a_404():
    body = ROUTES.split("def _target(")[1].split("\n\n\n")[0]
    assert "user is None or user.is_deleted" in body and "abort(404)" in body
    paid = ROUTES.split("def _paid_circle(")[1].split("\n\n\n")[0]
    assert "row.kind != circles.PAID" in paid and "owner is None or owner.is_deleted" in paid


def test_a_block_ends_both_ways():
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
    assert re.search(r"\.format\(", STORE) is None
    assert STORE.count(".commit()") == 1 and "db.commit()" in STORE.split("def renew_due(")[1]


def test_renewals_are_at_the_signed_up_price_and_only_a_lapse_is_told():
    renew = STORE.split("def renew_due(")[1]
    assert "circles.renewal(now, row.renews_utc, row.cancelled, balance, row.price_coins)" in renew
    job = read("scripts", "renew_circles.py")
    assert 'if event == "lapsed"' in job and "--once" in job and "circle_store.renew_due(db, now)" in job
    assert "circles.plain_name(circle_name)" in job
    assert "block" not in job.lower().split('"""', 2)[2].replace("a block among them", "")


def test_the_job_runs_as_its_own_supervisord_program():
    conf = read("supervisord.conf")
    assert "[program:ruqquscircles]" in conf and "scripts/renew_circles.py" in conf


# --- the pages ----------------------------------------------------------------------------------------

def test_the_settings_tab_is_in_both_navs_and_the_pages_draw_nothing_as_html():
    nav = read("ruqqus", "templates", "settings.html")
    assert nav.count('href="/settings/circle"') == 2 and nav.count("request.path.startswith('/settings/circle')") == 2
    for name in ("settings_circle.html", "settings_circle_one.html", "settings_circles.html"):
        html = read("ruqqus", "templates", name)
        assert "| safe" not in html, name
        assert "formkey" not in html, name                      # circle_settings.js adds it
        assert "asset_url('js/circle_settings.js')" in html, name
    script = read("ruqqus", "assets", "js", "circle_settings.js")
    assert "innerHTML" not in script and "textContent" in script and "formkey()" in script


def test_the_profile_offers_paid_circles_and_add_to_circles_and_never_says_you_were_added():
    header = read("ruqqus", "templates", "partials", "profile_header.html")
    assert '{% include "partials/profile_header.html" %}' in read("ruqqus", "templates", "userpage.html")
    assert "{% set st = circle_status(u, v) %}" in header
    assert "app.jinja_env.globals.update(circle_status=circle_status, circle_choices=circle_choices)" in ROUTES
    assert "circle-join" in header and "/api/circles/{{ c.id }}/subscribe" in header
    assert 'data-circle-member="{{ u.username }}"' in header and '{% include "partials/circle_member_modal.html" %}' in header
    for gone in ("In their Circle", "circle-friend-add", "you_added", "Close Friends"):
        assert gone not in header, gone
    status = ROUTES.split("def circle_status(")[1].split("\n\n\n")[0]
    assert "if viewer is None or viewer.id == owner.id:\n        return None" in status
    assert "paid_circles(" in status and "custom" not in status.split('"""', 2)[2]
    sheet = read("ruqqus", "assets", "js", "circle_members.js")
    assert "innerHTML" not in sheet and "textContent = circle.name" in sheet


def test_claude_md_describes_circles():
    doc = read("CLAUDE.md")
    section = doc.split("## Circles (legacy app)")[1].split("\n## ")[0]
    for needle in ("Friends", "custom", "paid", "Premium", "coin_balance >= :c", "renews_utc", "signed up at",
                   "ruqquscircles", "post_circles", "Nobody is told they were put in a custom circle", "2026-10-20_many_circles.sql"):
        assert needle in section, needle
