"""Guild events and their calendar (helpers/guild_events.py, helpers/guild_event_store.py, routes/guild_events.py).
The promises: counts only, never who (no route, template or JSON reads who answered; only the reminder job does);
guildmasters with content permission alone create, edit and cancel; one guild never reaches another's event; the
.ics feed exists for public guilds only and its text is escaped; reminders go out once."""
import calendar
import re
from pathlib import Path

import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.orm import Session

from ruqqus.helpers import guild_events as rules
from ruqqus.helpers import guild_event_store as store

ROOT = Path(__file__).resolve().parent.parent
NOW = 1_800_000_000


def read(*parts):
    return ROOT.joinpath(*parts).read_text(encoding="utf-8")


# --- the rules -------------------------------------------------------------------------------------------------------

def test_titles_details_places_and_links():
    assert rules.clean_title("  Movie   night ") == "Movie night"
    for bad in ("", "<b>x</b>", "x" * (rules.TITLE_MAX + 1), "bell\x07"):
        with pytest.raises(rules.Refused):
            rules.clean_title(bad)
    with pytest.raises(rules.Refused):
        rules.clean_details("x" * (rules.DETAILS_MAX + 1))
    assert rules.clean_location("", " The  park ") == ("", "The park")
    assert rules.clean_location("https://meet.example.com/x", "")[0] == "https://meet.example.com/x"
    for bad in ("javascript:alert(1)", "https://u:p@example.com"):
        with pytest.raises(rules.Refused):
            rules.clean_location(bad, "")
    with pytest.raises(rules.Refused):
        rules.clean_location("", "<script>")


def test_times():
    assert rules.clean_times(str(NOW + 600), "", NOW) == (NOW + 600, NOW + 600 + 3600)
    assert rules.clean_times(str(NOW - 60), str(NOW + 60), NOW) == (NOW - 60, NOW + 60)        # a minute ago is fine
    for start, end in ((NOW - 3600, ""), (NOW + 400 * 86400, ""), (NOW + 60, NOW + 60), (NOW + 60, NOW + 8 * 86400),
                       ("abc", ""), ("", ""), ("١٢٣", "")):
        with pytest.raises(rules.Refused):
            rules.clean_times(str(start), str(end), NOW)
    assert rules.clean_times(str(NOW - 3600), str(NOW + 60), NOW, keep_start=NOW - 3600)[0] == NOW - 3600  # an edit


def test_answers_status_and_limits():
    assert rules.clean_status("Going") == "going" and rules.clean_status("none") is None and rules.clean_status("") is None
    with pytest.raises(rules.Refused):
        rules.clean_status("maybe")
    ok = dict(event=(0, NOW + 60), now=NOW, signed_in=True, suspended=False, bot=False, exiled=False)
    assert rules.rsvp_refusal(**ok) is None
    for change in ({"signed_in": False}, {"suspended": True}, {"bot": True}, {"exiled": True},
                   {"event": (NOW - 5, NOW + 60)}, {"event": (0, NOW - 1)}):
        assert rules.rsvp_refusal(**dict(ok, **change))
    assert rules.status_of((NOW + 5, NOW + 10, 0), NOW) == "upcoming"
    assert rules.status_of((NOW - 5, NOW + 10, 0), NOW) == "live"
    assert rules.status_of((NOW - 50, NOW - 10, 0), NOW) == "over"
    assert rules.status_of((NOW + 5, NOW + 10, NOW), NOW) == "cancelled"
    assert rules.make_refusal(rules.PER_MONTH - 1) is None and rules.make_refusal(rules.PER_MONTH)


def test_the_month_grid():
    assert rules.parse_month("2026-02", NOW) == (2026, 2)
    assert rules.parse_month("2026-13", NOW) == rules.parse_month("garbage", NOW) == rules.parse_month(None, NOW)
    assert rules.shift_month(2026, 12, 1) == (2027, 1) and rules.shift_month(2026, 1, -1) == (2025, 12)
    weeks = rules.month_grid(2026, 2)
    assert all(len(w) == 7 for w in weeks) and weeks[0][0].weekday() == 0      # Monday first
    start, end = rules.grid_span(weeks)
    assert start < calendar.timegm((2026, 2, 1, 0, 0, 0)) and end > calendar.timegm((2026, 3, 1, 0, 0, 0))
    assert rules.day_key(calendar.timegm((2026, 2, 3, 23, 0, 0))) == "2026-02-03"


