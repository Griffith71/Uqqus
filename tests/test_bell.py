"""The bells (ruqqus/helpers/bell.py): who is rung, on SQLite, and that every hook rings after its own commit, never
for anything anonymous, never past a circle, and never with a title.

An account's bell: followers who turned it on, not blocked either way, not muting the account, not deleted or
suspended; a private or nofollow account rings nobody. A guild's bell: members who turned it on, not muting the guild,
not exiled; a private or Circle guild rings only its approved members and guildmasters. One thing rings once."""
from pathlib import Path

import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.orm import Session

from ruqqus.helpers import bell

ROOT = Path(__file__).resolve().parent.parent
ACTOR, ANN, BOB, CAT, DAN, EVE, FAY = 1, 2, 3, 4, 5, 6, 7
GUILD = 50

TABLES = """
CREATE TABLE users (id INTEGER PRIMARY KEY, is_deleted BOOLEAN DEFAULT 0, is_banned INTEGER DEFAULT 0,
    is_private BOOLEAN DEFAULT 0, is_nofollow BOOLEAN DEFAULT 0);
CREATE TABLE follows (id INTEGER PRIMARY KEY, user_id INTEGER, target_id INTEGER, get_notifs BOOLEAN DEFAULT 0);
CREATE TABLE subscriptions (id INTEGER PRIMARY KEY, user_id INTEGER, board_id INTEGER, is_active BOOLEAN,
    get_notifs BOOLEAN DEFAULT 0);
CREATE TABLE userblocks (id INTEGER PRIMARY KEY, user_id INTEGER, target_id INTEGER);
CREATE TABLE usermutes (id INTEGER PRIMARY KEY, user_id INTEGER, target_id INTEGER);
CREATE TABLE boardblocks (id INTEGER PRIMARY KEY, user_id INTEGER, board_id INTEGER);
CREATE TABLE bans (id INTEGER PRIMARY KEY, user_id INTEGER, board_id INTEGER, is_active BOOLEAN);
CREATE TABLE contributors (id INTEGER PRIMARY KEY, user_id INTEGER, board_id INTEGER, is_active BOOLEAN);
CREATE TABLE mods (id INTEGER PRIMARY KEY, user_id INTEGER, board_id INTEGER, accepted BOOLEAN, invite_rescinded BOOLEAN);
CREATE TABLE bell_rings (id INTEGER PRIMARY KEY, actor_id INTEGER NOT NULL, kind TEXT NOT NULL, target_id INTEGER NOT NULL,
    created_utc INTEGER NOT NULL, CONSTRAINT bell_rings_once UNIQUE (actor_id, kind, target_id));
"""


@pytest.fixture
def db():
    session = Session(create_engine("sqlite://"))
    for statement in TABLES.split(";"):
        if statement.strip():
            session.execute(text(statement))
    for uid in (ACTOR, ANN, BOB, CAT, DAN, EVE, FAY):
        session.execute(text("INSERT INTO users (id) VALUES (:i)"), {"i": uid})
    yield session
    session.close()


def run(db, sql, **params):
    db.execute(text(sql), params)


def test_an_accounts_bell_rings_its_followers_who_turned_it_on(db):
    for uid, on in ((ANN, 1), (BOB, 0), (CAT, 1), (DAN, 1), (EVE, 1), (FAY, 1)):
        run(db, "INSERT INTO follows (user_id, target_id, get_notifs) VALUES (:u, :a, :n)", u=uid, a=ACTOR, n=on)
    run(db, "INSERT INTO userblocks (user_id, target_id) VALUES (:a, :c)", a=ACTOR, c=CAT)      # the actor blocked Cat
    run(db, "INSERT INTO userblocks (user_id, target_id) VALUES (:d, :a)", d=DAN, a=ACTOR)      # Dan blocked the actor
    run(db, "INSERT INTO usermutes (user_id, target_id) VALUES (:e, :a)", e=EVE, a=ACTOR)       # Eve muted the actor
    run(db, "UPDATE users SET is_banned = 1 WHERE id = :f", f=FAY)                             # Fay is suspended
    assert bell.account_ring_ids(db, ACTOR) == [ANN]


