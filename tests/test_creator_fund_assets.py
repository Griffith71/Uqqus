"""The creator fund (helpers/creator_fund.py, helpers/creator_fund_store.py, routes/creator_fund.py): never tied to
ads, earnings never public, only top admins pay, a month paid once, and the schema agrees with the migration."""
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
FUND = ("ruqqus/helpers/creator_fund.py", "ruqqus/helpers/creator_fund_store.py", "ruqqus/routes/creator_fund.py",
        "ruqqus/templates/settings_creator_fund.html", "ruqqus/templates/admin/creator_fund.html")


def read(path):
    return (ROOT / path).read_text(encoding="utf-8")


def code(path):
    """The file without its comments and docstrings (they explain why ads must stay out)."""
    text = re.sub(r'"""[\s\S]*?"""', "", read(path))
    text = re.sub(r"\{#[\s\S]*?#\}", "", text)
    return "\n".join(line.split("#")[0] for line in text.splitlines())


def test_the_fund_never_reads_anything_about_ads():
    for path in FUND:
        body = code(path).lower()
        for word in ("adsense", r"helpers\.ads", "import ads", "ad_slot", r"ads\.config", "revenue", "impressions?", "clicks?"):
            assert not re.search(rf"\b{word}\b", body), (path, word)
    for path in ("ruqqus/helpers/creator_fund_store.py",):
        sql = code(path)
        assert "post_view_days" not in sql and "view_history" not in sql       # views never earn


def test_only_level_four_admins_pay_and_every_change_needs_the_form_key():
    routes = read("ruqqus/routes/creator_fund.py")
    assert "ADMIN_LEVEL = 4" in routes
    for name in ("admin_creator_fund", "admin_creator_fund_pay", "admin_creator_fund_release"):
        head = routes.split(f"def {name}(")[0].rsplit("@app.", 1)[1]
        assert "@admin_level_required(ADMIN_LEVEL)" in head, name
    for name in ("admin_creator_fund_pay", "admin_creator_fund_release", "settings_creator_fund_save"):
        head = routes.split(f"def {name}(")[0].rsplit("@app.", 1)[1]
        assert head.startswith("post(") and "@validate_formkey" in head, name
    assert "from .creator_fund import *" in read("ruqqus/routes/__init__.py")


def test_payment_commits_before_the_notifications():
    pay = read("ruqqus/routes/creator_fund.py").split("def admin_creator_fund_pay(")[1].split("\n@app.")[0]
    assert pay.index("g.db.flush()") < pay.index("store.pay(") < pay.index("g.db.commit()") < pay.index("send_notification(")
    assert "except store.AlreadyPaid:" in pay


def test_a_month_and_a_held_payout_are_paid_once():
    store = read("ruqqus/helpers/creator_fund_store.py")
    assert "ON CONFLICT (month) DO NOTHING" in store and "if inserted.rowcount != 1:" in store
    assert "WHERE id = :id AND status = 'held'" in store and "if done.rowcount != 1:" in store


def test_earnings_are_shown_only_to_the_member_and_to_admins():
    routes = read("ruqqus/routes/creator_fund.py")
    assert "payouts_of_member(g.db, v.id)" in routes and "member=user_id" in routes
    assert routes.count("payouts_of_month(") == 1 and routes.count("store.preview(") == 1
    for path in ROOT.joinpath("ruqqus", "templates").rglob("*.html"):
        rel = str(path.relative_to(ROOT))
        if rel not in FUND:
            text = path.read_text(encoding="utf-8")
            assert "payouts" not in text or "creator" not in text, rel


def test_schema_and_migration_agree():
    migration, schema = read("scripts/migrations/2026-10-12_creator_fund.sql"), read("schema.sql")
    assert "ALTER TABLE users ADD COLUMN IF NOT EXISTS creator_fund_opt_in_utc integer NOT NULL DEFAULT 0;" in migration
    assert "    creator_fund_opt_in_utc integer DEFAULT 0 NOT NULL\n);" in schema
    for table in ("creator_fund_months", "creator_fund_payouts"):
        body_m = re.search(rf"CREATE TABLE IF NOT EXISTS public\.{table} \((.*?)\n\);", migration, re.S).group(1)
        body_s = re.search(rf"CREATE TABLE public\.{table} \((.*?)\n\);", schema, re.S).group(1)
        assert body_m == body_s, table
    assert "CONSTRAINT creator_fund_months_month_key UNIQUE (month)" in schema
    assert "CONSTRAINT creator_fund_payouts_pair_key UNIQUE (month, user_id)" in schema
    assert "creator_fund_opt_in_utc = Column(Integer, default=0)" in read("ruqqus/classes/user.py")


def test_settings_page_and_terms():
    page = read("ruqqus/templates/settings_creator_fund.html")
    assert 'name="agree" value="1" required' in page and 'name="action" value="leave"' in page
    assert page.count('href="/settings/creator_fund"') == 0 and "{{ v.formkey }}" in page
    assert read("ruqqus/templates/settings.html").count('href="/settings/creator_fund"') == 2
    terms = read("ruqqus/templates/help/terms.html")
    assert "## Creator Fund" in terms and "no cash value" in terms.split("## Creator Fund")[1].split("##")[0]
    for sheet in ("main.scss", "main_dark.scss"):
        assert ".creator-fund-agree {" in read(f"ruqqus/assets/style/{sheet}"), sheet
