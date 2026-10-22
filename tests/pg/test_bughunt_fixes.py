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
        # every ban this admin made: the target's alts are banned too, and in these tests every member is an alt of
        # the others (they all sign in from the test client's address), which would leave that address banned
        db.execute(text("DELETE FROM ips WHERE addr IN (:a, :b) OR banned_by = :v"),
                   {"a": signed_up, "b": commented, "v": admin.id})
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


def test_m1_the_sites_own_pictures_and_animations_still_save(guildmaster_with_post, real_app, monkeypatch):
    app, _ = real_app
    # the bucket has no default since the hunt's bucket fix (an unset one allows no pictures); name one for this test
    monkeypatch.setitem(app.config, "S3_BUCKET", "i.laba.example")
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


# ---------------------------------------------------------------- L5: a picture follows the post page's own rule

def test_l5_a_comment_picture_in_a_private_guild_follows_the_post_pages_rule(make_member, guildmaster_with_post):
    """Pictures on a post in a private guild were public and cached for a year. The first fix used the Circle-guild
    rule and hid pictures on pages their readers may open (reverted); the post page's own rule is used now."""
    from ruqqus.helpers import circles
    from ruqqus.helpers.media import attach
    _, author, copy_id = guildmaster_with_post
    cid = _comment_on(author, copy_id)
    db = fresh(author)
    asset_id = db.execute(text("INSERT INTO media_assets (user_id, account_id, provider, kind, token, status, comment_id) "
                               "VALUES (:u, 0, 'dev', 'image', :t, 'ready', :c) RETURNING id"),
                          {"u": author.id, "t": secrets.token_hex(8), "c": cid}).scalar()
    db.commit()
    MediaAsset = attach._models()[0]

    def audience(post_public, pinned):
        d = fresh(author)
        d.execute(text("UPDATE submissions SET post_public = :p, is_pinned = :n WHERE id = :s"),
                  {"p": post_public, "n": pinned, "s": copy_id})
        d.commit()
        return attach.audience_context(d, d.query(MediaAsset).filter_by(id=asset_id).first())[0]
    try:
        db.execute(text("UPDATE boards SET is_private = true WHERE id = 1"))
        db.commit()
        assert audience(False, False) == circles.PRIVATE_GUILD, "never public there: the guild's readers only"
        assert audience(True, False) == 0, "forwarded while public: anyone may read the page, and see the picture"
        assert audience(False, True) == 0, "pinned: the page is readable by anyone"
    finally:
        db = fresh(author)
        db.execute(text("UPDATE boards SET is_private = false WHERE id = 1"))
        db.execute(text("UPDATE submissions SET post_public = true, is_pinned = false WHERE id = :s"), {"s": copy_id})
        db.execute(text("DELETE FROM media_assets WHERE id = :a"), {"a": asset_id})
        db.commit()

# ---------------------------------------------------------------- L6: feed dates carry their time zone

def test_l6_feed_dates_say_they_are_utc(real_app):
    feeds = sys.modules["ruqqus.routes.feeds"]
    assert feeds.atom_time(0) == "1970-01-01T00:00:00+00:00"
    assert feeds.atom_time(1_792_000_000).endswith("+00:00")


def test_a_new_comment_with_a_banned_link_is_a_400_not_a_401(make_member, guildmaster_with_post):
    """A 401 means "not signed in": an API client would drop its token over a link."""
    from ruqqus.helpers.base36 import base36encode
    _, author, copy_id = guildmaster_with_post
    banned = f"spam-{secrets.token_hex(3)}.example"
    db = fresh(author)
    db.execute(text("INSERT INTO domains (domain, can_submit, can_comment, reason, show_thumbnail) VALUES (:d, false, false, 1, false)"),
               {"d": banned})
    db.commit()
    try:
        here = author.browser()
        answer = post(here, "/api/comment", {"parent_fullname": f"t2_{base36encode(copy_id)}",
                                             "body": f"see [this](https://{banned}/x)", "formkey": author.formkey(here)})
        assert answer.status_code == 400 and banned in answer.get_json()["error"]
    finally:
        db = fresh(author)
        db.execute(text("DELETE FROM domains WHERE domain = :d"), {"d": banned})
        db.commit()


def test_a_banner_and_a_thumbnail_are_drawn_from_the_sites_own_bucket(make_member, real_app):
    app, _ = real_app
    member = make_member()
    user = member.user()
    user.has_banner, user.banner_set_utc, user.banner_nonce = True, 1_700_000_000, 1
    assert user.banner_url.startswith(f"https://{app.config['S3_BUCKET']}/uid/")
    member.db_session().rollback()


def test_a_sign_up_form_with_a_missing_or_odd_field_is_refused_not_a_500(real_app):
    import hmac as _hmac
    import os
    app, _ = real_app
    client = app.test_client()
    client.get("/signup", base_url=BASE)                         # gives the session its sign-up token
    with client.session_transaction() as s:
        token = s.get("signup_token", "")
    assert token
    assert post(client, "/signup", {"now": "abc", "formkey": "x"}).status_code in (302, 400)
    then = str(int(time.time()) - 60)
    key = _hmac.new(key=bytes(os.environ["MASTER_KEY"], "utf-16"), msg=bytes(then + token + "ci", "utf-16"),
                    digestmod="md5").hexdigest()
    answer = post(client, "/signup", {"now": then, "formkey": key})          # no username, no password
    assert answer.status_code == 302 and "/signup" in answer.headers["Location"]


def test_turning_a_guild_private_makes_the_cdn_forget_its_pictures(guildmaster_with_post, monkeypatch):
    from ruqqus.helpers.media import attach, cdn
    mod, author, copy_id = guildmaster_with_post
    cid = _comment_on(author, copy_id)
    db = fresh(author)
    asset_id = db.execute(text("INSERT INTO media_assets (user_id, account_id, provider, kind, token, ext, status, comment_id) "
                               "VALUES (:u, 0, 'gdrive', 'image', :t, 'png', 'ready', :c) RETURNING id"),
                          {"u": author.id, "t": secrets.token_hex(8), "c": cid}).scalar()
    db.commit()
    # a copy never public there (one that was public stays public, and is not purged: audience_context)
    db.execute(text("UPDATE submissions SET post_public = false WHERE id = :s"), {"s": copy_id})
    db.commit()
    asked = []
    monkeypatch.setattr(cdn, "purge", lambda paths: asked.extend(paths))
    try:
        client = mod.browser()
        answer = post(client, "/mod/1/settings/private", {"guildprivacy": "true", "formkey": mod.formkey(client)})
        assert answer.status_code == 204
        path = fresh(author).query(attach._models()[0]).filter_by(id=asset_id).first().path
        assert path in asked
    finally:
        db = fresh(author)
        db.execute(text("UPDATE boards SET is_private = false WHERE id = 1"))
        db.execute(text("UPDATE submissions SET post_public = true WHERE id = :s"), {"s": copy_id})
        db.execute(text("DELETE FROM media_assets WHERE id = :a"), {"a": asset_id})
        db.commit()


