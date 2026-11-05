"""The child lock on the real app and a real Postgres (helpers/age_declaration.py, helpers/age_assurance.py, helpers/visibility.py): which
sign-ups start locked, what a reader in a strict country sees until their age is confirmed, how a lock is lifted (a tick where that is
accepted, a check where it isn't, an admin always), what a locked account can't do and what nobody can start with it. Sources are read
by tests/test_child_locked_assets.py."""
import hmac
import os
import re
import secrets
import time

import pytest
from sqlalchemy import text

BASE = "http://localhost"
AGENT = "Mozilla/5.0 (Macintosh; Intel Mac OS X 14_5) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.5 Safari/605.1.15"


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


def path_of(app, endpoint):
    """The site's own address for a route, not an API alias (/api/v1, /api/v2 want an app's token)."""
    found = [rule.rule for rule in app.url_map.iter_rules() if rule.endpoint == endpoint]
    return sorted(found, key=lambda rule: (rule.startswith("/api/v1") or rule.startswith("/api/v2"), rule))[0]


def columns(member, *names):
    db = member.db_session()
    db.rollback()
    return db.execute(text(f"SELECT {', '.join(names)} FROM users WHERE id = :u"), {"u": member.id}).first()


def set_columns(member, **values):
    db = member.db_session()
    db.execute(text("UPDATE users SET " + ", ".join(f"{k} = :{k}" for k in values) + " WHERE id = :u"), dict(values, u=member.id))
    db.commit()


def locked(member):
    set_columns(member, age_declared=2, age_declared_utc=5, filter_level=2, age_lock=1, private_posting=True)
    return member


def adult(member):
    set_columns(member, age_declared=1, age_declared_utc=5)
    return member


def add_post(member, board_id=2, **flags):
    from ruqqus.classes import Submission, SubmissionAux
    from ruqqus.helpers.base36 import base36encode
    db = member.db_session()
    db.rollback()
    with member.app.test_request_context("/"):
        row = Submission(author_id=member.id, board_id=board_id, original_board_id=board_id, created_utc=int(time.time()),
                         post_public=True, **flags)
    db.add(row)
    db.flush()
    db.add(SubmissionAux(id=row.id, title=f"Age lock test {secrets.token_hex(4)}", body="words", body_html="<p>words</p>"))
    db.commit()
    return base36encode(row.id)


# ---------------------------------------------------------------- sign-up

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
    return db.execute(text("SELECT age_declared, age_lock, private_posting, filter_level, age_assured_utc FROM users WHERE username = :u"),
                      {"u": username}).first()


@pytest.mark.parametrize("country", ["GB", "DE", "FR", "AU", "IE", "NO"])
@pytest.mark.parametrize("posted", ["1", "2"])
def test_every_sign_up_from_a_strict_country_starts_locked_whatever_was_posted(real_app, country, posted):
    app, _ = real_app
    client = app.test_client()
    form = sign_up_form(client, age_declaration=posted)
    assert post(client, "/signup", country=country, **form).status_code == 302
    row = made(real_app, form["username"])
    assert row is not None and row.age_declared == 2 and row.age_lock == 1 and row.private_posting is True and row.filter_level == 2
    assert row.age_assured_utc == 0


@pytest.mark.parametrize("country", ["US", "CA", "BR", None])
def test_an_adult_answer_from_anywhere_else_is_unlocked(real_app, country):
    app, _ = real_app
    client = app.test_client()
    form = sign_up_form(client, age_declaration="1")
    assert post(client, "/signup", country=country, **form).status_code == 302
    row = made(real_app, form["username"])
    assert row.age_declared == 1 and row.age_lock is None and row.private_posting is False and row.filter_level == 1


def test_under_18_from_anywhere_starts_the_lock(real_app):
    app, _ = real_app
    client = app.test_client()
    form = sign_up_form(client, age_declaration="2")
    assert post(client, "/signup", country="US", **form).status_code == 302
    row = made(real_app, form["username"])
    assert row.age_declared == 2 and row.age_lock == 1 and row.private_posting is True


