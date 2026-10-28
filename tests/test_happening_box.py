"""What the "What's happening" box and page draw (helpers/happening_box.py, helpers/happening_calendar.py,
helpers/hot_threads.py). The promises: each kind reads the way the plan says (a score line, a countdown, a result, an alert that
says it is automatic); a running match is not repeated under Today; the signature changes when something the reader would see
changes and NOT when only the clock moves (a poll must be able to answer 204); the month grid puts an item on every day it covers;
a hot thread is chosen by activity and age, and a post nobody has touched is never hot."""
from datetime import date
from pathlib import Path

import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.orm import Session

pytest.importorskip("ephem")

from ruqqus.helpers import happening_box as box
from ruqqus.helpers import happening_calendar as calendar_page
from ruqqus.helpers import hot_threads
from ruqqus.helpers import site_event_store as store
from ruqqus.helpers import site_events as ev

ROOT = Path(__file__).resolve().parent.parent
NOW = 1_793_000_000          # 2026-10-26 07:33:20 UTC
HOUR, DAY = 3600, 86400


def rate(value):
    return 3 if "badword" in value.lower() else 0


def _statements():
    """The tables, from the migration's own text (as tests/test_site_events.py)."""
    sql = (ROOT / "scripts" / "migrations" / "2026-12-03_site_events.sql").read_text(encoding="utf-8")
    sql = "\n".join(line for line in sql.splitlines() if not line.strip().startswith("--"))
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


def _add(db, **form):
    base = {"kind": "countdown", "title": "Thing", "starts": "2026-10-26 12:00"}
    return store.create(db, ev.clean_event(dict(base, **form), NOW), 1, NOW, rate)


def _model(db, now=NOW, **kw):
    return box.box(db, kw.pop("level", 1), kw.pop("sensitive", False), now, kw.pop("topics", []), kw.pop("hot", []), **kw)


# --- how a row reads -----------------------------------------------------------------------------------------------------

def _row(**over):
    row = {"id": 1, "kind": "score", "title": "Cup final", "date_kind": "instant", "starts_utc": NOW - 1200, "ends_utc": None,
           "expires_utc": None, "show_from_utc": None, "status": "scheduled", "status_changed_utc": 0, "state": "{}",
           "priority": 0, "hidden_utc": 0, "is_sensitive": False, "word_severity": 0, "starts_date": None, "ends_date": None,
           "poll_post_id": None, "link_url": "", "tentative": False}
    row.update(over)
    return row


def test_until_phrase_reads_in_the_unit_that_fits():
    assert box.until_phrase(5) == "now"
    assert box.until_phrase(60) == "in 1 minute" and box.until_phrase(45 * 60) == "in 45 minutes"
    assert box.until_phrase(119 * 60) == "in 119 minutes" and box.until_phrase(120 * 60) == "in 2 hours"
    assert box.until_phrase(47 * 3600) == "in 47 hours" and box.until_phrase(48 * 3600) == "in 2 days"
    assert box.until_phrase(8 * 86400 + 5) == "in 8 days"


def test_a_live_score_reads_as_a_score_line():
    state = '{"home": "Reds", "away": "Blues", "home_score": 2, "away_score": 1, "period": "2nd half", "clock": "67\'"}'
    r = box.present(_row(state=state, status="live"), NOW)
    assert (r["line1"], r["badge"], r["status"]) == ("Reds 2–1 Blues", "Live", "live")
    assert r["line2"] == "Cup final · 2nd half · 67'"
    assert r["at_text"] == ""                                   # a match under way does not repeat its start time
    soon = box.present(_row(state='{"home": "Owls", "away": "Hawks"}', starts_utc=NOW + 5400), NOW)
    assert (soon["line1"], soon["badge"], soon["until_text"]) == ("Owls v Hawks", "Soon", "in 90 minutes")
    assert soon["until_utc"] == NOW + 5400 and soon["at_text"].endswith("UTC")
    assert box.present(_row(status="final", state='{"home": "A", "away": "B", "home_score": 0, "away_score": 0}'), NOW)["badge"] == "Final"
    assert box.present(_row(), NOW)["line1"] == "Cup final"        # no teams: the title alone


