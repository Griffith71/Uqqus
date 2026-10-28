"""The "What's happening" admin and box on the real Flask app and a real Postgres (routes/happening_pages.py): who may read and
change events, that a change needs the form key, that an event an admin adds reaches the box and one they hide leaves it, that
the quick edit never blanks what it did not send, that two edits cannot overwrite each other, and that a pasted poll post must
exist. (The rules and the SQL are tested without Postgres in tests/test_site_events.py; this is the wiring.)"""
import json
import time

import pytest
from sqlalchemy import text


def _utc(offset=0):
    return time.strftime("%Y-%m-%d %H:%M", time.gmtime(time.time() + offset))


@pytest.fixture(scope="module", autouse=True)
def clean_slate(real_app):
    """The box shows three Live rows, so what earlier runs of this file added (a database used twice keeps its rows) must not
    crowd out what a test adds now: this file's own events are the admin ones."""
    _, db_session = real_app
    db = db_session()
    db.execute(text("DELETE FROM site_event_log WHERE event_id IN (SELECT id FROM site_events WHERE source = 'admin')"))
    db.execute(text("DELETE FROM site_events WHERE source = 'admin'"))
    db.commit()


@pytest.fixture
def admin(make_member):
    """A level-4 admin, a level-3 one, and a plain member, each with a browser."""
    def level(n):
        member = make_member()
        db = member.db_session()
        from ruqqus.classes import User
        row = db.query(User).filter_by(id=member.id).first()
        row.admin_level = n
        db.commit()
        return member
    return level(4), level(3), make_member()


def _post(member, path, client=None, **form):
    client = client or member.browser()
    return client.post(path, data=dict(form, formkey=member.formkey(client)), follow_redirects=False)


def _row(member, title):
    db = member.db_session()
    db.expire_all()
    found = db.execute(text("SELECT id, status, hidden_utc, state, updated_utc FROM site_events WHERE title = :t"), {"t": title}).fetchone()
    return dict(found._mapping) if found else None


def _box(member):
    return member.app.test_client().get("/inpage/happening").get_data(as_text=True)


def test_only_an_admin_reads_and_only_level_4_changes(admin):
    four, three, plain = admin
    assert plain.browser().get("/admin/happening").status_code != 200
    assert three.browser().get("/admin/happening").status_code == 200
    assert four.browser().get("/admin/happening").status_code == 200
    title = "Level three tries"
    refused = _post(three, "/admin/happening/create", kind="countdown", title=title, starts=_utc(3600))
    assert refused.status_code != 302 or "msg=" not in refused.headers.get("Location", "")
    assert _row(four, title) is None
    assert _post(plain, "/admin/happening/create", kind="countdown", title=title, starts=_utc(3600)).status_code != 302 \
        or _row(four, title) is None


def test_a_change_without_the_form_key_does_nothing(admin):
    four = admin[0]
    response = four.browser().post("/admin/happening/create", data={"kind": "countdown", "title": "No key", "starts": _utc(3600)})
    assert "msg=" not in response.headers.get("Location", "") and _row(four, "No key") is None      # (the app redirects away)


def test_an_event_an_admin_adds_reaches_the_box_and_hiding_it_removes_it(admin):
    four = admin[0]
    title = "Launch party " + str(int(time.time()))
    made = _post(four, "/admin/happening/create", kind="countdown", title=title, starts=_utc(2 * 86400), show_from=_utc(-3600))
    assert made.status_code == 302 and "msg=" in made.headers["Location"], made.headers.get("Location")
    row = _row(four, title)
    assert row and title in _box(four)                                             # a visitor sees it
    assert _post(four, f"/admin/happening/{row['id']}/hide").status_code == 302
    assert title not in _box(four) and _row(four, title)["hidden_utc"] > 0
    assert _post(four, f"/admin/happening/{row['id']}/unhide").status_code == 302
    assert title in _box(four)


def test_the_quick_edit_keeps_the_teams_and_a_stale_one_is_refused(admin):
    four = admin[0]
    title = "Quick edit cup " + str(int(time.time()))
    _post(four, "/admin/happening/create", kind="score", title=title, starts=_utc(-600), status="live",
          state_home="Reds", state_away="Blues", state_clock="5'")
    row = _row(four, title)
    state_form = {"status": "live", "state_home_score": "2", "state_away_score": "1", "state_clock": "67'", "state_period": "",
                  "expected": str(row["updated_utc"])}
    assert _post(four, f"/admin/happening/{row['id']}/state", **state_form).status_code == 302
    after = _row(four, title)
    assert json.loads(after["state"]) == {"home": "Reds", "away": "Blues", "home_score": 2, "away_score": 1, "clock": "67'"}
    assert "Reds 2–1 Blues" in _box(four)
    # a second admin who loaded the page before that edit is told, not obeyed
    stale = _post(four, f"/admin/happening/{row['id']}/state", **dict(state_form, state_home_score="9"))
    assert stale.status_code == 302 and "error=" in stale.headers["Location"]
    assert json.loads(_row(four, title)["state"])["home_score"] == 2


def test_a_poll_must_point_at_a_post_that_exists_and_say_when_it_closes(admin):
    four = admin[0]
    title = "A poll about nothing " + str(int(time.time()))
    ghost = _post(four, "/admin/happening/create", kind="poll", title=title, starts=_utc(-60), ends=_utc(3600), poll_post="zzzz")
    assert ghost.status_code == 302 and "error=" in ghost.headers["Location"] and _row(four, title) is None
    no_close = _post(four, "/admin/happening/create", kind="poll", title=title, starts=_utc(-60), poll_post="1")
    assert "error=" in no_close.headers["Location"] and _row(four, title) is None


def test_a_far_off_or_garbled_month_still_draws(admin):
    client = admin[2].browser()
    for month in ("9998-12", "1969-01", "nonsense", "2026-13"):
        assert client.get(f"/happening/calendar?month={month}").status_code == 200, month
    assert client.get("/happening/live?kind=nope").status_code == 404
    assert client.get("/happening/nope").status_code == 404
