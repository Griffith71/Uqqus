"""What a page of the Log's item, account and thread pages asks the database for (helpers/log_paging.py; the second review's C56), on SQLite.

The promises: page N of an item's or an account's log is entries (N-1)*25 to N*25 of the newest-first list, read with an offset (not by reading the first
N*25 and drawing the last 25), and its Next link is `fetch`'s "there are more"; the page of a deleted post's thread is cut from ids alone and is the same
tree the route used to cut from 2,000 loaded rows (compared with the old algorithm kept here as the oracle), and loading the page's comments, their text
included, is one statement however many there are, where a wildcard lazyload read each comment's text on its own. The real app can't be imported by the
unit suite (gevent), so the loader is run on stand-in models that have the real `Comment.comment_aux` relationship's settings (a test reads them off
comment.py), and the route is held to the helpers by its source. The page on the real app, with its statements counted, is tests/pg/test_log_paging.py."""
import random
from pathlib import Path

import pytest
from sqlalchemy import Column, ForeignKey, Integer, String, create_engine, event, text
from sqlalchemy.orm import Session, declarative_base, joinedload, lazyload, relationship

from ruqqus.helpers import action_log as log, log_paging, log_thread, log_view

import log_tables

ROOT = Path(__file__).resolve().parent.parent
NOW = 1_793_000_000


def read(*parts):
    return ROOT.joinpath(*parts).read_text(encoding="utf-8")


LOG_PAGES = read("ruqqus", "routes", "log_pages.py")


def body(source, name):
    """A function's source, from its `def` to the next top-level decorator or definition."""
    start = source.index(f"def {name}(")
    rest = source[start:]
    ends = [i for i in (rest.find("\n@app.", 1), rest.find("\ndef ", 1)) if i > 0]
    return rest[:min(ends)] if ends else rest


def counted(engine):
    """A list that collects every statement the engine runs, with its parameters, until `stop()`."""
    seen = []

    def listener(conn, cursor, statement, parameters, context, executemany):
        seen.append((statement, parameters))

    event.listen(engine, "before_cursor_execute", listener)
    return seen, lambda: event.remove(engine, "before_cursor_execute", listener)


# --- the window of an item's or an account's log -----------------------------------------------------------------------------------------

def test_the_window_is_the_page_held_to_forty_and_how_many_entries_come_before_it():
    assert (log_paging.ABOUT_MAX_PAGE, log_view.PER_PAGE) == (40, 25)
    assert log_paging.about_window(1) == (1, 0)
    assert log_paging.about_window(2) == (2, 25)
    assert log_paging.about_window(40) == (40, 975)
    assert log_paging.about_window(41) == log_paging.about_window(1000) == (40, 975), "how far back it reads is bounded: 40 pages"
    assert log_paging.about_window(0) == (1, 0) and log_paging.about_window(-3) == (1, 0)


@pytest.fixture
def db():
    engine = create_engine("sqlite://")
    with engine.begin() as conn:
        for statement in log_tables.migration_statements(read("scripts", "migrations", "2026-12-09_action_log.sql")):
            conn.execute(text(statement))
        log_tables.apply(conn)
        conn.execute(text("CREATE TABLE submissions (id INTEGER PRIMARY KEY, audience INTEGER DEFAULT 0, author_id INTEGER, repost_id INTEGER DEFAULT 0, "
                          "is_anonymous BOOLEAN DEFAULT 0)"))
        conn.execute(text("CREATE TABLE comments (id INTEGER PRIMARY KEY, parent_submission INTEGER, author_id INTEGER, is_anonymous BOOLEAN DEFAULT 0)"))
        conn.execute(text("CREATE TABLE boards (id INTEGER PRIMARY KEY, is_private BOOLEAN DEFAULT 0, is_circle BOOLEAN DEFAULT 0)"))
        conn.execute(text("CREATE TABLE users (id INTEGER PRIMARY KEY, admin_level INTEGER DEFAULT 0)"))
        conn.execute(text("CREATE TABLE contributors (id INTEGER PRIMARY KEY, user_id INTEGER, board_id INTEGER, is_active BOOLEAN)"))
        conn.execute(text("CREATE TABLE mods (id INTEGER PRIMARY KEY, user_id INTEGER, board_id INTEGER, invite_rescinded BOOLEAN)"))
        conn.execute(text("INSERT INTO boards VALUES (1, 0, 0)"))
        conn.execute(text("INSERT INTO users VALUES (10, 0), (30, 0)"))
        conn.execute(text("INSERT INTO submissions (id, author_id, repost_id, is_anonymous) VALUES (100, 30, 0, 0)"))
    with Session(engine) as session:
        yield session