@pytest.mark.parametrize("country, adult_disabled, second", [("GB", True, "I'm 13 or over"), ("DE", True, "I'm 16 or over"),
                                                             ("AU", True, "I'm 16 or over"), ("US", False, "I'm 13 to 17"),
                                                             (None, False, "I'm 13 to 17"), ("KR", False, "I'm 14 to 17")])
def test_the_sign_up_page_greys_out_18_where_a_vendor_must_confirm_it(real_app, country, adult_disabled, second):
    app, _ = real_app
    page = get(app.test_client(), "/signup", country).get_data(as_text=True)
    adult_input = re.search(r'<input[^>]*name="age_declaration" value="1"[^>]*>', page).group(0)
    assert ("disabled" in adult_input) is adult_disabled
    assert second in page
    assert ("age-check service" in page) is adult_disabled
    assert ("You must be at least" in page) and "child lock" in page


# ---------------------------------------------------------------- what a reader sees

def notice(served):
    return "Hidden until you confirm your age" in served.get_data(as_text=True)


def test_a_visitor_in_a_strict_country_gets_the_child_view_and_elsewhere_the_standard_one(make_member, real_app):
    app, _ = real_app
    author = adult(make_member())
    strong = add_post(author, word_severity=1)                     # profanity: Child hides it, Standard does not
    flagged = add_post(author, is_sensitive=True)
    for b36 in (strong, flagged):
        path = f"/@{author.username}/post/{b36}"
        gb = get(app.test_client(), path, "GB")
        assert gb.status_code == 403 and notice(gb), b36
        assert "Sign in" in gb.get_data(as_text=True)
        us = get(app.test_client(), path, "US")
        assert us.status_code == 200 and not notice(us), b36
        unknown = get(app.test_client(), path, None)
        assert unknown.status_code == 200, "an unknown country is not locked"


def test_an_unconfirmed_adult_account_is_held_to_it_wherever_it_is_used_in_a_strict_country(make_member):
    author = adult(make_member())
    strong = add_post(author, word_severity=1)
    reader = adult(make_member())
    path = f"/@{author.username}/post/{strong}"
    assert get(reader.browser(), path, "US").status_code == 200
    in_uk = get(reader.browser(), path, "GB")
    assert in_uk.status_code == 403 and notice(in_uk)
    assert "Confirm your age" in in_uk.get_data(as_text=True)


def test_a_confirmed_age_is_not_held_to_it(make_member):
    author = adult(make_member())
    strong = add_post(author, word_severity=1)
    reader = adult(make_member())
    set_columns(reader, age_assured_utc=int(time.time()))
    assert get(reader.browser(), f"/@{author.username}/post/{strong}", "GB").status_code == 200


def test_a_locked_account_sees_the_age_wording_not_the_word_filter_one(make_member):
    author = adult(make_member())
    strong = add_post(author, word_severity=1)
    child = locked(make_member())
    served = get(child.browser(), f"/@{author.username}/post/{strong}", "US")
    assert served.status_code == 403 and notice(served)
    assert "word filter" not in served.get_data(as_text=True).split("<h1", 1)[1].split("</h1>", 1)[0]


def test_settings_says_what_applies_and_shows_the_level_really_in_force(make_member):
    member = adult(make_member())
    page = get(member.browser(), "/settings/content", "GB").get_data(as_text=True)
    select = page.split('id="filterlevel"', 1)[1].split(">", 1)[0]
    assert "disabled" in select and 'data-current="2"' in select
    assert "Age confirmation isn&#39;t available yet." in page or "Age confirmation isn't available yet." in page
    assert "before you can see sensitive content or strong language" in page, "an unlocked adult has no lock to take off: the line says what is held"
    assert 'action="/settings/age_assurance/start"' not in page, "no vendor is set up"
    elsewhere = get(member.browser(), "/settings/content", "US").get_data(as_text=True)
    assert "disabled" not in elsewhere.split('id="filterlevel"', 1)[1].split(">", 1)[0]


def test_the_chat_page_gets_the_level_in_force_not_the_stored_one(make_member):
    member = adult(make_member())
    page = get(member.browser(), "/chat", "GB")
    assert "filterLevel: 2" in page.get_data(as_text=True) or page.status_code in (302, 503)


