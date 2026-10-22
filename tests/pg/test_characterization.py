"""Characterization tests for critical flows that had no behavioural test (bug hunt 2026-10, phase 1), on the real app
and a real Postgres. They pin only behaviour that must survive the fixes that follow: the correct paths around each
change. A wrong behaviour gets its own reproducing test in the commit that fixes it, so no test here needs editing to
accept a fix. PayPal is faked at the module's client object: nothing leaves the machine."""
import secrets
import sys
import time
from types import SimpleNamespace
from urllib.parse import urlsplit

import pytest
from sqlalchemy import text

BASE = "http://localhost"


def addr():
    return f"10.{secrets.randbelow(250)}.{secrets.randbelow(250)}.{secrets.randbelow(250) + 1}"


def post(client, path, data, **kw):
    return client.post(path, base_url=BASE, headers={"User-Agent": "ci"}, environ_base={"REMOTE_ADDR": addr()},
                       data=data, **kw)


def fresh(member):
    db = member.db_session()
    db.rollback()                                # a request may have left the shared session closed or aborted
    db.expire_all()
    return db


# ---------------------------------------------------------------- signing in and the password reset

def test_signing_in_lands_on_a_page_of_this_site(make_member, real_app):
    app, _ = real_app
    member = make_member()
    client = app.test_client()
    answer = post(client, "/login", {"username": member.username, "password": member.password, "redirect": "/settings/profile"})
    assert answer.status_code == 302 and urlsplit(answer.headers["Location"]).path == "/settings/profile"
    assert client.get("/settings/profile", base_url=BASE).status_code == 200, "signed in"


def test_a_wrong_password_does_not_sign_in(make_member, real_app):
    app, _ = real_app
    member = make_member()
    client = app.test_client()
    answer = post(client, "/login", {"username": member.username, "password": "not-" + member.password, "redirect": "/"})
    assert answer.status_code in (200, 401)
    assert client.get("/settings/profile", base_url=BASE).status_code in (302, 401), "still a visitor"


def test_a_signed_in_member_opening_the_login_page_goes_to_a_page_of_this_site(make_member):
    member = make_member()
    answer = member.browser().get("/login?redirect=/settings/security", base_url=BASE)
    assert answer.status_code == 302 and urlsplit(answer.headers["Location"]).path == "/settings/security"


def test_a_password_reset_with_a_valid_link_and_a_good_password_works_once(make_member, real_app):
    from ruqqus.helpers.security import generate_hash
    app, _ = real_app
    member = make_member()
    user = member.user()
    uid, nonce = user.id, user.login_nonce               # read now: the rows detach once a request runs
    now = int(time.time())
    forgot = generate_hash(f"{uid}+{now}+forgot+{nonce}")
    page = app.test_client().get(f"/reset?id={uid}&time={now}&token={forgot}", base_url=BASE)
    assert page.status_code == 200
    reset = generate_hash(f"{uid}+{now}+reset+{nonce}")
    new_password = "Reset-" + secrets.token_hex(6)
    done = post(app.test_client(), "/reset", {"user_id": uid, "time": now, "token": reset,
                                               "password": new_password, "confirm_password": new_password})
    assert done.status_code in (200, 302)
    assert fresh(member).execute(text("SELECT login_nonce FROM users WHERE id = :u"), {"u": uid}).scalar() > nonce
    signed = post(app.test_client(), "/login", {"username": member.username, "password": new_password, "redirect": "/"})
    assert signed.status_code == 302, "the new password signs in"
    again = post(app.test_client(), "/reset", {"user_id": uid, "time": now, "token": reset,
                                                "password": new_password, "confirm_password": new_password})
    assert again.status_code == 400, "the link works once (its token holds the old nonce)"


# ---------------------------------------------------------------- buying coins (PayPal faked)

@pytest.fixture
def fake_paypal(real_app, monkeypatch):
    paypal_routes = sys.modules["ruqqus.routes.paypal"]
    calls = SimpleNamespace(created=[], captured=[], verify="SUCCESS")

    def create(txn):
        calls.created.append((txn.coin_count, txn.usd_cents))
        txn.paypal_id = "ORDER-" + secrets.token_hex(6).upper()
        txn.status = 1

    def capture(txn):
        calls.captured.append(txn.paypal_id)
        txn.status = 3
        return True

    def _post(url, data=None):
        return SimpleNamespace(json=lambda: {"verification_status": calls.verify})

    monkeypatch.setattr(paypal_routes.CLIENT, "create", create)
    monkeypatch.setattr(paypal_routes.CLIENT, "capture", capture)
    monkeypatch.setattr(paypal_routes.CLIENT, "_post", _post)
    # PayPal's own record of a capture (asked for a refund of an older order): none here, and never the network
    monkeypatch.setattr(paypal_routes.CLIENT, "_get", lambda url: SimpleNamespace(json=lambda: {}))
    return calls


