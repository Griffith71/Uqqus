"""The signup guard in the source: which routes refuse a held account (helpers/quarantine.py) and which must not, the
sign-up form's captcha and hidden field, the migration and schema, the admin pages' levels, and the per-account places
(retention, account closure, the data export)."""
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
    ("polls.py", "poll_vote"): "poll_vote",
    ("stories.py", "story_create"): "story",
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


def test_nothing_else_carries_it_without_being_listed():
    listed = {(m, n) for m, n in HELD_OUT}
    for path in (RUQQUS / "routes").glob("*.py"):
        for name in re.findall(r'@not_quarantined\("\w+"\)\n(?:@[^\n]+\n)*def (\w+)\(', path.read_text(encoding="utf-8")):
            assert (path.name, name) in listed, (path.name, name)


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


def test_verifying_an_email_releases_a_held_account_unless_it_is_a_throwaway():
    activate = read("mail", "mail.py").split("def activate(", 1)[1]
    assert "user.quarantined_utc and not signup_signals.disposable(email)" in activate
    assert 'signup_review_store.release(g.db, user.id, "verified"' in activate


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
