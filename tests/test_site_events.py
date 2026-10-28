"""The site's "What's happening" events (helpers/site_events.py rules, helpers/site_event_store.py SQL on SQLite with the
tables built from migration 2026-12-03_site_events.sql).
The promises: alerts, news and elections are link-out only on https and an alert always expires; a civil date and an
instant never mix; status is worked out from the times; a source (the seed, a connector) never undoes an admin's hide,
edit, priority or link; two edits at once never silently overwrite each other; the Child level leaves out the kinds that
need care."""
import json
from datetime import date
from pathlib import Path

import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from ruqqus.helpers import site_event_store as store
from ruqqus.helpers import site_events as rules

ROOT = Path(__file__).resolve().parent.parent
MIGRATION = "2026-12-03_site_events.sql"
NOW = 1_793_000_000          # 2026-10-26 07:33:20 UTC
HOUR, DAY = 3600, 86400


def read(*parts):
    return ROOT.joinpath(*parts).read_text(encoding="utf-8")


def rate(value):
    return 3 if "badword" in value.lower() else 0


def _form(**over):
    base = {"kind": "countdown", "title": "Launch day", "starts": "2026-11-01 12:00"}
    base.update(over)
    return base


# --- the rules: what an admin may enter --------------------------------------------------------------------------------

def test_a_plain_event_is_cleaned():
    f = rules.clean_event(_form(title="  Launch   day ", details="Be there"), NOW)
    assert f["title"] == "Launch day" and f["details"] == "Be there"
    assert f["date_kind"] == "instant" and f["starts_utc"] == 1_793_534_400 and f["starts_date"] is None
    assert f["expires_utc"] is None and f["status"] == "scheduled" and f["state"] == {}


def test_titles_are_plain_text_and_bounded():
    for bad in ("", "<b>x</b>", "x" * (rules.TITLE_MAX + 1), "bell\x07"):
        with pytest.raises(rules.Refused):
            rules.clean_event(_form(title=bad), NOW)
    with pytest.raises(rules.Refused):
        rules.clean_event(_form(kind="nonsense"), NOW)
    with pytest.raises(rules.Refused):
        rules.clean_event(_form(details="x" * (rules.DETAILS_MAX + 1)), NOW)


@pytest.mark.parametrize("kind", ["alert", "news", "election"])
def test_link_out_kinds_need_an_https_link(kind):
    extra = {"expires": "2026-10-27 08:00"} if kind == "alert" else {}
    with pytest.raises(rules.Refused):
        rules.clean_event(_form(kind=kind, **extra), NOW)                                    # no link
    for bad in ("http://example.org/a", "javascript:alert(1)", "https://u:p@example.org/a", "ftp://example.org/a"):
        with pytest.raises(rules.Refused):
            rules.clean_event(_form(kind=kind, link_url=bad, **extra), NOW)
    ok = rules.clean_event(_form(kind=kind, link_url="example.org/story", **extra), NOW)       # a missing scheme is https
    assert ok["link_url"] == "https://example.org/story"


def test_an_alert_always_expires_and_cannot_last_a_week():
    base = _form(kind="alert", link_url="https://example.org/a", starts="2026-10-26 07:00")
    assert rules.clean_event(base, NOW)["expires_utc"] == NOW + DAY                            # the default: a day
    assert rules.clean_event(dict(base, expires="2026-10-27 00:00"), NOW)["expires_utc"] == 1_793_059_200
    with pytest.raises(rules.Refused):
        rules.clean_event(dict(base, expires="2026-11-30 00:00"), NOW)


def test_civil_and_instant_times_never_mix():
    civil = rules.clean_event(_form(kind="holiday", starts="2026-12-25", ends="2026-12-26"), NOW)
    assert civil["date_kind"] == "civil" and civil["starts_date"] == date(2026, 12, 25) and civil["starts_utc"] is None
    for bad in ("tomorrow", "2026-13-01", "2026-02-30", "26-11-01", "1960-01-01"):
        with pytest.raises(rules.Refused):
            rules.clean_event(_form(starts=bad), NOW)
    with pytest.raises(rules.Refused):
        rules.clean_event(_form(starts="2026-11-02 10:00", ends="2026-11-01 10:00"), NOW)     # ends before it starts
    with pytest.raises(rules.Refused):
        rules.clean_event(_form(starts=""), NOW)


