"""Sign-ins and credentials, on the real app: what a changed password, switched 2FA or a reset does to the account's
other browsers. A sign-in is valid while its cookie's nonce is at least the account's (`wrappers.get_logged_in_user`)."""
import secrets
import time

import pyotp

BASE = "http://localhost"
HEADERS = {"User-Agent": "ci-security"}


def get(client, path):
    return client.get(path, base_url=BASE, headers=HEADERS)


def post(client, path, data):
    return client.post(path, base_url=BASE, headers=HEADERS, data=data, environ_base={"REMOTE_ADDR": fresh_address()})


def fresh_address():
    """A client address no earlier run used: the login throttle's heat per address lives in Redis between runs."""
    return f"10.{secrets.randbelow(250)}.{secrets.randbelow(250)}.{secrets.randbelow(250) + 1}"


def signed_in(client):
    return get(client, "/settings/security").status_code == 200


def test_changing_the_password_signs_out_the_other_browsers_and_keeps_this_one(make_member):
    member = make_member()
    here, elsewhere = member.browser(), member.browser()
    assert signed_in(here) and signed_in(elsewhere)

    answer = post(here, "/settings/security", {"old_password": member.password, "new_password": "Another-Passw0rd",
                                              "cnf_password": "Another-Passw0rd", "formkey": member.formkey(here)})
    assert answer.status_code == 302 and "msg=" in answer.headers["Location"], answer.headers.get("Location")
    assert "signed+out" in answer.headers["Location"] or "signed%20out" in answer.headers["Location"] or "signed out" in answer.headers["Location"]

    assert signed_in(here), "the browser that changed the password stays signed in"
    assert not signed_in(elsewhere), "another browser must be signed out"
    assert member.user().verifyPass("Another-Passw0rd")


def test_a_wrong_old_password_signs_nobody_out(make_member):
    member = make_member()
    here, elsewhere = member.browser(), member.browser()
    post(here, "/settings/security", {"old_password": "not-the-password", "new_password": "Another-Passw0rd",
                                     "cnf_password": "Another-Passw0rd", "formkey": member.formkey(here)})
    assert signed_in(here) and signed_in(elsewhere)


def test_switching_two_factor_on_and_off_signs_out_the_other_browsers(make_member):
    member = make_member()
    here, elsewhere = member.browser(), member.browser()
    secret = pyotp.random_base32()
    totp = pyotp.TOTP(secret)

    answer = post(here, "/settings/security", {"password": member.password, "2fa_secret": secret,
                                              "2fa_token": totp.now(), "formkey": member.formkey(here)})
    assert answer.status_code == 200 and "Your backup codes" in answer.get_data(as_text=True)    # shown, not redirected
    assert member.user().mfa_secret == secret
    assert signed_in(here) and not signed_in(elsewhere)

    again = member.browser()                                   # signed in after 2FA is on
    assert signed_in(again)
    answer = post(here, "/settings/security", {"password": member.password, "2fa_remove": totp.at(time.time() + 30),
                                              "formkey": member.formkey(here)})
    assert answer.status_code == 302 and "msg=" in answer.headers["Location"], answer.headers.get("Location")
    assert member.user().mfa_secret is None
    assert signed_in(here) and not signed_in(again)


def test_a_failed_two_factor_change_signs_nobody_out(make_member):
    member = make_member()
    here, elsewhere = member.browser(), member.browser()
    post(here, "/settings/security", {"password": member.password, "2fa_secret": pyotp.random_base32(),
                                     "2fa_token": "000000", "formkey": member.formkey(here)})
    assert member.user().mfa_secret is None and signed_in(here) and signed_in(elsewhere)


def test_log_out_all_others_still_works(make_member):
    member = make_member()
    here, elsewhere = member.browser(), member.browser()
    answer = post(here, "/settings/log_out_all_others", {"password": member.password, "formkey": member.formkey(here)})
    assert answer.status_code == 200
    assert signed_in(here) and not signed_in(elsewhere)


def test_a_password_reset_ends_every_sign_in_and_the_link_works_once(make_member):
    from ruqqus.helpers.security import generate_hash
    member = make_member()
    elsewhere = member.browser()
    assert signed_in(elsewhere)

    now = int(time.time())
    token = generate_hash(f"{member.id}+{now}+reset+{member.user().login_nonce}")
    form = {"user_id": member.id, "time": now, "token": token, "password": "Reset-Passw0rd1", "confirm_password": "Reset-Passw0rd1"}
    visitor = member.app.test_client()
    assert post(visitor, "/reset", form).status_code == 200
    assert member.user().verifyPass("Reset-Passw0rd1")
    assert not signed_in(elsewhere), "a reset must end the sign-ins of whoever held the old password"

    other = "Other-" + secrets.token_hex(6)                      # a second use of the same link tries to set this
    again = post(member.app.test_client(), "/reset", dict(form, password=other, confirm_password=other))
    assert again.status_code == 400, "the same reset link must not work twice"
    assert member.user().verifyPass("Reset-Passw0rd1")


