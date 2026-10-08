"""Who may see something made for an audience (ruqqus/helpers/circle_guard.py): real SQL on in-memory SQLite. The promise
under test: a post or a comment on a post made for a Circle is shown to the author, an admin and the right members of the
Circle, and to nobody else, whatever route asked."""
from types import SimpleNamespace as NS

import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.orm import Session

from ruqqus.helpers import circle_guard as guard
from ruqqus.helpers import circle_store as store
from ruqqus.helpers import circles as c

NOW = 2_000_000_000
DAY = 86400
OWNER, FRIEND, FAN, STRANGER, EXPIRED, ENDED, OTHER_OWNER = 1, 2, 3, 4, 5, 6, 7
GUILD = 9
MEMBER, GUILD_MOD, EXILED, PENDING_MOD, REMOVED = 20, 21, 22, 23, 24
PUBLIC_POST, SUBS_POST, FRIENDS_POST, GUILD_POST, OTHERS_POST = 100, 101, 102, 103, 104


def viewer(uid, admin=0):
    return NS(id=uid, admin_level=admin)


@pytest.fixture
def db():
    engine = create_engine("sqlite://")
    with engine.begin() as conn:
        conn.execute(text("CREATE TABLE users (id integer PRIMARY KEY, username text, coin_balance integer DEFAULT 0, "
                          "is_deleted boolean DEFAULT 0, is_banned integer DEFAULT 0, unban_utc integer DEFAULT 0)"))
        conn.execute(text("CREATE TABLE userblocks (id integer PRIMARY KEY AUTOINCREMENT, user_id integer, target_id integer)"))
        conn.execute(text("CREATE TABLE circles (id integer PRIMARY KEY AUTOINCREMENT, user_id integer, board_id integer, "
                          "price_coins integer NOT NULL DEFAULT 0, created_utc integer NOT NULL DEFAULT 0)"))
        conn.execute(text("CREATE TABLE circle_members (id integer PRIMARY KEY AUTOINCREMENT, circle_id integer NOT NULL, "
                          "user_id integer NOT NULL, tier text NOT NULL, status text NOT NULL DEFAULT 'active', "
                          "started_utc integer NOT NULL DEFAULT 0, renews_utc integer NOT NULL DEFAULT 0, "
                          "cancelled boolean NOT NULL DEFAULT 0, price_coins integer NOT NULL DEFAULT 0, "
                          "created_utc integer NOT NULL DEFAULT 0, UNIQUE (circle_id, user_id))"))
        conn.execute(text("CREATE TABLE submissions (id integer PRIMARY KEY, author_id integer, audience integer NOT NULL DEFAULT 0, board_id integer)"))
        conn.execute(text("CREATE TABLE contributors (id integer PRIMARY KEY AUTOINCREMENT, user_id integer, board_id integer, is_active boolean DEFAULT 1)"))
        conn.execute(text("CREATE TABLE mods (id integer PRIMARY KEY AUTOINCREMENT, user_id integer, board_id integer, accepted boolean DEFAULT 0, invite_rescinded boolean DEFAULT 0)"))
        conn.execute(text("CREATE TABLE bans (id integer PRIMARY KEY AUTOINCREMENT, user_id integer, board_id integer, is_active boolean DEFAULT 0)"))
        for uid in range(1, 8):
            conn.execute(text("INSERT INTO users (id, username) VALUES (:i, :n)"), {"i": uid, "n": f"u{uid}"})
        for pid, owner, audience in ((PUBLIC_POST, OWNER, 0), (SUBS_POST, OWNER, 1), (FRIENDS_POST, OWNER, 2), (GUILD_POST, OWNER, 3),
                                     (OTHERS_POST, OTHER_OWNER, 1)):
            conn.execute(text("INSERT INTO submissions (id, author_id, audience, board_id) VALUES (:i, :a, :u, :b)"),
                         {"i": pid, "a": owner, "u": audience, "b": GUILD if audience == 3 else 2})
        for uid, active in ((MEMBER, 1), (EXILED, 1), (REMOVED, 0)):
            conn.execute(text("INSERT INTO contributors (user_id, board_id, is_active) VALUES (:u, :b, :a)"), {"u": uid, "b": GUILD, "a": active})
        conn.execute(text("INSERT INTO mods (user_id, board_id, accepted) VALUES (:u, :b, 1)"), {"u": GUILD_MOD, "b": GUILD})
        conn.execute(text("INSERT INTO mods (user_id, board_id, accepted) VALUES (:u, :b, 0)"), {"u": PENDING_MOD, "b": GUILD})
        conn.execute(text("INSERT INTO bans (user_id, board_id, is_active) VALUES (:u, :b, 1)"), {"u": EXILED, "b": GUILD})
    session = Session(engine)
    circle = store.set_price(session, OWNER, 10, NOW)
    store.add_friend(session, OWNER, FRIEND, NOW)
    for uid, when in ((FAN, NOW), (EXPIRED, NOW - 40 * DAY), (ENDED, NOW)):
        session.execute(text("INSERT INTO circle_members (circle_id, user_id, tier, status, renews_utc, price_coins, created_utc) "
                             "VALUES (:c, :u, 'subscriber', :s, :r, 10, :t)"),
                        {"c": circle, "u": uid, "s": "ended" if uid == ENDED else "active", "r": when + c.PERIOD, "t": when})
    yield session
    session.close()