def test_buying_coins_charges_the_listed_price_and_credits_the_coins_once(make_member, fake_paypal):
    from ruqqus.helpers.base36 import base36encode
    member = make_member()
    client = member.browser()
    before = member.user().coin_balance or 0
    answer = post(client, "/shop/buy_coins", {"coin_count": "5", "formkey": member.formkey(client)})
    assert answer.status_code == 302 and answer.headers["Location"].startswith("https://www.paypal.com/checkoutnow?token=ORDER-")
    assert fake_paypal.created == [(5, 499)]                     # the price list: 4 to 11 coins at 100n - 1 cents
    txn_id = fresh(member).execute(text("SELECT id FROM paypal_txns WHERE user_id = :u ORDER BY id DESC"),
                                   {"u": member.id}).scalar()
    done = client.get(f"/shop/buy_coins_completed?txid={base36encode(txn_id)}", base_url=BASE)
    assert done.status_code == 200
    user = member.user()
    # five coins arrive; buying coins also starts a week of Premium, which spends one (helpers/premium.py)
    assert user.coin_balance in (before + 5, before + 4)
    status = fresh(member).execute(text("SELECT status FROM paypal_txns WHERE id = :t"), {"t": txn_id}).scalar()
    assert status == 3
    assert client.get(f"/shop/buy_coins_completed?txid={base36encode(txn_id)}", base_url=BASE).status_code == 400, \
        "a completed order is not credited twice"


def test_a_webhook_that_paypal_did_not_sign_is_refused(real_app, fake_paypal):
    app, _ = real_app
    fake_paypal.verify = "FAILURE"
    answer = app.test_client().post("/shop/paypal_webhook", base_url=BASE, json={"event_type": "PAYMENT.CAPTURE.REFUNDED",
                                                                                   "resource": {"id": "X"}})
    assert answer.status_code == 403


# ---------------------------------------------------------------- guild moderation

@pytest.fixture
def guildmaster_with_post(make_member):
    """A member who is a guildmaster of +general with every permission, and a post of someone else forwarded there."""
    from ruqqus.classes import ModRelationship, Submission
    from ruqqus.helpers.base36 import base36decode
    mod = make_member()
    db = mod.db_session()
    db.add(ModRelationship(user_id=mod.id, board_id=1, accepted=True, invite_rescinded=False, perm_full=True))
    db.commit()
    author = make_member()
    here = author.browser()
    answer = post(here, "/api/vue/submit", {"title": f"Char {secrets.token_hex(4)}", "body": "text",
                                            "formkey": author.formkey(here), "post_to_profile": "1", "forward_guilds": "general"})
    assert answer.status_code == 200, answer.get_data(as_text=True)[:300]
    original = base36decode(answer.get_json()["id"])
    db = fresh(mod)
    copy = db.query(Submission).filter(Submission.repost_id == original, Submission.board_id == 1).first()
    return mod, author, copy.id


def test_a_guildmaster_hides_a_post_with_a_plain_reason_and_readers_see_it(guildmaster_with_post):
    from ruqqus.helpers.base36 import base36encode
    mod, author, copy_id = guildmaster_with_post
    client = mod.browser()
    reason = "Off topic for this guild " + secrets.token_hex(3)
    answer = post(client, f"/mod/kick/general/{base36encode(copy_id)}", {"reason": reason, "formkey": mod.formkey(client)})
    assert answer.status_code in (200, 204, 302)
    row = fresh(mod).execute(text("SELECT s.hidden_by_guild, a.hidden_reason FROM submissions s "
                                  "JOIN submissions_aux a ON a.id = s.id WHERE s.id = :p"), {"p": copy_id}).first()
    assert row[0] is True and row[1] == reason
    page = author.browser().get(f"/post/{base36encode(copy_id)}", base_url=BASE, follow_redirects=True)
    assert reason in page.get_data(as_text=True), "the reason is shown to readers"
    log = author.browser().get("/+general/mod/log", base_url=BASE)
    assert log.status_code == 200 and reason in log.get_data(as_text=True), "and in the public mod log"
    missing = post(client, f"/mod/kick/general/{base36encode(copy_id)}", {"reason": " ", "formkey": mod.formkey(client)})
    assert missing.status_code == 400, "a reason is required"


def test_a_guilds_stylesheet_keeps_plain_rules_and_refuses_an_outside_address(guildmaster_with_post):
    mod, _, _ = guildmaster_with_post
    client = mod.browser()
    ok = post(client, "/mod/1/settings/css", {"css": ".post-title { color: #123456; }", "formkey": mod.formkey(client)})
    assert ok.status_code in (200, 204, 302)
    css = fresh(mod).execute(text("SELECT css FROM boards WHERE id = 1")).scalar()
    assert "#123456" in css
    refused = post(client, "/mod/1/settings/css", {"css": "body { background: url(https://example.org/x.png); }",
                                                     "formkey": mod.formkey(client)})
    assert refused.status_code == 422
    assert "example.org" not in fresh(mod).execute(text("SELECT css FROM boards WHERE id = 1")).scalar()


# ---------------------------------------------------------------- admin tools

def test_the_admin_ip_page_opens_for_level_4_and_only_for_them(make_member):
    admin, member = make_member(), make_member()
    db = admin.db_session()
    db.execute(text("UPDATE users SET admin_level = 4 WHERE id = :u"), {"u": admin.id})
    db.commit()
    assert admin.browser().get("/admin/get_ip", base_url=BASE).status_code == 200
    assert member.browser().get("/admin/get_ip", base_url=BASE).status_code in (403, 404)
