"""Guild analytics on the real app and a real Postgres: only guildmasters with the right permission read the page (everyone
else a plain 404), joins, leaves and page views are counted, and nothing names anyone."""
import secrets
import time

import pytest
from sqlalchemy import text

BASE = "http://localhost"
GUILD = "general"


def addr():
    return f"10.{secrets.randbelow(250)}.{secrets.randbelow(250)}.{secrets.randbelow(250) + 1}"


def get(client, path, agent="ci-insights", **kw):
    return client.get(path, base_url=BASE, headers={"User-Agent": agent}, environ_base={"REMOTE_ADDR": kw.pop("ip", None) or addr()}, **kw)


def person(make_member):
    member = make_member()
    while member.id == 1:
        member = make_member()
    return member


def make_mod(member, **perms):
    from ruqqus.classes import ModRelationship
    db = member.db_session()
    db.add(ModRelationship(user_id=member.id, board_id=1, accepted=True, invite_rescinded=False, **perms))
    db.commit()
    db.expire_all()


def today():
    return int(time.time()) // 86400


def counters(member):
    db = member.db_session()
    db.expire_all()
    row = db.execute(text("SELECT COALESCE(SUM(joins),0), COALESCE(SUM(leaves),0), COALESCE(SUM(page_views),0) "
                          "FROM guild_day_stats WHERE board_id = 1")).first()
    return tuple(int(x) for x in row)


@pytest.fixture
def mods(make_member, real_app):
    app, _ = real_app
    full, config, access, plain = (person(make_member) for _ in range(4))
    make_mod(full, perm_full=True)
    make_mod(config, perm_config=True)
    make_mod(access, perm_access=True)
    return app, full, config, access, plain


def test_a_guildmaster_with_configuration_content_or_full_reads_the_page_and_everyone_else_gets_a_404(mods, make_member):
    app, full, config, access, plain = mods
    content = person(make_member)
    make_mod(content, perm_content=True)
    for member in (full, config, content):
        answer = get(member.browser(), f"/+{GUILD}/mod/insights")
        assert answer.status_code == 200 and "Net growth" in answer.get_data(as_text=True), member.username
    for who, client in (("a guildmaster without those permissions", access.browser()), ("a member", plain.browser()),
                        ("a visitor", app.test_client())):
        for path in (f"/+{GUILD}/mod/insights", f"/+{GUILD}/mod/insights.json"):
            answer = get(client, path)
            assert answer.status_code in (404, 302), (who, path, answer.status_code)
            if answer.status_code == 302:
                assert answer.headers["Location"].startswith("/login"), (who, path)
    assert get(full.browser(), "/+nosuchguildxyz/mod/insights").status_code == 404


def test_the_json_is_counts_only(mods):
    app, full, *_ = mods
    body = get(full.browser(), f"/+{GUILD}/mod/insights.json").get_json()
    assert {"days", "members", "joined", "left", "net", "views", "posts", "comments", "upvotes", "reports", "mod_actions", "chart"} <= set(body)
    text_form = str(body).lower()
    for word in ("username", "user_id", "author", "email", full.username.lower()):
        assert word not in text_form, word
    assert get(full.browser(), f"/+{GUILD}/mod/insights.json", ).headers["Cache-Control"] == "no-store"
    assert get(full.browser(), f"/+{GUILD}/mod/insights.json?days=7").get_json()["days"] == 7
    assert get(full.browser(), f"/+{GUILD}/mod/insights.json?days=3").get_json()["days"] == 28


def test_a_join_and_a_leave_are_counted_once_each(mods, make_member):
    app, full, *_ = mods
    joiner = person(make_member)
    here = joiner.browser()
    before = counters(full)
    form = {"formkey": joiner.formkey(here)}
    assert here.post(f"/api/subscribe/{GUILD}", base_url=BASE, data=form, environ_base={"REMOTE_ADDR": addr()}).status_code == 200
    assert counters(full) == (before[0] + 1, before[1], before[2])
    again = here.post(f"/api/subscribe/{GUILD}", base_url=BASE, data=form, environ_base={"REMOTE_ADDR": addr()})
    assert again.status_code == 409 and counters(full)[0] == before[0] + 1, "a refused join counts nothing"
    assert here.post(f"/api/unsubscribe/{GUILD}", base_url=BASE, data=form, environ_base={"REMOTE_ADDR": addr()}).status_code == 200
    assert counters(full) == (before[0] + 1, before[1] + 1, before[2])
    body = get(full.browser(), f"/+{GUILD}/mod/insights.json").get_json()
    assert body["joined"] >= 1 and body["left"] >= 1 and body["net"] == body["joined"] - body["left"]


def test_page_views_count_once_per_viewer_and_never_a_robot_a_guildmaster_or_the_api(mods):
    app, full, *_ = mods
    before = counters(full)[2]
    visitor, ip = app.test_client(), addr()
    get(visitor, f"/+{GUILD}", ip=ip)
    assert counters(full)[2] == before + 1
    get(visitor, f"/+{GUILD}", ip=ip)
    assert counters(full)[2] == before + 1, "the same viewer within half an hour is one view"
    get(app.test_client(), f"/+{GUILD}", agent="Googlebot/2.1 (+http://www.google.com/bot.html)")
    get(app.test_client(), f"/+{GUILD}", agent="")
    assert counters(full)[2] == before + 1, "robots and empty agents are not viewers"
    get(full.browser(), f"/+{GUILD}")
    assert counters(full)[2] == before + 1, "a guildmaster's own opens are not counted"
    get(app.test_client(), f"/api/v1/guild/{GUILD}/listing")
    assert counters(full)[2] == before + 1, "the API is not a page view"
    get(app.test_client(), f"/+{GUILD}", agent="Mozilla/5.0 (Windows NT 10.0) Chrome/126 Safari/537.36")
    assert counters(full)[2] == before + 2


def test_a_small_guild_shows_totals_only_and_a_big_one_a_day_by_day_table(mods):
    app, full, *_ = mods
    db = full.db_session()
    small = get(full.browser(), f"/+{GUILD}/mod/insights.json").get_json()
    members = small["members"]
    if members < 25:
        assert small["chart"] is False and small["series"] is None
        page = get(full.browser(), f"/+{GUILD}/mod/insights").get_data(as_text=True)
        assert "25 members" in page and 'class="insight-days"' not in page
        base = 910_000_000
        db.execute(text("INSERT INTO subscriptions (user_id, board_id, created_utc, is_active) "
                        "SELECT :b + g, 1, 0, true FROM generate_series(1, :n) g"), {"b": base, "n": 25 - members})
        db.commit()
    try:
        big = get(full.browser(), f"/+{GUILD}/mod/insights.json").get_json()
        assert big["chart"] is True and len(big["series"]) == 28
        assert 'class="insight-days"' in get(full.browser(), f"/+{GUILD}/mod/insights").get_data(as_text=True)
    finally:
        db.execute(text("DELETE FROM subscriptions WHERE user_id >= 910000000"))
        db.commit()


def test_the_nav_links_insights_only_for_those_who_may_read_it(mods):
    app, full, config, access, plain = mods
    assert "/mod/insights" in get(full.browser(), f"/+{GUILD}/mod/insights").get_data(as_text=True)
    assert "/mod/insights" not in get(access.browser(), f"/+{GUILD}/mod/access").get_data(as_text=True)
