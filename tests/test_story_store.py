"""Stories, the database side (ruqqus/helpers/story_store.py): real SQL on in-memory SQLite. The promise under test: a
story is watched only by the people it was made for (Public, Subscribers, Close Friends), never across a block, never past
the viewer's word filter, only while it lives (or a Highlight keeps it), and nothing here ever names a viewer."""
import time
from types import SimpleNamespace as NS

import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.orm import Session

from ruqqus.helpers import circle_store as circles_db
from ruqqus.helpers import circles as c
from ruqqus.helpers import stories as rules
from ruqqus.helpers import story_store as store
from ruqqus.helpers.media import attach
from ruqqus.helpers.media import rules as media_rules

NOW = 2_000_000_000
HOUR = 3600
DAY = 86400
OWNER, FRIEND, FAN, STRANGER, BLOCKED, OTHER = 1, 2, 3, 4, 5, 6


def viewer(uid, level=1, admin=0):
    return NS(id=uid, admin_level=admin, filter_level=level)


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
        conn.execute(text("CREATE TABLE stories (id integer PRIMARY KEY AUTOINCREMENT, user_id integer NOT NULL, kind text NOT NULL, "
                          "body text NOT NULL DEFAULT '', background text NOT NULL DEFAULT '', video_ref text NOT NULL DEFAULT '', "
                          "audience integer NOT NULL DEFAULT 0, word_severity integer NOT NULL DEFAULT 0, created_utc integer NOT NULL, "
                          "expires_utc integer NOT NULL, deleted_utc integer NOT NULL DEFAULT 0)"))
        conn.execute(text("CREATE TABLE story_views (id integer PRIMARY KEY AUTOINCREMENT, story_id integer NOT NULL, viewer_id integer NOT NULL, "
                          "created_utc integer NOT NULL DEFAULT 0, UNIQUE (story_id, viewer_id))"))
        conn.execute(text("CREATE TABLE highlights (id integer PRIMARY KEY AUTOINCREMENT, user_id integer NOT NULL, title text NOT NULL, "
                          "position integer NOT NULL DEFAULT 0, created_utc integer NOT NULL DEFAULT 0)"))
        conn.execute(text("CREATE TABLE highlight_stories (highlight_id integer NOT NULL, story_id integer NOT NULL, "
                          "position integer NOT NULL DEFAULT 0, PRIMARY KEY (highlight_id, story_id))"))
        conn.execute(text("CREATE TABLE story_reports (id integer PRIMARY KEY AUTOINCREMENT, story_id integer NOT NULL, reporter_id integer NOT NULL, "
                          "reason text NOT NULL DEFAULT '', created_utc integer NOT NULL DEFAULT 0, resolved_utc integer NOT NULL DEFAULT 0, "
                          "UNIQUE (story_id, reporter_id))"))
        conn.execute(text("CREATE TABLE media_assets (id integer PRIMARY KEY AUTOINCREMENT, user_id integer NOT NULL, kind text NOT NULL DEFAULT 'image', "
                          "status text NOT NULL DEFAULT 'ready', token text NOT NULL DEFAULT 'tok', ext text NOT NULL DEFAULT 'png', "
                          "submission_id integer, comment_id integer, story_id integer)"))
        for uid in range(1, 7):
            conn.execute(text("INSERT INTO users (id, username) VALUES (:i, :n)"), {"i": uid, "n": f"u{uid}"})
        conn.execute(text("INSERT INTO userblocks (user_id, target_id) VALUES (:o, :b)"), {"o": OWNER, "b": BLOCKED})
    session = Session(engine)
    circle = circles_db.set_price(session, OWNER, 10, NOW)
    circles_db.add_friend(session, OWNER, FRIEND, NOW)
    session.execute(text("INSERT INTO circle_members (circle_id, user_id, tier, status, renews_utc, price_coins, created_utc) "
                         "VALUES (:c, :u, 'subscriber', 'active', :r, 10, :t)"), {"c": circle, "u": FAN, "r": NOW + c.PERIOD, "t": NOW})
    yield session
    session.close()


def make(db, audience=0, kind="text", body="hello", severity=0, when=NOW, user=OWNER, video=""):
    return store.create(db, user, kind, body, "ocean", video, audience, severity, when)


def ids(rows):
    return [r.id for r in rows]


# --- who may watch ------------------------------------------------------------------------------------------