def test_the_ics_text_is_escaped_and_folded():
    body = rules.ics([{"id": 4, "title": "Meet; greet, and \\ more\nnext", "details": "x" * 200, "starts_utc": NOW,
                       "ends_utc": NOW + 3600, "url": "https://site.test/+pets/events/4", "location": "Park",
                       "cancelled": True}], host="site.test", name="+pets events", now=NOW)
    assert body.startswith("BEGIN:VCALENDAR\r\n") and body.endswith("END:VCALENDAR\r\n")
    assert "SUMMARY:Meet\\; greet\\, and \\\\ more\\nnext" in body
    assert "STATUS:CANCELLED" in body and "UID:guild-event-4@site.test" in body
    assert all(len(line.encode()) <= 75 for line in body.split("\r\n"))
    assert "\n" not in body.replace("\r\n", "")
    unfolded = body.replace("\r\n ", "")
    assert "DESCRIPTION:" + "x" * 200 in unfolded
    assert "\x07" not in rules.ics_text("bell\x07")


# --- the SQL ---------------------------------------------------------------------------------------------------------

def _tables():
    sql = read("scripts", "migrations", "2026-10-31_guild_events.sql")
    sql = "\n".join(line for line in sql.splitlines() if not line.strip().startswith("--"))
    for s in (s.strip() for s in sql.split(";")):
        if s.startswith("CREATE"):
            yield (s.replace("public.", "").replace("IF NOT EXISTS ", "")
                   .replace("SERIAL PRIMARY KEY", "INTEGER PRIMARY KEY AUTOINCREMENT"))


@pytest.fixture
def db():
    engine = create_engine("sqlite://")
    with engine.begin() as conn:
        conn.execute(text("CREATE TABLE boards (id INTEGER PRIMARY KEY)"))
        conn.execute(text("INSERT INTO boards (id) VALUES (1), (2)"))
        for statement in _tables():
            conn.execute(text(statement))
    with Session(engine) as session:
        yield session


def _fields(**over):
    f = {"title": "Meetup", "details": "", "details_html": "", "word_severity": 0, "starts_utc": NOW + 1800,
         "ends_utc": NOW + 5400, "location_url": "", "location_text": "Park", "space_post_id": None}
    f.update(over)
    return f


def test_events_lists_and_one_guild_only(db):
    a = store.create(db, 1, 7, _fields(), NOW)
    b = store.create(db, 1, 7, _fields(title="Later", starts_utc=NOW + 86400, ends_utc=NOW + 90000), NOW)
    old = store.create(db, 1, 7, _fields(title="Old", starts_utc=NOW - 9000, ends_utc=NOW - 5400), NOW - 9999)
    theirs = store.create(db, 2, 8, _fields(title="Theirs"), NOW)
    assert [r[0] for r in store.upcoming(db, 1, NOW)] == [a, b] and [r[0] for r in store.past(db, 1, NOW)] == [old]
    assert store.event(db, 2, a) is None and store.event(db, 1, theirs) is None
    store.edit(db, 2, a, _fields(title="Hijacked"))
    store.cancel(db, 2, a, NOW)
    assert store.event(db, 1, a)[3] == "Meetup" and store.event(db, 1, a)[12] == 0
    store.cancel(db, 1, a, NOW)
    assert store.next_event(db, 1, NOW)[0] == b                       # a cancelled event is not "next"
    assert [r[0] for r in store.between(db, 1, NOW, NOW + 3600)] == [a]
    assert store.made_since(db, 1, NOW - 60) == 2
    with pytest.raises(Exception):
        store.create(db, 1, 7, _fields(starts_utc=NOW, ends_utc=NOW), NOW)   # the table's own check