def test_polls_and_predictions_point_at_a_poll_post():
    closes = "2026-11-01 12:00"
    with pytest.raises(rules.Refused):
        rules.clean_event(_form(kind="poll", ends=closes), NOW)                                # no poll post
    with pytest.raises(rules.Refused, match="closes"):
        rules.clean_event(_form(kind="poll", poll_post="1a2b"), NOW)                           # no close time: it would end as it starts
    f = rules.clean_event(_form(kind="prediction", poll_post="https://site.example/@alice/post/1a2b", ends=closes), NOW)
    assert f["poll_post_id"] == int("1a2b", 36)
    assert rules.clean_event(_form(kind="poll", poll_post="1a2b", ends=closes), NOW)["poll_post_id"] == int("1a2b", 36)
    for bad in ("not a post!", "zzzzzzz", "0", "https://site.example/@alice/post/zzzzzzz"):          # the column is 32 bits
        with pytest.raises(rules.Refused):
            rules.clean_event(_form(kind="poll", poll_post=bad, ends=closes), NOW)
    assert rules.clean_event(_form(kind="countdown", poll_post="1a2b"), NOW)["poll_post_id"] is None   # only polls keep it


def test_titles_cannot_be_reordered_or_hidden_with_invisible_characters():
    for bad in ("an alert\u202ereversed", "x\u2066y", "bell\x07", "del\x7f"):
        with pytest.raises(rules.Refused):
            rules.clean_event(_form(title=bad), NOW)
    assert rules.clean_event(_form(title="line\u2028break"), NOW)["title"] == "line break"     # a line separator is just a space
    assert rules.clean_event(_form(title="Family \U0001F468\u200d\U0001F469 day"), NOW)["title"].endswith("day")     # a joiner is fine


def test_state_keeps_only_a_kinds_own_bounded_keys():
    f = rules.clean_event(_form(kind="score", state_home="Reds", state_away="Blues", state_home_score="2", state_away_score="1",
                                state_period="2nd half", state_clock="67'", state_evil="x"), NOW)
    assert f["state"] == {"home": "Reds", "away": "Blues", "home_score": 2, "away_score": 1, "period": "2nd half", "clock": "67'"}
    for bad in ({"state_home_score": "1000"}, {"state_home_score": "-1"}, {"state_home": "<b>"}, {"state_clock": "x" * 11}):
        with pytest.raises(rules.Refused):
            rules.clean_event(_form(kind="score", **bad), NOW)
    e = rules.clean_event(_form(kind="election", link_url="https://example.org/r", state_status="results"), NOW)
    assert e["state"] == {"status": "results"}
    with pytest.raises(rules.Refused):
        rules.clean_event(_form(kind="election", link_url="https://example.org/r", state_status="won"), NOW)


# --- the rules: what a row is at a moment ------------------------------------------------------------------------------

def _row(**over):
    row = {"id": 1, "kind": "score", "title": "A v B", "date_kind": "instant", "starts_utc": NOW + HOUR, "ends_utc": None,
           "expires_utc": None, "show_from_utc": None, "status": "scheduled", "status_changed_utc": 0, "state": "{}",
           "priority": 0, "hidden_utc": 0, "is_sensitive": False, "word_severity": 0, "starts_date": None, "ends_date": None}
    row.update(over)
    return row


def test_status_is_worked_out_from_the_times():
    row = _row(starts_utc=NOW + HOUR)                                                           # a match: 3 hours by default
    assert rules.effective_status(row, NOW) == "scheduled"
    assert rules.effective_status(row, NOW + HOUR) == "live"
    assert rules.effective_status(row, NOW + HOUR + 3 * HOUR - 1) == "live"
    assert rules.effective_status(row, NOW + HOUR + 3 * HOUR) == "ended"
    for stored in ("final", "cancelled", "postponed", "ended"):
        assert rules.effective_status(_row(status=stored), NOW + 99 * DAY) == stored             # a stored end wins
    assert rules.effective_status(_row(status="live"), NOW) == "live"                             # forced live, early
    late = _row(status="live", starts_utc=NOW - 10 * HOUR)
    assert rules.effective_status(late, NOW - 10 * HOUR + 3 * HOUR + rules.LIVE_GRACE - 1) == "live"
    assert rules.effective_status(late, NOW - 10 * HOUR + 3 * HOUR + rules.LIVE_GRACE) == "ended"  # forgotten: not live forever
    assert rules.effective_status(_row(starts_utc=NOW - HOUR, expires_utc=NOW - 1), NOW) == "ended"