def about_post_100(db, count):
    return [log.record(db, "hide_post_from_guild", 10, board_id=1, post_id=100, now=NOW + i) for i in range(count)]


def old_page(db, scope, page):
    """What the route drew before: read the first page*25 entries, keep the last 25 of them."""
    rows, more = log.fetch(db, level=0, scope=scope, limit=page * 25)
    return [r["id"] for r in rows[(page - 1) * 25:page * 25]], len(rows) > (page - 1) * 25 + 25


def new_page(db, scope, page):
    page, offset = log_paging.about_window(page)
    rows, more = log.fetch(db, level=0, scope=scope, limit=log_view.PER_PAGE, offset=offset)
    return [r["id"] for r in rows], more


def test_page_n_shows_the_same_entries_the_old_read_drew_and_the_next_link_finally_has_something_to_say(db):
    about_post_100(db, 60)
    scope = log.about_scope(post_ids=[100])
    newest_first = [r["id"] for r in log.fetch(db, level=0, scope=scope, limit=60)[0]]
    assert len(newest_first) == 60
    for page, expected_more in ((1, True), (2, True), (3, False)):
        entries, more = new_page(db, scope, page)
        assert entries == old_page(db, scope, page)[0] == newest_first[(page - 1) * 25:page * 25], page
        assert more is expected_more, page
        assert old_page(db, scope, page)[1] is False, "the old test could never be true: fetch hands back at most `limit` rows"
    assert len(new_page(db, scope, 3)[0]) == 10 and new_page(db, scope, 4)[0] == []


def test_a_page_reads_its_own_twenty_six_rows_and_not_everything_before_it(db):
    about_post_100(db, 80)
    seen, stop = counted(db.get_bind())
    try:
        new_page(db, log.about_scope(post_ids=[100]), 3)
    finally:
        stop()
    selects = [(sql, params) for sql, params in seen if "FROM log_entries" in sql and "ORDER BY created_utc DESC" in sql]
    assert len(selects) == 1
    sql, params = selects[0]
    values = list(params.values()) if isinstance(params, dict) else list(params)
    assert "LIMIT" in sql and "OFFSET" in sql
    assert 26 in values and 50 in values and 76 not in values, values


# --- the page of a thread ------------------------------------------------------------------------------------------------------------------

def old_algorithm(rows, page):
    """The route's own cut before C56, on (id, parent) pairs: the same code, kept here as the oracle."""
    children, top = {}, []
    for c in rows:
        if c[1]:
            children.setdefault(c[1], []).append(c)
        else:
            top.append(c)
    shown_top = top[(page - 1) * 25:page * 25]
    in_page = []

    def walk(c, depth):
        in_page.append(c)
        return {"id": c[0], "depth": depth, "children": [walk(k, depth + 1) for k in children.get(c[0], ())] if depth < 8 else []}

    tree = [walk(c, 0) for c in shown_top]
    return tree, [c[0] for c in in_page], len(top) > page * 25, len(rows)


def random_thread(seed, size, top_share=0.15, orphans=0):
    rng = random.Random(seed)
    rows = []
    for i in range(1, size + 1):
        parent = None if (not rows or rng.random() < top_share) else rng.choice(rows)[0]
        rows.append((i, parent))
    for n in range(orphans):
        rows.append((size + 1 + n, 900_000 + n))           # a comment whose parent was beyond the read
    return rows


def deep_chain(length):
    return [(i, None if i == 1 else i - 1) for i in range(1, length + 1)]


