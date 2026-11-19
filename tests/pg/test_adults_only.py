"""Accounts are for adults only (the owner, 2026-11-19), on the real app with child and parent accounts switched off (the default:
helpers/age_declaration.children_enabled). Sign-up asks one thing, "I'm 18 or over"; an account made in the UK, the EU/EEA or Australia
is still locked until an age check confirms it; nobody can say they are under 18 (sign-up, Settings, an admin); Settings > Children and
its addresses are a 404; and an age check that finds someone under 18 closes the account. tests/pg/test_age_lock.py and
tests/pg/test_child_accounts.py hold the same features with child accounts switched on."""
import hmac
import os
import secrets
import time

import pytest
from sqlalchemy import text

BASE = "http://localhost"
AGENT = "Mozilla/5.0 (Macintosh; Intel Mac OS X 14_5) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.5 Safari/605.1.15"


@pytest.fixture(autouse=True)
def children_off(monkeypatch):
    monkeypatch.delenv("CHILD_ACCOUNTS_ENABLED", raising=False)


def address():
    return f"10.{secrets.randbelow(250)}.{secrets.randbelow(250)}.{secrets.randbelow(250) + 1}"


def headers(country=None):
    found = {"User-Agent": AGENT, "Accept-Language": "en-GB,en;q=0.9"}
    if country:
        found["CF-IPCountry"] = country
    return found


def get(client, path, country=None):
    return client.get(path, base_url=BASE, headers=headers(country), environ_base={"REMOTE_ADDR": address()})


def post(client, path, country=None, **form):
    return client.post(path, base_url=BASE, headers=headers(country), environ_base={"REMOTE_ADDR": address()}, data=form)


def text_of(answer):
    return answer.get_data(as_text=True).replace("&#39;", "'")


def sign_up_form(client, **fields):
    now = str(int(time.time()) - 60)
    token = secrets.token_hex(16)
    with client.session_transaction() as session:
        session["signup_token"] = token
    key = hmac.new(key=bytes(os.environ["MASTER_KEY"], "utf-16"), msg=bytes(now + token + AGENT, "utf-16"), digestmod="md5").hexdigest()
    name = "ci" + "".join(secrets.choice("abcdefghjkmnoprstuvy") for _ in range(8))
    return dict(formkey=key, now=now, username=name, password="ci-Password-1", password_confirm="ci-Password-1", **fields)


def made(real_app, username):
    _, db_session = real_app
    db = db_session()
    db.rollback()
    return db.execute(text("SELECT id, age_declared, age_lock, filter_level, is_deleted FROM users WHERE username = :u"),
                      {"u": username}).first()


def columns(member, *names):
    db = member.db_session()
    db.rollback()
    return db.execute(text(f"SELECT {', '.join(names)} FROM users WHERE id = :u"), {"u": member.id}).first()


def set_columns(member, **values):
    db = member.db_session()
    db.execute(text("UPDATE users SET " + ", ".join(f"{k} = :{k}" for k in values) + " WHERE id = :u"), dict(values, u=member.id))
    db.commit()


# ---------------------------------------------------------------- sign-up

@pytest.mark.parametrize("country", [None, "US", "GB"])
def test_sign_up_asks_one_thing_and_offers_no_child_account(real_app, country):
    app, _ = real_app
    page = text_of(get(app.test_client(), "/signup", country))
    assert 'type="checkbox" class="custom-control-input" id="signup-age-adult" name="age_declaration" value="1" required' in page
    assert 'name="age_declaration" value="2"' not in page and "signup-age-child" not in page and "to 17" not in page
    assert "Accounts are for people 18 or over." in page
    assert ("age-check service has to confirm it" in page) is (country == "GB")


@pytest.mark.parametrize("posted", ["2", "", None])
def test_sign_up_without_saying_18_or_over_is_refused(real_app, posted):
    app, _ = real_app
    client = app.test_client()
    form = sign_up_form(client, **({} if posted is None else {"age_declaration": posted}))
    answer = post(client, "/signup", "US", **form)
    assert answer.status_code == 302 and "/signup" in answer.headers["Location"] and "error=" in answer.headers["Location"]
    assert "18+or+over" in answer.headers["Location"] or "18%20or%20over" in answer.headers["Location"]
    assert made(real_app, form["username"]) is None


def test_18_or_over_is_a_regular_account_elsewhere_and_a_locked_one_in_a_strict_country(real_app):
    app, _ = real_app
    for country, declared, lock, level in (("US", 1, None, 1), (None, 1, None, 1), ("GB", 2, 1, 2), ("AU", 2, 1, 2)):
        client = app.test_client()
        form = sign_up_form(client, age_declaration="1")
        assert post(client, "/signup", country, **form).status_code == 302
        row = made(real_app, form["username"])
        assert (row.age_declared, row.age_lock, row.filter_level) == (declared, lock, level), country


# ---------------------------------------------------------------- Settings and the admin