def test_a_civil_event_is_live_while_its_day_is_on_the_clock_somewhere():
    holiday = _row(kind="holiday", date_kind="civil", starts_date="2026-10-26", ends_date="2026-10-27", starts_utc=None)
    first = rules.midnight(date(2026, 10, 26))
    assert rules.effective_status(holiday, first - 14 * HOUR - 1) == "scheduled"
    assert rules.effective_status(holiday, first - 14 * HOUR) == "live"                  # Kiritimati (UTC+14) has reached the 26th
    assert rules.effective_status(holiday, first + 2 * DAY + 12 * HOUR - 1) == "live"    # the 27th is still on at UTC-12
    assert rules.effective_status(holiday, first + 2 * DAY + 12 * HOUR) == "ended"
    assert rules.on_day(holiday, date(2026, 10, 27)) and not rules.on_day(holiday, date(2026, 10, 28))     # the grid is UTC days
    assert not rules.on_day(holiday, date(2026, 10, 25))


def test_election_day_does_not_end_while_the_polls_are_open_in_california():
    election = _row(kind="election", date_kind="civil", starts_date="2026-11-03", starts_utc=None, link_url="https://x.gov/e")
    evening_in_california = rules.midnight(date(2026, 11, 4)) + 5 * HOUR                  # 21:00 on 3 November at UTC-8
    assert rules.in_live_section(election, evening_in_california)
    assert rules.in_live_section(election, rules.midnight(date(2026, 11, 3)) - 3 * HOUR)  # the polls open in the east first
    assert not rules.in_live_section(election, rules.midnight(date(2026, 11, 4)) + 13 * HOUR)


def test_a_postponed_match_shows_for_a_day_from_the_news_not_until_its_old_date():
    postponed = _row(status="postponed", starts_utc=NOW + 20 * DAY, status_changed_utc=NOW - HOUR)
    assert rules.in_live_section(postponed, NOW) and rules.effective_status(postponed, NOW) == "postponed"
    assert not rules.in_live_section(postponed, NOW + rules.FINAL_SHOW + 2 * HOUR)


def test_a_featured_row_goes_above_what_is_live():
    live = _row(id=1, starts_utc=NOW - HOUR)
    featured = _row(id=2, kind="countdown", starts_utc=NOW + 30 * DAY, show_from_utc=NOW - 1, featured=True)
    assert [r["id"] for r in rules.live_rows([live, featured], NOW)] == [2, 1]


def test_the_live_section_picks_what_matters_now():
    soon = _row(id=1, starts_utc=NOW + 2 * HOUR)
    far = _row(id=2, starts_utc=NOW + 2 * DAY)
    live = _row(id=3, starts_utc=NOW - HOUR, priority=0)
    boosted = _row(id=4, starts_utc=NOW + 3 * HOUR, priority=5)
    featured = _row(id=5, kind="countdown", starts_utc=NOW + 30 * DAY, show_from_utc=NOW - 1)
    hidden_until = _row(id=6, kind="countdown", starts_utc=NOW + 30 * DAY, show_from_utc=NOW + DAY)
    ended = _row(id=7, starts_utc=NOW - 9 * HOUR)
    hidden = _row(id=8, starts_utc=NOW - HOUR, hidden_utc=NOW - 5)
    rows = [soon, far, live, boosted, featured, hidden_until, ended, hidden]
    assert [r["id"] for r in rules.live_rows(rows, NOW)] == [4, 3, 1, 5]    # pinned, then live, then soon, then by start
    # a far-off countdown with a modest priority does not outrank a match under way; only a pinned (5+) one does
    boosted_far = _row(id=9, kind="countdown", starts_utc=NOW + 30 * DAY, show_from_utc=NOW - 1, priority=2)
    assert [r["id"] for r in rules.live_rows([boosted_far, live], NOW)] == [3, 9]
    assert [r["id"] for r in rules.live_rows([dict(boosted_far, priority=5), live], NOW)] == [9, 3]