# ---- two-factor backup codes ----

import re  # noqa: E402


def enable_two_factor(member):
    """Turns two-factor on through Settings; returns (secret, the codes shown, the browser that did it)."""
    here = member.browser()
    secret = pyotp.random_base32()
    answer = post(here, "/settings/security", {"password": member.password, "2fa_secret": secret,
                                              "2fa_token": pyotp.TOTP(secret).now(), "formkey": member.formkey(here)})
    assert answer.status_code == 200, (answer.status_code, answer.get_data(as_text=True)[:300])
    assert answer.headers["Cache-Control"] == "no-store"
    codes = re.findall(r"<li>([A-Z2-9]{5}-[A-Z2-9]{5})</li>", answer.get_data(as_text=True))
    return secret, codes, here


def sign_in(member, answer, address=None):
    """The two steps of the sign-in form: password, then the second factor. Returns the second step's response."""
    visitor = member.app.test_client()
    environ = {"REMOTE_ADDR": address or fresh_address()}
    first = visitor.post("/login", base_url=BASE, headers=HEADERS, environ_base=environ,
                         data={"username": member.username, "password": member.password})
    page = first.get_data(as_text=True)
    assert first.status_code == 200 and 'name="hash"' in page, page[:300]
    form = {"username": member.username, "2fa_token": answer, "redirect": "/",
            "time": re.search(r'name="time" value="(\d+)"', page).group(1),
            "hash": re.search(r'name="hash" value="([^"]+)"', page).group(1)}
    return visitor.post("/login", base_url=BASE, headers=HEADERS, environ_base=environ, data=form)


def test_turning_two_factor_on_shows_ten_backup_codes_once(make_member):
    member = make_member()
    secret, codes, here = enable_two_factor(member)
    assert len(codes) == len(set(codes)) == 10
    page = get(here, "/settings/security").get_data(as_text=True)
    assert "10 unused backup codes left." in page
    assert not any(code in page for code in codes), "the codes are shown once and never again"


def test_a_backup_code_signs_in_once_and_leaves_two_factor_on(make_member):
    member = make_member()
    secret, codes, _ = enable_two_factor(member)

    first = sign_in(member, codes[0].lower())                      # typed in lower case, with its hyphen
    assert first.status_code == 302, first.get_data(as_text=True)[:300]
    assert member.user().mfa_secret == secret, "using a backup code must not switch two-factor off"

    again = sign_in(member, codes[0])
    assert again.status_code == 200 and "Invalid verification code" in again.get_data(as_text=True)

    other = sign_in(member, codes[1].replace("-", " "))
    assert other.status_code == 302
    page = get(member.browser(), "/settings/security").get_data(as_text=True)
    assert "8 unused backup codes left." in page


def test_an_authenticator_code_cannot_be_used_twice(make_member):
    member = make_member()
    secret, _, _ = enable_two_factor(member)
    token = pyotp.TOTP(secret).at(time.time() + 30)               # the step after the one that switched it on
    assert sign_in(member, token).status_code == 302
    replay = sign_in(member, token)
    assert replay.status_code == 200 and "Invalid verification code" in replay.get_data(as_text=True)


def test_the_code_that_switched_two_factor_on_cannot_be_used_to_sign_in_at_once(make_member):
    member = make_member()
    secret = pyotp.random_base32()
    here = member.browser()
    token = pyotp.TOTP(secret).now()
    post(here, "/settings/security", {"password": member.password, "2fa_secret": secret, "2fa_token": token,
                                     "formkey": member.formkey(here)})
    assert member.user().mfa_secret == secret
    assert sign_in(member, token).status_code == 200