@pytest.mark.parametrize("size", (0, 1, 5, 80, 400, 2000))
def test_the_page_is_the_tree_the_route_used_to_cut_from_its_rows(size):
    for seed in range(4):
        rows = random_thread(seed, size, top_share=0.15 if seed % 2 else 0.02, orphans=3 if size else 0)
        for page in (1, 2, 3, 40, 1000):
            assert log_paging.thread_page(rows, page) == old_algorithm(rows, page), (size, seed, page)


def test_a_chain_is_shown_to_depth_eight_and_no_further_and_more_than_twenty_five_top_level_comments_page():
    chain = deep_chain(12)
    tree, ids, more, total = log_paging.thread_page(chain, 1)
    assert ids == list(range(1, 10)), "levels 0 to 8: nine comments of a chain of twelve"
    assert (more, total) == (False, 12)
    assert (tree, ids, more, total) == old_algorithm(chain, 1)
    wide = [(i, None) for i in range(1, 61)]
    tree1, ids1, more1, total1 = log_paging.thread_page(wide, 1)
    tree3, ids3, more3, _ = log_paging.thread_page(wide, 3)
    assert ids1 == list(range(1, 26)) and more1 and total1 == 60
    assert ids3 == list(range(51, 61)) and not more3
    assert log_paging.thread_page(wide, 4)[0] == [] and log_paging.thread_page(wide, 4)[1] == []


def test_a_next_page_exists_only_when_a_top_level_comment_is_left():
    for top, page, more in ((25, 1, False), (26, 1, True), (50, 2, False), (51, 2, True), (0, 1, False)):
        rows = [(i, None) for i in range(1, top + 1)] + [(1000 + i, 1 + i) for i in range(5) if top]       # comments under the first ones change nothing
        assert log_paging.thread_page(rows, page)[2] is more, (top, page)
        assert log_paging.thread_page(rows, page) == old_algorithm(rows, page), (top, page)


def test_a_comment_whose_parent_was_not_read_is_in_no_tree_and_a_page_lists_parents_before_children():
    rows = [(1, None), (2, 1), (3, 77), (4, 2), (5, None)]
    tree, ids, more, total = log_paging.thread_page(rows, 1)
    assert ids == [1, 2, 4, 5] and 3 not in ids and total == 5 and not more
    assert tree[0]["children"][0]["children"][0] == {"id": 4, "depth": 2, "children": []}


@pytest.fixture
def thread_db():
    engine = create_engine("sqlite://")
    with engine.begin() as conn:
        conn.execute(text("CREATE TABLE comments (id INTEGER PRIMARY KEY, parent_submission INTEGER, parent_comment_id INTEGER, author_id INTEGER, "
                          "created_utc INTEGER)"))
    with Session(engine) as session:
        yield session


def put(db, rows):
    db.execute(text("INSERT INTO comments (id, parent_submission, parent_comment_id, author_id, created_utc) "
                    "VALUES (:i, :p, :c, 1, :t)"), [{"i": i, "p": p, "c": c, "t": t} for i, p, c, t in rows])


def test_the_index_is_one_posts_comments_oldest_first_with_ties_by_id_and_cut_at_the_limit(thread_db):
    put(thread_db, [(5, 1, None, 100), (3, 1, 5, 100), (4, 1, None, 90), (9, 2, None, 80), (7, 1, 4, 120)])
    assert log_paging.comment_index(thread_db, 1) == [(4, None), (3, 5), (5, None), (7, 4)]
    assert log_paging.comment_index(thread_db, 1, limit=2) == [(4, None), (3, 5)]
    assert log_paging.comment_index(thread_db, 2) == [(9, None)] and log_paging.comment_index(thread_db, 3) == []


@pytest.mark.parametrize("size", (5, 500))
def test_the_index_is_one_statement_however_big_the_thread(thread_db, size):
    put(thread_db, [(i, 1, None if i == 1 else i - 1, i) for i in range(1, size + 1)])
    seen, stop = counted(thread_db.get_bind())
    try:
        index = log_paging.comment_index(thread_db, 1)
    finally:
        stop()
    assert len(index) == size and len(seen) == 1


