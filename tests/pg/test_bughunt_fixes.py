"""One reproducing test (or a few) per bug fixed in the bug hunt of 2026-10, on the real app and a real Postgres. Each
section names the finding (C1, H1, ... in the plan); each test failed on the code before its fix."""
import secrets
import sys

from sqlalchemy import text

from .test_characterization import BASE, addr, fresh, guildmaster_with_post, post

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


# ---------------------------------------------------------------- H1, H2: a member's link can't make the site read its own network

def _serve(*answers):
    """A server on this machine that answers one request per entry: (status, content type, body). Returns its address
    and the requests it got (none, if the site never called it)."""
    import socket
    import threading
    listener = socket.socket()
    listener.bind(("127.0.0.1", 0))
    listener.listen(len(answers))
    listener.settimeout(20)
    seen = []

    def run():
        try:
            for status, kind, body in answers:
                conn, _ = listener.accept()
                seen.append(conn.recv(65536).decode("latin-1"))
                conn.sendall(f"HTTP/1.1 {status} X\r\nContent-Type: {kind}\r\nContent-Length: {len(body)}\r\n\r\n".encode() + body)
                conn.close()
        except OSError:
            pass
        finally:
            listener.close()

    threading.Thread(target=run, daemon=True).start()
    return f"http://127.0.0.1:{listener.getsockname()[1]}", seen


PAGE = b'<html><head><title>Inside</title><meta property="og:image" content="/pic.png"></head></html>'


def _png():
    from io import BytesIO
    from PIL import Image
    out = BytesIO()
    Image.new("RGB", (40, 40), (20, 140, 210)).save(out, "PNG")
    return out.getvalue()


def test_h1_the_title_suggestion_never_reads_an_address_off_the_public_internet(make_member, monkeypatch):
    monkeypatch.delenv("FEED_SERVER_ALLOW_LOCAL", raising=False)         # the dev container sets it for its test servers
    member = make_member()
    base, seen = _serve((200, "text/html", PAGE))
    answer = member.browser().get("/submit/title?url=" + base + "/", base_url=BASE, environ_base={"REMOTE_ADDR": addr()})
    assert answer.status_code == 400 and "Inside" not in answer.get_data(as_text=True)
    assert seen == [], "the site never called it"
    for inside in ("http://169.254.169.254/latest/meta-data/", "http://postgres:5432/", "http://redis:6379/"):
        assert member.browser().get("/submit/title?url=" + inside, base_url=BASE, environ_base={"REMOTE_ADDR": addr()}).status_code == 400, inside


def test_h1_the_title_suggestion_still_reads_a_page(make_member, monkeypatch):
    monkeypatch.setenv("FEED_SERVER_ALLOW_LOCAL", "1")                  # the only way to stand in for a public page here
    member = make_member()
    base, seen = _serve((302, "text/html", b""), (200, "text/html", PAGE))
    answer = member.browser().get("/submit/title?url=" + base + "/", base_url=BASE, environ_base={"REMOTE_ADDR": addr()})
    assert answer.status_code == 400 and seen, "a redirect with no Location goes nowhere"
    base, seen = _serve((200, "text/html", PAGE))
    answer = member.browser().get("/submit/title?url=" + base + "/a", base_url=BASE, environ_base={"REMOTE_ADDR": addr()})
    assert answer.status_code == 200 and answer.get_json()["title"] == "Inside" and len(seen) == 1


def _link_post(member, url):
    """A text post of `member` whose link is then set to `url` (so no preview runs while it is made)."""
    from ruqqus.helpers.base36 import base36decode
    here = member.browser()
    answer = post(here, "/api/vue/submit", {"title": f"Link {secrets.token_hex(4)}", "body": "text",
                                            "formkey": member.formkey(here), "post_to_profile": "1"})
    assert answer.status_code == 200, answer.get_data(as_text=True)[:300]
    pid = base36decode(answer.get_json()["id"])
    db = fresh(member)
    db.execute(text("UPDATE submissions_aux SET url = :u, embed_url = '' WHERE id = :p"), {"u": url, "p": pid})
    db.commit()
    return pid


def _preview(pid):
    from ruqqus.helpers.base36 import base36encode
    from ruqqus.helpers.thumbs import thumbnail_thread
    return thumbnail_thread(base36encode(pid))


def test_h2_a_link_preview_never_reads_an_address_off_the_public_internet(make_member, real_app, monkeypatch):
    monkeypatch.delenv("FEED_SERVER_ALLOW_LOCAL", raising=False)
    member = make_member()
    base, seen = _serve((200, "text/html", PAGE), (200, "image/png", _png()))
    pid = _link_post(member, base + "/page")
    app, _ = real_app
    with app.app_context():
        done, why = _preview(pid)
    assert not done and why == "Unable to connect to source"
    assert seen == []