def test_cdn_purges_go_in_batches_of_thirty(real_app):
    """Cloudflare purges at most 30 addresses a call; a guild's pictures can be many more."""
    from ruqqus.helpers.media import cdn
    urls = [f"https://x/{i}" for i in range(65)]
    assert [len(b) for b in cdn.batches(urls)] == [30, 30, 5] and sum(cdn.batches(urls), []) == urls
    assert cdn.batches([]) == []



# ---------------------------------------------------------------- batch D review

def test_a_link_typed_without_https_works_and_one_without_a_host_is_refused(make_member):
    member = make_member()
    here = member.browser()
    made = post(here, "/api/vue/submit", {"title": f"Link {secrets.token_hex(4)}", "url": f"example.org/{secrets.token_hex(3)}",
                                          "formkey": member.formkey(here), "post_to_profile": "1"})
    assert made.status_code == 200, made.get_data(as_text=True)[:300]
    from ruqqus.helpers.base36 import base36decode
    url = fresh(member).execute(text("SELECT url FROM submissions_aux WHERE id = :p"),
                                {"p": base36decode(made.get_json()["id"])}).scalar()
    assert url.startswith("https://example.org/"), url
    refused = post(here, "/api/vue/submit", {"title": f"Link {secrets.token_hex(4)}", "url": "foo",
                                             "formkey": member.formkey(here), "post_to_profile": "1"})
    assert refused.status_code == 400


def test_with_no_bucket_a_post_with_an_empty_host_deletes_normally(make_member, real_app, monkeypatch):
    from ruqqus.helpers.base36 import base36encode
    app, _ = real_app
    monkeypatch.setitem(app.config, "S3_BUCKET", "")
    member = make_member()
    pid = _link_post(member, "https:///foo")                     # stored before links were checked
    here = member.browser()
    answer = post(here, f"/delete_post/{base36encode(pid)}", {"formkey": member.formkey(here)})
    assert answer.status_code < 500, answer.status_code


def test_unpinning_a_post_in_a_private_guild_purges_its_pictures(guildmaster_with_post, monkeypatch):
    from ruqqus.helpers.base36 import base36encode
    from ruqqus.helpers.media import attach, cdn
    mod, author, copy_id = guildmaster_with_post
    db = fresh(author)
    db.execute(text("UPDATE submissions SET post_public = false, is_pinned = true WHERE id = :s"), {"s": copy_id})
    db.execute(text("UPDATE boards SET is_private = true WHERE id = 1"))
    asset_id = db.execute(text("INSERT INTO media_assets (user_id, account_id, provider, kind, token, ext, status, submission_id) "
                               "VALUES (:u, 0, 'gdrive', 'image', :t, 'png', 'ready', :s) RETURNING id"),
                          {"u": author.id, "t": secrets.token_hex(8), "s": copy_id}).scalar()
    db.commit()
    asked = []
    monkeypatch.setattr(cdn, "purge", lambda paths: asked.extend(paths))
    try:
        client = mod.browser()
        answer = post(client, f"/mod/post_pin/general/{base36encode(copy_id)}/0", {"formkey": mod.formkey(client)})
        assert answer.status_code in (200, 204), answer.get_data(as_text=True)[:200]
        path = fresh(author).query(attach._models()[0]).filter_by(id=asset_id).first().path
        assert path in asked
    finally:
        db = fresh(author)
        db.execute(text("UPDATE boards SET is_private = false WHERE id = 1"))
        db.execute(text("UPDATE submissions SET post_public = true, is_pinned = false WHERE id = :s"), {"s": copy_id})
        db.execute(text("DELETE FROM media_assets WHERE id = :a"), {"a": asset_id})
        db.commit()


def test_an_overlong_sign_up_time_is_refused_not_a_500(real_app):
    app, _ = real_app
    client = app.test_client()
    client.get("/signup", base_url=BASE)
    assert post(client, "/signup", {"now": "9" * 5000, "formkey": "x"}).status_code in (302, 400)


# ---------------------------------------------------------------- review of the OAuth API and admin tools

def test_deleting_an_account_ends_the_grants_it_gave_apps(make_member):
    """An app's token signed it in as a deleted account: it kept posting, voting and refreshing its token."""
    member = make_member()
    db = fresh(member)
    token = secrets.token_hex(32)
    db.execute(text("INSERT INTO client_auths (user_id, access_token, refresh_token, access_token_expire_utc, scope_identity) "
                    "VALUES (:u, :t, :r, :e, true)"), {"u": member.id, "t": token, "r": secrets.token_hex(32), "e": int(time.time()) + 3600})
    db.commit()
    client = member.browser()
    done = post(client, "/settings/delete_account", {"password": member.password, "formkey": member.formkey(client)})
    assert done.status_code in (200, 302), done.status_code
    left = fresh(member).execute(text("SELECT count(*) FROM client_auths WHERE user_id = :u"), {"u": member.id}).scalar()
    assert left == 0


def test_an_admin_acts_only_on_accounts_below_their_own_level(make_member):
    """A level-3 admin could ban (and nuke, demod, IP-ban) an admin of any level, the site's owner included."""
    junior, senior, peer = _admin(make_member, 3), _admin(make_member, 4), _admin(make_member, 4)

    def banned(member):
        return fresh(member).execute(text("SELECT is_banned FROM users WHERE id = :u"), {"u": member.id}).scalar()
    here = junior.browser()
    assert post(here, f"/api/ban_user/{senior.id}", {"reason": "x", "formkey": junior.formkey(here)}).status_code == 403
    assert not banned(senior)
    here = senior.browser()
    assert post(here, "/admin/nuke_user", {"user": peer.username, "formkey": senior.formkey(here)}).status_code == 403
    assert post(here, f"/api/ban_user/{junior.id}", {"reason": "x", "days": "1", "formkey": senior.formkey(here)}).status_code in (200, 302)
    assert banned(junior), "a senior admin still bans below their level"
    db = fresh(junior)
    db.execute(text("UPDATE users SET is_banned = 0, unban_utc = 0 WHERE id = :u"), {"u": junior.id})
    db.commit()


# ---------------------------------------------------------------- review of chat, calls and Spaces

def _board_space(host):
    here = host.browser()
    made = post(here, "/api/spaces", {"title": f"Board {secrets.token_hex(3)}", "space_kind": "board", "space_hours": "1",
                                      "formkey": host.formkey(here)})
    assert made.status_code == 200, made.get_data(as_text=True)[:300]
    data = made.get_json()
    sid = data.get("space_id") or data.get("id")
    if isinstance(sid, str) and not sid.isdigit():
        sid = fresh(host).execute(text("SELECT id FROM spaces WHERE host_id = :h ORDER BY id DESC"), {"h": host.id}).scalar()
    return int(sid)