def post(pid, owner, audience, board=2):
    return NS(id=pid, author_id=owner, audience=audience, board_id=board)


# --- the single post ----------------------------------------------------------------------------------

@pytest.mark.parametrize("who, audience, seen", [
    (STRANGER, 0, True), (STRANGER, 1, False), (STRANGER, 2, False), (STRANGER, 3, False),
    (FAN, 0, True), (FAN, 1, True), (FAN, 2, False), (FAN, 3, False),
    (FRIEND, 0, True), (FRIEND, 1, True), (FRIEND, 2, True), (FRIEND, 3, False),
    (EXPIRED, 1, False), (EXPIRED, 2, False), (ENDED, 1, False),
    (OWNER, 1, True), (OWNER, 2, True), (OWNER, 3, False),
])
def test_what_each_kind_of_account_sees_of_the_owners_posts(db, who, audience, seen):
    assert guard.may_see(db, audience, OWNER, viewer(who), NOW, GUILD if audience == 3 else 2) is seen


def test_a_visitor_sees_only_public(db):
    assert guard.may_see(db, 0, OWNER, None, NOW) is True
    for audience in (1, 2, 3):
        assert guard.may_see(db, audience, OWNER, None, NOW) is False


def test_an_admin_sees_everything_a_moderator_of_the_site_would_need(db):
    for audience in (1, 2, 3):
        assert guard.may_see(db, audience, OWNER, viewer(STRANGER, admin=4), NOW)
        assert not guard.may_see(db, audience, OWNER, viewer(STRANGER, admin=3), NOW)


def test_a_membership_in_someone_elses_circle_gives_nothing_here(db):
    # FRIEND is a close friend of OWNER only; OTHER_OWNER's post for subscribers is not theirs to see
    assert guard.may_see(db, 1, OTHER_OWNER, viewer(FRIEND), NOW) is False
    assert guard.may_see(db, 1, OTHER_OWNER, viewer(FAN), NOW) is False


def test_an_audience_nobody_knows_is_hidden_from_everyone_but_the_author_and_admins(db):
    for audience in (9, -1, 4):
        assert guard.may_see(db, audience, OWNER, viewer(FRIEND), NOW) is False
        assert guard.may_see(db, audience, OWNER, viewer(OWNER), NOW) is True


def test_a_posts_own_audience_and_author_are_what_is_asked(db):
    assert guard.may_see_post(db, post(SUBS_POST, OWNER, 1), viewer(FAN), NOW)
    assert not guard.may_see_post(db, post(FRIENDS_POST, OWNER, 2), viewer(FAN), NOW)


# --- lists ----------------------------------------------------------------------------------------------

def test_a_list_loses_only_what_the_viewer_may_not_see_and_keeps_its_order(db):
    posts = [post(SUBS_POST, OWNER, 1), post(PUBLIC_POST, OWNER, 0), post(FRIENDS_POST, OWNER, 2), post(OTHERS_POST, OTHER_OWNER, 1)]
    assert [p.id for p in guard.visible_posts(db, posts, viewer(FAN), NOW)] == [SUBS_POST, PUBLIC_POST]
    assert [p.id for p in guard.visible_posts(db, posts, viewer(FRIEND), NOW)] == [SUBS_POST, PUBLIC_POST, FRIENDS_POST]
    assert [p.id for p in guard.visible_posts(db, posts, viewer(STRANGER), NOW)] == [PUBLIC_POST]
    assert [p.id for p in guard.visible_posts(db, posts, None, NOW)] == [PUBLIC_POST]
    assert [p.id for p in guard.visible_posts(db, posts, viewer(OWNER), NOW)] == [SUBS_POST, PUBLIC_POST, FRIENDS_POST]


def test_a_list_with_no_audience_costs_no_query_at_all(db):
    class Explodes:
        def execute(self, *a, **k):
            raise AssertionError("a page of public posts must not cost a query")
    posts = [post(1, OWNER, 0), post(2, OWNER, 0)]
    assert guard.visible_posts(Explodes(), posts, viewer(STRANGER), NOW) is posts


# --- comments -------------------------------------------------------------------------------------------