@pytest.mark.parametrize("who, audience, seen", [
    (STRANGER, 0, True), (STRANGER, 1, False), (STRANGER, 2, False),
    (FAN, 0, True), (FAN, 1, True), (FAN, 2, False),
    (FRIEND, 0, True), (FRIEND, 1, True), (FRIEND, 2, True),
    (OWNER, 0, True), (OWNER, 1, True), (OWNER, 2, True),
    (BLOCKED, 0, False), (BLOCKED, 1, False), (BLOCKED, 2, False),
])
def test_each_kind_of_account_watches_only_what_was_made_for_it(db, who, audience, seen):
    story = make(db, audience)
    assert (story in ids(store.watchable(db, OWNER, viewer(who), NOW))) is seen


def test_a_visitor_at_this_level_gets_public_only(db):
    public, subs, close = make(db, 0), make(db, 1), make(db, 2)
    assert ids(store.watchable(db, OWNER, None, NOW)) == [public]


def test_a_block_either_way_hides_every_story_even_public_ones(db):
    public = make(db, 0)
    db.execute(text("INSERT INTO userblocks (user_id, target_id) VALUES (:s, :o)"), {"s": STRANGER, "o": OWNER})     # the viewer blocked the owner
    assert store.watchable(db, OWNER, viewer(STRANGER), NOW) == []
    assert public in ids(store.watchable(db, OWNER, viewer(OTHER), NOW))


def test_admins_see_circle_stories_only_from_level_four(db):
    subs, close = make(db, 1), make(db, 2)
    assert ids(store.watchable(db, OWNER, viewer(STRANGER, admin=4), NOW)) == [subs, close]
    assert store.watchable(db, OWNER, viewer(STRANGER, admin=3), NOW) == []


def test_a_membership_in_someone_elses_circle_gives_nothing(db):
    other = make(db, 1, user=OTHER)
    assert store.watchable(db, OTHER, viewer(FAN), NOW) == []        # FAN pays OWNER, not OTHER
    assert other in ids(store.watchable(db, OTHER, viewer(OTHER), NOW))


def test_a_lapsed_subscription_stops_the_stories_at_once(db):
    subs = make(db, 1)
    assert subs in ids(store.watchable(db, OWNER, viewer(FAN), NOW))
    db.execute(text("UPDATE circle_members SET renews_utc = :r WHERE user_id = :u"), {"r": NOW - 1, "u": FAN})
    assert store.watchable(db, OWNER, viewer(FAN), NOW) == []


# --- time and deletion --------------------------------------------------------------------------------------

def test_a_story_is_watchable_for_a_day_and_then_only_in_the_archive(db):
    story = make(db, 0, when=NOW - 23 * HOUR)
    assert story in ids(store.watchable(db, OWNER, viewer(STRANGER), NOW))
    assert store.watchable(db, OWNER, viewer(STRANGER), NOW + 2 * HOUR) == []
    assert story in ids(store.archive(db, OWNER))


def test_stories_come_oldest_first(db):
    later, earlier = make(db, when=NOW - HOUR), make(db, when=NOW - 5 * HOUR)
    assert ids(store.watchable(db, OWNER, viewer(STRANGER), NOW)) == [earlier, later]
    assert ids(store.archive(db, OWNER)) == [later, earlier]         # the archive is newest first


def test_only_the_owner_deletes_a_story_and_only_once(db):
    story = make(db)
    assert store.delete(db, story, STRANGER) is False
    assert store.delete(db, story, OWNER) is True
    assert store.delete(db, story, OWNER) is False
    assert store.watchable(db, OWNER, viewer(STRANGER), NOW) == []
    assert store.watchable(db, OWNER, viewer(OWNER), NOW) == []
    assert store.archive(db, OWNER) == []


def test_an_admin_takes_a_story_down_for_everyone(db):
    story = make(db)
    assert store.remove(db, story, NOW) is True
    assert store.remove(db, story, NOW) is False
    assert store.watchable(db, OWNER, viewer(OWNER), NOW) == []


def test_the_archive_is_the_members_own_and_limited(db):
    mine = [make(db, when=NOW - i * 10) for i in range(5)]
    theirs = make(db, user=OTHER)
    assert ids(store.archive(db, OWNER)) == mine
    assert theirs not in ids(store.archive(db, OWNER))
    assert len(store.archive(db, OWNER, limit=2)) == 2


def test_stories_made_today_are_counted_for_the_daily_limit(db):
    make(db, when=NOW - HOUR)
    make(db, when=NOW - 25 * HOUR)
    make(db, user=OTHER, when=NOW - HOUR)
    assert store.count_today(db, OWNER, NOW) == 1


# --- the word filter ----------------------------------------------------------------------------------------