def test_someone_removed_from_a_space_can_neither_listen_nor_read_it_again(make_member, real_app):
    """The live-events topic guard only asked whether the post was visible: a reload let a removed person back in."""
    from flask import g
    from ruqqus.helpers import space_store
    app, db_session = real_app
    host, removed, other = make_member(), make_member(), make_member()
    sid = _board_space(host)
    db = fresh(host)
    space_store.remove(db, sid, removed.id, host.id, int(time.time()))
    db.commit()
    assert removed.browser().get(f"/api/spaces/{sid}/messages", base_url=BASE).status_code == 404
    assert other.browser().get(f"/api/spaces/{sid}/messages", base_url=BASE).status_code == 200
    spaces = sys.modules["ruqqus.routes.spaces"]
    with app.test_request_context():
        g.db = db_session()
        users = sys.modules["ruqqus.classes"].User
        assert spaces._guard_space_topic(g.db.query(users).get(removed.id), str(sid)) is False
        assert spaces._guard_space_topic(g.db.query(users).get(other.id), str(sid)) is True


def test_a_board_has_no_stage_roster_or_cohosts(make_member, real_app):
    """join, invite, accept and cohost took any kind of Space: a board's host could name co-hosts and anyone who opened
    a board could join its call roster."""
    host, other = make_member(), make_member()
    sid = _board_space(host)
    here = host.browser()
    invited = post(here, f"/api/spaces/{sid}/invite", {"username": other.username, "formkey": host.formkey(here)})
    assert invited.status_code == 409, invited.get_data(as_text=True)[:300]
    them = other.browser()
    joined = post(them, f"/api/spaces/{sid}/join", {"device_id": "ABCDEFGHIJ", "formkey": other.formkey(them)})
    assert joined.status_code == 409, joined.get_data(as_text=True)[:300]
    assert post(them, f"/api/spaces/{sid}/accept", {"formkey": other.formkey(them)}).status_code == 409
    named = post(here, f"/api/spaces/{sid}/cohost", {"username": other.username, "formkey": host.formkey(here)})
    assert named.status_code == 409
    assert fresh(host).execute(text("SELECT count(*) FROM space_roles WHERE space_id = :s"), {"s": sid}).scalar() == 0


def _app_token(member, **scopes):
    db = fresh(member)
    app_id = db.execute(text("INSERT INTO oauth_apps (client_id, client_secret, app_name, redirect_uri, author_id, is_banned) "
                             "VALUES (:c, :s, 'ci app', 'https://example.org/cb', :u, false) RETURNING id"),
                        {"c": secrets.token_hex(16), "s": secrets.token_hex(32), "u": member.id}).scalar()
    token = secrets.token_hex(32)
    db.execute(text("INSERT INTO client_auths (user_id, oauth_client, access_token, access_token_expire_utc, scope_identity, "
                    "scope_update) VALUES (:u, :a, :t, :e, true, :up)"),
               {"u": member.id, "a": app_id, "t": token, "e": int(time.time()) + 3600, "up": bool(scopes.get("update"))})
    db.commit()
    return token


def test_following_through_the_api_needs_the_update_scope(make_member, real_app):
    """POST /api/v2/users/<name>/follow (and three like it) had no api() decorator: a token crashed them (an unexpected
    `c` argument) and no scope was checked."""
    app, _ = real_app
    member, target = make_member(), make_member()
    weak, strong = _app_token(member), _app_token(member, update=True)

    def follow(token):
        return app.test_client().post(f"/api/v2/users/{target.username}/follow", base_url=BASE,
                                      headers={"Authorization": f"Bearer {token}", "User-Agent": "ci"},
                                      environ_base={"REMOTE_ADDR": addr()})
    assert follow(weak).status_code == 403
    assert follow(strong).status_code < 300
    assert fresh(member).execute(text("SELECT count(*) FROM follows WHERE user_id = :u AND target_id = :t"),
                                 {"u": member.id, "t": target.id}).scalar() == 1


def test_an_admin_siege_never_installs_a_suspended_account(make_member):
    """The siege route checked the admin's own age and standing, though its messages named the account installed."""
    admin, target = _admin(make_member, 3), make_member()
    db = fresh(admin)
    db.execute(text("UPDATE users SET is_banned = 1, unban_utc = 0, created_utc = created_utc - 9000000 WHERE id = :u"),
               {"u": target.id})
    db.execute(text("UPDATE users SET created_utc = created_utc - 9000000 WHERE id = :u"), {"u": admin.id})
    db.commit()
    here = admin.browser()
    post(here, "/admin/siege_guild", {"guild": "general", "username": target.username, "formkey": admin.formkey(here)})
    mods = fresh(admin).execute(text("SELECT count(*) FROM mods WHERE user_id = :u AND board_id = 1"), {"u": target.id}).scalar()
    assert mods == 0
    db = fresh(admin)
    db.execute(text("UPDATE users SET is_banned = 0 WHERE id = :u"), {"u": target.id})
    db.commit()


def test_an_admin_siege_on_a_guild_with_a_guildmaster_answers_without_an_error(make_member, guildmaster_with_post):
    """The check of the guildmasters' recent activity named ModAction.User (an AttributeError): every admin siege that
    reached it was a 500."""
    admin, target = _admin(make_member, 3), make_member()
    db = fresh(admin)
    db.execute(text("UPDATE users SET created_utc = created_utc - 9000000 WHERE id IN (:a, :t)"), {"a": admin.id, "t": target.id})
    db.commit()
    here = admin.browser()
    answer = post(here, "/admin/siege_guild", {"guild": "general", "username": target.username, "formkey": admin.formkey(here)})
    assert answer.status_code < 500, answer.status_code


# ---------------------------------------------------------------- review of media, collab, channels, stories

def _draft(member):
    here = member.browser()
    made = post(here, "/collab/new", {"formkey": member.formkey(here)})
    assert made.status_code == 302, made.status_code
    return int(made.headers["Location"].rstrip("/").rsplit("/", 1)[-1])


def test_a_shared_draft_never_keeps_a_banned_link(make_member):
    """Any author may save the draft's link; an autoban link then suspended whoever published, chosen by someone else."""
    member = make_member()
    doc = _draft(member)
    banned = f"spam-{secrets.token_hex(3)}.example"
    db = fresh(member)
    db.execute(text("INSERT INTO badlinks (reason, link, autoban) VALUES (1, :l, true)"), {"l": banned})
    db.commit()
    try:
        here = member.browser()
        answer = post(here, f"/api/collab/{doc}/options", {"url": f"https://{banned}/x", "formkey": member.formkey(here)})
        assert answer.status_code == 400
        assert banned not in (fresh(member).execute(text("SELECT options FROM collab_docs WHERE id = :d"), {"d": doc}).scalar() or "")
        assert not fresh(member).execute(text("SELECT is_banned FROM users WHERE id = :u"), {"u": member.id}).scalar()
    finally:
        db = fresh(member)
        db.execute(text("DELETE FROM badlinks WHERE link = :l"), {"l": banned})
        db.commit()