def comment(cid, parent):
    return NS(id=cid, parent_submission=parent)


def test_comments_under_a_circle_post_follow_the_post(db):
    comments = [comment(1, PUBLIC_POST), comment(2, SUBS_POST), comment(3, FRIENDS_POST), comment(4, None)]
    assert [x.id for x in guard.visible_comments(db, comments, viewer(FAN), NOW)] == [1, 2, 4]
    assert [x.id for x in guard.visible_comments(db, comments, viewer(FRIEND), NOW)] == [1, 2, 3, 4]
    assert [x.id for x in guard.visible_comments(db, comments, viewer(STRANGER), NOW)] == [1, 4]
    assert [x.id for x in guard.visible_comments(db, comments, None, NOW)] == [1, 4]      # 4: a system message has no post


def test_comments_with_no_parent_post_cost_no_query(db):
    class Explodes:
        def execute(self, *a, **k):
            raise AssertionError("no parent post, no query")
    assert len(guard.visible_comments(Explodes(), [comment(1, None), comment(2, 0)], None, NOW)) == 2


def test_a_single_comment_and_whether_it_is_under_an_audience(db):
    assert guard.may_see_comment(db, comment(1, SUBS_POST), viewer(FAN), NOW)
    assert not guard.may_see_comment(db, comment(1, FRIENDS_POST), viewer(FAN), NOW)
    assert guard.under_audience(db, comment(1, SUBS_POST)) and guard.under_audience(db, comment(1, GUILD_POST))
    assert not guard.under_audience(db, comment(1, PUBLIC_POST)) and not guard.under_audience(db, comment(1, None))


def test_hidden_parents_lists_only_posts_made_for_an_audience(db):
    assert guard.hidden_parents(db, [PUBLIC_POST, SUBS_POST, FRIENDS_POST, None, 0]) == {SUBS_POST: (1, OWNER, 2), FRIENDS_POST: (2, OWNER, 2)}
    assert guard.hidden_parents(db, []) == {}


# --- who is told ----------------------------------------------------------------------------------------

def test_only_people_who_may_see_it_are_eligible_to_be_told(db):
    assert guard.eligible_ids(db, OWNER, 1, NOW) == {FRIEND, FAN}
    assert guard.eligible_ids(db, OWNER, 2, NOW) == {FRIEND}
    assert guard.eligible_ids(db, OWNER, 1, NOW + 400 * DAY) == {FRIEND}          # the subscriptions have run out
    assert guard.eligible_ids(db, OTHER_OWNER, 1, NOW) == set()


# --- a Circle guild: its members decide ---------------------------------------------------------------------

@pytest.mark.parametrize("who, seen", [
    (MEMBER, True), (GUILD_MOD, True), (EXILED, False), (PENDING_MOD, False), (REMOVED, False), (STRANGER, False),
    (OWNER, False),      # the author of a guild post who is not a member (any more) does not read it
])
def test_a_circle_guilds_posts_are_for_its_members_only(db, who, seen):
    assert guard.may_see(db, 3, OWNER, viewer(who), NOW, GUILD) is seen


def test_an_admin_sees_a_circle_guilds_posts_and_a_visitor_never(db):
    assert guard.may_see(db, 3, OWNER, viewer(STRANGER, admin=4), NOW, GUILD)
    assert not guard.may_see(db, 3, OWNER, None, NOW, GUILD)


def test_a_guild_post_with_no_guild_is_seen_by_nobody_but_admins(db):
    assert not guard.may_see(db, 3, OWNER, viewer(MEMBER), NOW, None)
    assert not guard.may_see(db, 3, OWNER, viewer(OWNER), NOW, None)


def test_the_guilds_members_are_the_ones_told_about_a_post_in_it(db):
    assert guard.eligible_ids(db, OWNER, 3, NOW, GUILD) == {MEMBER, GUILD_MOD}
    assert guard.guild_member_ids(db, 777) == set()


def test_comments_under_a_guild_post_follow_membership(db):
    comments = [comment(1, GUILD_POST), comment(2, PUBLIC_POST)]
    assert [x.id for x in guard.visible_comments(db, comments, viewer(MEMBER), NOW)] == [1, 2]
    assert [x.id for x in guard.visible_comments(db, comments, viewer(STRANGER), NOW)] == [2]
    assert [x.id for x in guard.visible_comments(db, comments, viewer(EXILED), NOW)] == [2]


def test_a_lookup_is_remembered_for_the_request_only():
    # outside a request the cache is a throwaway: two calls never share an answer
    assert guard._remember() is not guard._remember()


def test_the_viewer_falls_back_to_nobody_outside_a_request():
    assert guard.current(None) is None
    someone = viewer(5)
    assert guard.current(someone) is someone
