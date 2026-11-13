"""Reporting a message in a private chat: the rules (ruqqus/helpers/chat_reports.py), the SQL (helpers/chat_report_store.py,
on SQLite from the migration), the retention rule, and the wiring that keeps the promises: who sent each message and
when come from the chat server, the words are the reporter's copy and the admin page says so, nobody is told who
reported, and the box never puts a message in as HTML."""
import json
from pathlib import Path

import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.orm import Session

from ruqqus.helpers import chat_report_store as store
from ruqqus.helpers import chat_reports as rules
from ruqqus.helpers import retention

ROOT = Path(__file__).resolve().parent.parent
E1, E2, E3 = "$aaaaaaaaaaaa", "$bbbbbbbbbbbb", "$cccccccccccc"
ROOM = "!roomroomroom"


def read(*parts):
    return ROOT.joinpath(*parts).read_text(encoding="utf-8")


# --- the rules ------------------------------------------------------------------------------------------------------

def test_the_context_is_each_message_once_the_reported_one_last_and_nothing_else():
    raw = json.dumps([{"event_id": E3, "body": "the bad one"}, {"event_id": E1, "body": "first"}, {"event_id": E2, "body": "x" * 5000}])
    got = rules.parse_context(raw, E3)
    assert [e for e, _ in got] == [E1, E2, E3] and len(got[1][1]) == rules.BODY_MAX
    for bad in ("", "{", "[]", json.dumps({"a": 1}), json.dumps([{"event_id": E1, "body": "a"}]),            # the reported one missing
                json.dumps([{"event_id": "not-an-id", "body": "a"}]), json.dumps([{"event_id": E3, "body": 5}]),
                json.dumps([{"event_id": E3, "body": "a"}, {"event_id": E3, "body": "b"}]),
                json.dumps([{"event_id": f"${c * 12}", "body": "a"} for c in "abcdef"]),                       # more than five
                "[" * 5000 + "]" * 5000):
        with pytest.raises(rules.Refused):
            rules.parse_context(bad, E3)


def test_only_the_chat_servers_word_counts_for_who_and_when():
    good = {"type": "m.room.encrypted", "room_id": ROOM, "sender": "@ruqqus_2:s", "origin_server_ts": 1700000000000}
    assert rules.checked(good, ROOM) == ("@ruqqus_2:s", 1700000000000)
    assert rules.checked({**good, "room_id": "!other"}, ROOM) is None
    assert rules.checked({**good, "type": "m.room.member"}, ROOM) is None          # not a message
    assert rules.checked({**good, "origin_server_ts": True}, ROOM) is None
    assert rules.checked(None, ROOM) is None


def test_refusals():
    me = "@ruqqus_1:s"
    assert rules.refusal(me, "@ruqqus_2:s", 0, 0, False) is None
    assert rules.refusal(me, None, 0, 0, False) == "That message isn't in this chat any more."
    assert rules.refusal(me, me, 0, 0, False) == "You can't report your own message."
    assert rules.refusal(me, "@ruqqus_2:s", 0, 0, True) == "You've already reported this message."
    assert rules.refusal(me, "@ruqqus_2:s", rules.PER_HOUR, 0, False) == "You've sent a lot of reports. Try again later."
    assert rules.refusal(me, "@ruqqus_2:s", 0, rules.PER_DAY, False) == "You've sent a lot of reports. Try again later."
    assert rules.clean_reason("  spam \n spam " + "x" * 300).startswith("spam spam") and len(rules.clean_reason("x" * 300)) == 200


# --- the SQL --------------------------------------------------------------------------------------------------------

@pytest.fixture
def db():
    sql = read("scripts", "migrations", "2026-12-25_chat_reports.sql")
    sql = "\n".join(line for line in sql.splitlines() if not line.strip().startswith("--"))
    engine = create_engine("sqlite://")
    with engine.begin() as conn:
        for statement in (s.strip() for s in sql.split(";") if "CREATE" in s):
            conn.execute(text(statement.replace("public.", "").replace("IF NOT EXISTS ", "")
                              .replace("SERIAL PRIMARY KEY", "INTEGER PRIMARY KEY AUTOINCREMENT")))
    with Session(engine) as session:
        yield session