def test_finished_matches_and_predictions_linger_a_while_then_go():
    done = _row(status="final", starts_utc=NOW - 4 * HOUR, status_changed_utc=NOW - 2 * HOUR)
    assert rules.in_live_section(done, NOW)
    assert not rules.in_live_section(done, NOW + rules.FINAL_SHOW + 3 * HOUR)
    answered = _row(kind="prediction", status="final", starts_utc=NOW - DAY, ends_utc=NOW - 20 * HOUR,
                    status_changed_utc=NOW - HOUR)
    assert rules.in_live_section(answered, NOW) and rules.in_live_section(answered, NOW + 40 * HOUR)
    assert not rules.in_live_section(answered, NOW + rules.OUTCOME_SHOW + DAY)
    assert not rules.in_live_section(_row(kind="poll", starts_utc=NOW + HOUR), NOW)          # a poll shows once open


def test_the_child_level_and_sensitivity_leave_rows_out():
    alert = _row(kind="alert", starts_utc=NOW - HOUR, expires_utc=NOW + HOUR)
    election = _row(kind="election", starts_utc=NOW - 600)
    score = _row(starts_utc=NOW - HOUR)
    for level, kept in ((0, 3), (1, 3), (2, 1)):
        assert len(rules.live_rows([alert, election, score], NOW, level=level)) == kept
    sensitive = _row(starts_utc=NOW - HOUR, is_sensitive=True)
    assert not rules.live_rows([sensitive], NOW)                                              # the All rule: not by default
    assert rules.live_rows([sensitive], NOW, show_sensitive=True)                             # a member chose to see them
    assert not rules.live_rows([sensitive], NOW, level=2, show_sensitive=True)                # never on the Child filter
    rude = _row(starts_utc=NOW - HOUR, word_severity=3)
    assert not rules.live_rows([rude], NOW) and rules.live_rows([rude], NOW, level=0)


def test_the_today_window_is_the_same_for_everyone_around_utc_today():
    today = rules.utc_date(NOW)
    rows = [
        _row(id=1, kind="holiday", date_kind="civil", starts_date=today.isoformat(), starts_utc=None),
        _row(id=2, kind="holiday", date_kind="civil", starts_date="2026-10-24", starts_utc=None),     # two days back: out
        _row(id=3, kind="holiday", date_kind="civil", starts_date="2026-10-25", starts_utc=None),     # yesterday: in
        _row(id=4, kind="astronomy", starts_utc=rules.midnight(today) + 3 * DAY - 1),                   # +2 days late: in
        _row(id=5, kind="astronomy", starts_utc=rules.midnight(today) + 3 * DAY),                       # +3 days: out
        _row(id=6, kind="alert", starts_utc=NOW, expires_utc=NOW + HOUR),                              # not a calendar kind
        _row(id=7, kind="holiday", date_kind="civil", starts_date=today.isoformat(), starts_utc=None, status="cancelled"),
    ]
    assert [r["id"] for r in rules.window_rows(rows, NOW)] == [3, 1, 4]


# --- the SQL ---------------------------------------------------------------------------------------------------------------

def _statements():
    sql = "\n".join(line for line in read("scripts", "migrations", MIGRATION).splitlines() if not line.strip().startswith("--"))
    for s in (s.strip() for s in sql.split(";")):
        if s.startswith("CREATE"):
            yield (s.replace("public.", "").replace("IF NOT EXISTS ", "")
                   .replace("SERIAL PRIMARY KEY", "INTEGER PRIMARY KEY AUTOINCREMENT"))


@pytest.fixture
def db():
    engine = create_engine("sqlite://")
    with engine.begin() as conn:
        for statement in _statements():
            conn.execute(text(statement))
    with Session(engine) as session:
        yield session


def _fields(**over):
    return rules.clean_event(_form(**over), NOW)