def test_a_member_from_before_can_only_say_18_or_over(make_member):
    member = make_member()
    set_columns(member, age_declared=None, age_declared_utc=0)
    client = member.browser()
    page = text_of(get(client, "/settings/content", "US"))
    assert 'id="age-settings-adult" name="age_declaration" value="1" required' in page and 'value="2"' not in page.split('id="age-declaration"', 1)[1].split("</form>", 1)[0]
    refused = post(client, "/settings/age_declaration", "US", formkey=member.formkey(client), age_declaration="2")
    assert refused.status_code == 400 and "Please confirm that you're 18 or over." in text_of(refused)
    assert columns(member, "age_declared").age_declared is None
    assert post(client, "/settings/age_declaration", "US", formkey=member.formkey(client), age_declaration="1").status_code == 303
    assert columns(member, "age_declared", "age_lock") == (1, None)


def test_a_locked_members_page_doesnt_call_them_a_child(make_member):
    member = make_member()
    set_columns(member, age_declared=2, age_declared_utc=5, filter_level=2, age_lock=1)
    page = text_of(get(member.browser(), "/settings/content", "GB"))
    assert "Your account is locked until your age is confirmed" in page and "A locked account only reads" in page
    assert "child lock" not in page and "child account" not in page.lower()


def test_an_admin_cant_make_an_account_a_childs(make_member):
    boss, member = make_member(), make_member()
    set_columns(boss, admin_level=6, age_declared=1, age_declared_utc=5, age_assured_utc=5)
    set_columns(member, age_declared=1, age_declared_utc=5)
    client = boss.browser()
    page = text_of(get(client, f"/@{member.username}"))
    assert '<option value="2" disabled' in page and "Child account (starts the child lock)" not in page
    answer = post(client, "/admin/age_declaration", formkey=boss.formkey(client), username=member.username, answer="2")
    assert answer.status_code == 400 and columns(member, "age_declared", "age_lock") == (1, None)


# ---------------------------------------------------------------- parent accounts

def test_settings_children_and_every_parent_address_is_a_404(make_member):
    parent = make_member()
    set_columns(parent, age_declared=1, age_declared_utc=5, age_assured_utc=5)
    client = parent.browser()
    assert 'href="/settings/children"' not in text_of(get(client, "/settings/content", "US"))
    assert get(client, "/settings/children", "US").status_code == 404
    key = parent.formkey(client)
    for path in ("/settings/children/new", "/settings/children/someone/controls", "/settings/children/someone/answer",
                 "/settings/children/someone/stop", "/settings/children/someone/password", "/settings/children/someone/delete",
                 "/settings/children/someone/export", "/settings/parent/ask"):
        assert post(client, path, "US", formkey=key, username="kid" + secrets.token_hex(3), password="ci-Password-1",
                    birth_month="6", birth_year="2011", terms="1", kind="release").status_code == 404, path
    assert get(client, "/settings/children/someone", "US").status_code == 404


# ---------------------------------------------------------------- the age check

def run_check(member, result, country, monkeypatch):
    monkeypatch.setenv("AGE_ASSURANCE_PROVIDER", "dev")
    monkeypatch.setenv("AGE_ASSURANCE_DEV", "1")
    monkeypatch.setenv("AGE_ASSURANCE_DEV_RESULT", result)
    client = member.browser()
    started = post(client, "/settings/age_assurance/start", country, formkey=member.formkey(client))
    assert started.status_code == 303
    return client, started.headers["Location"].replace(BASE, "")


def test_an_age_check_that_finds_someone_under_18_closes_the_account(make_member, monkeypatch):
    member = make_member()
    set_columns(member, age_declared=2, age_declared_utc=5, filter_level=2, age_lock=1)
    client, where = run_check(member, "minor", "GB", monkeypatch)
    done = get(client, where, "GB")
    assert done.status_code == 200 and "Your account was closed" in text_of(done)
    assert "Accounts here are for people 18 or over" in text_of(done)
    assert columns(member, "is_deleted").is_deleted


def test_an_age_check_that_confirms_18_still_lifts_the_lock(make_member, monkeypatch):
    member = make_member()
    set_columns(member, age_declared=2, age_declared_utc=5, filter_level=2, age_lock=1)
    client, where = run_check(member, "adult", "GB", monkeypatch)
    done = get(client, where, "GB")
    assert done.status_code == 303 and done.headers["Location"].endswith("age=confirmed")
    row = columns(member, "age_declared", "age_lock", "age_assured_utc")
    assert (row.age_declared, row.age_lock) == (1, None) and row.age_assured_utc > 0
    assert "Thanks. Your account is no longer locked." in text_of(get(client, "/settings/content?age=confirmed", "GB"))


# ---------------------------------------------------------------- what the pages say

def test_the_terms_and_the_privacy_page_say_accounts_are_for_adults(real_app):
    app, _ = real_app
    terms = text_of(get(app.test_client(), "/help/terms"))
    assert "You must be 18 or over to have an account." in terms and "parent or guardian" not in terms and "child lock" not in terms
    privacy = text_of(get(app.test_client(), "/help/privacy"))
    assert "Accounts are for people 18 or over." in privacy and "If a parent or guardian manages your account" not in privacy
    rules = text_of(get(app.test_client(), "/help/rules"))
    assert "A locked account can't post at all until its age is confirmed." in rules and "child lock" not in rules
