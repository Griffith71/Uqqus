"""The groundwork of QR sign-in: the sign-in function login_post shares, the migration, the setting, what Recent sign-ins says and
the export agree (the behaviour on the real app is tests/pg/test_qr_login_setting.py)."""
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def read(*parts):
    return ROOT.joinpath(*parts).read_text(encoding="utf-8")


LOGIN = read("ruqqus", "routes", "login.py")
SETTINGS = read("ruqqus", "routes", "settings.py")
PAGE = read("ruqqus", "templates", "settings_security.html")
SIGN_IN = LOGIN.split("def complete_sign_in(", 1)[1].split("\n@app.route", 1)[0]
LOGIN_POST = LOGIN.split("def login_post(", 1)[1].split("def complete_sign_in(", 1)[0]
BRANCH = SETTINGS.split('if request.form.get("qr_login_form", "")', 1)[1].split("# a form with none of the fields above", 1)[0]


def test_the_shared_sign_in_does_everything_a_sign_in_did_in_order():
    order = ['flask_session["user_id"] = account.id', 'flask_session["session_id"] = token_hex(16)',
             'flask_session["login_nonce"] = account.login_nonce', "flask_session.permanent = True",
             "signin_alerts.check(", "g.db.add(LoginEvent(", "method=method", "g.db.commit()", "send_mail_quietly",
             "check_for_alts(account.id)", "account.refresh_selfset_badges()"]
    places = [SIGN_IN.index(step) for step in order]
    assert places == sorted(places), "the alert is worked out before the sign-in is recorded, and alts after"
    assert "# set session and user id" in SIGN_IN


def test_the_ad_hold_belongs_to_the_password_sign_in_and_not_to_the_shared_function():
    """A QR sign-in writes no pre-key (assets/js/chat_key.js), so nothing about it holds the ads back."""
    assert "hold_after_password" not in SIGN_IN
    assert LOGIN_POST.index("hold_after_password(flask_session,") < LOGIN_POST.index("complete_sign_in(account, sign_in_history.METHOD_PASSWORD,")
    assert LOGIN_POST.count("flask_session[") == 0, "the session is only ever written by the shared function"


def test_both_places_that_record_a_sign_in_say_how():
    assert LOGIN.count("g.db.add(LoginEvent(") == 2
    assert LOGIN.count("method=") == 2 and "method=sign_in_history.METHOD_PASSWORD" in LOGIN.split("def sign_up_post(", 1)[1]


def test_the_migration_the_schema_and_the_ledger():
    sql = read("scripts", "migrations", "2026-12-13_qr_login.sql")
    assert "ADD COLUMN IF NOT EXISTS qr_login_off boolean DEFAULT false NOT NULL" in sql
    assert "login_events ADD COLUMN IF NOT EXISTS method character varying(16)" in sql
    schema = read("schema.sql")
    assert "    qr_login_off boolean DEFAULT false NOT NULL,\n" in schema
    assert re.search(r"CREATE TABLE public\.login_events \(.*?method character varying\(16\)\n\);", schema, re.S)
    assert "('2026-12-13_qr_login.sql')" in schema
    assert "qr_login_off = Column(Boolean, default=False)" in read("ruqqus", "classes", "user.py")
    assert "method = Column(String(16), default=None)" in read("ruqqus", "classes", "login_events.py")


def test_the_setting_is_only_drawn_and_only_answers_while_the_site_offers_it():
    section = PAGE.split("{% if qr_login_offered() %}", 1)[1].split("{% endif %}\n\n  <h2 class=\"h5\">", 1)[0]
    assert 'id="qr-login"' in section and PAGE.count('name="qr_login_form"') == 1 and 'name="qr_login_form"' in section
    assert 'request.form.get("qr_login_form", "") and qr_login_offered():' in SETTINGS
    assert "QR_LOGIN_ENABLED" not in PAGE and "QR_LOGIN_ENABLED" not in SETTINGS, "one helper decides"
    assert 'qr_login_rules.offered(environ, r, app.config["READ_ONLY"])' in read("ruqqus", "helpers", "jinja2.py")


def test_only_turning_it_back_on_asks_the_password_and_tells_the_member():
    off = BRANCH.split('if request.form.get("qr_login") == "off":', 1)[1].split("if not v.qr_login_off:", 1)[0]
    assert "v.qr_login_off = True" in off and "verifyPass" not in off and "_tell_qr_login_on" not in off
    on = BRANCH.split("if not v.qr_login_off:", 1)[1]
    assert on.index('v.verifyPass(request.form.get("password") or "")') < on.index("v.qr_login_off = False")
    assert on.index("g.db.commit()") < on.index("_tell_qr_login_on(v)"), "told once it is true, and a failed notice undoes nothing"


def test_the_password_box_is_drawn_only_for_turning_it_on():
    section = PAGE.split("{% if qr_login_offered() %}", 1)[1].split('<h2 class="h5">', 1)[0]
    on, off = section.split("{% else %}", 1)
    assert 'value="on"' in on and 'name="password"' in on and 'autocomplete="current-password"' in on
    assert 'value="off"' in off and 'name="password"' not in off


def test_the_alert_goes_by_the_sign_in_emails_rule_and_never_carries_the_address():
    tell = SETTINGS.split("def _tell_qr_login_on(", 1)[1].split("\n\n\n", 1)[0]
    assert "notify(v, N_(" in tell and tell.index("notify(v,") < tell.index("signin_alerts.wants_email(v)")
    assert "force_locale(locale_for_user(v))" in tell and "gevent.spawn(send_mail_quietly" in tell
    assert "remote_addr" not in tell
    email = read("ruqqus", "templates", "email", "qr_login_on.html")
    assert '{% extends "email/default.html" %}' in email and "ip" not in re.findall(r"\{\{\s*(\w+)", email)


def test_recent_sign_ins_draws_the_method_and_an_old_row_says_nothing():
    assert "{% if s.method %} &middot; {{ s.method }}{% endif %}" in PAGE


def test_the_export_has_both():
    export = read("ruqqus", "helpers", "data_export.py")
    assert "qr_login_off" in export.split('("account",', 1)[1].split('("age",', 1)[0]
    assert "method FROM login_events" in export


def test_the_switch_is_on_in_the_examples_now_the_flow_exists():
    assert "QR_LOGIN_ENABLED=1" in read(".env.example")
    assert "QR_LOGIN_ENABLED=${QR_LOGIN_ENABLED:-1}" in read("docker-compose.yml")