def test_the_index_reads_at_most_two_thousand_comments_as_the_page_always_did():
    assert log_paging.THREAD_MAX == 2000 and log_paging.THREAD_PER_PAGE == 25 and log_paging.THREAD_DEPTH == 8


# --- loading the page's comments, their text with them -------------------------------------------------------------------------------------

Base = declarative_base()


class Person(Base):
    __tablename__ = "users"
    id = Column(Integer, primary_key=True)
    username = Column(String)


class Aux(Base):
    __tablename__ = "comments_aux"
    id = Column(Integer, ForeignKey("comments.id"), primary_key=True)
    body_html = Column(String)


class Note(Base):
    """The settings of `Comment` that matter here: its text is a joined, inner-joined relationship and so is its author (classes/comment.py)."""
    __tablename__ = "comments"
    id = Column(Integer, primary_key=True)
    author_id = Column(Integer, ForeignKey("users.id"))
    parent_submission = Column(Integer)
    parent_comment_id = Column(Integer)
    created_utc = Column(Integer, default=0)
    comment_aux = relationship(Aux, lazy="joined", uselist=False, innerjoin=True, primaryjoin="Note.id==Aux.id")
    author = relationship(Person, lazy="joined", innerjoin=True, primaryjoin="Person.id==Note.author_id")

    @property
    def body_html(self):
        return self.comment_aux.body_html


def test_the_stand_in_has_the_real_comments_settings():
    source = read("ruqqus", "classes", "comment.py")
    aux = source.split("comment_aux = relationship(")[1].split(")")[0]
    assert 'lazy="joined"' in aux and "innerjoin=True" in aux and "uselist=False" in aux
    author = source.split("    author = relationship(")[1].split(")")[0]
    assert 'lazy="joined"' in author and "innerjoin=True" in author


@pytest.fixture
def orm():
    engine = create_engine("sqlite://")
    Base.metadata.create_all(engine)
    with Session(engine) as session:
        session.add_all([Person(id=1, username="alice"), Person(id=2, username="bob")])
        session.commit()
        yield session


def fill(session, count, no_aux=()):
    for i in range(1, count + 1):
        session.add(Note(id=i, author_id=1 + i % 2, parent_submission=1, parent_comment_id=None if i % 5 == 1 else i - 1, created_utc=i))
        if i not in no_aux:
            session.add(Aux(id=i, body_html=f"<p>{i}</p>"))
    session.commit()
    session.expunge_all()


def route_load(session, ids):
    """The route's own query."""
    return {c.id: c for c in session.query(Note).options(lazyload("*"), joinedload(Note.comment_aux, innerjoin=False))
            .filter(Note.id.in_(ids)).all()}


@pytest.mark.parametrize("count", (5, 200))
def test_the_pages_comments_come_with_their_text_in_one_statement(orm, count):
    fill(orm, count)
    seen, stop = counted(orm.get_bind())
    try:
        loaded = route_load(orm, list(range(1, count + 1)))
        texts = [c.body_html for c in loaded.values()]          # what draw() reads of each live comment
    finally:
        stop()
    assert len(loaded) == count and texts[0] == "<p>1</p>"
    assert len(seen) == 1, "the text rode in the same statement; nothing is read when a comment is drawn"


def test_without_the_joined_text_a_comment_reads_its_own_which_is_what_this_replaces(orm):
    fill(orm, 50)
    seen, stop = counted(orm.get_bind())
    try:
        loaded = orm.query(Note).options(lazyload("*")).filter(Note.id.in_(list(range(1, 51)))).all()
        [c.body_html for c in loaded]
    finally:
        stop()
    assert len(seen) == 51, "one for the comments and one for each text: the old page"


def test_a_comment_with_no_text_row_is_still_loaded_and_is_not_silently_dropped(orm):
    fill(orm, 5, no_aux=(3,))
    loaded = route_load(orm, [1, 2, 3, 4, 5])
    assert sorted(loaded) == [1, 2, 3, 4, 5] and loaded[3].comment_aux is None