def test_a_suspended_account_publishes_nothing_from_a_shared_draft(make_member, real_app):
    """A suspended proposer's passed publish still edited the post; the check came after the edit was committed."""
    from flask import g
    from ruqqus.helpers import collab_store
    app, db_session = real_app
    member = make_member()
    doc_id = _draft(member)
    db = fresh(member)
    db.execute(text("UPDATE users SET is_banned = 1, unban_utc = :t WHERE id = :u"), {"t": int(time.time()) + 86400, "u": member.id})
    db.commit()
    try:
        with app.test_request_context():
            g.db = db_session()
            user = g.db.query(sys.modules["ruqqus.classes"].User).get(member.id)
            snap = {"title": f"Draft {secrets.token_hex(3)}", "body": "text", "revision": 1, "options": {}}
            answer = sys.modules["ruqqus.routes.collab"]._publish(collab_store.doc(g.db, doc_id), user, snap, int(time.time()))
            assert answer == "Your account can't publish right now."
        made = fresh(member).execute(text("SELECT count(*) FROM submissions WHERE author_id = :u"), {"u": member.id}).scalar()
        assert made == 0
    finally:
        db = fresh(member)
        db.execute(text("UPDATE users SET is_banned = 0, unban_utc = 0 WHERE id = :u"), {"u": member.id})
        db.commit()


def test_an_encrypted_channels_removal_never_redacts_the_rooms_state(real_app, monkeypatch, guildmaster_with_post):
    """Any event id was redacted by the bot, m.room.encryption and the power levels included."""
    from types import SimpleNamespace
    from flask import g
    app, db_session = real_app
    channels = sys.modules["ruqqus.routes.guild_channels"]
    matrix = channels.matrix
    redacted = []
    monkeypatch.setattr(matrix, "redact_as_bot", lambda room, event_id, txn, reason: redacted.append(event_id))
    monkeypatch.setattr(matrix, "event_sender", lambda room, event_id: "@ruqqus_5:chat.localhost")
    monkeypatch.setattr(channels, "_log", lambda *a, **k: None)
    events = {"$stateEventAAAAAAAA": {"type": "m.room.encryption", "state_key": "", "sender": "@bot:chat.localhost"},
              "$messageEventAAAAAA": {"type": "m.room.encrypted", "sender": "@ruqqus_5:chat.localhost"}}
    monkeypatch.setattr(matrix, "event", lambda room, event_id: events.get(event_id))
    row = SimpleNamespace(matrix_room_id="!room:chat.localhost", name="general")
    with app.test_request_context():
        g.db = db_session()
        assert channels._remove_encrypted_message(row, None, None, "$stateEventAAAAAAAA") is False
        assert channels._remove_encrypted_message(row, None, None, "$messageEventAAAAAA") is True
    assert redacted == ["$messageEventAAAAAA"]


def test_unblocking_puts_both_back_in_their_chat(make_member, monkeypatch):
    """A block bans the blocked person from the pair's room; nothing ever lifted it, so the chat stayed broken."""
    from ruqqus.helpers import matrix_client
    a, b = make_member(), make_member()
    room = f"!room{secrets.token_hex(4)}:chat.localhost"
    db = fresh(a)
    low, high = min(a.id, b.id), max(a.id, b.id)
    db.execute(text("INSERT INTO chat_conversations (user_a_id, user_b_id, initiator_id, matrix_room_id, status) "
                    "VALUES (:l, :h, :l, :r, 'inbox')"), {"l": low, "h": high, "r": room})
    for member in (a, b):
        db.execute(text("INSERT INTO chat_identities (user_id, matrix_user_id) VALUES (:u, :m) ON CONFLICT DO NOTHING"),
                   {"u": member.id, "m": f"@ci_{member.id}:chat.localhost"})
    db.commit()
    calls = []
    monkeypatch.setattr(matrix_client, "ban_user_as_bot", lambda r, m: calls.append(("ban", m)))
    monkeypatch.setattr(matrix_client, "unban_user_as_bot", lambda r, m: calls.append(("unban", m)))
    monkeypatch.setattr(matrix_client, "put_in_room", lambda r, m: calls.append(("in", m)))
    here = a.browser()
    assert post(here, "/settings/block", {"username": b.username, "formkey": a.formkey(here)}).status_code < 400
    assert ("ban", f"@ci_{b.id}:chat.localhost") in calls
    here = a.browser()
    assert post(here, "/settings/unblock", {"username": b.username, "formkey": a.formkey(here)}).status_code < 400
    assert ("unban", f"@ci_{b.id}:chat.localhost") in calls and ("in", f"@ci_{b.id}:chat.localhost") in calls


def test_a_public_channel_keeps_nothing_but_signed_messages_and_emoji_reactions(real_app, monkeypatch):
    """A writer's own Matrix app could send stickers, polls or encrypted events into a public channel, around the word
    filter, the daily count and reports; and a removed message still raised every member's unread count."""
    from flask import g
    app, db_session = real_app
    chat = sys.modules["ruqqus.routes.chat"]
    redacted = []
    monkeypatch.setattr(chat.matrix, "redact_as_bot", lambda room, event_id, txn, reason="": redacted.append(event_id))
    monkeypatch.setattr(chat.broadcast_store, "by_room", lambda db, room: (7, 1, "x", "y", 0))
    with app.test_request_context():
        g.db = db_session()
        sticker = {"room_id": "!c:chat.localhost", "type": "m.sticker", "sender": "@w:chat.localhost", "event_id": "$s",
                   "content": {"body": "hi"}}
        emoji = {"room_id": "!c:chat.localhost", "type": "m.reaction", "sender": "@w:chat.localhost", "event_id": "$r",
                 "content": {"m.relates_to": {"key": "\U0001F44D"}}}
        assert chat._guard_room_event(sticker) is True
        assert not chat._guard_room_event(emoji)
    assert redacted == ["$s"]


def test_accepting_a_group_invitation_is_refused_across_a_block_with_someone_in_it(make_member, real_app, monkeypatch):
    """Adding someone checks blocks with everyone in the group, but a block made after the invitation went unasked
    when it was accepted, and put the two in one group."""
    from ruqqus.helpers import matrix_client
    joined = []
    monkeypatch.setattr(matrix_client, "join_room_as", lambda room, mxid: joined.append(mxid))
    owner, member, invitee = make_member(), make_member(), make_member()
    db = fresh(owner)
    now = int(time.time())
    gid = db.execute(text("INSERT INTO chat_groups (matrix_room_id, created_by_id, created_utc) VALUES (:r, :o, :n) "
                          "RETURNING id"), {"r": f"!g{secrets.token_hex(6)}:chat.localhost", "o": owner.id, "n": now}).scalar()
    for uid, role, status in ((owner.id, "owner", "joined"), (member.id, "member", "joined"), (invitee.id, "member", "invited")):
        db.execute(text("INSERT INTO chat_group_members (group_id, user_id, role, status, invited_by_id, invited_utc) "
                        "VALUES (:g, :u, :r, :s, :o, :n)"), {"g": gid, "u": uid, "r": role, "s": status, "o": owner.id, "n": now})
    db.commit()
    here = member.browser()
    assert post(here, "/settings/block", {"username": invitee.username, "formkey": member.formkey(here)}).status_code < 400
    them = invitee.browser()
    answer = post(them, f"/api/chat/groups/{gid}/accept", {"formkey": invitee.formkey(them)})
    assert answer.status_code == 403
    assert "block" not in answer.get_data(as_text=True).lower()
    assert joined == []
    assert fresh(owner).execute(text("SELECT status FROM chat_group_members WHERE group_id = :g AND user_id = :u"),
                                {"g": gid, "u": invitee.id}).scalar() == "invited"