def test_ads_follow_the_stored_lock_not_the_country(make_member, real_app):
    from ruqqus.helpers import ads
    app, _ = real_app
    with app.test_request_context("/", headers={"CF-IPCountry": "GB"}):
        assert ads.viewer_allows(None) is True, "a visitor in a strict country keeps the ads they had"
        assert ads.viewer_allows(adult(make_member()).user()) is True
        assert ads.viewer_allows(locked(make_member()).user()) is False


# ---------------------------------------------------------------- lifting it

def test_a_tick_lifts_the_lock_outside_the_strict_countries_and_not_inside(make_member):
    member = locked(make_member())
    client = member.browser()
    refused = post(client, "/settings/age_declaration", country="GB", formkey=member.formkey(client), age_declaration="1")
    assert refused.status_code == 403 and "age-check service" in refused.get_data(as_text=True)
    assert columns(member, "age_lock").age_lock == 1
    lifted = post(client, "/settings/age_declaration", country="US", formkey=member.formkey(client), age_declaration="1")
    assert lifted.status_code == 303
    row = columns(member, "age_declared", "age_lock", "private_posting", "filter_level", "age_assured_utc")
    assert (row.age_declared, row.age_lock, row.private_posting, row.filter_level, row.age_assured_utc) == (1, None, False, 1, 0)


def test_a_parents_sub_account_cant_lift_itself(make_member):
    member = locked(make_member())
    set_columns(member, age_lock=2)
    client = member.browser()
    refused = post(client, "/settings/age_declaration", country="US", formkey=member.formkey(client), age_declaration="1")
    assert refused.status_code == 403 and columns(member, "age_lock").age_lock == 2


def test_the_vendor_routes_are_a_404_while_none_is_set_up(make_member, monkeypatch):
    monkeypatch.delenv("AGE_ASSURANCE_PROVIDER", raising=False)
    member = locked(make_member())
    client = member.browser()
    assert post(client, "/settings/age_assurance/start", formkey=member.formkey(client)).status_code == 404
    assert get(client, "/settings/age_assurance/callback?state=x&result=adult").status_code == 404


def run_check(member, result, country="GB", monkeypatch=None):
    monkeypatch.setenv("AGE_ASSURANCE_PROVIDER", "dev")
    monkeypatch.setenv("AGE_ASSURANCE_DEV", "1")
    monkeypatch.setenv("AGE_ASSURANCE_DEV_RESULT", result)
    client = member.browser()
    started = post(client, "/settings/age_assurance/start", country=country, formkey=member.formkey(client))
    assert started.status_code == 303
    return client, started.headers["Location"]


def test_an_adult_result_lifts_the_lock_anywhere_and_records_the_check(make_member, monkeypatch):
    member = locked(make_member())
    client, where = run_check(member, "adult", "GB", monkeypatch)
    done = get(client, where.replace("http://localhost", ""), "GB")
    assert done.status_code == 303 and done.headers["Location"].endswith("age=confirmed")
    row = columns(member, "age_declared", "age_lock", "private_posting", "filter_level", "age_assured_utc")
    assert (row.age_declared, row.age_lock, row.private_posting, row.filter_level) == (1, None, False, 1) and row.age_assured_utc > 0
    # confirmed, so the strict country no longer holds the reader to the Child view
    author = adult(make_member())
    strong = add_post(author, word_severity=1)
    assert get(client, f"/@{author.username}/post/{strong}", "GB").status_code == 200


def test_a_state_is_good_once_and_only_for_the_account_that_started_it(make_member, monkeypatch):
    member, other = locked(make_member()), locked(make_member())
    client, where = run_check(member, "adult", "GB", monkeypatch)
    path = where.replace("http://localhost", "")
    stolen = get(other.browser(), path, "GB")
    assert stolen.status_code == 400 and columns(other, "age_lock").age_lock == 1
    assert get(client, path, "GB").status_code == 303
    assert get(client, path, "GB").status_code == 400, "once"