def test_the_whole_page_is_a_fixed_number_of_statements_however_many_comments_it_holds():
    """Index, rows with their text, and the block question: three statements for 8 comments and for 800."""
    results = {}
    for count in (8, 800):
        engine = create_engine("sqlite://")
        Base.metadata.create_all(engine)
        with engine.begin() as conn:
            conn.execute(text("CREATE TABLE userblocks (id INTEGER PRIMARY KEY, user_id INTEGER, target_id INTEGER, created_utc INTEGER)"))
            conn.execute(text("INSERT INTO userblocks (user_id, target_id, created_utc) VALUES (7, 2, 0)"))
        with Session(engine) as session:
            session.add_all([Person(id=1, username="alice"), Person(id=2, username="bob")])
            for i in range(1, count + 1):
                session.add(Note(id=i, author_id=1 + i % 2, parent_submission=1, parent_comment_id=None if i % 4 == 1 else i - 1, created_utc=i))
                session.add(Aux(id=i, body_html=f"<p>{i}</p>"))
            session.commit()
            session.expunge_all()
            seen, stop = counted(engine)
            try:
                tree, ids, more, total = log_paging.thread_page(log_paging.comment_index(session, 1), 1)
                loaded = route_load(session, ids)
                in_page = [loaded[i] for i in ids if i in loaded]
                blocked = log_thread.blocked_among(session, 7, {c.author_id for c in in_page})
                [c.body_html for c in in_page]
            finally:
                stop()
            results[count] = len(seen)
            assert blocked == {2} and total == count
    assert results == {8: 3, 800: 3}, results


# --- the route is held to the helpers -----------------------------------------------------------------------------------------------------

def test_the_about_pages_page_by_offset_and_say_whether_there_is_a_next_page():
    page = body(LOG_PAGES, "_about_page")
    assert "log_paging.about_window(page)" in page and "offset=offset" in page and "limit=log_view.PER_PAGE" in page
    assert "limit=want" not in page and "start +" not in page and "rows[start" not in page, "no more reading N pages to draw one"
    assert "more=more and page < log_paging.ABOUT_MAX_PAGE" in page and "len(rows) >" not in page
    fetch = page.split("action_log.fetch(")[1].split(")")[0]
    assert "level=0" in fetch and "member_boards" not in fetch and "open_guilds_only" not in fetch, "what /log shows, never more"
    assert "ABOUT_MAX_PAGE =" not in LOG_PAGES, "the bound has one home"


def test_the_thread_reads_an_index_then_only_the_pages_comments_with_their_text():
    thread = body(LOG_PAGES, "log_thread")
    assert "log_paging.comment_index(g.db, post.id)" in thread and "log_paging.thread_page(" in thread
    assert 'joinedload(Comment.comment_aux, innerjoin=False)' in thread and 'lazyload("*")' in thread
    assert "Comment.id.in_(ids)" in thread and ".limit(THREAD_MAX)" not in thread
    assert "THREAD_MAX" not in LOG_PAGES and "THREAD_PER_PAGE" not in LOG_PAGES, "the constants live in helpers/log_paging.py"
    assert "any_block_exists" not in thread and "log_thread_rules.blocked_among(" in thread
    assert "more=more" in thread and "total=total" in thread and "len(top)" not in thread and "len(rows)" not in thread
    assert "from sqlalchemy.orm import joinedload, lazyload" in LOG_PAGES and "log_paging" in LOG_PAGES.split("from ruqqus.helpers import action_log")[1].split("\n")[0]
    assert "def _" not in thread.split("def draw(")[0], "the second review's tests cut this function at its first nested helper"


def test_a_comment_gone_between_the_index_and_the_rows_is_no_line_at_all():
    thread = body(LOG_PAGES, "log_thread")
    assert 'c = loaded.get(node["id"])' in thread and "if c is None:" in thread
    assert "if k is not None" in thread and "if n is not None" in thread


def test_the_paging_helpers_build_no_sql_from_text_and_commit_nothing():
    source = read("ruqqus", "helpers", "log_paging.py")
    assert 'f"' not in source.split('"""', 2)[2] and ".format(" not in source and "commit" not in source.split('"""', 2)[2]
    assert "ORDER BY created_utc ASC, id ASC LIMIT :limit" in source
