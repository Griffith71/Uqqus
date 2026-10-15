"""Who may see something made for an audience (ruqqus/helpers/circle_guard.py): real SQL on in-memory SQLite (tables
in tests/circle_tables.py). The promise under test: a post, or a comment on a post, made for circles is shown to its
author, an admin and the people in ANY of its circles right now, and to nobody else, whatever route asked."""
from types import SimpleNamespace as NS

import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.orm import Session

from circle_tables import add_users, block, create, follow
from ruqqus.helpers import circle_guard as guard
from ruqqus.helpers import circle_store as store
from ruqqus.helpers import circles as c

NOW = 2_000_000_000
DAY = 86400
OWNER, FAMILY, WORKER, FAN, MUTUAL, ONE_WAY, STRANGER, EXPIRED, ENDED, OTHER_OWNER = range(1, 11)
GUILD = 9
MEMBER, GUILD_MOD, EXILED, PENDING_MOD, REMOVED = 20, 21, 22, 23, 24
PUBLIC_POST, FAMILY_POST, MIXED_POST, FRIENDS_POST, GUILD_POST, OTHERS_POST, ORPHAN_POST = 100, 101, 102, 103, 104, 105, 106


def viewer(uid, admin=0):
    return NS(id=uid, admin_level=admin)


@pytest.fixture
def db():
    engine = create_engine("sqlite://")
    with engine.begin() as conn:
        create(conn)
        conn.execute(text("CREATE TABLE submissions (id integer PRIMARY KEY, author_id integer, audience integer NOT NULL DEFAULT 0, board_id integer)"))
        add_users(conn, {uid: (f"u{uid}", 50) for uid in list(range(1, 11)) + [MEMBER, GUILD_MOD, EXILED, PENDING_MOD, REMOVED]})
        for uid, active in ((MEMBER, 1), (EXILED, 1), (REMOVED, 0)):
            conn.execute(text("INSERT INTO contributors (user_id, board_id, is_active) VALUES (:u, :b, :a)"), {"u": uid, "b": GUILD, "a": active})
        conn.execute(text("INSERT INTO mods (user_id, board_id, accepted) VALUES (:u, :b, 1)"), {"u": GUILD_MOD, "b": GUILD})
        conn.execute(text("INSERT INTO mods (user_id, board_id, accepted) VALUES (:u, :b, 0)"), {"u": PENDING_MOD, "b": GUILD})
        conn.execute(text("INSERT INTO bans (user_id, board_id, is_active) VALUES (:u, :b, 1)"), {"u": EXILED, "b": GUILD})
    session = Session(engine)
    ids = {"friends": store.ensure_friends(session, OWNER, NOW)}
    ids["family"] = store.create(session, OWNER, c.CUSTOM, "Family", 0, NOW)[0]
    ids["work"] = store.create(session, OWNER, c.CUSTOM, "Work", 0, NOW)[0]
    ids["fans"] = store.create(session, OWNER, c.PAID, "Fans", 10, NOW)[0]
    ids["theirs"] = store.create(session, OTHER_OWNER, c.CUSTOM, "Mates", 0, NOW)[0]
    store.add_member(session, OWNER, ids["family"], FAMILY, NOW)
    store.add_member(session, OWNER, ids["work"], WORKER, NOW)
    store.add_member(session, OTHER_OWNER, ids["theirs"], FAN, NOW)
    store.subscribe(session, ids["fans"], FAN, NOW)
    for uid, when, status in ((EXPIRED, NOW - 40 * DAY, "active"), (ENDED, NOW, "ended")):
        session.execute(text("INSERT INTO circle_members (circle_id, user_id, tier, status, renews_utc, price_coins, created_utc) "
                             "VALUES (:c, :u, 'subscriber', :s, :r, 10, :t)"), {"c": ids["fans"], "u": uid, "s": status, "r": when + c.PERIOD, "t": when})
    follow(session, OWNER, MUTUAL)
    follow(session, MUTUAL, OWNER)
    follow(session, ONE_WAY, OWNER)
    for pid, owner, audience, circles_for in (
            (PUBLIC_POST, OWNER, 0, ()), (FAMILY_POST, OWNER, 4, ("family",)), (MIXED_POST, OWNER, 4, ("family", "fans")),
            (FRIENDS_POST, OWNER, 4, ("friends",)), (GUILD_POST, OWNER, 3, ()), (OTHERS_POST, OTHER_OWNER, 4, ("theirs",)),
            (ORPHAN_POST, OWNER, 4, ())):
        session.execute(text("INSERT INTO submissions (id, author_id, audience, board_id) VALUES (:i, :a, :u, :b)"),
                        {"i": pid, "a": owner, "u": audience, "b": GUILD if audience == 3 else 2})
        store.set_post_circles(session, pid, [ids[name] for name in circles_for])
    session.ids = ids
    yield session
    session.close()