def test_a_poll_and_a_prediction_point_at_their_poll_post():
    poll = box.present(_row(kind="poll", id=2, title="Best snack?", poll_post_id=1000, starts_utc=NOW - HOUR,
                            ends_utc=NOW + 2 * DAY), NOW)
    assert poll["link"] == "/post/rs" and poll["external"] is False and poll["badge"] == "Poll"
    assert poll["line2"] == "Vote · closes in 2 days"
    pred = _row(kind="prediction", id=3, title="Who wins?", poll_post_id=1000, starts_utc=NOW - HOUR, ends_utc=NOW + HOUR,
                state="{}")
    assert box.present(pred, NOW)["line2"] == "Prediction · closes in 60 minutes"
    done = box.present(dict(pred, status="final", state='{"outcome": "Reds"}'), NOW)
    assert (done["badge"], done["line2"]) == ("Result", "Result: Reds")


def test_an_alert_and_a_headline_link_out_and_say_what_they_are():
    alert = box.present(_row(kind="alert", id=4, title="M 6.1 earthquake", starts_utc=NOW - 600, expires_utc=NOW + HOUR,
                             link_url="https://earthquake.usgs.gov/x", state='{"source": "USGS", "severity": "severe"}'), NOW)
    assert alert["link"] == "https://earthquake.usgs.gov/x" and alert["external"] and alert["alert"]
    assert alert["line1"] == "M 6.1 earthquake"                                  # the source's words, never rewritten
    assert alert["line2"] == "USGS · Automatic, not an official warning"
    news = box.present(_row(kind="news", id=5, title="Talks resume", starts_utc=NOW - 600, link_url="https://example.org/n",
                            state='{"source": "Wikipedia"}', expires_utc=NOW + DAY), NOW)
    assert (news["badge"], news["line2"], news["external"]) == ("News", "Wikipedia", True)


def test_an_election_counts_down_then_points_at_the_results():
    election = _row(kind="election", id=6, title="General election", date_kind="civil", starts_date="2026-11-03", starts_utc=None,
                    link_url="https://example.gov/e", state='{"status": "upcoming"}')
    r = box.present(election, NOW)
    assert r["badge"] == "Election" and r["until_text"] == "in 7 days" and r["civil"] and r["date"] == "2026-11-03"
    results = box.present(dict(election, status="final", state='{"status": "results", "source": "the Commission"}'), NOW)
    assert results["line2"] == "Results: see the Commission"


def test_a_festival_and_an_instant_of_the_sky_tell_when():
    festival = box.present(_row(kind="holiday", title="Hanukkah", date_kind="civil", starts_date="2026-12-05", ends_date="2026-12-12",
                                starts_utc=None, state='{"evening_before": true}', tentative=False), NOW)
    assert festival["when_text"] == "Sat 05 Dec – Sat 12 Dec" and festival["line2"] == "Begins the evening before"
    assert box.present(_row(kind="holiday", date_kind="civil", starts_date="2026-12-05", starts_utc=None, tentative=True), NOW)["badge"] == "Expected"
    moon = box.present(_row(kind="astronomy", title="Full Moon", starts_utc=NOW + 3 * HOUR), NOW)
    assert moon["at_text"].endswith("UTC") and moon["when_text"].endswith("UTC") and moon["starts_utc"] == NOW + 3 * HOUR


# --- the box ---------------------------------------------------------------------------------------------------------------