def test_new_backup_codes_replace_the_old_ones(make_member):
    member = make_member()
    secret, old, here = enable_two_factor(member)
    wrong = post(here, "/settings/security", {"password": member.password, "new_backup_codes": "000000",
                                             "formkey": member.formkey(here)})
    assert wrong.status_code == 302 and "error=" in wrong.headers["Location"]
    assert sign_in(member, old[0]).status_code == 302, "a refused request changes nothing"

    token = pyotp.TOTP(secret).at(time.time() + 30)
    made = post(here, "/settings/security", {"password": member.password, "new_backup_codes": token,
                                            "formkey": member.formkey(here)})
    assert made.status_code == 200 and made.headers["Cache-Control"] == "no-store"
    new = re.findall(r"<li>([A-Z2-9]{5}-[A-Z2-9]{5})</li>", made.get_data(as_text=True))
    assert len(new) == 10 and not set(new) & set(old)

    assert sign_in(member, old[1]).status_code == 200, "an old code must stop working"
    assert sign_in(member, new[0]).status_code == 302


def test_turning_two_factor_off_with_a_backup_code_removes_the_codes(make_member):
    member = make_member()
    secret, codes, here = enable_two_factor(member)
    off = post(here, "/settings/security", {"password": member.password, "2fa_remove": codes[0],
                                           "formkey": member.formkey(here)})
    assert off.status_code == 302 and "msg=" in off.headers["Location"], off.headers.get("Location")
    assert member.user().mfa_secret is None
    from sqlalchemy import text
    db = member.db_session()
    db.expire_all()
    assert db.execute(text("SELECT count(*) FROM mfa_backup_codes WHERE user_id = :u"), {"u": member.id}).scalar() == 0


def test_two_factor_already_on_is_not_replaced_by_a_password_and_a_new_device(make_member):
    member = make_member()
    secret, _, here = enable_two_factor(member)
    other_secret = pyotp.random_base32()
    refused = post(here, "/settings/security", {"password": member.password, "2fa_secret": other_secret,
                                               "2fa_token": pyotp.TOTP(other_secret).now(), "formkey": member.formkey(here)})
    assert refused.status_code == 302 and "already" in refused.headers["Location"], refused.headers["Location"]
    assert member.user().mfa_secret == secret


def test_the_older_recovery_code_works_only_for_an_account_with_no_backup_codes(make_member):
    legacy = make_member()                                         # two-factor switched on before backup codes existed
    db = legacy.db_session()
    user = legacy.user()
    user.mfa_secret = pyotp.random_base32()
    db.add(user)
    db.commit()
    code = legacy.user().mfa_removal_code.lower().replace(" ", "")
    assert sign_in(legacy, code).status_code == 302
    assert legacy.user().mfa_secret is None, "the older recovery code still turns two-factor off"

    modern = make_member()
    secret, codes, _ = enable_two_factor(modern)
    refused = sign_in(modern, modern.user().mfa_removal_code)
    assert refused.status_code == 200 and "Invalid verification code" in refused.get_data(as_text=True)
    assert modern.user().mfa_secret == secret


# ---- sign-in history ----

CHROME = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) "
          "Chrome/126.0.0.0 Safari/537.36")


def test_a_sign_in_is_recorded_and_listed_without_its_address(make_member):
    member = make_member()
    address = fresh_address()
    visitor = member.app.test_client()
    answer = visitor.post("/login", base_url=BASE, environ_base={"REMOTE_ADDR": address},
                          headers={"User-Agent": CHROME, "cf-ipcountry": "DE"},
                          data={"username": member.username, "password": member.password})
    assert answer.status_code == 302, answer.get_data(as_text=True)[:300]

    from sqlalchemy import text
    db = member.db_session()
    db.expire_all()
    row = db.execute(text("SELECT ip, cf_country, user_agent FROM login_events WHERE user_id = :u ORDER BY id DESC LIMIT 1"),
                     {"u": member.id}).first()
    assert row[0] == address and row[1] == "DE" and row[2] == CHROME

    page = get(member.browser(), "/settings/security").get_data(as_text=True)
    assert "Chrome on Windows" in page and "Germany" in page and "Recent sign-ins" in page
    assert address not in page and CHROME not in page, "neither the address nor the raw browser text is shown"


def test_a_page_with_no_sign_ins_says_so(make_member):
    member = make_member()                      # made directly, so it has never signed in
    page = get(member.browser(), "/settings/security").get_data(as_text=True)
    assert "No sign-ins recorded yet." in page


# ---- new sign-in emails ----

def verified(member):
    db = member.db_session()
    user = member.user()
    user.is_activated = True
    db.add(user)
    db.commit()


def sign_in_from(member, country, agent, address):
    visitor = member.app.test_client()
    answer = visitor.post("/login", base_url=BASE, environ_base={"REMOTE_ADDR": address},
                          headers={"User-Agent": agent, "cf-ipcountry": country},
                          data={"username": member.username, "password": member.password})
    assert answer.status_code == 302, answer.get_data(as_text=True)[:300]


