"""Unfair exposure (helpers/exposure.py; the owner, 2026-11-14): a guess tested against a suspect is noted for the staff, once a day, and never shown to anyone else.

On SQLite with the Log's tables built from its migration."""
from pathlib import Path

import log_tables
import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.orm import Session

from ruqqus.helpers import action_log as log
from ruqqus.helpers import exposure

ROOT = Path(__file__).resolve().parent.parent
NOW = 1_793_000_000
DAY = 86400


@pytest.fixture
def db():
    engine = create_engine("sqlite://")
    with engine.begin() as conn:
        for statement in log_tables.migration_statements((ROOT / "scripts" / "migrations" / "2026-12-09_action_log.sql").read_text(encoding="utf-8")):
            conn.execute(text(statement))
        log_tables.apply(conn, modactions=True)
        conn.execute(text("CREATE TABLE users (id INTEGER PRIMARY KEY, username VARCHAR, admin_level INTEGER DEFAULT 0, is_banned INTEGER DEFAULT 0, "
                          "unban_utc INTEGER DEFAULT 0, is_deleted BOOLEAN DEFAULT 0)"))
        conn.execute(text("CREATE TABLE boards (id INTEGER PRIMARY KEY, is_private BOOLEAN DEFAULT 0, is_circle BOOLEAN DEFAULT 0)"))
        conn.execute(text("INSERT INTO boards (id) VALUES (3), (4)"))
        conn.execute(text("INSERT INTO users (id, username) VALUES (10, 'guildmaster_a'), (11, 'guildmaster_b'), (30, 'suspect')"))
    with Session(engine) as session:
        yield session


def test_a_tested_guess_is_a_staff_only_entry_that_names_the_guildmaster_the_guild_and_the_account(db):
    entry = exposure.note(db, 3, 10, 30, exposure.EXILED_BY_NAME, by_id=11, now=NOW)
    row = db.execute(text("SELECT action, actor_id, actor_role, board_id, target_user_id, audience, log_type, staff_note FROM log_entries WHERE id = :e"), {"e": entry}).mappings().one()
    assert (row["action"], row["actor_id"], row["actor_role"], row["board_id"], row["target_user_id"], row["audience"]) == ("possible_probing", 10, "guildmaster", 3, 30, 2)
    assert "@guildmaster_a exiled by name an account that is exiled by alias" in row["staff_note"] and "made by @guildmaster_b" in row["staff_note"]


def test_nothing_below_staff_can_read_it(db):
    exposure.note(db, 3, 10, 30, exposure.MUTED_BY_NAME, now=NOW)
    assert log.fetch(db, level=0)[0] == [], "the public Log doesn't have it"
    assert log.fetch(db, level=0, member_boards=[3])[0] == [], "nor a member of the guild's own"
    assert len(log.fetch(db, level=3)[0]) == 1, "the staff do"


def test_the_same_test_is_one_line_a_day_and_another_account_or_guildmaster_is_another(db):
    first = exposure.note(db, 3, 10, 30, exposure.EXILED_BY_NAME, now=NOW)
    assert first and exposure.note(db, 3, 10, 30, exposure.EXILED_BY_NAME, now=NOW + 3600) is None
    assert exposure.note(db, 3, 11, 30, exposure.EXILED_BY_NAME, now=NOW + 3600), "another guildmaster"
    assert exposure.note(db, 4, 10, 30, exposure.EXILED_BY_NAME, now=NOW + 3600), "another guild"
    assert exposure.note(db, 3, 10, 31, exposure.EXILED_BY_NAME, now=NOW + 3600), "another account"
    assert exposure.note(db, 3, 10, 30, exposure.EXILED_BY_NAME, now=NOW + DAY + 10), "a day later it is noted again"


def test_a_note_that_cannot_be_written_never_stops_the_action(db):
    db.execute(text("DROP TABLE log_entries"))
    assert exposure.note(db, 3, 10, 30, exposure.EXILED_BY_NAME, now=NOW) is None


def test_the_staff_page_counts_the_last_thirty_days(db):
    exposure.note(db, 3, 10, 30, exposure.EXILED_BY_NAME, now=NOW - 40 * DAY)
    exposure.note(db, 3, 10, 30, exposure.MUTED_BY_NAME, now=NOW - DAY)
    assert exposure.waiting(db, now=NOW) == 1
    assert [r["staff_note"][:14] for r in exposure.recent(db, now=NOW)] == ["@guildmaster_a"]
    assert exposure.recent(db, since_days=90, now=NOW)[0]["target_user_id"] == 30


def test_it_is_a_staff_action_and_not_a_decision_the_public_or_the_person_is_told_of():
    assert "possible_probing" in log.STAFF_ACTIONS and "possible_probing" not in log.PUBLIC_ACTIONS
    assert log.audience_for("possible_probing", board_id=3, private=False) >= log.STAFF