def test_h2_a_link_preview_still_reads_the_title_and_picture(make_member, real_app, monkeypatch):
    monkeypatch.setenv("FEED_SERVER_ALLOW_LOCAL", "1")
    # thumbs.expand_url makes every picture address https (as it always has); the stand-in server here speaks http
    thumbs = sys.modules["ruqqus.helpers.thumbs"]
    monkeypatch.setattr(thumbs, "expand_url", lambda page, found: page.rsplit("/", 1)[0] + found)
    member = make_member()
    base, seen = _serve((200, "text/html", PAGE), (200, "image/png", _png()))
    pid = _link_post(member, base + "/page")
    app, _ = real_app
    with app.app_context():
        done, why = _preview(pid)
    assert done, why
    row = fresh(member).execute(text("SELECT meta_title, preview_image_url FROM submissions_aux WHERE id = :p"), {"p": pid}).first()
    assert row[0] == "Inside" and row[1].endswith("/pic.png") and len(seen) == 2


# ---------------------------------------------------------------- M2: an IP ban is a POST with the form key

def _doc_ip():
    return f"203.0.113.{secrets.randbelow(250) + 1}"           # a documentation range: never a real member's


def _admin(make_member, level=4):
    admin = make_member()
    db = admin.db_session()
    db.execute(text("UPDATE users SET admin_level = :l WHERE id = :u"), {"l": level, "u": admin.id})
    db.commit()
    return admin


def _banned(db, *ips):
    return db.execute(text("SELECT count(*) FROM ips WHERE addr = ANY(:a)"), {"a": list(ips)}).scalar()


def test_m2_opening_a_link_never_bans_anyone(make_member):
    admin, target = _admin(make_member), make_member()
    ip = _doc_ip()
    db = fresh(admin)
    db.execute(text("UPDATE users SET creation_ip = :i, creation_region = 'GB' WHERE id = :u"), {"i": ip, "u": target.id})
    db.commit()
    try:
        admin.browser().get(f"/admin/get_ip?link=/@{target.username}&ipnuke=1", base_url=BASE)
        assert _banned(fresh(admin), ip) == 0, "a GET a crafted link can make an admin's browser send"
    finally:
        db = fresh(admin)
        db.execute(text("DELETE FROM ips WHERE addr = :i"), {"i": ip})
        db.commit()


def test_m2_the_ban_needs_the_form_key_and_covers_the_accounts_comments(make_member, guildmaster_with_post):
    _, author, copy_id = guildmaster_with_post
    admin = _admin(make_member)
    cid = _comment_on(author, copy_id)
    signed_up, commented = _doc_ip(), _doc_ip()
    db = fresh(admin)
    db.execute(text("UPDATE users SET creation_ip = :i, creation_region = 'GB' WHERE id = :u"), {"i": signed_up, "u": author.id})
    db.execute(text("UPDATE comments SET creation_ip = :i, creation_region = 'GB' WHERE id = :c"), {"i": commented, "c": cid})
    db.commit()
    try:
        client = admin.browser()
        refused = post(client, "/admin/ipnuke", {"link": f"/@{author.username}"})
        # a 401 to a page request is drawn as a redirect to signing in (routes/errors.py)
        assert refused.status_code == 401 or (refused.status_code == 302 and "/login" in refused.headers["Location"])
        assert _banned(fresh(admin), signed_up, commented) == 0
        level3 = _admin(make_member, 3)
        here = level3.browser()
        assert post(here, "/admin/ipnuke", {"link": f"/@{author.username}", "formkey": level3.formkey(here)}).status_code in (403, 404)
        done = post(client, "/admin/ipnuke", {"link": f"/@{author.username}", "formkey": admin.formkey(client)})
        assert done.status_code == 200, done.get_data(as_text=True)[:300]
        assert _banned(fresh(admin), signed_up, commented) == 2, "the comment's address too"
    finally:
        db = fresh(admin)
        db.execute(text("DELETE FROM ips WHERE addr IN (:a, :b)"), {"a": signed_up, "b": commented})
        db.commit()


# ---------------------------------------------------------------- M8: another site can't sign a visitor in

def _sign_in(app, member, **headers):
    client = app.test_client()
    answer = client.post("/login", base_url=BASE, environ_base={"REMOTE_ADDR": addr()},
                         headers={"User-Agent": "ci", **headers},
                         data={"username": member.username, "password": member.password, "redirect": "/"})
    signed_in = client.get("/settings/profile", base_url=BASE).status_code == 200
    return answer.status_code, signed_in


def test_m8_a_sign_in_sent_from_another_sites_page_is_refused(make_member, real_app):
    app, _ = real_app
    member = make_member()
    for headers in ({"Origin": "https://evil.example"}, {"Origin": "null"}, {"Sec-Fetch-Site": "cross-site"},
                    {"Origin": "https://localhost.evil.example"}):
        status, signed_in = _sign_in(app, member, **headers)
        assert status == 403 and not signed_in, headers


def test_m8_a_sign_in_from_the_sites_own_page_still_works(make_member, real_app):
    app, _ = real_app
    member = make_member()
    assert _sign_in(app, member, Origin="http://localhost", **{"Sec-Fetch-Site": "same-origin"}) == (302, True)
    assert _sign_in(app, member) == (302, True), "an app or a script sends neither header"