FIREFOX = "Mozilla/5.0 (X11; Linux x86_64; rv:127.0) Gecko/20100101 Firefox/127.0"


def test_a_sign_in_from_somewhere_new_is_emailed_once_a_day_and_never_carries_the_address(make_member, monkeypatch):
    import gevent
    sent = []
    monkeypatch.setattr("ruqqus.routes.login.send_mail_quietly", lambda to, subject, html: sent.append((to, subject, html)))
    member = make_member()
    verified(member)

    sign_in_from(member, "GB", CHROME, fresh_address())             # the first sign-in: nothing to compare with
    gevent.sleep(0.2)
    assert sent == []
    sign_in_from(member, "GB", CHROME, fresh_address())             # the same country and browser
    gevent.sleep(0.2)
    assert sent == []

    where = fresh_address()
    sign_in_from(member, "FR", FIREFOX, where)
    gevent.sleep(0.2)
    assert len(sent) == 1
    to, subject, html = sent[0]
    assert to == f"{member.username}@example.com" and subject.startswith("New sign-in to your ")
    assert "Firefox on Linux" in html and "France" in html and "Change your password" in html
    assert where not in html, "the address is never in the email"
    assert "/settings/security" in html

    sign_in_from(member, "DE", FIREFOX, fresh_address())            # another new place, the same day
    gevent.sleep(0.2)
    assert len(sent) == 1, "at most one email a day"


def test_no_email_without_a_verified_address_or_when_switched_off(make_member, monkeypatch):
    import gevent
    sent = []
    monkeypatch.setattr("ruqqus.routes.login.send_mail_quietly", lambda to, subject, html: sent.append(subject))

    unverified = make_member()                                       # never verified its email
    sign_in_from(unverified, "GB", CHROME, fresh_address())
    sign_in_from(unverified, "FR", FIREFOX, fresh_address())

    quiet = make_member()
    verified(quiet)
    db = quiet.db_session()
    user = quiet.user()
    user.signin_alerts = False
    db.add(user)
    db.commit()
    sign_in_from(quiet, "GB", CHROME, fresh_address())
    sign_in_from(quiet, "FR", FIREFOX, fresh_address())
    gevent.sleep(0.2)
    assert sent == []


def test_the_setting_needs_the_password(make_member):
    member = make_member()
    here = member.browser()
    refused = post(here, "/settings/security", {"signin_alerts_form": "1", "password": "wrong", "formkey": member.formkey(here)})
    assert refused.status_code == 302 and "error=" in refused.headers["Location"]
    assert member.user().signin_alerts is True

    off = post(here, "/settings/security", {"signin_alerts_form": "1", "password": member.password,
                                           "formkey": member.formkey(here)})            # the switch is absent = off
    assert off.status_code == 302 and "msg=" in off.headers["Location"]
    assert member.user().signin_alerts is False
    assert "checked" not in get(here, "/settings/security").get_data(as_text=True).split('id="signin_alerts"')[1].split(">")[0]

    on = post(here, "/settings/security", {"signin_alerts_form": "1", "signin_alerts": "1", "password": member.password,
                                          "formkey": member.formkey(here)})
    assert on.status_code == 302 and member.user().signin_alerts is True


def test_mail_is_not_sent_and_nothing_crashes_without_a_key(real_app, monkeypatch):
    from ruqqus.mail import mail
    monkeypatch.delenv("MAILGUN_KEY", raising=False)
    assert mail.send_mail("a@example.com", "s", "<p>x</p>") is None
    assert mail.send_mail_quietly("a@example.com", "s", "<p>x</p>") is None


def test_the_sending_domain_and_sender_come_from_the_environment(real_app, monkeypatch):
    from ruqqus.mail import mail
    monkeypatch.setenv("MAILGUN_KEY", "k")
    monkeypatch.delenv("MAILGUN_DOMAIN", raising=False)
    monkeypatch.delenv("MAIL_FROM", raising=False)
    key, domain, sender = mail.mail_settings()
    app, _ = real_app
    assert key == "k" and domain == f"mail.{app.config['SERVER_NAME']}"
    assert sender == f"{app.config['SITE_NAME']} <noreply@{domain}>"
    assert "ruqqus" not in domain.lower() and "ruqqus" not in sender.lower()
    monkeypatch.setenv("MAILGUN_DOMAIN", "mg.example.org")
    monkeypatch.setenv("MAIL_FROM", "Example <hello@example.org>")
    assert mail.mail_settings() == ("k", "mg.example.org", "Example <hello@example.org>")
