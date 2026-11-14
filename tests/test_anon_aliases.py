"""A stable alias per thread for anonymous participants (the Log plan's decision 23; slice D3e): helpers/anon_aliases.py on SQLite with the table built from the
migration, helpers/anonymity.py's stand-in with a number, and the wiring of every place that makes someone anonymous or draws them.

The promises: a number is given in the order people first become anonymous in a thread and is never reused or changed; asking twice gives the same number;
the same person is a different number in another thread; a guild copy and a repost share the primary post's thread; the author is one number on their
post and on their anonymous comments; the number outlives the account; and the stand-in an anonymous author is drawn as carries only that number."""
from pathlib import Path
from types import SimpleNamespace

import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.orm import Session

from ruqqus.helpers import anon_aliases as aliases, anonymity

ROOT = Path(__file__).resolve().parent.parent


def read(*parts):
    return ROOT.joinpath(*parts).read_text(encoding="utf-8")


@pytest.fixture
def db():
    engine = create_engine("sqlite://")
    sql = "\n".join(line for line in read("scripts", "migrations", "2027-01-05_anon_aliases.sql").splitlines() if not line.strip().startswith("--"))
    with engine.begin() as conn:
        for statement in (part.strip().replace("public.", "").replace("IF NOT EXISTS ", "") for part in sql.split(";") if part.strip().startswith("CREATE")):
            conn.execute(text(statement))
        conn.execute(text("CREATE TABLE submissions (id INTEGER PRIMARY KEY, author_id INTEGER, repost_id INTEGER DEFAULT 0)"))
        conn.execute(text("CREATE TABLE comments (id INTEGER PRIMARY KEY, author_id INTEGER, parent_submission INTEGER)"))
        conn.execute(text("INSERT INTO submissions VALUES (1, 7, 0), (2, 8, 0), (3, 7, 1), (4, 9, 1)"))        # 3 is a guild copy of 1, 4 someone's repost of 1
        conn.execute(text("INSERT INTO comments VALUES (10, 7, 1), (11, 8, 1), (12, 7, 2), (13, 9, 3)"))
    with Session(engine) as session:
        yield session


POST1 = ("post", 1)


def test_numbers_are_given_in_the_order_people_become_anonymous_and_never_change(db):
    assert [aliases.alias_for(db, POST1, u) for u in (7, 8, 9)] == [1, 2, 3]
    assert [aliases.alias_for(db, POST1, u) for u in (9, 7, 8)] == [3, 1, 2], "asked again, the same number: idempotent"
    assert aliases.alias_for(db, POST1, 10) == 4
    assert aliases.numbers_in(db, POST1) == {7: 1, 8: 2, 9: 3, 10: 4}


def test_the_same_person_is_a_different_number_in_every_other_thread_and_there_is_no_global_one(db):
    assert aliases.alias_for(db, POST1, 7) == 1 and aliases.alias_for(db, POST1, 8) == 2
    assert aliases.alias_for(db, ("post", 2), 8) == 1, "first to be anonymous in the second thread"
    assert aliases.alias_for(db, ("post", 2), 7) == 2
    assert aliases.numbers_in(db, ("post", 2)) == {8: 1, 7: 2} and aliases.numbers_in(db, ("post", 99)) == {}
    assert db.execute(text("SELECT COUNT(*) FROM anon_aliases WHERE user_id = 7")).scalar() == 2, "one row per thread, nothing global"


def test_a_number_is_not_given_twice_even_if_the_unique_index_is_all_that_stands_between(db):
    aliases.alias_for(db, POST1, 7)
    # a second writer that read the same maximum: its insert conflicts on the number and is dropped; the loop reads again and takes the next one
    db.execute(text("INSERT INTO anon_aliases (scope_kind, scope_id, user_id, n, created_utc) VALUES ('post', 1, 99, 2, 0)"))
    assert aliases.alias_for(db, POST1, 8) == 3
    numbers = [r[0] for r in db.execute(text("SELECT n FROM anon_aliases WHERE scope_id = 1 ORDER BY n"))]
    assert numbers == [1, 2, 3] and len(set(numbers)) == len(numbers)


def test_the_thread_is_the_primary_posts_for_the_post_its_copies_and_its_comments(db):
    original = SimpleNamespace(id=1, repost_id=0)
    copy = SimpleNamespace(id=3, repost_id=1)
    repost = SimpleNamespace(id=4, repost_id=1)
    assert aliases.scope_key(original) == aliases.scope_key(copy) == aliases.scope_key(repost) == POST1
    comment_on_copy = SimpleNamespace(parent_submission=3, post=copy)
    assert aliases.scope_of(comment_on_copy) == POST1 and aliases.scope_of(original) == POST1
    assert aliases.scope_of(SimpleNamespace(parent_submission=None, post=None)) is None, "a system notice has no thread"


def test_the_author_is_one_number_on_their_post_and_their_anonymous_comments_and_the_logs_lines_find_it(db):
    aliases.alias_for(db, POST1, 8)                              # someone else first
    aliases.give(db, POST1, 7)                                   # the post's author becomes anonymous: number 2
    aliases.give(db, aliases.scope_of(SimpleNamespace(parent_submission=3, post=SimpleNamespace(id=3, repost_id=1))), 7)
    found = aliases.numbers_for_items(db, post_ids=[1, 3, 2, 4], comment_ids=[10, 11, 12, 13])
    assert found[("post", 1)] == found[("post", 3)] == 2, "the post and its guild copy: the same author, the same number"
    assert found[("comment", 10)] == 2 and found[("comment", 11)] == 1, "comments: their authors' numbers in the post's thread"
    assert ("post", 2) not in found and ("comment", 12) not in found, "no number given yet in that thread: drawn as plain Anonymous"
    assert ("post", 4) not in found and ("comment", 13) not in found, "someone with no number in the thread (9, a repost's author and a comment on a copy)"
    assert found == {("post", 1): 2, ("post", 3): 2, ("comment", 10): 2, ("comment", 11): 1}


