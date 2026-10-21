"""The Subscriber badge (ruqqus/helpers/subscriber_badges.py) on SQLite, and the paid-circle description rules.

A badge says what one row of the owner's Subscribers list says, so it follows both of that row's settings: the owner's
own for who may open the list, the subscriber's own for who may see the circles they pay for. Paid circles only, never
a custom circle; never across a block; never a name the viewer's word filter hides."""
from sqlalchemy import create_engine, text
from sqlalchemy.orm import Session

from ruqqus.helpers import circle_store, circles
from ruqqus.helpers import follow_lists as fl
from ruqqus.helpers import subscriber_badges as sb

NOW = 1_800_000_000
OWNER, ANN, BOB, CAT, DAN, VIEWER = 1, 2, 3, 4, 5, 9
LATER, EARLIER = NOW + 86400, NOW - 1

TABLES = """
CREATE TABLE users (id INTEGER PRIMARY KEY, is_deleted BOOLEAN DEFAULT 0, is_banned INTEGER DEFAULT 0,
    name_severity INTEGER DEFAULT 0, subscribers_visibility INTEGER DEFAULT 0, subscriptions_visibility INTEGER DEFAULT 0);
CREATE TABLE follows (id INTEGER PRIMARY KEY, user_id INTEGER, target_id INTEGER, created_utc INTEGER DEFAULT 0);
CREATE TABLE userblocks (id INTEGER PRIMARY KEY, user_id INTEGER, target_id INTEGER);
CREATE TABLE circles (id INTEGER PRIMARY KEY, user_id INTEGER, board_id INTEGER, price_coins INTEGER DEFAULT 0,
    created_utc INTEGER DEFAULT 0, name TEXT DEFAULT '', kind TEXT NOT NULL, description TEXT NOT NULL DEFAULT '',
    description_severity INTEGER NOT NULL DEFAULT 0);
CREATE TABLE circle_members (id INTEGER PRIMARY KEY, circle_id INTEGER, user_id INTEGER, tier TEXT, status TEXT DEFAULT 'active',
    started_utc INTEGER DEFAULT 0, renews_utc INTEGER DEFAULT 0, cancelled BOOLEAN DEFAULT 0);
"""


def make(users=None):
    db = Session(create_engine("sqlite://"))
    for statement in TABLES.split(";"):
        if statement.strip():
            db.execute(text(statement))
    for uid in (OWNER, ANN, BOB, CAT, DAN, VIEWER):
        db.execute(text("INSERT INTO users (id) VALUES (:i)"), {"i": uid})
    for uid, cols in (users or {}).items():
        for col, val in cols.items():
            db.execute(text(f"UPDATE users SET {col} = :v WHERE id = :i"), {"v": val, "i": uid})
    db.execute(text("INSERT INTO circles (id, user_id, price_coins, name, kind) VALUES "
                    "(10, 1, 5, 'Backstage', 'paid'), (11, 1, 0, 'Family', 'custom'), (12, 7, 5, 'Other', 'paid')"))
    rows = [(10, ANN, "subscriber", LATER, 0),       # pays now
            (10, BOB, "subscriber", EARLIER, 0),     # lapsed
            (10, DAN, "subscriber", LATER, 1),       # cancelled, but paid days are ahead: still in
            (11, CAT, "member", 0, 0),               # a custom circle: never a badge
            (12, CAT, "subscriber", LATER, 0)]       # someone else's paid circle
    for cid, uid, tier, renews, cancelled in rows:
        db.execute(text("INSERT INTO circle_members (circle_id, user_id, tier, renews_utc, cancelled) VALUES (:c, :u, :t, :r, :x)"),
                   {"c": cid, "u": uid, "t": tier, "r": renews, "x": cancelled})
    return db


def badges(db, viewer=VIEWER, owner_setting=fl.EVERYONE, follows_owner=False, cut=None, authors=(ANN, BOB, CAT, DAN, OWNER)):
    return sb.badges(db, OWNER, authors, viewer, cut, NOW, owner_setting, follows_owner)


def test_paid_subscribers_with_access_now_and_never_a_custom_circle():
    assert badges(make()) == {ANN: "Backstage", DAN: "Backstage"}