def test_the_table_refuses_what_the_rules_forbid(db):
    base = dict(kind="alert", title="t", details="", link_url="https://example.org/a", date_kind="instant", starts_date=None,
                ends_date=None, starts_utc=NOW, ends_utc=None, expires_utc=NOW + HOUR, show_from_utc=None, status="scheduled",
                state="{}", priority=0, featured=False, tentative=False, poll_post_id=None, is_sensitive=False, now=NOW,
                severity=0, version="", admin=1, key="k")

    def insert(**over):
        p = dict(base, **over)
        db.execute(text(
            "INSERT INTO site_events (kind, title, details, link_url, date_kind, starts_date, ends_date, starts_utc, ends_utc, "
            "expires_utc, status, state, source, source_key) VALUES (:kind, :title, :details, :link_url, :date_kind, "
            ":starts_date, :ends_date, :starts_utc, :ends_utc, :expires_utc, :status, :state, 'admin', :key)"), p)

    insert(key="ok")
    db.commit()
    for n, bad in enumerate(({"expires_utc": None}, {"link_url": ""}, {"link_url": "http://example.org/a"},
                             {"date_kind": "civil"}, {"starts_utc": None}, {"ends_utc": NOW - 1}, {"kind": "joke"},
                             {"status": "weird"}, {"key": "ok"})):
        with pytest.raises(IntegrityError):
            insert(**dict(bad, key=bad.get("key", f"bad{n}")))
        db.rollback()


def _make(db, **over):
    return store.create(db, _fields(**over), 7, NOW, rate)


def test_create_log_get_and_hide(db):
    event_id = _make(db, title="Launch")
    row = store.get(db, event_id)
    assert row["title"] == "Launch" and row["source"] == "admin" and row["created_by_id"] == 7 and row["hidden_utc"] == 0
    assert [e["action"] for e in store.log_of(db, event_id)] == ["create"]
    assert store.hide(db, event_id, 8, NOW + 5) and not store.hide(db, event_id, 8, NOW + 6)
    assert store.get(db, event_id)["hidden_by_id"] == 8
    assert store.live_candidates(db, NOW + 10) == []                                         # hidden: not read for the box
    assert store.unhide(db, event_id, 8, NOW + 9) and store.get(db, event_id)["hidden_utc"] == 0
    assert [e["action"] for e in store.log_of(db, event_id)] == ["unhide", "hide", "create"]


def test_two_admins_cannot_overwrite_each_other(db):
    event_id = _make(db, title="Launch")
    stamp = store.get(db, event_id)["updated_utc"]
    assert store.update(db, event_id, _fields(title="First"), 7, NOW + 10, stamp, rate)
    assert not store.update(db, event_id, _fields(title="Second"), 8, NOW + 20, stamp, rate)   # saw the old row: refused
    assert store.get(db, event_id)["title"] == "First"
    stamp2 = store.get(db, event_id)["updated_utc"]
    assert not store.set_state(db, event_id, "live", {}, 8, NOW + 30, stamp, rate)
    assert store.set_state(db, event_id, "live", {"clock": "10'"}, 8, NOW + 30, stamp2, rate)
    row = store.get(db, event_id)
    assert row["status"] == "live" and row["status_changed_utc"] == NOW + 30 and json.loads(row["state"]) == {"clock": "10'"}
    with pytest.raises(rules.Refused):
        store.set_state(db, event_id, "nonsense", {}, 8, NOW + 40, row["updated_utc"], rate)


def test_text_is_rated_when_written_and_when_the_list_changes(db):
    event_id = _make(db, title="A BADWORD here")
    assert store.get(db, event_id)["word_severity"] == 3
    clean = _make(db, title="Fine")
    assert store.get(db, clean)["word_severity"] == 0
    assert store.rescan_severities(db, lambda t: 0, "v2") == 1                                # the list changed
    row = store.get(db, event_id)
    assert row["word_severity"] == 0 and row["word_filter_version"] == "v2"
    # the rating covers the details and the state's text too, not only the title
    sneaky = _make(db, kind="score", title="Cup", state_home="badword FC", state_away="Blues")
    assert store.get(db, sneaky)["word_severity"] == 3