@pytest.mark.parametrize("level, severity, seen", [
    (0, 2, True), (1, 0, True), (1, 1, True), (1, 2, False), (2, 0, True), (2, 1, False), (2, 2, False),
])
def test_a_story_goes_through_the_viewers_word_filter(db, level, severity, seen):
    story = make(db, 0, severity=severity)
    assert (story in ids(store.watchable(db, OWNER, viewer(STRANGER, level=level), NOW))) is seen


def test_the_owner_always_sees_their_own_words(db):
    story = make(db, 0, severity=2)
    assert story in ids(store.watchable(db, OWNER, viewer(OWNER, level=2), NOW))


# --- the ring and who has seen ------------------------------------------------------------------------------

def test_the_ring_is_unseen_then_seen_then_unseen_again_with_a_new_story(db):
    first = make(db, 0, when=NOW - HOUR)
    assert store.rings(db, [OWNER], viewer(STRANGER), NOW) == {OWNER: "unseen"}
    store.mark_seen(db, first, STRANGER, NOW)
    assert store.rings(db, [OWNER], viewer(STRANGER), NOW) == {OWNER: "seen"}
    make(db, 0, when=NOW - 60)
    assert store.rings(db, [OWNER], viewer(STRANGER), NOW) == {OWNER: "unseen"}


def test_the_ring_is_per_viewer(db):
    story = make(db, 0)
    store.mark_seen(db, story, STRANGER, NOW)
    assert store.rings(db, [OWNER], viewer(STRANGER), NOW) == {OWNER: "seen"}
    assert store.rings(db, [OWNER], viewer(OTHER), NOW) == {OWNER: "unseen"}


def test_a_ring_only_counts_stories_the_viewer_may_watch(db):
    make(db, 2)
    assert store.rings(db, [OWNER], viewer(STRANGER), NOW) == {}
    assert store.rings(db, [OWNER], viewer(FRIEND), NOW) == {OWNER: "unseen"}
    assert store.rings(db, [OWNER], viewer(BLOCKED), NOW) == {}


def test_rings_for_several_accounts_and_for_a_visitor(db):
    make(db, 0)
    make(db, 0, user=OTHER)
    assert store.rings(db, [OWNER, OTHER, STRANGER], viewer(FAN), NOW) == {OWNER: "unseen", OTHER: "unseen"}
    assert store.rings(db, [OWNER], None, NOW) == {}
    assert store.rings(db, [], viewer(FAN), NOW) == {}


def test_an_expired_or_deleted_story_leaves_no_ring(db):
    old = make(db, 0, when=NOW - 2 * DAY)
    gone = make(db, 0)
    store.delete(db, gone, OWNER)
    assert old and store.rings(db, [OWNER], viewer(STRANGER), NOW) == {}


def test_seeing_twice_is_one_view_and_only_a_number_comes_out(db):
    story = make(db)
    for who in (STRANGER, STRANGER, OTHER):
        store.mark_seen(db, story, who, NOW)
    assert store.view_counts(db, [story]) == {story: 2}
    assert store.view_counts(db, []) == {}
    assert store.seen_ids(db, STRANGER, [story]) == {story}
    assert store.seen_ids(db, FAN, [story]) == set()
    assert store.seen_ids(db, None, [story]) == set()
    assert all(isinstance(n, int) for n in store.view_counts(db, [story]).values())


# --- the picture --------------------------------------------------------------------------------------------

def asset(db, user=OWNER, **fields):
    values = {"user_id": user, "kind": "image", "status": "ready", "submission_id": None, "comment_id": None, "story_id": None, **fields}
    return db.execute(text("INSERT INTO media_assets (user_id, kind, status, submission_id, comment_id, story_id, token, ext) "
                           "VALUES (:user_id, :kind, :status, :submission_id, :comment_id, :story_id, 'abc123', 'png') RETURNING id"), values).scalar()


def test_a_ready_unused_picture_of_your_own_goes_on_the_story_once(db):
    story, other = make(db, kind="image"), make(db, kind="image")
    pic = asset(db)
    assert store.attach_picture(db, story, pic, OWNER) is True
    assert store.picture_of(db, story) == media_rules.media_path(pic, "abc123", "png")
    assert store.attach_picture(db, other, pic, OWNER) is False        # already used
    assert store.picture_of(db, other) is None


@pytest.mark.parametrize("fields, user", [
    ({}, STRANGER),                                  # someone else's
    ({"status": "pending"}, OWNER), ({"status": "failed"}, OWNER),
    ({"kind": "audio"}, OWNER), ({"kind": "video"}, OWNER),
    ({"submission_id": 7}, OWNER), ({"comment_id": 7}, OWNER),
])
def test_a_picture_that_is_not_yours_ready_unused_and_a_picture_is_refused(db, fields, user):
    story = make(db, kind="image")
    pic = asset(db, user=OWNER if user == STRANGER else user, **fields)
    assert store.attach_picture(db, story, pic, user) is False