def test_a_running_match_is_not_repeated_under_today(db):
    _add(db, kind="score", title="Cup final", starts="2026-10-26 07:00", status="live", state_home="Reds", state_away="Blues")
    _add(db, kind="event", title="Open day", date_kind="civil", starts="2026-10-26")        # live all day: also in Live
    _add(db, kind="countdown", title="Far countdown", starts="2026-12-01 10:00", show_from="2026-10-01 00:00")
    m = _model(db)
    live_titles = [r["line1"] for r in m["live"]]
    today_titles = [r["line1"] for r in m["today"]]
    assert "Reds v Blues" in live_titles and "Open day" in live_titles and "Far countdown" in live_titles
    assert "Reds v Blues" not in today_titles and "Open day" not in today_titles
    assert "Full Moon" in today_titles                                                         # the sky is still there


def test_the_box_is_never_longer_than_its_rows_and_counts_the_rest(db):
    for n in range(5):
        _add(db, kind="countdown", title=f"Soon {n}", starts=f"2026-10-26 1{n}:00", show_from="2026-10-01 00:00")
    m = _model(db)
    assert len(m["live"]) == box.ROWS and m["live_more"] == 2
    assert len(_model(db, rows=6)["live"]) == 5 and _model(db, rows=6)["live_more"] == 0


def test_the_child_level_leaves_alerts_elections_and_news_out(db):
    _add(db, kind="alert", title="Alert", starts="2026-10-26 06:00", link_url="https://example.org/a")
    _add(db, kind="election", title="Election", date_kind="civil", starts="2026-10-26", link_url="https://example.org/e")
    _add(db, kind="score", title="Match", starts="2026-10-26 07:00", status="live")
    assert {r["line1"] for r in _model(db, level=1)["live"]} == {"Alert", "Election", "Match"}
    assert {r["line1"] for r in _model(db, level=2)["live"]} == {"Match"}


def test_the_signature_ignores_the_clock_and_notices_a_change(db):
    event_id = _add(db, kind="score", title="Cup", starts="2026-10-26 07:00", status="live", state_home="A", state_away="B",
                    state_home_score="0", state_away_score="0")
    first = _model(db)["rev"]
    assert _model(db, now=NOW + 20)["rev"] == first                       # 20 seconds on: nothing a reader sees changed
    stamp = store.get(db, event_id)["updated_utc"]
    store.set_state(db, event_id, "live", {"home_score": 1}, 1, NOW + 30, stamp, rate)
    assert _model(db)["rev"] != first                                      # a goal
    assert _model(db, now=NOW + 2 * DAY)["rev"] != first                   # a new UTC day moves the Today window


def test_a_poll_counting_down_is_not_a_change_but_a_goal_is(db):
    _add(db, kind="poll", title="Best snack?", starts="2026-10-26 06:00", ends="2026-10-26 09:00", poll_post="1a2b")
    first = _model(db)
    assert first["live"][0]["line2"].startswith("Vote \u00b7 closes in ")
    assert _model(db, now=NOW + 120)["rev"] == first["rev"]                    # 'closes in 90 minutes' became '88': the clock only


def test_the_box_asks_often_only_while_something_changes_by_the_minute(db):
    _add(db, kind="event", title="Conference", date_kind="civil", starts="2026-10-26", ends="2026-10-28")
    assert _model(db)["live"] and _model(db)["next"] == box.POLL_SECONDS                # a conference under way: no need
    _add(db, kind="score", title="Cup", starts="2026-10-26 07:00", status="live")
    assert _model(db)["next"] == box.POLL_LIVE_SECONDS


def test_a_whole_day_event_under_way_names_its_days_not_the_word_today(db):
    _add(db, kind="event", title="Open day", date_kind="civil", starts="2026-10-26")
    row = _model(db)["live"][0]
    assert row["badge"] == "Live" and row["line2"] == "Mon 26 Oct"                    # 'Today' is only true in some zones


def test_what_a_reader_without_script_sees_as_today_is_the_utc_day(db):
    ids = {name: _add(db, kind="event", title=name, date_kind="civil", starts=day)
           for name, day in (("yesterday", "2026-10-25"), ("today", "2026-10-26"), ("tomorrow", "2026-10-27"))}
    seen = {name: box.present(store.get(db, event_id), NOW)["today_utc"] for name, event_id in ids.items()}
    assert seen == {"yesterday": False, "today": True, "tomorrow": False}