def test_a_minor_result_keeps_the_lock_and_hides_the_option(make_member, monkeypatch):
    member = locked(make_member())
    client, where = run_check(member, "minor", "GB", monkeypatch)
    done = get(client, where.replace("http://localhost", ""), "GB")
    assert done.status_code == 303 and done.headers["Location"].endswith("age=under")
    row = columns(member, "age_lock", "age_under_utc", "age_assured_utc")
    assert row.age_lock == 1 and row.age_under_utc > 0 and row.age_assured_utc == 0
    page = get(client, "/settings/content", "GB").get_data(as_text=True)
    assert "found that you're under 18" in page.replace("&#39;", "'")
    assert post(client, "/settings/age_assurance/start", country="GB", formkey=member.formkey(client)).status_code == 409


def test_an_under_the_minimum_result_closes_the_account(make_member, monkeypatch):
    member = locked(make_member())
    client, where = run_check(member, "under_minimum", "AU", monkeypatch)
    done = get(client, where.replace("http://localhost", ""), "AU")
    assert done.status_code == 200 and "Your account was closed" in done.get_data(as_text=True)
    assert columns(member, "is_deleted").is_deleted is True


def test_a_result_that_didnt_finish_changes_nothing(make_member, monkeypatch):
    member = locked(make_member())
    client, where = run_check(member, "failed", "GB", monkeypatch)
    done = get(client, where.replace("http://localhost", ""), "GB")
    assert done.status_code == 303 and done.headers["Location"].endswith("age=failed")
    assert columns(member, "age_lock").age_lock == 1


def admin(make_member):
    member = make_member()
    set_columns(member, admin_level=3, age_declared=1, age_declared_utc=5)
    return member


def test_an_admin_confirming_an_age_lifts_the_lock_and_clears_the_regional_rule(make_member):
    boss, member = admin(make_member), locked(make_member())
    client = boss.browser()
    done = post(client, "/admin/age_declaration", formkey=boss.formkey(client), username=member.username, answer="confirmed", reason="id seen")
    assert done.status_code == 302
    row = columns(member, "age_declared", "age_lock", "age_assured_utc", "filter_level")
    assert (row.age_declared, row.age_lock) == (1, None) and row.age_assured_utc > 0
    assert row.filter_level == 2, "an admin's confirmation leaves the filter: the member lowers it with their password"
    db = member.db_session()
    db.rollback()
    entry = db.execute(text("SELECT staff_note FROM log_entries WHERE target_user_id = :u AND action = 'age_declaration' ORDER BY id DESC"),
                       {"u": member.id}).first()
    assert entry.staff_note == "child account to 18 or over, age confirmed"
    again = post(client, "/admin/age_declaration", formkey=boss.formkey(client), username=member.username, answer="confirmed")
    assert again.status_code == 302


def test_an_admin_marking_a_child_account_starts_the_lock(make_member):
    boss, member = admin(make_member), adult(make_member())
    client = boss.browser()
    assert post(client, "/admin/age_declaration", formkey=boss.formkey(client), username=member.username, answer="2").status_code == 302
    row = columns(member, "age_declared", "age_lock", "private_posting", "filter_level")
    assert (row.age_declared, row.age_lock, row.private_posting, row.filter_level) == (2, 1, True, 2)


# ---------------------------------------------------------------- what a locked account can't do

GATED_PATHS = [("chat_start", {"username": "x"}), ("chat_group_create", {"members": "x"}), ("space_create", {"title": "t"}),
               ("shop_buy_coins", {"coins": "1"}), ("premium_redeem", {}), ("circle_create", {"name": "n", "kind": "paid", "price": "5"}),
               ("create_board_post", {"name": "lockedguild"})]


@pytest.mark.parametrize("endpoint, form", GATED_PATHS)
def test_a_locked_account_is_refused_with_a_403_that_says_so(make_member, real_app, endpoint, form):
    app, _ = real_app
    member = locked(make_member())
    client = member.browser()
    served = post(client, path_of(app, endpoint), formkey=member.formkey(client), **form)
    assert served.status_code == 403, (endpoint, served.status_code)
    body = served.get_data(as_text=True)
    assert "age_locked" in body or "age-restricted" in body, endpoint