def test_the_subscribers_own_setting():
    db = make({ANN: {"subscriptions_visibility": fl.ONLY_ME}, DAN: {"subscriptions_visibility": fl.FOLLOWERS}})
    assert badges(db) == {}
    db.execute(text("INSERT INTO follows (user_id, target_id) VALUES (:v, :d)"), {"v": VIEWER, "d": DAN})
    assert badges(db) == {DAN: "Backstage"}
    assert badges(db, viewer=ANN) == {ANN: "Backstage"}      # your own badge is yours to see; Ann doesn't follow Dan


def test_the_owners_setting_for_their_subscribers_list():
    db = make()
    assert badges(db, owner_setting=fl.ONLY_ME) == {}
    assert badges(db, owner_setting=fl.FOLLOWERS) == {}
    assert badges(db, owner_setting=fl.FOLLOWERS, follows_owner=True) == {ANN: "Backstage", DAN: "Backstage"}
    assert badges(db, viewer=OWNER, owner_setting=fl.ONLY_ME) == {ANN: "Backstage", DAN: "Backstage"}


def test_blocks_and_the_word_filter():
    db = make({DAN: {"name_severity": 2}})
    db.execute(text("INSERT INTO userblocks (user_id, target_id) VALUES (:a, :b)"), {"a": ANN, "b": VIEWER})
    assert badges(db, cut=2) == {}
    assert badges(db, cut=None) == {DAN: "Backstage"}


def test_a_visitor_and_no_authors():
    db = make()
    assert badges(db, viewer=None) == {ANN: "Backstage", DAN: "Backstage"}
    assert badges(db, authors=()) == {} and badges(db, authors=(OWNER,)) == {}


def test_the_store_lists_paid_circles_only():
    db = make()
    assert circle_store.subscribed_authors(db, OWNER, [ANN, BOB, CAT, DAN], NOW) == {ANN: "Backstage", DAN: "Backstage"}
    assert circle_store.subscribed_authors(db, 7, [CAT], NOW) == {CAT: "Other"}
    assert circle_store.subscribed_authors(db, OWNER, [], NOW) == {}


def test_only_the_owner_sets_a_paid_circles_description():
    db = make()
    assert circle_store.set_description(db, OWNER, 10, "Early posts.", 0)
    assert circle_store.description_of(db, 10) == ("Early posts.", 0)
    assert not circle_store.set_description(db, ANN, 10, "mine now", 0)          # not theirs
    assert not circle_store.set_description(db, OWNER, 11, "family", 0)          # not a paid circle
    assert circle_store.description_of(db, 10) == ("Early posts.", 0)
    assert circle_store.description_of(db, 999) == ("", 0)


def test_a_description_is_plain_text_within_its_limit():
    assert circles.parse_description("  Early posts\r\n\r\n\r\n\r\nand  a  badge‮ ") == ("Early posts\n\nand a badge", None)
    assert circles.parse_description("") == ("", None) and circles.parse_description(None) == ("", None)
    text_, error = circles.parse_description("x" * (circles.DESCRIPTION_MAX + 1))
    assert text_ is None and str(circles.DESCRIPTION_MAX) in error


# --- where it is drawn -----------------------------------------------------------------------------------------------

def _read(*parts):
    from pathlib import Path
    return (Path(__file__).resolve().parent.parent / "ruqqus").joinpath(*parts).read_text(encoding="utf-8")


def test_the_badge_is_attached_under_a_post_and_on_a_profile_and_never_for_anonymity():
    tree = _read("classes", "submission.py").split("def tree_comments(", 1)[1].split("\n    def ", 1)[0]
    assert "subscriber_badges.attach(None if self.is_anonymous else self.author, comments, viewer)" in tree
    users = _read("routes", "users.py")
    assert users.count("subscriber_badges.attach(") == 3
    helper = _read("helpers", "subscriber_badges.py")
    assert "not x.is_anonymous" in helper.split("def attach(", 1)[1]


def test_the_badge_sits_after_the_name_link_and_is_text():
    mark = _read("templates", "partials", "subscriber_badge.html")
    assert "| safe" not in mark and "subscriber_badge(item)" in mark
    listing = _read("templates", "submission_listing.html")
    assert listing.count("{{ subscriber_mark(p) }}{% if p.gm_distinguish %}") == 2
    assert "{{ subscriber_mark(c) }}" in _read("templates", "comments.html")
    for name in ("main.scss", "main_dark.scss"):
        assert ".subscriber-badge {" in _read("assets", "style", name), name