def test_a_picture_that_is_not_ready_has_no_address(db):
    story = make(db, kind="image")
    asset(db, status="pending", story_id=story)
    assert store.picture_of(db, story) is None


# --- the media rules read the story ------------------------------------------------------------------------

@pytest.fixture
def no_models(monkeypatch):
    """The story branches of the media rules read the stories table only; the model layer (which needs gevent) is not touched."""
    monkeypatch.setattr(attach, "_models", lambda: (None, None, None))


def story_asset(db, story):
    return NS(story_id=story, submission_id=None, comment_id=None)


def test_a_public_storys_picture_is_marked_per_viewer_never_public(db, no_models):
    public, subs, close = make(db, 0), make(db, 1), make(db, 2)
    assert attach.audience_of(db, story_asset(db, public)) == (c.STORY_OPEN, OWNER)
    assert attach.audience_of(db, story_asset(db, subs)) == (c.SUBSCRIBERS, OWNER)
    assert attach.audience_of(db, story_asset(db, close)) == (c.FRIENDS, OWNER)


def test_the_picture_of_a_story_that_is_gone_is_for_nobody(db, no_models):
    audience, owner = attach.audience_of(db, story_asset(db, 999))
    assert audience == c.GUILD and owner is None


def test_a_storys_picture_lives_with_the_story_or_its_highlight(db, no_models):
    now = int(time.time())                               # attach.is_live reads the real clock
    story = make(db, 0, when=now - 2 * DAY)
    kept = make(db, 0, when=now - 2 * DAY)
    hid, _ = store.create_highlight(db, OWNER, "Trip", [str(kept)], now)
    assert hid
    assert attach.is_live(db, story_asset(db, story)) is False          # past its day, in no Highlight
    assert attach.is_live(db, story_asset(db, kept)) is True            # a Highlight keeps it
    fresh = make(db, 0, when=now)
    assert attach.is_live(db, story_asset(db, fresh)) is True
    store.delete(db, fresh, OWNER)
    assert attach.is_live(db, story_asset(db, fresh)) is False
    store.delete(db, kept, OWNER)
    assert attach.is_live(db, story_asset(db, kept)) is False           # deleted wins over a Highlight
    assert attach.is_live(db, story_asset(db, 999)) is False


# --- Highlights ---------------------------------------------------------------------------------------------

def test_a_highlight_is_made_of_your_own_stories_only(db):
    mine, theirs = make(db), make(db, user=OTHER)
    hid, error = store.create_highlight(db, OWNER, "Trip", [str(mine), str(theirs), "x", str(mine)], NOW)
    assert error is None
    shown = store.highlight_stories(db, hid, viewer(OWNER), NOW)[1]
    assert ids(shown) == [mine]


def test_a_highlight_needs_a_story_of_yours(db):
    theirs = make(db, user=OTHER)
    assert store.create_highlight(db, OWNER, "Trip", [], NOW)[0] is None
    assert store.create_highlight(db, OWNER, "Trip", [str(theirs)], NOW)[0] is None
    assert store.create_highlight(db, OWNER, "Trip", ["999"], NOW)[0] is None
    gone = make(db)
    store.delete(db, gone, OWNER)
    assert store.create_highlight(db, OWNER, "Trip", [str(gone)], NOW)[0] is None


def test_a_member_has_a_limited_number_of_highlights(db):
    story = make(db)
    for n in range(rules.HIGHLIGHTS_MAX):
        assert store.create_highlight(db, OWNER, f"h{n}", [str(story)], NOW)[1] is None
    hid, error = store.create_highlight(db, OWNER, "one more", [str(story)], NOW)
    assert hid is None and str(rules.HIGHLIGHTS_MAX) in error
    assert store.create_highlight(db, OTHER, "mine", [str(make(db, user=OTHER))], NOW)[1] is None


def test_a_highlight_keeps_a_story_past_its_day(db):
    old = make(db, 0, when=NOW - 3 * DAY)
    hid, _ = store.create_highlight(db, OWNER, "Trip", [str(old)], NOW)
    assert store.watchable(db, OWNER, viewer(STRANGER), NOW) == []
    assert ids(store.highlight_stories(db, hid, viewer(STRANGER), NOW)[1]) == [old]
    assert [(h, t, ids(rows)) for h, t, rows in store.highlights(db, OWNER, viewer(STRANGER), NOW)] == [(hid, "Trip", [old])]


