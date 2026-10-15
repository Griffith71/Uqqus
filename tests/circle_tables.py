"""The circle tables as schema.sql has them, for the SQLite tests of helpers/circle_store.py, circle_guard.py,
circle_clause.py and story_store.py (tests/test_circles_assets.py checks the columns against schema.sql). Plus the
tables those helpers read: users, follows, userblocks, boards, contributors, mods, bans."""
from sqlalchemy import text

TABLES = (
    "CREATE TABLE users (id integer PRIMARY KEY, username text, coin_balance integer DEFAULT 0, "
    "is_deleted boolean DEFAULT 0, is_banned integer DEFAULT 0, unban_utc integer DEFAULT 0)",
    "CREATE TABLE userblocks (id integer PRIMARY KEY AUTOINCREMENT, user_id integer, target_id integer)",
    "CREATE TABLE follows (id integer PRIMARY KEY AUTOINCREMENT, user_id integer, target_id integer)",
    "CREATE TABLE boards (id integer PRIMARY KEY, name text, creator_id integer, is_circle boolean DEFAULT 0, is_banned boolean DEFAULT 0)",
    "CREATE TABLE circles (id integer PRIMARY KEY AUTOINCREMENT, user_id integer, board_id integer, "
    "price_coins integer NOT NULL DEFAULT 0, created_utc integer NOT NULL DEFAULT 0, name text NOT NULL DEFAULT '', "
    "kind text NOT NULL, CHECK (kind IN ('friends', 'custom', 'paid', 'guild')))",
    "CREATE UNIQUE INDEX circles_friends_key ON circles (user_id) WHERE kind = 'friends'",
    "CREATE UNIQUE INDEX circles_board_key ON circles (board_id) WHERE board_id IS NOT NULL",
    "CREATE TABLE circle_members (id integer PRIMARY KEY AUTOINCREMENT, circle_id integer NOT NULL, "
    "user_id integer NOT NULL, tier text NOT NULL, status text NOT NULL DEFAULT 'active', "
    "started_utc integer NOT NULL DEFAULT 0, renews_utc integer NOT NULL DEFAULT 0, "
    "cancelled boolean NOT NULL DEFAULT 0, price_coins integer NOT NULL DEFAULT 0, "
    "created_utc integer NOT NULL DEFAULT 0, UNIQUE (circle_id, user_id), CHECK (tier IN ('member', 'subscriber')))",
    "CREATE TABLE circle_payments (id integer PRIMARY KEY AUTOINCREMENT, circle_id integer, "
    "payer_id integer NOT NULL, owner_id integer NOT NULL, coins integer NOT NULL, kind text NOT NULL, "
    "created_utc integer NOT NULL DEFAULT 0)",
    "CREATE TABLE post_circles (post_id integer NOT NULL, circle_id integer NOT NULL, PRIMARY KEY (post_id, circle_id))",
    "CREATE TABLE story_circles (story_id integer NOT NULL, circle_id integer NOT NULL, PRIMARY KEY (story_id, circle_id))",
    "CREATE TABLE contributors (id integer PRIMARY KEY AUTOINCREMENT, user_id integer, board_id integer, "
    "created_utc integer DEFAULT 0, is_active boolean DEFAULT 1, approving_mod_id integer)",
    "CREATE TABLE mods (id integer PRIMARY KEY AUTOINCREMENT, user_id integer, board_id integer, accepted boolean DEFAULT 0, invite_rescinded boolean DEFAULT 0)",
    "CREATE TABLE bans (id integer PRIMARY KEY AUTOINCREMENT, user_id integer, board_id integer, is_active boolean DEFAULT 0)",
)


def create(conn, skip=()):
    for statement in TABLES:
        name = statement.split()[2] if statement.startswith("CREATE TABLE") else ""
        if name not in skip:
            conn.execute(text(statement))


def add_users(conn, users):
    """users: {id: (username, coins)}"""
    for uid, (name, coins) in users.items():
        conn.execute(text("INSERT INTO users (id, username, coin_balance) VALUES (:i, :n, :c)"), {"i": uid, "n": name, "c": coins})


def follow(db, a, b):
    db.execute(text("INSERT INTO follows (user_id, target_id) VALUES (:a, :b)"), {"a": a, "b": b})


def block(db, a, b):
    db.execute(text("INSERT INTO userblocks (user_id, target_id) VALUES (:a, :b)"), {"a": a, "b": b})