def test_give_is_safe_when_the_table_is_missing_and_the_numbers_outlive_the_account(db):
    aliases.give(db, POST1, 7)
    db.execute(text("DELETE FROM submissions WHERE author_id = 7"))                    # nothing about the account or its items is needed to read it back
    assert aliases.numbers_in(db, POST1) == {7: 1}
    db.execute(text("DROP TABLE anon_aliases"))
    assert aliases.give(db, POST1, 8) is None, "a database that hasn't reached the migration still posts, comments and deletes"
    assert db.execute(text("SELECT 1")).scalar() == 1, "and its transaction is not broken"


# --- the stand-in -------------------------------------------------------------------------------------------------------------------------

def item(number=None, author=7, anonymous=True):
    return SimpleNamespace(is_anonymous=anonymous, author_id=author, author=SimpleNamespace(username="alice", is_deleted=False), _alias=number)


def user(id_, admin=0):
    return SimpleNamespace(id=id_, admin_level=admin, qr_limited=False)


@pytest.fixture
def resolver():
    anonymity.alias_resolver = lambda it: getattr(it, "_alias", None)
    yield
    anonymity.alias_resolver = None


def test_a_hidden_author_is_drawn_with_their_number_and_nothing_more(resolver):
    stranger = user(3)
    drawn = anonymity.author_of(item(3), stranger)
    assert str(drawn.shown_name) == "Anonymous #3" and drawn.alias == 3 and anonymity.label_of(item(3), stranger) == "Anonymous #3"
    assert drawn.username == "Anonymous" and drawn.id is None and drawn.permalink == "javascript:void(0)" and drawn.is_deleted is False
    assert drawn.display_name == "" and drawn.profile_url == anonymity.DEFAULT_AVATAR and drawn.anything_new is None, "a template that asks for more learns nothing"
    assert isinstance(drawn, anonymity.AnonymousAuthor)


def test_no_number_yet_is_plain_anonymous_and_admins_and_the_author_always_see_the_account(resolver):
    assert anonymity.author_of(item(None), user(3)) is anonymity.ANONYMOUS and anonymity.label_of(item(None), user(3)) == "Anonymous"
    for viewer in (user(7), user(9, admin=3)):
        assert anonymity.author_of(item(3), viewer).username == "alice", "never hidden from the author or an admin"
    assert anonymity.author_of(item(3, anonymous=False), user(3)).username == "alice"


def test_without_the_apps_resolver_every_anonymous_author_is_the_plain_stand_in():
    anonymity.alias_resolver = None
    assert anonymity.author_of(item(3), user(3)) is anonymity.ANONYMOUS and anonymity.alias_of(item(3)) is None


# --- the wiring ----------------------------------------------------------------------------------------------------------------------------

def test_every_way_of_becoming_anonymous_gives_a_number_and_the_database_gets_the_table():
    posts, comments = read("ruqqus", "routes", "posts.py"), read("ruqqus", "routes", "comments.py")
    assert "anon_aliases.give(g.db, anon_aliases.scope_key(new_post), v.id)" in posts and "if new_post.is_anonymous:" in posts
    assert "anon_aliases.give(g.db, anon_aliases.scope_key(parent_post), v.id)" in comments and "if c.is_anonymous:" in comments
    anonymise = read("ruqqus", "helpers", "anonymise.py")
    assert anonymise.count("anon_aliases.give(") == 3, "a post, a comment and the author's own comments under a deleted or unlinked post"
    job = read("ruqqus", "helpers", "account_anonymise.py")
    assert job.count("anon_aliases.give(") == 2, "the account job: the threads of its posts and of its comments"
    assert "anon_aliases" in read("ruqqus", "routes", "anonymity.py") and "anonymity.alias_resolver = _alias_resolver" in read("ruqqus", "routes", "anonymity.py")
    migration, schema = read("scripts", "migrations", "2027-01-05_anon_aliases.sql"), read("schema.sql")
    assert "PRIMARY KEY (scope_kind, scope_id, user_id)" in migration and "REFERENCES" not in migration
    assert "CREATE TABLE public.anon_aliases" in schema and "('2027-01-05_anon_aliases.sql');" in schema
    assert "CREATE UNIQUE INDEX anon_aliases_number_idx" in schema, "the unique index is what makes a number never repeat"


def test_the_log_and_the_json_draw_the_number():
    pages = read("ruqqus", "routes", "log_pages.py")
    assert "numbers = _numbers(entries)" in pages and pages.count("log_view.anonymous_actor(numbers.get(item))") == 2
    assert 'log_view.anonymous_actor(getattr(author, "alias", None))' in pages and "isinstance(author, anonymity.AnonymousAuthor)" in pages
    assert '"alias"' in read("ruqqus", "classes", "comment.py") and '"alias"' in read("ruqqus", "classes", "submission.py")
    assert "author_of(owner, v).shown_name" in read("ruqqus", "templates", "content_history.html"), "the history page's 'Edited by'"
