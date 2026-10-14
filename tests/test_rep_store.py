"""Rep's SQL (ruqqus/helpers/rep_store.py) on SQLite: the upvotes and downvotes an account's live posts and comments
have had from other accounts, never its own."""
from sqlalchemy import create_engine, text
from sqlalchemy.orm import Session

from ruqqus.helpers import rep_store

TABLES = """
CREATE TABLE submissions (id INTEGER PRIMARY KEY, author_id INTEGER, is_banned BOOLEAN DEFAULT 0, deleted_utc INTEGER DEFAULT 0);
CREATE TABLE comments (id INTEGER PRIMARY KEY, author_id INTEGER, is_banned BOOLEAN DEFAULT 0, deleted_utc INTEGER DEFAULT 0);
CREATE TABLE votes (id INTEGER PRIMARY KEY, user_id INTEGER, submission_id INTEGER, vote_type INTEGER);
CREATE TABLE commentvotes (id INTEGER PRIMARY KEY, user_id INTEGER, comment_id INTEGER, vote_type INTEGER);
"""
ME, A, B = 1, 2, 3


def make():
    db = Session(create_engine("sqlite://"))
    for statement in TABLES.split(";"):
        if statement.strip():
            db.execute(text(statement))
    rows = [
        "INSERT INTO submissions (id, author_id) VALUES (10, 1)",
        "INSERT INTO submissions (id, author_id) VALUES (11, 1)",
        "INSERT INTO submissions (id, author_id, is_banned) VALUES (12, 1, 1)",      # removed
        "INSERT INTO submissions (id, author_id, deleted_utc) VALUES (13, 1, 5)",     # deleted
        "INSERT INTO submissions (id, author_id) VALUES (14, 2)",                     # someone else's
        "INSERT INTO comments (id, author_id) VALUES (20, 1)",
        # my own upvote on my post never counts
        "INSERT INTO votes (user_id, submission_id, vote_type) VALUES (1, 10, 1)",
        "INSERT INTO votes (user_id, submission_id, vote_type) VALUES (2, 10, 1)",
        "INSERT INTO votes (user_id, submission_id, vote_type) VALUES (3, 10, -1)",
        "INSERT INTO votes (user_id, submission_id, vote_type) VALUES (2, 11, 1)",
        "INSERT INTO votes (user_id, submission_id, vote_type) VALUES (3, 11, 0)",    # a vote taken back
        "INSERT INTO votes (user_id, submission_id, vote_type) VALUES (2, 12, 1)",
        "INSERT INTO votes (user_id, submission_id, vote_type) VALUES (2, 13, 1)",
        "INSERT INTO votes (user_id, submission_id, vote_type) VALUES (3, 14, 1)",
        "INSERT INTO commentvotes (user_id, comment_id, vote_type) VALUES (2, 20, 1)",
        "INSERT INTO commentvotes (user_id, comment_id, vote_type) VALUES (3, 20, -1)",
        "INSERT INTO commentvotes (user_id, comment_id, vote_type) VALUES (1, 20, 1)",
    ]
    for row in rows:
        db.execute(text(row))
    return db


def test_counts_only_other_accounts_votes_on_live_items():
    assert rep_store.counts(make(), ME) == (3, 2)


def test_an_account_with_nothing_has_nothing():
    assert rep_store.counts(make(), 99) == (0, 0)