def _story(db, owner_id, age=0):
    now = int(time.time()) - age
    return db.execute(text("INSERT INTO stories (user_id, kind, body, background, created_utc, expires_utc) "
                           "VALUES (:u, 'text', 'hello there', 'blue', :c, :e) RETURNING id"),
                      {"u": owner_id, "c": now, "e": now + 86400}).scalar()


def test_a_highlight_story_past_its_day_can_still_be_viewed_and_reported(make_member, real_app):
    """story_view and story_report only knew live stories: a Highlight's older stories answered 404."""
    owner, viewer = make_member(), make_member()
    db = fresh(owner)
    sid = _story(db, owner.id, age=3 * 86400)
    hid = db.execute(text("INSERT INTO highlights (user_id, title) VALUES (:u, 'Trips') RETURNING id"), {"u": owner.id}).scalar()
    db.execute(text("INSERT INTO highlight_stories (highlight_id, story_id) VALUES (:h, :s)"), {"h": hid, "s": sid})
    loose = _story(db, owner.id, age=3 * 86400)                   # past its day and in no Highlight
    db.commit()
    here = viewer.browser()
    assert here.get(f"/api/highlights/{hid}", base_url=BASE).status_code == 200
    assert post(here, f"/api/stories/{sid}/view", {"formkey": viewer.formkey(here)}).status_code == 200
    reported = post(here, f"/api/stories/{sid}/report", {"reason": "spam", "formkey": viewer.formkey(here)})
    assert reported.status_code == 200, reported.get_data(as_text=True)[:200]
    assert post(here, f"/api/stories/{loose}/view", {"formkey": viewer.formkey(here)}).status_code == 404


def test_a_suspended_accounts_story_is_neither_viewed_nor_its_picture_served(make_member, real_app):
    """The story lists and Highlights ask the profile's gates; viewing, reporting and the picture didn't."""
    owner, viewer = make_member(), make_member()
    db = fresh(owner)
    sid = _story(db, owner.id)
    asset_id = db.execute(text("INSERT INTO media_assets (user_id, account_id, provider, kind, token, ext, status, story_id) "
                               "VALUES (:u, 0, 'gdrive', 'image', :t, 'png', 'ready', :s) RETURNING id"),
                          {"u": owner.id, "t": secrets.token_hex(8), "s": sid}).scalar()
    db.commit()
    from ruqqus.helpers.media import attach
    path = fresh(owner).query(attach._models()[0]).filter_by(id=asset_id).first().path
    here = viewer.browser()
    assert here.get(path, base_url=BASE).status_code == 410             # allowed: refused only for the missing account
    db = fresh(owner)
    db.execute(text("UPDATE users SET is_banned = 1, unban_utc = 0 WHERE id = :u"), {"u": owner.id})
    db.commit()
    assert here.get(path, base_url=BASE).status_code == 404
    assert post(here, f"/api/stories/{sid}/view", {"formkey": viewer.formkey(here)}).status_code == 404
    assert post(here, f"/api/stories/{sid}/report", {"reason": "x", "formkey": viewer.formkey(here)}).status_code == 404


def test_an_admin_finds_an_account_by_an_address_with_an_underscore(make_member, real_app):
    """The lookup compares exactly, but escaped `_` as for a LIKE: every address with one was never found."""
    admin, member = _admin(make_member), make_member()
    address = f"first_last_{secrets.token_hex(3)}@example.org"
    db = fresh(member)
    db.execute(text("UPDATE users SET email = :e WHERE id = :u"), {"e": address, "u": member.id})
    db.commit()
    answer = admin.browser().get(f"/admin/email/{address}", base_url=BASE)
    assert answer.status_code == 302 and member.username in answer.headers["Location"]


def test_granting_a_badge_goes_back_to_the_form_and_a_bad_badge_is_not_a_500(make_member, real_app):
    """Errors redirected to /badge_grant, which has no page; a missing or unknown badge id was a 500."""
    admin = _admin(make_member)
    here = admin.browser()
    nobody = post(here, "/badge_grant", {"username": f"nobody{secrets.token_hex(4)}", "badge_id": "1",
                                         "formkey": admin.formkey(here)})
    assert nobody.status_code == 302 and nobody.headers["Location"].endswith("/admin/badge_grant?error=no_user")
    member = make_member()
    for bad in ("", "x", "999999"):
        answer = post(here, "/badge_grant", {"username": member.username, "badge_id": bad, "formkey": admin.formkey(here)})
        assert answer.status_code in (400, 403), (bad, answer.status_code)


def _oauth_app(member, *uris):
    db = fresh(member)
    client_id, secret = secrets.token_hex(16), secrets.token_hex(32)
    app_id = db.execute(text("INSERT INTO oauth_apps (client_id, client_secret, app_name, redirect_uri, author_id, is_banned) "
                             "VALUES (:c, :s, 'ci app', :r, :u, false) RETURNING id"),
                        {"c": client_id, "s": secret, "r": ",".join(uris), "u": member.id}).scalar()
    db.commit()
    return app_id, client_id, secret


def test_oauth_redirects_are_encoded_codes_expire_and_odd_input_is_not_a_500(make_member, real_app):
    """A code never expired; the state went into the redirect unencoded after a second `?`; `localhost.<anything>`
    passed the http check on the prompt while `localhost:8080` failed it on the answer; `permanent=abc`, missing
    scopes and a missing description were 500s; rerolling or rescinding something missing was a 500."""
    from urllib.parse import parse_qs, urlparse
    member = make_member()
    _, client_id, secret = _oauth_app(member, "https://example.org/cb?x=1", "http://localhost.evil.example/cb",
                                      "http://localhost:8080/cb")
    here = member.browser()
    prompt = here.get("/oauth/authorize", base_url=BASE, query_string={
        "client_id": client_id, "scope": "identity", "redirect_uri": "http://localhost.evil.example/cb", "state": "s"})
    assert prompt.status_code == 400

    def authorize(**over):
        form = {"client_id": client_id, "scopes": "identity", "state": "a&code=forged", "permanent": "0",
                "redirect_uri": "https://example.org/cb?x=1", "formkey": member.formkey(here)}
        form.update(over)
        return post(here, "/oauth/authorize", {k: v for k, v in form.items() if v is not None})

    local = authorize(redirect_uri="http://localhost:8080/cb")
    assert local.status_code == 302, local.get_data(as_text=True)[:200]
    answer = authorize()
    assert answer.status_code == 302
    where = urlparse(answer.headers["Location"])
    query = parse_qs(where.query)
    assert where.path == "/cb" and query["x"] == ["1"] and query["state"] == ["a&code=forged"] and len(query["code"]) == 1
    assert authorize(permanent="abc").status_code < 500
    assert authorize(scopes=None).status_code == 400

    db = fresh(member)
    db.execute(text("UPDATE client_auths SET access_token_expire_utc = :t WHERE oauth_code = :c"),
               {"t": int(time.time()) - 1, "c": query["code"][0]})
    db.commit()
    grant = real_app[0].test_client().post("/oauth/grant", base_url=BASE, data={
        "client_id": client_id, "client_secret": secret, "grant_type": "code", "code": query["code"][0]})
    assert grant.status_code == 401

    fresh_code = parse_qs(urlparse(authorize().headers["Location"]).query)["code"][0]
    good = real_app[0].test_client().post("/oauth/grant", base_url=BASE, data={
        "client_id": client_id, "client_secret": secret, "grant_type": "code", "code": fresh_code})
    assert good.status_code == 200 and good.get_json()["expires_at"] > time.time() + 3000

    assert post(here, "/oauth/reroll/zzzzzz", {"formkey": member.formkey(here)}).status_code == 404
    assert post(here, "/oauth/rescind/zzzzzz", {"formkey": member.formkey(here)}).status_code == 404
    made = post(here, "/help/api_keys", {"name": "x", "redirect_uri": "https://example.org/cb", "formkey": member.formkey(here)})
    assert made.status_code < 500
    assert post(here, "/edit_app/abc", {"formkey": member.formkey(here)}).status_code == 404