def test_a_source_never_undoes_an_admin(db):
    seed = _fields(kind="election", title="Midterms", link_url="https://example.org/a", starts="2026-11-03 12:00")
    assert store.upsert_source(db, "curated", "midterms-2026", seed, NOW, rate) == "inserted"
    row = db.execute(text("SELECT id FROM site_events WHERE source = 'curated'")).scalar()
    # an admin hides it, raises its priority and corrects the link
    db.execute(text("UPDATE site_events SET hidden_utc = :n, priority = 5, link_url = 'https://official.example/x', "
                    "featured = 1 WHERE id = :i"), {"n": NOW, "i": row})
    assert store.upsert_source(db, "curated", "midterms-2026", dict(seed, title="Midterm elections"), NOW + 60, rate) == "updated"
    after = store.get(db, row)
    assert after["title"] == "Midterm elections"                                              # the source's own column moved
    assert (after["hidden_utc"], after["priority"], after["link_url"], bool(after["featured"])) == \
        (NOW, 5, "https://official.example/x", True)                                          # the admin's did not
    # a row an admin edited (locked) is only marked seen
    db.execute(text("UPDATE site_events SET locked_utc = :n, title = 'Edited by hand' WHERE id = :i"), {"n": NOW, "i": row})
    assert store.upsert_source(db, "curated", "midterms-2026", seed, NOW + 120, rate) == "kept"
    assert store.get(db, row)["title"] == "Edited by hand" and store.get(db, row)["last_seen_utc"] == NOW + 120
    with pytest.raises(ValueError):
        store.upsert_source(db, "admin", "x", seed, NOW, rate)


def test_a_sources_status_only_moves_forward(db):
    seed = _fields(kind="score", title="A v B", starts="2026-10-26 10:00")
    store.upsert_source(db, "demo", "m1", dict(seed, status="live"), NOW, rate)
    row = db.execute(text("SELECT id FROM site_events WHERE source = 'demo'")).scalar()
    store.upsert_source(db, "demo", "m1", dict(seed, status="final"), NOW + 10, rate)
    assert store.get(db, row)["status"] == "final" and store.get(db, row)["status_changed_utc"] == NOW + 10
    store.upsert_source(db, "demo", "m1", dict(seed, status="live"), NOW + 20, rate)           # a stale answer: ignored
    assert store.get(db, row)["status"] == "final"
    store.upsert_source(db, "demo", "m1", dict(seed, status="postponed"), NOW + 30, rate)      # but postponed may happen
    assert store.get(db, row)["status"] == "postponed"


def test_calendar_rows_cover_a_span_exactly(db):
    inside = _make(db, kind="event", title="In", starts="2026-11-05 10:00")
    edge_end = _make(db, kind="event", title="Ends at the start", starts="2026-11-01 00:00", ends="2026-11-02 00:00")
    civil = _make(db, kind="holiday", title="Day", starts="2026-11-30")
    outside = _make(db, kind="event", title="Out", starts="2026-12-01 00:00")
    spanning = _make(db, kind="event", title="Spans", starts="2026-10-20 00:00", ends="2026-11-04 00:00")
    first, last = rules.midnight(date(2026, 11, 2)), rules.midnight(date(2026, 12, 1))          # 2 Nov to 30 Nov inclusive
    rows, cut = store.calendar_rows(db, first, last)
    titles = sorted(r["title"] for r in rows)
    assert titles == ["Day", "In", "Spans"] and not cut
    assert "Ends at the start" not in titles and "Out" not in titles
    assert {inside, civil, spanning} == {r["id"] for r in rows} and edge_end and outside


def test_the_cap_says_it_cut(db):
    for n in range(store.CALENDAR_CAP + 3):
        db.execute(text("INSERT INTO site_events (kind, title, date_kind, starts_utc, source, source_key) "
                        "VALUES ('event', :t, 'instant', :s, 'x', :k)"), {"t": f"e{n}", "s": NOW + n, "k": f"k{n}"})
    rows, cut = store.calendar_rows(db, NOW - 1, NOW + 100000)
    assert len(rows) == store.CALENDAR_CAP and cut


def test_old_events_are_forgotten_with_their_log(db):
    from ruqqus.helpers import retention
    old = _make(db, title="Old", starts="2024-01-01 10:00")
    old_day = _make(db, kind="holiday", title="An old day", starts="2024-12-25")
    fresh = _make(db, title="Fresh", starts="2026-11-01 10:00")
    retention.prune(db, NOW)
    assert store.get(db, old) is None and store.get(db, old_day) is None and store.get(db, fresh) is not None
    assert store.log_of(db, old) == [] and len(store.log_of(db, fresh)) == 1