def post(db, pid):
    row = db.execute(text("SELECT id, author_id, audience, board_id FROM submissions WHERE id = :i"), {"i": pid}).fetchone()
    return NS(id=row.id, author_id=row.author_id, audience=row.audience, board_id=row.board_id)


def sees(db, who, pid, admin=0):
    return guard.may_see_post(db, post(db, pid), viewer(who, admin) if who else None, NOW)


# --- the single post ----------------------------------------------------------------------------------

@pytest.mark.parametrize("who, seen", [
    (STRANGER, {PUBLIC_POST}),
    (FAMILY, {PUBLIC_POST, FAMILY_POST, MIXED_POST}),
    (FAN, {PUBLIC_POST, MIXED_POST, OTHERS_POST}),
    (MUTUAL, {PUBLIC_POST, FRIENDS_POST}),
    (ONE_WAY, {PUBLIC_POST}),                                 # following without being followed back is not Friends
    (WORKER, {PUBLIC_POST}),                                  # in a circle none of these posts is for
    (EXPIRED, {PUBLIC_POST}), (ENDED, {PUBLIC_POST}),
    (OWNER, {PUBLIC_POST, FAMILY_POST, MIXED_POST, FRIENDS_POST, ORPHAN_POST}),
])
def test_what_each_account_sees(db, who, seen):
    shown = {pid for pid in (PUBLIC_POST, FAMILY_POST, MIXED_POST, FRIENDS_POST, OTHERS_POST, ORPHAN_POST) if sees(db, who, pid)}
    assert shown == seen


def test_a_visitor_sees_only_public(db):
    assert sees(db, None, PUBLIC_POST)
    for pid in (FAMILY_POST, MIXED_POST, FRIENDS_POST, GUILD_POST, ORPHAN_POST):
        assert not sees(db, None, pid)


def test_an_admin_sees_everything_a_moderator_of_the_site_would_need(db):
    for pid in (FAMILY_POST, MIXED_POST, FRIENDS_POST, GUILD_POST, ORPHAN_POST):
        assert sees(db, STRANGER, pid, admin=4)
        assert not sees(db, STRANGER, pid, admin=3)


def test_a_block_either_way_hides_every_circle_post(db):
    block(db, FAMILY, OWNER)
    assert not sees(db, FAMILY, FAMILY_POST) and not sees(db, FAMILY, MIXED_POST)
    block(db, OWNER, FAN)
    assert not sees(db, FAN, MIXED_POST)
    assert sees(db, FAN, OTHERS_POST)                         # nobody else's circles are touched


def test_a_post_with_no_circles_left_is_its_authors_alone(db):
    assert sees(db, OWNER, ORPHAN_POST)
    for who in (FAMILY, FAN, MUTUAL, WORKER):
        assert not sees(db, who, ORPHAN_POST)


def test_removing_someone_or_their_paid_days_running_out_ends_it_at_once(db):
    store.remove_member(db, OWNER, db.ids["family"], FAMILY)
    assert not sees(db, FAMILY, FAMILY_POST)
    assert not guard.may_see_post(db, post(db, MIXED_POST), viewer(FAN), NOW + c.PERIOD)


def test_unfollowing_takes_someone_out_of_friends(db):
    db.execute(text("DELETE FROM follows WHERE user_id = :a AND target_id = :b"), {"a": OWNER, "b": MUTUAL})
    assert not sees(db, MUTUAL, FRIENDS_POST)


@pytest.mark.parametrize("audience", [1, 2, 5, 9, -1])
def test_an_audience_nobody_knows_is_hidden_from_everyone_but_the_author_and_admins(db, audience):
    everything = list(db.ids.values())
    assert guard.may_see(db, audience, OWNER, viewer(FAMILY), NOW, circle_ids=everything) is False
    assert guard.may_see(db, audience, OWNER, viewer(OWNER), NOW) is True
    assert guard.may_see(db, audience, OWNER, viewer(STRANGER, admin=4), NOW) is True


# --- lists ----------------------------------------------------------------------------------------------

def test_a_list_loses_only_what_the_viewer_may_not_see_and_keeps_its_order(db):
    posts = [post(db, pid) for pid in (MIXED_POST, PUBLIC_POST, FAMILY_POST, OTHERS_POST, FRIENDS_POST)]
    assert [p.id for p in guard.visible_posts(db, posts, viewer(FAN), NOW)] == [MIXED_POST, PUBLIC_POST, OTHERS_POST]
    assert [p.id for p in guard.visible_posts(db, posts, viewer(FAMILY), NOW)] == [MIXED_POST, PUBLIC_POST, FAMILY_POST]
    assert [p.id for p in guard.visible_posts(db, posts, viewer(STRANGER), NOW)] == [PUBLIC_POST]
    assert [p.id for p in guard.visible_posts(db, posts, None, NOW)] == [PUBLIC_POST]