def test_unblocking_never_joins_a_requests_recipient_and_survives_an_unreachable_chat_server(make_member, monkeypatch):
    """After an unblock both people were joined to the room, also the recipient of a request they never accepted; and
    a connection error from the chat server was a 500 that rolled the unblock back."""
    import requests
    from ruqqus.helpers import matrix_client
    a, b = make_member(), make_member()
    room = f"!room{secrets.token_hex(4)}:chat.localhost"
    db = fresh(a)
    low, high = min(a.id, b.id), max(a.id, b.id)
    db.execute(text("INSERT INTO chat_conversations (user_a_id, user_b_id, initiator_id, matrix_room_id, status) "
                    "VALUES (:l, :h, :i, :r, 'request')"), {"l": low, "h": high, "i": a.id, "r": room})
    for member in (a, b):
        db.execute(text("INSERT INTO chat_identities (user_id, matrix_user_id) VALUES (:u, :m) ON CONFLICT DO NOTHING"),
                   {"u": member.id, "m": f"@ci_{member.id}:chat.localhost"})
    db.commit()
    calls = []
    monkeypatch.setattr(matrix_client, "ban_user_as_bot", lambda r, m: calls.append(("ban", m)))
    monkeypatch.setattr(matrix_client, "unban_user_as_bot", lambda r, m: calls.append(("unban", m)))
    monkeypatch.setattr(matrix_client, "invite_as_bot", lambda r, m: calls.append(("invite", m)))
    monkeypatch.setattr(matrix_client, "put_in_room", lambda r, m: calls.append(("in", m)))
    here = a.browser()
    assert post(here, "/settings/block", {"username": b.username, "formkey": a.formkey(here)}).status_code < 400
    here = a.browser()
    assert post(here, "/settings/unblock", {"username": b.username, "formkey": a.formkey(here)}).status_code < 400
    recipient = f"@ci_{b.id}:chat.localhost"
    assert ("invite", recipient) in calls and ("in", recipient) not in calls

    def down(*args):
        raise requests.ConnectionError("chat server down")
    monkeypatch.setattr(matrix_client, "unban_user_as_bot", down)
    monkeypatch.setattr(matrix_client, "ban_user_as_bot", down)
    here = a.browser()
    assert post(here, "/settings/block", {"username": b.username, "formkey": a.formkey(here)}).status_code < 400
    here = a.browser()
    assert post(here, "/settings/unblock", {"username": b.username, "formkey": a.formkey(here)}).status_code < 400
    assert fresh(a).execute(text("SELECT count(*) FROM userblocks WHERE user_id = :a AND target_id = :b"),
                            {"a": a.id, "b": b.id}).scalar() == 0


def test_the_ip_ban_by_username_covers_comments_and_spares_higher_admins(make_member, guildmaster_with_post):
    """/admin/user_ipban was a copy of the ipnuke without its fixes: any comment outside Tor was a 500, and an
    admin's own level never mattered."""
    _, author, copy_id = guildmaster_with_post
    admin = _admin(make_member, 7)
    cid = _comment_on(author, copy_id)
    signed_up, commented = _doc_ip(), _doc_ip()
    db = fresh(admin)
    db.execute(text("UPDATE users SET creation_ip = :i, creation_region = 'GB' WHERE id = :u"), {"i": signed_up, "u": author.id})
    db.execute(text("UPDATE comments SET creation_ip = :i, creation_region = 'GB' WHERE id = :c"), {"i": commented, "c": cid})
    db.commit()
    try:
        client = admin.browser()
        senior = _admin(make_member, 9)
        assert post(client, "/admin/user_ipban", {"username": senior.username, "formkey": admin.formkey(client)}).status_code == 403
        assert post(client, "/admin/user_ipban", {"username": "nobody" + secrets.token_hex(4),
                                                  "formkey": admin.formkey(client)}).status_code == 404
        done = post(client, "/admin/user_ipban", {"username": author.username, "formkey": admin.formkey(client)})
        assert done.status_code == 200, done.get_data(as_text=True)[:300]
        assert _banned(fresh(admin), signed_up, commented) == 2
    finally:
        db = fresh(admin)
        # every ban this admin made: the target's alts are banned too, and in these tests every member is an alt of
        # the others (they all sign in from the test client's address), which would leave that address banned
        db.execute(text("DELETE FROM ips WHERE addr IN (:a, :b) OR banned_by = :v"),
                   {"a": signed_up, "b": commented, "v": admin.id})
        db.commit()


def test_admins_read_a_calls_chat_they_moderate(make_member, real_app):
    """A call's chat is read only by the people in the call or running it; admins remove its messages, so they read
    it too (the review of that fix)."""
    host, admin, other = make_member(), _admin(make_member, 3), make_member()
    sid = _board_space(host)
    db = fresh(host)
    db.execute(text("UPDATE spaces SET kind = 'call' WHERE id = :s"), {"s": sid})
    db.execute(text("INSERT INTO space_messages (space_id, author_id, body, created_utc) VALUES (:s, :a, 'hello', :n)"),
               {"s": sid, "a": host.id, "n": int(time.time())})
    db.commit()
    seen = admin.browser().get(f"/api/spaces/{sid}/messages", base_url=BASE)
    assert seen.status_code == 200 and len(seen.get_json()["messages"]) == 1
    assert seen.get_json()["messages"][0]["may_remove"] is True
    outsider = other.browser().get(f"/api/spaces/{sid}/messages", base_url=BASE)
    assert outsider.status_code == 200 and outsider.get_json()["messages"] == []