def test_the_live_box_reads_nothing_that_is_over_hidden_or_cancelled(db):
    ids = {name: _make(db, kind="score", title=name, starts=when) for name, when in
           (("soon", "2026-10-26 09:00"), ("old", "2026-10-01 09:00"))}
    cancelled = _make(db, kind="score", title="cancelled", starts="2026-10-26 09:30")
    db.execute(text("UPDATE site_events SET status = 'cancelled' WHERE id = :i"), {"i": cancelled})
    got = {r["title"] for r in store.live_candidates(db, NOW)}
    assert got == {"soon"} and ids["old"]                                 # a new row for an old match is not read just for being new


def test_a_prediction_settled_long_after_it_closed_is_still_shown(db):
    event_id = _make(db, kind="prediction", title="Who wins?", starts="2026-10-20 10:00", ends="2026-10-21 10:00", poll_post="1a2b")
    assert not rules.live_rows(store.live_candidates(db, NOW), NOW)        # closed days ago, no answer yet
    stamp = store.get(db, event_id)["updated_utc"]
    assert store.set_state(db, event_id, "final", {"outcome": "Reds"}, 1, NOW, stamp, rate)
    shown = rules.live_rows(store.live_candidates(db, NOW), NOW)
    assert [r["id"] for r in shown] == [event_id] and json.loads(shown[0]["state"]) == {"outcome": "Reds"}


def test_a_quick_edit_never_touches_what_it_did_not_send(db):
    event_id = _make(db, kind="score", title="Cup", starts="2026-10-26 07:00", state_home="Reds", state_away="Blues",
                     state_clock="10'", state_period="1st half")
    state = rules.clean_state("score", {"state_home_score": "2", "state_away_score": "1", "state_period": "", "state_clock": "67'"})
    assert state == {"home_score": 2, "away_score": 1, "period": None, "clock": "67'"}      # what was not sent is not in it
    stamp = store.get(db, event_id)["updated_utc"]
    assert store.set_state(db, event_id, "live", state, 1, NOW + 5, stamp, rate)
    assert json.loads(store.get(db, event_id)["state"]) == {"home": "Reds", "away": "Blues", "home_score": 2, "away_score": 1,
                                                            "clock": "67'"}                 # the teams stay; the empty period went


def test_two_edits_in_the_same_second_still_cannot_overwrite_each_other(db):
    event_id = _make(db, title="Launch")
    stamp = store.get(db, event_id)["updated_utc"]                           # written at NOW
    assert store.update(db, event_id, _fields(title="First"), 7, NOW, stamp, rate)     # saved in the same second
    assert not store.update(db, event_id, _fields(title="Second"), 8, NOW, stamp, rate)
    assert store.get(db, event_id)["title"] == "First" and store.get(db, event_id)["updated_utc"] > stamp


def test_a_double_loaded_seed_does_not_break_and_follows_a_corrected_date(db):
    seed = _fields(kind="countdown", title="Launch", starts="2026-12-01 10:00", show_from="2026-11-01 00:00")
    assert store.upsert_source(db, "curated", "launch", seed, NOW, rate) == "inserted"
    assert store.upsert_source(db, "curated", "launch", seed, NOW, rate) == "updated"       # the second press: no error
    corrected = dict(seed, show_from_utc=seed["show_from_utc"] + DAY)
    store.upsert_source(db, "curated", "launch", corrected, NOW + 1, rate)
    row = db.execute(text("SELECT show_from_utc FROM site_events WHERE source = 'curated'")).scalar()
    assert row == corrected["show_from_utc"]                                                # a corrected seed reaches the row
    assert db.execute(text("SELECT COUNT(*) FROM site_events")).scalar() == 1


def test_the_schema_mirrors_the_migration():
    migration, schema = read("scripts", "migrations", MIGRATION), read("schema.sql")
    for table in ("site_events", "site_event_log"):
        assert f"CREATE TABLE IF NOT EXISTS public.{table}" in migration and f"CREATE TABLE public.{table} (" in schema
    assert f"('{MIGRATION}')" in schema.split("schema_migrations", 1)[-1]
    # the mirror holds every constraint the migration has
    for name in ("site_events_source_key", "site_events_link_out", "site_events_alert_expires", "site_events_date_kind"):
        assert name in migration and name in schema