def test_words_an_admin_wrote_carry_their_language_and_ours_do_not(db):
    _add(db, kind="news", title="Las conversaciones se reanudan", starts="2026-10-26 06:00", link_url="https://example.org/n",
         title_lang="es")
    spanish = _model(db)["live"][0]
    assert (spanish["lang"], spanish["foreign"]) == ("es", True)
    moon = box.present(_row(kind="astronomy", source="rules", title="Full Moon", starts_utc=NOW + HOUR), NOW)
    assert (moon["lang"], moon["foreign"]) == ("", False)
    template = (ROOT / "ruqqus" / "templates" / "partials" / "happening_rows.html").read_text(encoding="utf-8")
    assert 'lang="{{ r.lang }}"' in template and 'dir="auto"' in template


def test_the_live_heading_has_its_dot_only_while_something_is_live(db):
    assert _model(db)["live_on"] is False
    _add(db, kind="countdown", title="Far off", starts="2026-11-30 12:00", show_from="2026-10-01 00:00")
    assert _model(db)["live_on"] is False                                  # a countdown that is not under way is not "Live"
    _add(db, kind="score", title="Cup final", starts="2026-10-26 06:30", ends="2026-10-26 09:30", status="live",
         state_home="Reds", state_away="Blues")
    assert _model(db)["live_on"] is True


def test_the_topics_and_hot_threads_are_either_or():
    class Row:
        def __init__(self, i):
            self.id = i
    engine = create_engine("sqlite://")
    with engine.begin() as conn:
        for statement in _statements():
            conn.execute(text(statement))
    with Session(engine) as db:
        with_topics = box.box(db, 1, False, NOW, [Row(1), Row(2)], [Row(9)])
        assert [t.id for t in with_topics["topics"]] == [1, 2] and with_topics["hot"] == []
        without = box.box(db, 1, False, NOW, [], [Row(9)])
        assert without["topics"] == [] and [p.id for p in without["hot"]] == [9]


# --- the month ---------------------------------------------------------------------------------------------------------------

def test_the_month_puts_an_item_on_every_day_it_covers(db):
    _add(db, kind="event", title="Conference", date_kind="civil", starts="2026-11-09", ends="2026-11-11")
    month = calendar_page.month(db, 2026, 11, NOW)
    cells = {c["iso"]: c for week in month["weeks"] for c in week}
    for day in ("2026-11-09", "2026-11-10", "2026-11-11"):
        assert "Conference" in [chip["line1"] for chip in cells[day]["chips"]]
    assert "Conference" not in [chip["line1"] for chip in cells["2026-11-12"]["chips"]]
    assert (month["prev"], month["next"]) == ("2026-10", "2026-12") and month["today"] == "2026-10-26"
    assert cells["2026-10-26"]["other"] and not cells["2026-11-09"]["other"]            # the grid's first week is October's
    assert month["weeks"][0][0]["date"].weekday() == 0                                  # Monday first, as the guild calendar
    assert any(i["line1"] == "Conference" for i in month["list"])


def test_a_busy_day_sends_its_chips_and_counts_those_past_three(db):
    for n in range(5):
        _add(db, kind="event", title=f"E{n}", date_kind="civil", starts="2026-11-20")
    cell = {c["iso"]: c for week in calendar_page.month(db, 2026, 11, NOW)["weeks"] for c in week}["2026-11-20"]
    assert sum(1 for c in cell["chips"] if c["line1"].startswith("E")) == 5                # all of them are sent...
    assert cell["more"] == len(cell["chips"]) - calendar_page.CHIPS                        # ...the count is what is past three
    page = (ROOT / "ruqqus" / "templates" / "happening.html").read_text(encoding="utf-8")
    assert "chip(c, loop.index > 3)" in page and ".cal-chip[hidden]" in (ROOT / "ruqqus" / "assets" / "style" / "_happening.scss").read_text()