def test_the_wiki_editor_never_starts_from_a_revision_the_admins_removed(guildmaster_with_post):
    """The edit form put the current revision's text in the box even when the admins had removed it, and saving it
    published it again."""
    mod, _, _ = guildmaster_with_post
    slug, secret = f"p{secrets.token_hex(4)}", f"home address {secrets.token_hex(4)}"
    db = fresh(mod)
    db.execute(text("UPDATE boards SET wiki_enabled = true WHERE id = 1"))
    pid = db.execute(text("INSERT INTO guild_wiki_pages (board_id, slug, title) VALUES (1, :s, 'Page') RETURNING id"),
                     {"s": slug}).scalar()
    rid = db.execute(text("INSERT INTO guild_wiki_revisions (page_id, editor_id, body, body_html, word_severity, reason, "
                          "created_utc, removed_utc) VALUES (:p, :u, :b, :b, 0, '', :n, :n) RETURNING id"),
                     {"p": pid, "u": mod.id, "b": secret, "n": int(time.time())}).scalar()
    db.execute(text("UPDATE guild_wiki_pages SET current_revision_id = :r WHERE id = :p"), {"r": rid, "p": pid})
    db.commit()
    try:
        page = mod.browser().get(f"/+general/wiki/{slug}/edit", base_url=BASE)
        assert page.status_code == 200
        assert secret not in page.get_data(as_text=True)
    finally:
        db = fresh(mod)
        db.execute(text("UPDATE boards SET wiki_enabled = false WHERE id = 1"))
        db.commit()


def test_a_removed_member_of_a_private_guild_no_longer_finds_its_posts(make_member, guildmaster_with_post):
    """Feeds and search counted a guild as yours for any contributor row, but removing a member only marks the row
    inactive: a removed member still found the private guild's posts."""
    from ruqqus.classes import ContributorRelationship
    mod, author, copy_id = guildmaster_with_post
    db = fresh(mod)
    word = f"zq{secrets.token_hex(5)}"
    db.execute(text("UPDATE submissions_aux SET title = :t WHERE id = :s"), {"t": f"Private {word}", "s": copy_id})
    db.execute(text("UPDATE submissions SET post_public = false WHERE id = :s"), {"s": copy_id})
    db.execute(text("UPDATE boards SET is_private = true WHERE id = 1"))
    db.commit()
    try:
        removed = make_member()
        db = removed.db_session()
        db.add(ContributorRelationship(user_id=removed.id, board_id=1, is_active=False, created_utc=int(time.time())))
        db.commit()
        seen = mod.browser().get("/search", base_url=BASE, query_string={"q": word})
        assert f"Private {word}" in seen.get_data(as_text=True), "a guildmaster finds it"
        found = removed.browser().get("/search", base_url=BASE, query_string={"q": word})
        assert found.status_code == 200 and f"Private {word}" not in found.get_data(as_text=True)
    finally:
        db = fresh(mod)
        db.execute(text("UPDATE boards SET is_private = false WHERE id = 1"))
        db.execute(text("UPDATE submissions SET post_public = true WHERE id = :s"), {"s": copy_id})
        db.commit()


def test_exiling_never_tells_a_guildmaster_who_wrote_an_anonymous_item(make_member, guildmaster_with_post):
    """"Hasn't participated" counted anonymous comments, so an exile by name answered whether that account wrote an
    anonymous item in the guild. Its named comments still count."""
    mod, _, copy_id = guildmaster_with_post
    hidden, named = make_member(), make_member()
    cid = _comment_on(hidden, copy_id)
    _comment_on(named, copy_id)
    db = fresh(mod)
    db.execute(text("UPDATE comments SET is_anonymous = true WHERE id = :c"), {"c": cid})
    db.commit()
    here = mod.browser()
    refused = post(here, "/mod/exile/general", {"username": hidden.username, "formkey": mod.formkey(here)})
    assert refused.status_code == 403 and "participated" in refused.get_json()["error"]
    done = post(here, "/mod/exile/general", {"username": named.username, "formkey": mod.formkey(here)})
    assert done.status_code < 400, done.get_data(as_text=True)[:300]


def test_removing_a_contributor_through_the_v2_api_works_and_an_odd_thing_is_not_a_500(make_member, guildmaster_with_post,
                                                                                         real_app):
    """DELETE /api/v2/guilds/<guildname>/contributors called the view with `guildname` where it wanted `bid` (a
    TypeError on every call); an exile naming a user or a guild as its `thing` was a 500."""
    from ruqqus.classes import ContributorRelationship
    from ruqqus.helpers.base36 import base36encode
    app, _ = real_app
    mod, _, _ = guildmaster_with_post
    member = make_member()
    db = member.db_session()
    db.add(ContributorRelationship(user_id=member.id, board_id=1, is_active=True, created_utc=int(time.time())))
    db.commit()
    db = fresh(mod)
    db.execute(text("UPDATE client_auths SET scope_guildmaster = true WHERE access_token = :t"),
               {"t": (token := _app_token(mod))})
    db.commit()
    answer = app.test_client().delete("/api/v2/guilds/general/contributors", base_url=BASE,
                                      data={"username": member.username},
                                      headers={"Authorization": f"Bearer {token}", "User-Agent": "ci"},
                                      environ_base={"REMOTE_ADDR": addr()})
    assert answer.status_code < 400, answer.get_data(as_text=True)[:300]
    assert fresh(mod).execute(text("SELECT is_active FROM contributors WHERE user_id = :u AND board_id = 1"),
                              {"u": member.id}).scalar() is False
    here = mod.browser()
    odd = post(here, "/mod/exile/general", {"username": member.username, "thing": f"t1_{base36encode(member.id)}",
                                            "formkey": mod.formkey(here)})
    assert odd.status_code == 400


def test_switching_two_factor_off_with_the_recovery_code_signs_out_the_other_browsers(make_member):
    """From Settings, turning two-factor off ends every other sign-in; the recovery code at sign-in didn't."""
    import pyotp
    from .test_account_security import sign_in, signed_in
    member = make_member()
    elsewhere = member.browser()                # signed in before two-factor (a stolen session, say)
    assert signed_in(elsewhere)
    db = member.db_session()
    user = member.user()
    user.mfa_secret = pyotp.random_base32()
    db.add(user)
    db.commit()
    assert sign_in(member, member.user().mfa_removal_code.lower().replace(" ", "")).status_code == 302
    assert member.user().mfa_secret is None
    assert not signed_in(elsewhere)


def _curation(member, *, private, board_id=1):
    db = fresh(member)
    slug = f"c{secrets.token_hex(5)}"
    cid = db.execute(text("INSERT INTO curations (owner_id, name, slug, is_private, created_utc) VALUES (:o, :n, :s, :p, :t) "
                          "RETURNING id"), {"o": member.id, "n": f"Cur {slug}", "s": slug, "p": private, "t": int(time.time())}).scalar()
    db.execute(text("INSERT INTO curation_guilds (curation_id, board_id, created_utc) VALUES (:c, :b, 0)"),
               {"c": cid, "b": board_id})
    db.commit()
    return cid, slug