def test_a_private_or_nofollow_account_rings_nobody_and_the_cap_holds(db):
    for uid in (ANN, BOB, CAT):
        run(db, "INSERT INTO follows (user_id, target_id, get_notifs) VALUES (:u, :a, 1)", u=uid, a=ACTOR)
    assert bell.account_ring_ids(db, ACTOR, cap=2) == [ANN, BOB]
    run(db, "UPDATE users SET is_private = 1 WHERE id = :a", a=ACTOR)
    assert bell.account_ring_ids(db, ACTOR) == []
    run(db, "UPDATE users SET is_private = 0, is_nofollow = 1 WHERE id = :a", a=ACTOR)
    assert bell.account_ring_ids(db, ACTOR) == []


def test_a_guilds_bell_rings_members_who_turned_it_on(db):
    for uid, on, active in ((ANN, 1, 1), (BOB, 0, 1), (CAT, 1, 0), (DAN, 1, 1), (EVE, 1, 1), (FAY, 1, 1)):
        run(db, "INSERT INTO subscriptions (user_id, board_id, is_active, get_notifs) VALUES (:u, :b, :a, :n)",
            u=uid, b=GUILD, a=active, n=on)
    run(db, "INSERT INTO boardblocks (user_id, board_id) VALUES (:d, :b)", d=DAN, b=GUILD)     # muted the guild
    run(db, "INSERT INTO bans (user_id, board_id, is_active) VALUES (:e, :b, 1)", e=EVE, b=GUILD)  # exiled
    run(db, "INSERT INTO userblocks (user_id, target_id) VALUES (:f, :a)", f=FAY, a=ACTOR)     # blocked the guildmaster
    assert bell.guild_ring_ids(db, GUILD, ACTOR, members_only=False) == [ANN]


def test_a_private_guild_rings_only_its_approved_members_and_guildmasters(db):
    for uid in (ANN, BOB, CAT):
        run(db, "INSERT INTO subscriptions (user_id, board_id, is_active, get_notifs) VALUES (:u, :b, 1, 1)", u=uid, b=GUILD)
    run(db, "INSERT INTO contributors (user_id, board_id, is_active) VALUES (:u, :b, 1)", u=ANN, b=GUILD)
    run(db, "INSERT INTO mods (user_id, board_id, accepted, invite_rescinded) VALUES (:u, :b, 1, 0)", u=BOB, b=GUILD)
    assert bell.guild_ring_ids(db, GUILD, ACTOR, members_only=True) == [ANN, BOB]
    assert bell.guild_ring_ids(db, GUILD, ACTOR, members_only=False) == [ANN, BOB, CAT]


def test_one_thing_rings_once(db):
    assert bell.claim(db, ACTOR, bell.REPOST, 10, now=1)
    assert not bell.claim(db, ACTOR, bell.REPOST, 10, now=2)
    assert bell.claim(db, ACTOR, bell.FORWARD, 10, now=3)            # another kind
    assert bell.claim(db, GUILD, bell.PIN, 10, now=4)                 # another actor
    with pytest.raises(ValueError):
        bell.claim(db, ACTOR, "everything", 10)


def test_a_notice_names_who_did_it_and_links_to_it_never_a_title():
    assert bell.account_notice("ann", bell.FORWARD, "/+pets/post/1", "pets") == "@ann forwarded [a post](/+pets/post/1) to +pets."
    assert bell.account_notice("ann", bell.REPOST, "/post/1") == "@ann reposted [a post](/post/1)."
    assert bell.account_notice("ann", bell.SPACE_LIVE, "/space/3") == "@ann is live in [a Space](/space/3)."
    assert bell.account_notice("ann", bell.STORY, "/@ann") == "@ann shared [a story](/@ann)."
    assert bell.guild_notice("pets", bell.HERALD, "/post/1") == "+pets: the guildmasters made [an announcement](/post/1)."
    assert bell.guild_notice("pets", bell.PIN, "/post/1") == "+pets: the guildmasters pinned [a post](/post/1)."
    assert bell.guild_notice("pets", bell.EVENT, "/+pets/events/2") == "+pets has [a new event](/+pets/events/2)."


# --- where it rings --------------------------------------------------------------------------------------------------

def read(*parts):
    return ROOT.joinpath(*parts).read_text(encoding="utf-8")


