"""Feedback about the site (ruqqus/helpers/site_feedback.py): the rules, and the SQL on SQLite (the daily count, the
context whitelist, the inbox and its filters, closing and reopening an item, screenshots kept apart)."""
import json

from sqlalchemy import create_engine, text
from sqlalchemy.orm import Session

from ruqqus.helpers import site_feedback as sf

NOW = 1_800_000_000


def make():
    db = Session(create_engine("sqlite://"))
    db.execute(text("CREATE TABLE users (id INTEGER PRIMARY KEY, username TEXT)"))
    db.execute(text("""CREATE TABLE site_feedback (id INTEGER PRIMARY KEY, user_id INTEGER NOT NULL,
        kind TEXT DEFAULT 'other' NOT NULL, message TEXT DEFAULT '' NOT NULL, page TEXT DEFAULT '' NOT NULL,
        context TEXT DEFAULT '' NOT NULL, status TEXT DEFAULT 'open' NOT NULL, response TEXT DEFAULT '' NOT NULL,
        created_utc INTEGER DEFAULT 0 NOT NULL, resolved_utc INTEGER DEFAULT 0 NOT NULL)"""))
    db.execute(text("""CREATE TABLE site_feedback_images (feedback_id INTEGER PRIMARY KEY, mime TEXT NOT NULL,
        data BLOB NOT NULL, created_utc INTEGER DEFAULT 0 NOT NULL)"""))
    for uid, name in ((1, "alice"), (2, "bob")):
        db.execute(text("INSERT INTO users (id, username) VALUES (:i, :n)"), {"i": uid, "n": name})
    return db


def file(db, user=1, kind="bug", message="It broke", at=NOW, page="/", context="{}"):
    return sf.add(db, user, kind, message, page, context, at)


def test_kind_and_message_rules():
    assert sf.parse_kind(" Bug ") == ("bug", None)
    assert all(sf.parse_kind(k)[0] == k for k in sf.KINDS)
    assert sf.parse_kind("spam")[1] and sf.parse_kind(None)[1] and sf.parse_kind("")[1]
    assert sf.parse_message("  The  button\x00 is\r\nlate  \r\n\r\n\r\n\r\nvery  ") == ("The  button is\nlate\n\nvery", None)
    assert sf.parse_message("   \n ")[1] and sf.parse_message(None)[1]
    assert len(sf.parse_message("x" * 5000)[0]) == sf.MESSAGE_MAX
    assert sf.parse_response(" thanks\x07 ") == "thanks" and sf.parse_response(None) == ""
    assert sf.parse_status("FIXED") == "fixed" and sf.parse_status("nonsense") is None and sf.parse_status(None) is None


def test_the_context_is_a_whitelist_and_the_page_has_no_query_or_fragment():
    raw = json.dumps({"path": "/post/abc?token=SECRET#comment-9", "theme": "dark", "density": "compact", "width": 390,
                      "height": 844, "lang": "pt-BR", "ip": "1.2.3.4", "cookie": "session=abc", "extra": {"a": 1}})
    page, context = sf.parse_context(raw, "Mozilla/5.0 (X11)\x00")
    assert page == "/post/abc"
    assert json.loads(context) == {"path": "/post/abc", "theme": "dark", "density": "compact", "width": 390,
                                   "height": 844, "lang": "pt-BR", "browser": "Mozilla/5.0 (X11)"}
    assert "SECRET" not in context and "1.2.3.4" not in context and "session" not in context


def test_a_bad_context_is_dropped_piece_by_piece_never_trusted():
    assert sf.parse_context("not json") == ("", "{}")
    assert sf.parse_context('["a"]') == ("", "{}")
    assert sf.parse_context(None) == ("", "{}")
    page, context = sf.parse_context({"path": "https://evil.example/x", "theme": "purple", "density": "huge",
                                      "width": "wide", "height": -4, "lang": "x" * 40})
    assert page == "" and context == "{}"
    for sneaky in ("//evil.example/x", "/\\evil.example", "javascript:alert(1)", "/a b", "/a\x00b", "x/y"):
        assert sf.parse_context({"path": sneaky, "width": 10 ** 9}) == ("", "{}"), sneaky
    assert len(json.loads(sf.parse_context({}, "b" * 900)[1])["browser"]) == sf.BROWSER_MAX


def test_the_daily_count_counts_only_the_last_day_and_only_the_member():
    db = make()
    for n in range(3):
        file(db, 1, at=NOW - 10 - n)
    file(db, 1, at=NOW - 90000)
    file(db, 2, at=NOW - 10)
    assert sf.filed_today(db, 1, NOW) == 3 and sf.filed_today(db, 2, NOW) == 1 and sf.filed_today(db, 9, NOW) == 0


def test_the_inbox_lists_newest_first_and_filters_by_status_and_kind():
    db = make()
    a = file(db, 1, "bug", "one", NOW)
    b = file(db, 2, "design", "two", NOW + 1)
    c = file(db, 1, "idea", "three", NOW + 2)
    assert [i["id"] for i in sf.items(db)] == [c, b, a]
    sf.set_status(db, b, "seen", now=NOW + 5)
    sf.set_status(db, c, "fixed", now=NOW + 6)
    assert [i["id"] for i in sf.items(db, "waiting")] == [b, a]
    assert [i["id"] for i in sf.items(db, "fixed")] == [c]
    assert [i["id"] for i in sf.items(db, None, "design")] == [b]
    assert [i["id"] for i in sf.items(db, "waiting", "bug")] == [a]
    assert sf.items(db, "nonsense") and sf.waiting_count(db) == 2


def test_an_item_reads_back_with_its_sender_context_and_screenshot_flag():
    db = make()
    page, context = sf.parse_context({"path": "/home", "theme": "light", "width": 800})
    item_id = file(db, 2, "design", "Too grey", NOW, page, context)
    assert sf.get(db, item_id)["has_image"] is False
    sf.attach_image(db, item_id, "image/webp", b"RIFF....WEBPdata", NOW)
    item = sf.get(db, item_id)
    assert (item["username"], item["kind"], item["page"], item["status"]) == ("bob", "design", "/home", "open")
    assert item["context"] == {"path": "/home", "theme": "light", "width": 800} and item["has_image"] is True
    assert sf.image(db, item_id) == ("image/webp", b"RIFF....WEBPdata") and sf.image(db, 999) is None
    assert sf.get(db, 999) is None


def test_closing_records_when_reopening_forgets_and_a_blank_response_keeps_the_old_one():
    db = make()
    item_id = file(db)
    assert sf.set_status(db, item_id, "fixed", "Fixed in the next update", NOW + 100) is True
    item = sf.get(db, item_id)
    assert (item["status"], item["response"], item["resolved_utc"]) == ("fixed", "Fixed in the next update", NOW + 100)
    assert sf.set_status(db, item_id, "open", "", NOW + 200) is True
    item = sf.get(db, item_id)
    assert (item["status"], item["response"], item["resolved_utc"]) == ("open", "Fixed in the next update", 0)
    assert sf.set_status(db, 999, "seen") is False


def test_the_sender_of_a_deleted_account_still_reads_without_a_name():
    db = make()
    item_id = file(db, user=77)
    assert sf.get(db, item_id)["username"] is None