# buying coins calls PayPal, which a test has no account with: the locked half above is what it can show
@pytest.mark.parametrize("endpoint, form", [g for g in GATED_PATHS if g[0] != "shop_buy_coins"])
def test_an_unlocked_account_is_not_stopped_by_that_gate(make_member, real_app, endpoint, form):
    app, _ = real_app
    member = adult(make_member())
    client = member.browser()
    served = post(client, path_of(app, endpoint), formkey=member.formkey(client), **form)
    assert "age_locked" not in served.get_data(as_text=True), endpoint


def test_the_chat_and_spaces_pages_are_a_page_not_a_blank_for_a_locked_account(make_member):
    member = locked(make_member())
    client = member.browser()
    for path in ("/chat", "/settings/chat_apps"):
        served = get(client, path)
        assert served.status_code == 403 and "age-restricted" in served.get_data(as_text=True), path
        assert "/settings/content" in served.get_data(as_text=True)


def test_the_navigation_a_locked_account_cant_use_is_not_drawn(make_member):
    child, free = locked(make_member()), adult(make_member())
    shown = get(free.browser(), "/all").get_data(as_text=True)
    hidden = get(child.browser(), "/all").get_data(as_text=True)
    assert 'data-side-panel="chat"' in shown and 'data-side-panel="chat"' not in hidden
    assert 'href="/spaces/new"' in shown and 'href="/spaces/new"' not in hidden
    assert 'class="dropdown-item" href="/chat"' not in hidden and 'class="dropdown-item" href="/spaces"' not in hidden


def test_a_locked_account_cant_mark_a_post_sensitive_or_post_anonymously(make_member):
    member = locked(make_member())
    client = member.browser()
    for extra in ({"sensitive": "true"}, {"anonymous": "true"}):
        served = post(client, "/api/vue/submit", formkey=member.formkey(client), title="hello there", body="words", post_to_profile="1", **extra)
        assert served.status_code == 403 and "age-restricted" in served.get_data(as_text=True), extra
    page = get(client, "/submit").get_data(as_text=True)
    assert 'id="sensitiveCheck"' not in page and 'id="post-anon' not in page
    me = adult(make_member())
    assert 'id="sensitiveCheck"' in get(me.browser(), "/submit").get_data(as_text=True)


def test_a_locked_account_cant_comment_anonymously_or_mark_itself_sensitive(make_member):
    author = adult(make_member())
    b36 = add_post(author)
    member = locked(make_member())
    client = member.browser()
    refused = post(client, "/api/comment", formkey=member.formkey(client), parent_fullname=f"t2_{b36}", body="hello", anonymous="true")
    assert refused.status_code == 403 and "age-restricted" in refused.get_data(as_text=True)
    assert post(client, "/settings/sensitive_account", formkey=member.formkey(client), on="true").status_code == 403
    assert columns(member, "is_sensitive").is_sensitive is not True


def test_nobody_can_start_a_chat_a_group_or_a_tip_with_a_locked_account(make_member, real_app):
    app, _ = real_app
    child, me = locked(make_member()), adult(make_member())
    client = me.browser()
    started = post(client, path_of(app, "chat_start"), formkey=me.formkey(client), username=child.username)
    assert started.status_code == 403
    b36 = add_post(child)
    tip = post(client, f"/gift_post/{b36}", formkey=me.formkey(client), coins="1")
    assert tip.status_code == 403 and "can't tip this account" in tip.get_data(as_text=True).replace("&#39;", "'")


def test_a_locked_account_is_not_found_by_part_of_its_name_but_is_by_all_of_it(make_member):
    name = "ci_seek" + secrets.token_hex(3)
    locked(make_member(name))
    me = adult(make_member())
    client = me.browser()
    partial = get(client, f"/search?q=%40{name[:-2]}").get_data(as_text=True)
    assert name not in partial
    exact = get(client, f"/search?q=%40{name}").get_data(as_text=True)
    assert name in exact


def test_a_locked_account_has_no_guild_to_start(make_member):
    member = locked(make_member())
    set_columns(member, coin_balance=1000, premium_expires_utc=int(time.time()) + 86400)
    assert member.user().can_make_guild is False