def test_a_list_with_no_audience_costs_no_query_at_all(db):
    class Explodes:
        def execute(self, *a, **k):
            raise AssertionError("a page of public posts must not cost a query")
    posts = [NS(id=1, author_id=OWNER, audience=0, board_id=2), NS(id=2, author_id=OWNER, audience=0, board_id=2)]
    assert guard.visible_posts(Explodes(), posts, viewer(STRANGER), NOW) is posts


# --- comments -------------------------------------------------------------------------------------------

def comment(cid, parent):
    return NS(id=cid, parent_submission=parent)


def test_comments_under_a_circle_post_follow_the_post(db):
    comments = [comment(1, PUBLIC_POST), comment(2, MIXED_POST), comment(3, FAMILY_POST), comment(4, None), comment(5, FRIENDS_POST)]
    assert [x.id for x in guard.visible_comments(db, comments, viewer(FAN), NOW)] == [1, 2, 4]
    assert [x.id for x in guard.visible_comments(db, comments, viewer(FAMILY), NOW)] == [1, 2, 3, 4]
    assert [x.id for x in guard.visible_comments(db, comments, viewer(MUTUAL), NOW)] == [1, 4, 5]
    assert [x.id for x in guard.visible_comments(db, comments, None, NOW)] == [1, 4]      # 4: a system message has no post


def test_comments_with_no_parent_post_cost_no_query(db):
    class Explodes:
        def execute(self, *a, **k):
            raise AssertionError("no parent post, no query")
    assert len(guard.visible_comments(Explodes(), [comment(1, None), comment(2, 0)], None, NOW)) == 2


def test_a_single_comment_and_whether_it_is_under_an_audience(db):
    assert guard.may_see_comment(db, comment(1, MIXED_POST), viewer(FAN), NOW)
    assert not guard.may_see_comment(db, comment(1, FAMILY_POST), viewer(FAN), NOW)
    assert guard.under_audience(db, comment(1, FAMILY_POST)) and guard.under_audience(db, comment(1, GUILD_POST))
    assert not guard.under_audience(db, comment(1, PUBLIC_POST)) and not guard.under_audience(db, comment(1, None))


def test_hidden_parents_carry_the_circles_of_posts_made_for_them(db):
    found = guard.hidden_parents(db, [PUBLIC_POST, FAMILY_POST, MIXED_POST, GUILD_POST, None, 0])
    assert found == {FAMILY_POST: (4, OWNER, 2, frozenset({db.ids["family"]})),
                     MIXED_POST: (4, OWNER, 2, frozenset({db.ids["family"], db.ids["fans"]})),
                     GUILD_POST: (3, OWNER, GUILD, frozenset())}
    assert guard.hidden_parents(db, []) == {}


# --- who is told ----------------------------------------------------------------------------------------

def test_only_people_who_may_see_it_are_eligible_to_be_told(db):
    family, fans, friends = db.ids["family"], db.ids["fans"], db.ids["friends"]
    assert guard.eligible_ids(db, OWNER, 4, NOW, circle_ids=[family, fans]) == {FAMILY, FAN}
    assert guard.eligible_ids(db, OWNER, 4, NOW, circle_ids=[friends]) == {MUTUAL}
    assert guard.eligible_ids(db, OWNER, 4, NOW + 400 * DAY, circle_ids=[fans]) == set()    # the subscriptions ran out
    assert guard.eligible_ids(db, OWNER, 4, NOW, circle_ids=[db.ids["theirs"]]) == set()    # never someone else's circle
    assert guard.eligible_ids(db, OWNER, 0, NOW) == set()


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


# --- the public mark of a story's picture (circles.STORY_OPEN) -------------------------------------------

def test_a_public_storys_picture_is_for_any_signed_in_account_and_never_a_visitor(db):
    for who in (OWNER, FAMILY, FAN, STRANGER, EXPIRED):
        assert guard.may_see(db, c.STORY_OPEN, OWNER, viewer(who), NOW) is True
    assert guard.may_see(db, c.STORY_OPEN, OWNER, None, NOW) is False


def test_a_block_either_way_takes_a_public_storys_picture_away(db):
    block(db, OWNER, STRANGER)                     # the owner blocked
    block(db, FAN, OWNER)                          # the viewer blocked
    assert guard.may_see(db, c.STORY_OPEN, OWNER, viewer(STRANGER), NOW) is False
    assert guard.may_see(db, c.STORY_OPEN, OWNER, viewer(FAN), NOW) is False
    assert guard.may_see(db, c.STORY_OPEN, OWNER, viewer(OWNER), NOW) is True
    assert guard.may_see(db, c.STORY_OPEN, OWNER, viewer(FAMILY), NOW) is True


def test_the_public_mark_is_not_one_a_post_can_carry_or_a_member_can_choose(db):
    assert c.STORY_OPEN not in (c.PUBLIC, c.CIRCLES, c.GUILD)
    assert c.parse_audience(str(c.STORY_OPEN), [])[0] is None
