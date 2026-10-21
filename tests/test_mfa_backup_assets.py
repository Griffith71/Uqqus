"""Two-factor backup codes: the routes, templates, script, stylesheets, cleanup, export and schema agree (the behaviour is
in tests/test_mfa_backup.py, and on the real app in tests/pg/test_account_security.py)."""
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def read(*parts):
    return ROOT.joinpath(*parts).read_text(encoding="utf-8")


LOGIN = read("ruqqus", "routes", "login.py")
SETTINGS = read("ruqqus", "routes", "settings.py")


def test_no_route_checks_an_authenticator_code_without_remembering_it():
    """`validate_2fa` says only whether a code is right; every sign-in path uses `accept_2fa`, which also refuses a
    code that was already used in its window (helpers/totp.py)."""
    for name in sorted((ROOT / "ruqqus" / "routes").glob("*.py")):
        body = name.read_text(encoding="utf-8")
        assert ".validate_2fa(" not in body, name.name
    assert "accept_2fa(" in LOGIN and "accept_2fa(" in SETTINGS


def test_the_sign_in_spends_a_backup_code_without_turning_two_factor_off():
    step = LOGIN.split("is_2fa = account.accept_2fa(answer)", 1)[1].split("# set session and user id", 1)[0]
    assert "mfa_backup_store.spend(g.db, account.id, answer, now)" in step
    assert "legacy_code_allowed(mfa_backup_store.has_codes(g.db, account.id))" in step
    spent = step.split("elif is_backup:", 1)[1]
    assert "mfa_secret" not in spent.split("else:", 1)[0], "a backup code must leave two-factor on"
    recovered = step.split("elif is_recovery:", 1)[1].split("elif is_backup:", 1)[0]
    assert "account.mfa_secret=None" in recovered


def test_settings_makes_codes_only_for_an_account_with_two_factor_and_the_password_and_a_code():
    branch = SETTINGS.split('if request.form.get("new_backup_codes", ""):', 1)[1].split("if request.form.get(\"2fa_remove\"", 1)[0]
    assert "if not v.mfa_secret" in branch
    assert "v.verifyPass(" in branch and "v.accept_2fa(" in branch
    assert "mfa_backup_store.replace(" in branch and "_backup_codes_page(" in branch
    page = SETTINGS.split("def _backup_codes_page", 1)[1].split("@app.route", 1)[0]
    assert 'headers["Cache-Control"] = "no-store"' in page


def test_two_factor_is_not_replaced_while_it_is_on_and_turning_it_off_deletes_the_codes():
    enable = SETTINGS.split('if request.form.get("2fa_token", ""):', 1)[1].split('if request.form.get("new_backup_codes"', 1)[0]
    assert enable.index("if v.mfa_secret:") < enable.index("v.mfa_secret = secret")
    remove = SETTINGS.split('if request.form.get("2fa_remove", ""):', 1)[1].split("# a form with none of the fields above", 1)[0]
    assert "mfa_backup_store.delete_all(g.db, v.id)" in remove


def test_the_page_script_and_styles_exist_in_both_stylesheets():
    page = read("ruqqus", "templates", "settings_security.html")
    assert 'id="backup-codes-list"' in page and "data-backup-copy" in page and 'name="new_backup_codes"' in page
    assert "backup_codes.js" in read("ruqqus", "templates", "settings.html")
    script = read("ruqqus", "assets", "js", "backup_codes.js")
    assert "innerHTML" not in script and "textContent" in script
    for sheet in ("main.scss", "main_dark.scss"):
        css = read("ruqqus", "assets", "style", sheet)
        assert ".backup-codes-list" in css and ".backup-codes-new" in css, sheet


def test_deleting_an_account_removes_the_codes_and_the_export_never_includes_them():
    assert "DELETE FROM mfa_backup_codes WHERE user_id = :u" in read("ruqqus", "helpers", "account_cleanup.py")
    export = read("ruqqus", "helpers", "data_export.py")
    section = export.split('("two_factor",', 1)[1].split('("linked_storage"', 1)[0]
    assert "count(*)" in section and "code_hash" not in section and "salt" not in section


def test_the_table_is_in_the_migration_the_schema_and_the_ledger():
    schema = read("schema.sql")
    assert "('2026-11-15_mfa_backup_codes.sql')" in schema
    assert re.search(r"CREATE TABLE public\.mfa_backup_codes \(", schema)
    assert (ROOT / "scripts" / "migrations" / "2026-11-15_mfa_backup_codes.sql").exists()
    assert "REFERENCES public.users" not in schema.split("CREATE TABLE public.mfa_backup_codes", 1)[1].split(");", 1)[0]
