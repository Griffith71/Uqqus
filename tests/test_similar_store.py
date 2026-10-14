"""Similar accounts' SQL (ruqqus/helpers/similar_store.py) on SQLite: co-followed accounts count only from enough of
the owner's followers, anonymous and non-public posts never tie an account to a guild, private and Circle guilds and
profiles are left out, and the owner is never suggested."""
from sqlalchemy import create_engine, text
from sqlalchemy.orm import Session

from ruqqus.helpers import similar_store

NOW = 1_800_000_000
OWNER = 1

TABLES = """
CREATE TABLE follows (id INTEGER PRIMARY KEY, user_id INTEGER, target_id INTEGER);
CREATE TABLE boards (id INTEGER PRIMARY KEY, name TEXT, is_private BOOLEAN, is_banned BOOLEAN, is_circle BOOLEAN DEFAULT 0);
CREATE TABLE submissions (id INTEGER PRIMARY KEY, author_id INTEGER, board_id INTEGER, created_utc INTEGER,
    is_banned BOOLEAN DEFAULT 0, deleted_utc INTEGER DEFAULT 0, is_anonymous BOOLEAN DEFAULT 0, post_public BOOLEAN DEFAULT 1);
"""


def make(follows=(), boards=(), posts=()):
    db = Session(create_engine("sqlite://"))
    for statement in TABLES.split(";"):
        if statement.strip():
            db.execute(text(statement))
    for user_id, target_id in follows:
        db.execute(text("INSERT INTO follows (user_id, target_id) VALUES (:a, :b)"), {"a": user_id, "b": target_id})
    for id, name, private, circle in boards:
        db.execute(text("INSERT INTO boards (id, name, is_private, is_banned, is_circle) VALUES (:i, :n, :p, 0, :c)"),
                   {"i": id, "n": name, "p": private, "c": circle})
    for author, board, extra in posts:
        row = {"a": author, "b": board, "t": NOW - 3600, "anon": False, "pub": True, **extra}
        db.execute(text("INSERT INTO submissions (author_id, board_id, created_utc, is_anonymous, post_public) "
                        "VALUES (:a, :b, :t, :anon, :pub)"), row)
    return db


def ids(rows):
    return [r[0] for r in rows]


def test_co_followed_needs_three_of_the_owners_followers():
    # followers 10, 11, 12 of the owner all follow 50; only 10 and 11 follow 60; only 10 follows 70
    follows = [(f, OWNER) for f in (10, 11, 12)] + [(10, 50), (11, 50), (12, 50), (10, 60), (11, 60), (10, 70)]
    rows = similar_store.candidates(make(follows=follows), OWNER, "owner", NOW)
    assert ids(rows) == [50]
    assert rows[0][2] == "Followed by 3 people who follow @owner"


def test_the_owner_is_never_suggested():
    follows = [(f, OWNER) for f in (10, 11, 12)] + [(f, OWNER) for f in (10, 11, 12)]
    assert similar_store.candidates(make(follows=follows), OWNER, "owner", NOW) == []


def test_same_guilds_counts_public_named_posts_only():
    boards = [(1, "cats", False, False), (2, "secret", True, False), (3, "circle", False, True), (4, "systemprofile", False, False)]
    posts = [
        (OWNER, 1, {}), (OWNER, 2, {}), (OWNER, 3, {}), (OWNER, 4, {}),
        (20, 1, {}),                       # posts beside the owner in a public guild: suggested
        (21, 2, {}), (22, 3, {}), (23, 4, {}),  # private guild, Circle guild, profiles: never
        (24, 1, {"anon": True}),           # an anonymous post never ties its author to a guild
        (25, 1, {"pub": False}),           # nor does a post that is not public
        (26, 1, {"t": NOW - 200 * 86400}),  # nor an old one
    ]
    rows = similar_store.candidates(make(boards=boards, posts=posts), OWNER, "owner", NOW)
    assert ids(rows) == [20] and rows[0][2] == "Also active in +cats"


def test_the_owners_anonymous_posts_never_point_at_a_guild():
    boards = [(1, "cats", False, False)]
    posts = [(OWNER, 1, {"anon": True}), (20, 1, {})]
    assert similar_store.candidates(make(boards=boards, posts=posts), OWNER, "owner", NOW) == []