def body_of(source, name):
    return source.split(f"def {name}(", 1)[1].split("\n@app", 1)[0].split("\ndef ", 1)[0]


HOOKS = [
    (("ruqqus", "routes", "posts.py"), "forward_post", "bell_rules.FORWARD"),
    (("ruqqus", "routes", "posts.py"), "repost_post", "bell_rules.REPOST"),
    (("ruqqus", "routes", "comments.py"), "forward_comment", "bell_rules.COMMENT_FORWARD"),
    (("ruqqus", "routes", "comments.py"), "repost_comment", "bell_rules.COMMENT_REPOST"),
    (("ruqqus", "routes", "spaces.py"), "_begin", "bell_rules.SPACE_LIVE"),      # space_start and space_create
    (("ruqqus", "routes", "stories.py"), "story_create", "bell_rules.STORY"),
    (("ruqqus", "routes", "boards.py"), "mod_distinguish_post", "bell_rules.HERALD"),
    (("ruqqus", "routes", "boards.py"), "mod_toggle_post_pin", "bell_rules.PIN"),
    (("ruqqus", "routes", "guild_events.py"), "guild_event_create", "bell_rules.EVENT"),
]


def test_every_hook_rings_after_its_own_commit():
    for path, name, kind in HOOKS:
        body = body_of(read(*path), name)
        assert kind in body, name
        assert body.rindex("g.db.commit()") < body.index(kind), name


def test_nothing_anonymous_rings_and_circles_stay_in_their_circle():
    posts = read("ruqqus", "routes", "posts.py")
    assert "not (primary.is_anonymous and primary.author_id == v.id)" in body_of(posts, "forward_post")
    assert "not (target.is_private or target.is_circle)" in body_of(posts, "forward_post")
    assert "primary.in_private_guild_for(None)" in body_of(posts, "repost_post")
    comments = read("ruqqus", "routes", "comments.py")
    assert "not (comment.is_anonymous and comment.author_id == v.id)" in body_of(comments, "forward_comment")
    story = body_of(read("ruqqus", "routes", "stories.py"), "story_create")
    assert "only=circle_store.eligible_ids(g.db, v.id, circle_ids) if audience == circles.CIRCLES else None" in story
    space = body_of(read("ruqqus", "routes", "spaces.py"), "_begin")
    assert "_followers_who_may_read(space.post, ring)" in space


def test_the_guild_bell_no_longer_rings_for_every_post():
    submit = body_of(read("ruqqus", "routes", "posts.py"), "submit_post")
    assert "board_uids" not in submit and "Subscription.get_notifs" not in submit
    assert ".limit(bell_rules.MAX_RINGS)" in submit


def test_one_notice_is_one_message_for_many_people_and_old_rings_are_pruned():
    alerts = read("ruqqus", "helpers", "alerts.py")
    many = alerts.split("def send_to_many(", 1)[1]
    assert many.count("Comment(") == 1 and "Notification(comment_id=new_comment.id, user_id=uid)" in many
    assert "DELETE FROM bell_rings WHERE created_utc < :bells" in read("ruqqus", "helpers", "retention.py")
    migration = read("scripts", "migrations", "2026-11-12_defaults_descriptions_bells.sql")
    assert "CONSTRAINT bell_rings_once UNIQUE (actor_id, kind, target_id)" in migration
    assert "CONSTRAINT bell_rings_once UNIQUE (actor_id, kind, target_id)" in read("schema.sql")


def test_the_bells_say_what_they_do():
    header = read("ruqqus", "templates", "partials", "profile_header.html")
    assert "Notify me about everything @{{ u.username }} does" in header
    board = read("ruqqus", "templates", "board.html")
    assert "Be told when the guildmasters make an announcement" in board
    assert "Be notified when this guild has new content" not in board


def test_a_notices_link_carries_no_title():
    # a post's or a comment's permalink ends in its title's words: a bell links by id only (/post/<id>, /comment/<id>)
    for path, name, kind in HOOKS:
        body = body_of(read(*path), name)
        ring = body[body.index("bell_rules.ring_"):].split("\n\n", 1)[0]
        assert ".permalink" not in ring.replace("v.permalink", ""), name