def test_answers_are_counted_and_only_the_readers_own_is_read(db):
    e = store.create(db, 1, 7, _fields(), NOW)
    store.answer(db, e, 10, "going", NOW)
    store.answer(db, e, 11, "going", NOW)
    store.answer(db, e, 12, "interested", NOW)
    store.answer(db, e, 11, "interested", NOW)                        # changed: still one answer each
    assert store.counts(db, [e]) == {e: {"going": 1, "interested": 2}}
    assert store.answer_of(db, e, 11) == "interested" and store.answer_of(db, e, 99) is None
    store.answer(db, e, 12, None, NOW)
    assert store.counts(db, [e, 999]) == {e: {"going": 1, "interested": 1}, 999: {"going": 0, "interested": 0}}
    with pytest.raises(Exception):
        db.execute(text("INSERT INTO guild_event_rsvps (event_id, user_id, status) VALUES (:e, 50, 'maybe')"), {"e": e})


def test_reminders_go_out_once_and_again_after_a_new_start(db):
    soon = store.create(db, 1, 7, _fields(starts_utc=NOW + 1800, ends_utc=NOW + 3600), NOW)
    store.create(db, 1, 7, _fields(starts_utc=NOW + 7200, ends_utc=NOW + 9000), NOW)            # not within the hour
    store.answer(db, soon, 10, "going", NOW)
    store.answer(db, soon, 11, "interested", NOW)
    assert [r[0] for r in store.due_reminders(db, NOW, rules.REMIND_BEFORE)] == [soon]
    assert store.claim_reminder(db, soon, NOW) is True and store.claim_reminder(db, soon, NOW) is False
    assert store.due_reminders(db, NOW, rules.REMIND_BEFORE) == []
    assert store.reminder_user_ids(db, soon) == [10, 11]
    store.edit(db, 1, soon, _fields(starts_utc=NOW + 1800, ends_utc=NOW + 3600, title="Same time"))
    assert store.event(db, 1, soon)[14] == NOW                        # same start: still reminded
    store.edit(db, 1, soon, _fields(starts_utc=NOW + 2400, ends_utc=NOW + 4000))
    assert store.event(db, 1, soon)[14] == 0                          # moved: reminded again


# --- the wiring ------------------------------------------------------------------------------------------------------

def test_routes_guard_every_change_and_never_say_who():
    routes = read("ruqqus", "routes", "guild_events.py")
    found = re.findall(r'@app\.route\("([^"]+)", methods=\["(\w+)"\]\)\n((?:@[^\n]+\n)*)def (\w+)\(([^)]*)\):\n(.*?)(?=\n@app|\ndef |\Z)',
                       routes, re.S)
    assert len(found) == 12
    for path, method, decorators, name, _args, body in found:
        assert "visible_guild(guildname" in body, name
        if method == "POST":
            assert "@validate_formkey" in decorators, name
        if name in ("guild_event_new", "guild_event_create", "guild_event_edit", "guild_event_save", "guild_event_cancel"):
            assert "if not _content_gm(board, v):" in body, name
        if path.endswith(".ics"):
            assert "_public_or_404(board)" in body and "visible_guild(guildname, None" in body, name
    public = routes.split("def _public_or_404(", 1)[1].split("\n\n", 1)[0]
    assert "board.is_private or board.is_circle or board.is_banned" in public
    # who answered is read in one place: the reminder job
    assert routes.count("reminder_user_ids(") == 1
    assert routes.split("reminder_user_ids(", 1)[0].rsplit("\ndef ", 1)[1].startswith("send_due_reminders(")
    # an exile who answered before the exile is not reminded (audit G-05)
    assert "not board.can_view(user) or board.has_ban(user)" in routes.split("def send_due_reminders(", 1)[1]
    assert "html.escape(note)" in read("ruqqus", "routes", "guild_wiki.py")
    store_src = read("ruqqus", "helpers", "guild_event_store.py")
    assert store_src.count("SELECT user_id FROM guild_event_rsvps") == 1
    for name in ("events.html", "event.html", "event_edit.html"):
        t = read("ruqqus", "templates", "guild", name)
        assert "user_id" not in t and "username" not in t, name
    script = read("ruqqus", "assets", "js", "guild_events.js")
    assert "innerHTML" not in script and "d.going" in script
    assert "send_due_reminders(db_session)" in read("scripts", "recomputes.py")


def test_the_schema_mirrors_the_migration():
    migration, schema = read("scripts", "migrations", "2026-10-31_guild_events.sql"), read("schema.sql")
    for table in ("guild_events", "guild_event_rsvps"):
        assert f"CREATE TABLE IF NOT EXISTS public.{table}" in migration and f"CREATE TABLE public.{table} (" in schema