def test_a_report_keeps_the_copy_in_order_and_closes_once(db):
    rid = store.create(db, 1, ROOM, "pair", E2, 2, "spam", [(E1, 1, "@ruqqus_1:s", 1000, "hi"), (E2, 2, "@ruqqus_2:s", 2000, "buy now")], 500)
    assert store.already(db, 1, E2) and not store.already(db, 3, E2)
    assert store.sent_since(db, 1, 0) == 1 and store.sent_since(db, 1, 501) == 0
    assert [(m[1], m[2], m[5]) for m in store.messages_of(db, rid)] == [(E1, 1, "hi"), (E2, 2, "buy now")]
    assert [r[0] for r in store.open_reports(db)] == [rid]
    assert store.resolve(db, rid, rules.DISMISSED, 9, 600) and not store.resolve(db, rid, rules.ACTIONED, 9, 601)
    assert store.open_reports(db) == [] and store.report(db, rid)[8:11] == ("dismissed", 600, 9)


def test_the_copied_words_go_90_days_after_a_report_closed_and_an_open_one_keeps_them(db):
    old = store.create(db, 1, ROOM, "pair", E1, 2, "", [(E1, 2, "@ruqqus_2:s", 1, "old words")], 10)
    still = store.create(db, 1, ROOM, "pair", E2, 2, "", [(E2, 2, "@ruqqus_2:s", 1, "open words")], 10)
    store.resolve(db, old, rules.ACTIONED, 9, 20)
    rule = dict(retention.RULES)["words of closed chat reports"]
    db.execute(text(rule), {"chat_reports": 20})                 # the cutoff (now less 90 days) is not yet past its closing
    assert store.messages_of(db, old)[0][5] == "old words"
    db.execute(text(rule), {"chat_reports": 21})
    assert store.messages_of(db, old)[0][5] == "" and store.messages_of(db, still)[0][5] == "open words"
    assert retention.CHAT_REPORT_TEXT_DAYS == rules.KEEP_TEXT_DAYS
    assert '"chat_reports": now - CHAT_REPORT_TEXT_DAYS * DAY' in read("ruqqus", "helpers", "retention.py")


# --- the wiring -----------------------------------------------------------------------------------------------------

def test_the_route_takes_who_and_when_from_the_chat_server_and_only_members_report():
    route = read("ruqqus", "routes", "chat_reports_pages.py")
    body = route.split("def chat_report(v):", 1)[1].split("\n# --- admins", 1)[0]
    assert "report_rules.checked(matrix.event(room_id, eid), room_id)" in body
    assert body.index("kind = chat_rooms.kind_for(g.db, room_id, v.id)") < body.index("abort(404)                              # only the chat's own")
    assert "[(eid, ids.get(info[0]), info[0], info[1], words) for eid, words, info in kept]" in body
    for decorator in ("@auth_required", "@not_qr_limited", '@not_child_locked("chat")', "@validate_formkey"):
        assert decorator in route.split("def chat_report(v):", 1)[0].rsplit("@app.route", 1)[1]
    page = read("ruqqus", "templates", "admin", "chat_reports.html")
    assert "the words can't be checked" in page and "The page doesn't say who reported" in page
    assert "reporter" not in page.split("{% for r in reports %}", 1)[1]
    assert "{{ m.body }}" in page and "| safe" not in page


def test_the_box_and_the_context_are_text_only_and_the_page_reports_what_it_shows():
    report = read("ruqqus", "assets", "chat_src", "src", "report.js")
    assert "innerHTML" not in report and "textContent" in report
    assert "export const CONTEXT = 4;" in report and rules.CONTEXT_MAX == 5
    index = read("ruqqus", "assets", "chat_src", "src", "index.js")
    assert "contextOf(shown, item.eventId, textOf)" in index
    assert "if (!channel && !gchannel) return reportPrivately(item, { conversation, group, room });" in index