def test_a_private_guild_never_leaves_a_curation_through_publishing_forking_or_its_feed(make_member, guildmaster_with_post):
    """A curation with a private guild could be made public; forking a public one copied (and named, on the fork's
    edit page) a guild that had turned private; and its feed still showed that guild's earlier public posts."""
    mod, _, copy_id = guildmaster_with_post
    owner, forker = make_member(), make_member()
    word = f"zc{secrets.token_hex(5)}"
    db = fresh(mod)
    db.execute(text("UPDATE submissions_aux SET title = :t WHERE id = :s"), {"t": f"Curated {word}", "s": copy_id})
    db.commit()
    cid, slug = _curation(owner, private=False)
    try:
        assert f"Curated {word}" in forker.browser().get(f"/&{slug}?sort=new", base_url=BASE).get_data(as_text=True)
        db = fresh(mod)
        db.execute(text("UPDATE boards SET is_private = true WHERE id = 1"))
        db.commit()
        assert f"Curated {word}" not in forker.browser().get(f"/&{slug}?sort=new", base_url=BASE).get_data(as_text=True)
        here = forker.browser()
        assert post(here, f"/&{slug}/fork", {"formkey": forker.formkey(here)}).status_code in (200, 302)
        assert fresh(mod).execute(text("SELECT count(*) FROM curation_guilds cg JOIN curations c ON c.id = cg.curation_id "
                                       "WHERE c.owner_id = :f"), {"f": forker.id}).scalar() == 0
        _, private_slug = _curation(owner, private=True)
        here = owner.browser()
        made_public = post(here, f"/&{private_slug}/edit", {"name": "Now public", "is_private": "false",
                                                            "formkey": owner.formkey(here)})
        assert made_public.status_code == 409
    finally:
        db = fresh(mod)
        db.execute(text("UPDATE boards SET is_private = false WHERE id = 1"))
        db.commit()


def test_reporting_and_bookmarking_a_post_in_a_private_guild_need_its_membership(make_member, guildmaster_with_post):
    """The flag and bookmark routes answered for any post id, so an outsider could confirm and act on a post in a
    private guild (and the flag routes read a Circle post as a visitor, so its own members couldn't report it)."""
    from ruqqus.helpers.base36 import base36encode
    mod, _, copy_id = guildmaster_with_post
    outsider = make_member()
    db = fresh(mod)
    db.execute(text("UPDATE submissions SET post_public = false, is_pinned = false WHERE id = :s"), {"s": copy_id})
    db.execute(text("UPDATE boards SET is_private = true WHERE id = 1"))
    db.commit()
    try:
        here = outsider.browser()
        pid = base36encode(copy_id)
        assert post(here, f"/api/flag/post/{pid}", {"report_type": "admin", "formkey": outsider.formkey(here)}).status_code == 404
        assert post(here, "/bookmarks/move", {"kind": "post", "id": pid, "folder_id": "none",
                                              "formkey": outsider.formkey(here)}).status_code == 404
        assert fresh(mod).execute(text("SELECT count(*) FROM save_relationship WHERE user_id = :u"),
                                  {"u": outsider.id}).scalar() == 0
        inside = mod.browser()
        assert post(inside, f"/api/flag/post/{pid}", {"report_type": "admin", "formkey": mod.formkey(inside)}).status_code == 204
    finally:
        db = fresh(mod)
        db.execute(text("UPDATE boards SET is_private = false WHERE id = 1"))
        db.execute(text("UPDATE submissions SET post_public = true WHERE id = :s"), {"s": copy_id})
        db.commit()


def test_a_rename_spends_no_coins_while_the_account_owes_or_is_suspended(make_member):
    """The name change spends 20 coins with no check of what the account owes after a chargeback, and a suspended
    account could rename itself."""
    owes, suspended = make_member(), make_member()
    db = fresh(owes)
    db.execute(text("UPDATE users SET coin_balance = 50 WHERE id IN (:a, :b)"), {"a": owes.id, "b": suspended.id})
    db.execute(text("UPDATE users SET negative_balance_cents = 300 WHERE id = :a"), {"a": owes.id})
    db.commit()
    here = owes.browser()
    answer = post(here, "/settings/name_change", {"name": f"n{secrets.token_hex(5)}", "formkey": owes.formkey(here)})
    assert answer.status_code == 402
    them = suspended.browser()
    db = fresh(owes)
    db.execute(text("UPDATE users SET is_banned = 1, unban_utc = 0 WHERE id = :b"), {"b": suspended.id})
    db.commit()
    post(them, "/settings/name_change", {"name": f"n{secrets.token_hex(5)}", "formkey": suspended.formkey(them)})
    rows = dict(fresh(owes).execute(text("SELECT id, coin_balance FROM users WHERE id IN (:a, :b)"),
                                    {"a": owes.id, "b": suspended.id}).fetchall())
    assert rows == {owes.id: 50, suspended.id: 50}


def test_odd_input_is_refused_or_ignored_never_a_500(make_member, guildmaster_with_post, real_app):
    """A guild name ending in a newline passed the name check; an over-long guild description, a non-ASCII digit as
    a draft id, a circle id past a database integer, a page number past bigint and a non-numeric title id were 500s."""
    mod, _, _ = guildmaster_with_post
    boards = sys.modules["ruqqus.routes.boards"]
    assert boards.re.match(boards.valid_board_regex, "kittens\n") is None
    assert boards.re.match(boards.valid_board_regex, "kittens")
    here = mod.browser()
    long = post(here, "/mod/1/settings/description", {"description": "x" * 1600, "formkey": mod.formkey(here)})
    assert long.status_code == 400
    member = make_member()
    them = member.browser()
    draft = post(them, "/api/drafts", {"title": "A draft", "body": "text", "draft_id": "\u00b2",
                                       "formkey": member.formkey(them)})
    assert draft.status_code < 500, draft.get_data(as_text=True)[:200]
    assert them.get("/circles/99999999999", base_url=BASE).status_code == 404
    assert them.get(f"/@{member.username}/followers?page=99999999999999999999", base_url=BASE).status_code < 500
    assert post(them, "/settings/profile", {"title_id": "abc", "formkey": member.formkey(them)}).status_code == 400


def test_a_refused_tip_never_says_who_blocked_whom(make_member, guildmaster_with_post):
    """A tip to someone who blocked you said "someone that's blocking you"."""
    from ruqqus.helpers.base36 import base36encode
    _, author, copy_id = guildmaster_with_post
    tipper = make_member()
    here = author.browser()
    assert post(here, "/settings/block", {"username": tipper.username, "formkey": author.formkey(here)}).status_code < 400
    db = fresh(author)
    db.execute(text("UPDATE users SET coin_balance = 10 WHERE id = :u"), {"u": tipper.id})
    db.commit()
    them = tipper.browser()
    answer = post(them, f"/gift_post/{base36encode(copy_id)}", {"coins": "1", "formkey": tipper.formkey(them)})
    assert answer.status_code == 403
    assert "block" not in answer.get_data(as_text=True).lower()


def test_pinning_a_deleted_post_rings_nobody(guildmaster_with_post):
    """Pinning or heralding rang the guild's bell for a post that was deleted or removed: a notice leading to a 404."""
    from ruqqus.helpers.base36 import base36encode
    mod, _, copy_id = guildmaster_with_post
    db = fresh(mod)
    db.execute(text("UPDATE submissions SET deleted_utc = :n WHERE id = :s"), {"n": int(time.time()), "s": copy_id})
    db.commit()
    here = mod.browser()
    answer = post(here, f"/mod/post_pin/general/{base36encode(copy_id)}/1", {"formkey": mod.formkey(here)})
    assert answer.status_code < 500
    assert fresh(mod).execute(text("SELECT count(*) FROM bell_rings WHERE target_id = :s"), {"s": copy_id}).scalar() == 0
