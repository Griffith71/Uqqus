"""One reproducing test (or a few) per bug fixed in the bug hunt of 2026-10, on the real app and a real Postgres. Each
section names the finding (C1, H1, ... in the plan); each test failed on the code before its fix."""
import secrets
import sys

from sqlalchemy import text

from .test_characterization import BASE, fresh, guildmaster_with_post, post

# guildmaster_with_post is a fixture: importing it here makes it available to this file's tests
assert guildmaster_with_post

XSS = '<img src=x onerror="alert(1)">'


# ---------------------------------------------------------------- C1: a guild's hide reason is drawn as written

def _comment_on(member, post_id):
    from ruqqus.classes import Comment
    from ruqqus.helpers.base36 import base36encode
    here = member.browser()
    answer = post(here, "/api/comment", {"parent_fullname": f"t2_{base36encode(post_id)}", "body": "A comment " + secrets.token_hex(3),
                                         "formkey": member.formkey(here)})
    assert answer.status_code == 200, answer.get_data(as_text=True)[:300]
    return fresh(member).query(Comment).filter_by(parent_submission=post_id, author_id=member.id).order_by(Comment.id.desc()).first().id


def test_c1_a_post_hide_reason_with_markup_is_shown_as_text(guildmaster_with_post):
    from ruqqus.helpers.base36 import base36encode
    mod, author, copy_id = guildmaster_with_post
    client = mod.browser()
    assert post(client, f"/mod/kick/general/{base36encode(copy_id)}", {"reason": XSS, "formkey": mod.formkey(client)}).status_code == 200
    page = author.browser().get(f"/post/{base36encode(copy_id)}", base_url=BASE, follow_redirects=True).get_data(as_text=True)
    assert XSS not in page and "&lt;img src=x" in page, "the hidden post's page"
    log = author.browser().get("/+general/mod/log", base_url=BASE).get_data(as_text=True)
    assert XSS not in log and "&lt;img src=x" in log, "the public mod log"


def test_c1_a_comment_hide_reason_with_markup_is_shown_as_text(guildmaster_with_post):
    from ruqqus.helpers.base36 import base36encode
    mod, author, copy_id = guildmaster_with_post
    cid = _comment_on(author, copy_id)
    client = mod.browser()
    assert post(client, f"/mod/hide_comment/general/{base36encode(cid)}", {"reason": XSS, "formkey": mod.formkey(client)}).status_code == 200
    log = author.browser().get("/+general/mod/log", base_url=BASE).get_data(as_text=True)
    assert XSS not in log and "&lt;img src=x" in log


# ---------------------------------------------------------------- H5: signing in can send people to another site

OFF_SITE = ["https://evil.example/x", "//evil.example/x", "/\evil.example/x", "\\evil.example", "https:evil.example",
            "javascript:alert(1)", " https://evil.example", "/\tx//evil.example"]


def test_h5_signing_in_never_sends_the_browser_off_site(make_member, real_app):
    from urllib.parse import urlsplit
    app, _ = real_app
    member = make_member()
    for target in OFF_SITE:
        answer = post(app.test_client(), "/login", {"username": member.username, "password": member.password, "redirect": target})
        assert answer.status_code == 302, target
        where = urlsplit(answer.headers["Location"])
        assert where.netloc in ("", "localhost") and not where.path.startswith("//"), (target, answer.headers["Location"])


def test_h5_a_signed_in_member_opening_the_login_page_stays_on_the_site(make_member):
    from urllib.parse import quote, urlsplit
    member = make_member()
    for target in OFF_SITE:
        answer = member.browser().get("/login?redirect=" + quote(target, safe=""), base_url=BASE)
        assert answer.status_code == 302, target
        where = urlsplit(answer.headers["Location"])
        assert where.netloc in ("", "localhost") and not where.path.startswith("//"), (target, answer.headers["Location"])


# ---------------------------------------------------------------- the site-bots migration re-run refuses own-account rows

def test_running_the_site_bots_migration_again_keeps_own_account_rows(pg_engine):
    """2026-10-16_site_bots.sql re-added a two-kind CHECK on every run, so once a member's own account posted
    automatically (kind 'self', 2026-11-21) running the file again failed: migrations must be safe to run twice."""
    from pathlib import Path
    sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "scripts"))
    import migrate
    uid = 900_000_000 + secrets.randbelow(90_000_000)
    with pg_engine.connect() as conn:
        conn.execute(text("INSERT INTO bots (user_id, owner_id, username, kind, proposed_by_id, status) "
                          "VALUES (:u, :u, :n, 'self', :u, 'pending')"), {"u": uid, "n": f"own_{uid}"})
        conn.commit()
        try:
            migrate.run_file(conn, "2026-10-16_site_bots.sql")
            kinds = conn.execute(text("SELECT pg_get_constraintdef(oid) FROM pg_constraint WHERE conname = 'bots_kind'")).scalar()
            assert "'self'" in kinds, "the newer, wider rule stays"
        finally:
            conn.rollback()
            conn.execute(text("DELETE FROM bots WHERE user_id = :u"), {"u": uid})
            conn.commit()


# ---------------------------------------------------------------- H7: analytics only from the owner's own host

def test_h7_the_analytics_script_is_drawn_only_while_set_and_the_privacy_page_names_it(real_app, monkeypatch):
    app, _ = real_app
    off = app.test_client().get("/help/privacy", base_url=BASE).get_data(as_text=True)
    assert "ruqqus.com/js" not in off and 'id="analytics"' not in off and "stats.example.org" not in off
    monkeypatch.setitem(app.config, "ANALYTICS_SRC", "https://stats.example.org/js/script.js")
    monkeypatch.setitem(app.config, "ANALYTICS_DOMAIN", "laba.example")
    monkeypatch.setitem(app.config, "ANALYTICS_HOST", "stats.example.org")
    # Jinja folds `'NAME' | app_config` into the compiled template, and the app keeps compiled templates for good
    # (config doesn't change while a site runs): draw these pages from a fresh cache, put back after the test
    monkeypatch.setattr(app.jinja_env, "cache", {})
    page = app.test_client().get("/help/privacy", base_url=BASE).get_data(as_text=True)
    assert '<script defer data-domain="laba.example" src="https://stats.example.org/js/script.js"></script>' in page
    assert 'id="analytics"' in page and "at stats.example.org." in page
    home = app.test_client().get("/", base_url=BASE, follow_redirects=True).get_data(as_text=True)
    assert home.count('src="https://stats.example.org/js/script.js"') == 1, "and on the site's own layout"