def test_a_highlight_shows_each_viewer_only_what_they_may_see(db):
    public, subs, close = make(db, 0), make(db, 1), make(db, 2)
    hid, _ = store.create_highlight(db, OWNER, "Trip", [str(public), str(subs), str(close)], NOW)
    for who, expected in ((OWNER, [public, subs, close]), (FRIEND, [public, subs, close]), (FAN, [public, subs]), (STRANGER, [public]), (BLOCKED, [])):
        assert ids(store.highlight_stories(db, hid, viewer(who), NOW)[1]) == expected


def test_a_highlight_with_nothing_for_this_viewer_is_not_listed(db):
    close = make(db, 2)
    hid, _ = store.create_highlight(db, OWNER, "Close", [str(close)], NOW)
    assert [h for h, _, _ in store.highlights(db, OWNER, viewer(STRANGER), NOW)] == []
    assert [h for h, _, _ in store.highlights(db, OWNER, viewer(FRIEND), NOW)] == [hid]
    assert store.highlight_stories(db, hid, viewer(STRANGER), NOW)[1] == []
    assert store.highlight_stories(db, 999, viewer(STRANGER), NOW) is None


def test_highlights_are_listed_in_the_order_they_were_made(db):
    story = make(db)
    first = store.create_highlight(db, OWNER, "One", [str(story)], NOW)[0]
    second = store.create_highlight(db, OWNER, "Two", [str(story)], NOW)[0]
    assert [h for h, _, _ in store.highlights(db, OWNER, viewer(OWNER), NOW)] == [first, second]


def test_a_deleted_story_leaves_its_highlights(db):
    keep, drop = make(db), make(db)
    hid, _ = store.create_highlight(db, OWNER, "Trip", [str(keep), str(drop)], NOW)
    store.delete(db, drop, OWNER)
    assert ids(store.highlight_stories(db, hid, viewer(OWNER), NOW)[1]) == [keep]
    store.delete(db, keep, OWNER)
    assert [h for h, _, _ in store.highlights(db, OWNER, viewer(OWNER), NOW)] == []


def test_only_the_owner_renames_or_deletes_a_highlight_and_the_stories_stay(db):
    story = make(db)
    hid, _ = store.create_highlight(db, OWNER, "Trip", [str(story)], NOW)
    assert store.rename_highlight(db, STRANGER, hid, "Mine") is False
    assert store.rename_highlight(db, OWNER, hid, "Trip 2026") is True
    assert store.highlight_row(db, hid).title == "Trip 2026"
    assert store.delete_highlight(db, STRANGER, hid) is False
    assert store.delete_highlight(db, OWNER, hid) is True
    assert store.delete_highlight(db, OWNER, hid) is False
    assert store.highlight_row(db, hid) is None
    assert story in ids(store.archive(db, OWNER))
    assert db.execute(text("SELECT COUNT(*) FROM highlight_stories")).scalar() == 0


# --- reports ------------------------------------------------------------------------------------------------

def test_a_report_is_once_per_person_and_the_queue_shows_the_most_reported_first(db):
    quiet, loud = make(db, body="quiet"), make(db, body="loud")
    assert store.report(db, quiet, STRANGER, "meh", NOW) is True
    assert store.report(db, quiet, STRANGER, "again", NOW) is False
    for who, reason in ((STRANGER, "rude"), (OTHER, "spam"), (FAN, "last one")):
        store.report(db, loud, who, reason, NOW)
    queue = store.open_reports(db)
    assert [(r.story_id, r.reports) for r in queue] == [(loud, 3), (quiet, 1)]
    assert queue[0].username == "u1" and queue[0].body == "loud" and queue[0].reason == "last one"      # the newest reason, not the largest


def test_resolving_or_removing_clears_the_queue_and_a_deleted_story_is_not_in_it(db):
    first, second, third = make(db), make(db), make(db)
    for story in (first, second, third):
        store.report(db, story, STRANGER, "x", NOW)
    store.resolve_reports(db, first, NOW)
    store.remove(db, second, NOW)
    assert [r.story_id for r in store.open_reports(db)] == [third]
    store.delete(db, third, OWNER)
    assert store.open_reports(db) == []


def test_a_resolved_report_does_not_come_back_but_a_new_person_can_report_again(db):
    story = make(db)
    store.report(db, story, STRANGER, "x", NOW)
    store.resolve_reports(db, story, NOW)
    assert store.open_reports(db) == []
    store.report(db, story, OTHER, "y", NOW)
    assert [(r.story_id, r.reports) for r in store.open_reports(db)] == [(story, 1)]