def test_an_instant_just_outside_the_grid_is_sent_for_the_script_to_place(db):
    # a full moon at 22:00 UTC on 31 October is already 1 November for a reader at UTC+2 or more: 1 November is in the grid
    _add(db, kind="astronomy", title="Late moon", starts="2026-10-25 23:30")             # the day before the November grid starts
    _add(db, kind="astronomy", title="Early moon", starts="2026-12-07 00:30")            # the day after it ends
    _add(db, kind="astronomy", title="Far moon", starts="2026-12-20 12:00")
    month = calendar_page.month(db, 2026, 11, NOW)
    assert {c["line1"] for c in month["spill"]} == {"Late moon", "Early moon"}
    assert all(c["starts_utc"] is not None for c in month["spill"])
    in_cells = {chip["line1"] for week in month["weeks"] for cell in week for chip in cell["chips"]}
    assert "Far moon" not in in_cells and "Far moon" not in {c["line1"] for c in month["spill"]}


def test_a_multi_day_instant_moves_only_from_its_first_day(db):
    _add(db, kind="event", title="Festival", starts="2026-11-09 20:00", ends="2026-11-11 06:00")
    cells = {c["iso"]: c for week in calendar_page.month(db, 2026, 11, NOW)["weeks"] for c in week}
    first, middle, last = (cells[d]["chips"][0] for d in ("2026-11-09", "2026-11-10", "2026-11-11"))
    assert first["starts_utc"] is not None                      # the script may move the first to the reader's day...
    assert middle["starts_utc"] is None and last["starts_utc"] is None                  # ...the others are markers and stay


def test_the_month_leaves_out_what_the_reader_may_not_see(db):
    _add(db, kind="election", title="Election", date_kind="civil", starts="2026-11-03", link_url="https://example.org/e")
    _add(db, kind="event", title="Sensitive thing", date_kind="civil", starts="2026-11-04", is_sensitive="1")
    titles = lambda m: {chip["line1"] for week in m["weeks"] for c in week for chip in c["chips"]}
    assert {"Election", } <= titles(calendar_page.month(db, 2026, 11, NOW))
    assert "Sensitive thing" not in titles(calendar_page.month(db, 2026, 11, NOW))                     # the All rule
    assert "Sensitive thing" in titles(calendar_page.month(db, 2026, 11, NOW, show_sensitive=True))
    assert "Election" not in titles(calendar_page.month(db, 2026, 11, NOW, level=2))                   # Child
    assert date(2026, 11, 1).weekday() == 6


# --- hot threads -------------------------------------------------------------------------------------------------------------

def test_a_hot_thread_is_chosen_by_activity_and_age():
    now = 1_800_000_000
    rows = [
        (1, now - 3600, 10, 5),            # an hour old, 10 votes, 5 comments: hot
        (2, now - 40 * 3600, 30, 20),      # busy but old
        (3, now - 3600, 0, 0),             # nobody touched it
        (4, now - 2 * 3600, 2, 1),
        (5, now - 3600, -4, 0),            # downvoted, no comments: not hot
    ]
    assert hot_threads.rank(rows, now) == [1, 4, 2]
    assert hot_threads.rank(rows, now, keep=1) == [1]
    assert hot_threads.score(10, 5, 3600) > hot_threads.score(10, 5, 24 * 3600)           # the same activity fades
    assert hot_threads.score(0, 3, 3600) > hot_threads.score(3, 0, 3600)                  # a comment counts for more than a vote
    assert hot_threads.rank([], now) == []


def test_without_redis_nothing_is_hot(monkeypatch):
    monkeypatch.setattr(hot_threads, "_redis", lambda: None)
    assert hot_threads.cached_ids(None, 1) == []
