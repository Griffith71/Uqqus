"""The Log's thread of a deleted post asks "is this author in a block with the reader?" once for the whole page (C31 of the second review of the Log):
`helpers/log_thread.py blocked_among`, on SQLite with the real `userblocks` columns. The rendering of the placeholders is in tests/pg/test_log_thread_controls.py
and the wiring in tests/test_log_thread_assets.py."""
import pytest
from sqlalchemy import create_engine, event, text

from ruqqus.helpers import log_thread


@pytest.fixture
def db():
    engine = create_engine("sqlite://")
    with engine.begin() as conn:
        conn.execute(text("CREATE TABLE userblocks (id INTEGER PRIMARY KEY, user_id INTEGER, target_id INTEGER, created_utc INTEGER)"))
    with engine.connect() as conn:
        yield conn


def block(db, user_id, target_id):
    db.execute(text("INSERT INTO userblocks (user_id, target_id, created_utc) VALUES (:u, :t, 0)"), {"u": user_id, "t": target_id})


def test_a_block_either_way_is_found_and_other_peoples_blocks_are_not(db):
    block(db, 1, 2)          # the reader blocked 2
    block(db, 3, 1)          # 3 blocked the reader
    block(db, 4, 5)          # nothing to do with the reader
    block(db, 6, 1)          # 6 blocked the reader, but 6 is not an author of this page
    assert log_thread.blocked_among(db, 1, [2, 3, 4, 5, 7]) == {2, 3}


def test_nobody_is_blocked_for_a_visitor_or_a_page_with_no_named_authors(db):
    block(db, 1, 2)
    assert log_thread.blocked_among(db, None, [2]) == set()
    assert log_thread.blocked_among(db, 0, [2]) == set()
    assert log_thread.blocked_among(db, 1, []) == set()
    assert log_thread.blocked_among(db, 1, [None, 0]) == set()


def test_a_block_between_two_other_people_is_not_the_readers(db):
    block(db, 2, 3)
    block(db, 3, 2)
    assert log_thread.blocked_among(db, 1, [2, 3]) == set()


def test_it_is_one_statement_however_many_authors_the_page_has(db):
    for other in range(2, 60):
        block(db, other, 1)
    seen = []

    def count(conn, cursor, statement, parameters, context, executemany):
        seen.append(statement)

    event.listen(db.engine, "before_cursor_execute", count)
    try:
        found = log_thread.blocked_among(db, 1, range(2, 400))
    finally:
        event.remove(db.engine, "before_cursor_execute", count)
    assert found == set(range(2, 60))
    assert len(seen) == 1, "one query for the page, never one for each comment"


def test_the_duplicates_and_the_readers_own_id_are_harmless(db):
    block(db, 1, 2)
    block(db, 2, 1)
    block(db, 1, 2)
    assert log_thread.blocked_among(db, 1, [2, 2, 1, "2"]) == {2}
