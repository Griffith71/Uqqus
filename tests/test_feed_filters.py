"""The feed filters: Post type and the Advanced filter (ruqqus/helpers/feed_filters.py, the rules, and
ruqqus/helpers/feed_filter_sql.py, the conditions, run on SQLite with small stand-in models like
test_search_filters.py).

What matters most: a filter only narrows, a guild copy is the post it copies (its poll, Space, co-authors), an
account's anonymous posts are never found through it, and a name the viewer may not see reads as one that does not
exist."""
import sys
import types
from types import SimpleNamespace as NS

import pytest
from sqlalchemy import Boolean, Column, Integer, String, create_engine
from sqlalchemy.orm import Session, declarative_base

from ruqqus.helpers import feed_filter_sql as sql
from ruqqus.helpers import feed_filters as ff
from ruqqus.helpers import search_filters
from ruqqus.helpers import search_query as sq

# --- the rules -----------------------------------------------------------------------------------------------------


def test_post_types_are_known_keys_once_in_their_own_order():
    assert ff.parse_types("space_live,poll,nonsense,poll") == ["poll", "space_live"]
    assert ff.parse_types("") == [] and ff.parse_types(None) == []
    assert ff.parse_types(["guilds", "text"]) == ["text", "guilds"]
    assert len(ff.TYPE_KEYS) == len(set(ff.TYPE_KEYS))
    assert set(ff.GROUPS) == {"Content", "Where", "Who wrote it", "Spaces"}


def test_the_post_type_row_says_what_it_shows():
    assert ff.type_summary([]) == "All posts"
    assert ff.type_summary(["poll"]) == "Polls"
    assert ff.type_summary(["poll", "space_live", "guilds"]) == "Polls +2"


def test_clean_keeps_known_fields_within_their_limits():
    spec = ff.clean({"all_words": "one two two three four five six", "phrase": 'happy "hour"', "evil": "x",
                     "from": "@alice bob @not!valid", "in_guilds": "+pets &news +a +b +c", "topics": "#gaming",
                     "domain": "https://www.Example.com/path", "ai": "only", "forwards": "only",
                     "min_votes": "10", "min_comments": "-3", "since": "2026-01-01", "until": "2026-13-01"})
    assert spec["all_words"] == ["one", "two", "three", "four", "five"]          # once each, at most MAX_WORDS
    assert spec["phrase"] == "happy hour"
    assert "evil" not in spec
    assert spec["from"] == ["alice", "bob"]
    assert spec["in_guilds"] == ["pets", "&news", "a"]                           # at most MAX_NAMES; & kept
    assert spec["topics"] == ["gaming"]
    assert spec["domain"] == "example.com"
    assert spec["ai"] == "only" and spec["forwards"] == "show"                    # forwards can't be "only"
    assert spec["min_votes"] == "10" and spec["min_comments"] == ""
    assert spec["since"] == "2026-01-01" and spec["until"] == ""


def test_a_long_word_or_phrase_is_left_out_rather_than_cut():
    assert ff.clean({"all_words": "x" * (ff.WORD_CHARS + 1)})["all_words"] == []
    assert ff.clean({"phrase": "x" * (ff.PHRASE_CHARS + 1)})["phrase"] == ""


FULL = {"all_words": "cats dogs", "phrase": "happy hour", "any_words": "red blue", "none_words": "spam",
        "topics": "#gaming", "from": "@alice", "not_from": "@bob", "mentioning": "@carol", "in_guilds": "+pets &news",
        "not_in_guilds": "+memes", "domain": "example.com", "ai": "hide", "partnership": "only", "forwards": "hide",
        "min_votes": "10", "min_comments": "2", "since": "2026-01-01", "until": "2026-02-01"}


def test_the_filter_is_written_as_search_text_that_parses_without_a_problem():
    text = ff.query_text(FULL)
    query = sq.parse(text)
    assert query.problems == ()
    kinds = [(p.kind, p.value, p.negated) for p in query.parts]
    for expected in [("word", "cats", False), ("phrase", "happy hour", False), ("word", "spam", True),
                     ("from", "alice", False), ("from", "bob", True), ("in", "pets", False), ("in", "&news", False),
                     ("in", "#gaming", False), ("in", "memes", True), ("domain", "example.com", False),
                     ("is", "ai", True), ("is", "partnership", False), ("is", "forward", True),
                     ("min_votes", "10", False), ("min_comments", "2", False), ("since", "2026-01-01", False),
                     ("until", "2026-02-01", False)]:
        assert expected in kinds, expected
    # the two fields search has no operator for stay out of the text
    assert "red" not in text and "carol" not in text


def test_the_popup_is_filled_in_with_what_was_saved():
    spec = ff.clean(FULL)
    assert ff.clean(ff.form_values(spec)) == spec
    assert ff.form_values(spec)["in_guilds"] == "+pets &news"
    assert ff.form_values(spec)["from"] == "@alice"


def test_the_session_keeps_only_the_fields_that_are_set():
    assert ff.compact({}) == {} and ff.compact({"ai": "show", "all_words": ""}) == {}
    kept = ff.compact({"from": "@alice", "ai": "hide"})
    assert kept == {"from": ["alice"], "ai": "hide"}
    assert ff.clean(kept)["from"] == ["alice"]
    assert ff.field_count(kept) == 2 and ff.field_count({}) == 0
    assert ff.is_empty({}) and not ff.is_empty(kept)


def test_a_full_filter_stays_small_enough_for_the_session_cookie():
    longest = {f: " ".join(("x" * ff.WORD_CHARS + str(i)) for i in range(20)) for f in ff.WORD_FIELDS}
    longest.update({f: " ".join("n" * 24 + str(i) for i in range(20)) for f in ff.NAME_FIELDS})
    longest.update(phrase="p" * ff.PHRASE_CHARS, domain="a" * 60 + ".com", since="2026-01-01", until="2026-02-01",
                   min_votes="1000000", min_comments="1000000", ai="hide", partnership="hide", forwards="hide")
    import json
    assert len(json.dumps(ff.compact(longest))) < 1500


# --- the conditions on SQLite --------------------------------------------------------------------------------------

Base = declarative_base()
NOW = 1_800_000_000
PROFILE, PETS, NEWS, SECRET = 1, 10, 11, 12
A, B = 1, 2


class Submission(Base):
    __tablename__ = "submissions"
    id = Column(Integer, primary_key=True)
    author_id = Column(Integer)
    board_id = Column(Integer)
    is_anonymous = Column(Boolean, default=False, nullable=False)
    repost_id = Column(Integer, default=0)
    audience = Column(Integer, default=0)
    deleted_utc = Column(Integer, default=0)
    is_banned = Column(Boolean, default=False)
    language_code = Column(String)
    made_with_ai = Column(Boolean)
    paid_partnership = Column(Boolean)
    upvotes = Column(Integer, default=0)
    downvotes = Column(Integer, default=0)
    created_utc = Column(Integer, default=NOW)
    _own_is_image = Column("is_image", Boolean)


class SubmissionAux(Base):
    __tablename__ = "submissions_aux"
    id = Column(Integer, primary_key=True)
    title = Column(String)
    body = Column(String)
    url = Column(String)
    embed_url = Column(String)


class Poll(Base):
    __tablename__ = "polls"
    id = Column(Integer, primary_key=True)
    post_id = Column(Integer)


class MediaAsset(Base):
    __tablename__ = "media_assets"
    id = Column(Integer, primary_key=True)
    submission_id = Column(Integer)
    kind = Column(String)
    status = Column(String)


class Board(Base):
    __tablename__ = "boards"
    id = Column(Integer, primary_key=True)
    name = Column(String)
    subcat_id = Column(Integer)
    is_private = Column(Boolean, default=False)
    is_banned = Column(Boolean, default=False)


class Forward(Base):
    __tablename__ = "forwardrels"
    id = Column(Integer, primary_key=True)
    primary_submission_id = Column(Integer)
    forward_submission_id = Column(Integer)
    board_id = Column(Integer)


class Coauthor(Base):
    __tablename__ = "post_coauthors"
    id = Column(Integer, primary_key=True)
    post_id = Column(Integer)
    status = Column(String)


class CollabDoc(Base):
    __tablename__ = "collab_docs"
    id = Column(Integer, primary_key=True)
    post_id = Column(Integer)


class Space(Base):
    __tablename__ = "spaces"
    id = Column(Integer, primary_key=True)
    post_id = Column(Integer)
    status = Column(String)
    started_utc = Column(Integer, default=0)
    record = Column(Boolean, default=False)
    replay_asset_id = Column(Integer)
    kind = Column(String, default="call")
    replay_link = Column(String)


class Comment(Base):
    __tablename__ = "comments"
    id = Column(Integer, primary_key=True)
    parent_submission = Column(Integer)
    is_banned = Column(Boolean, default=False)
    deleted_utc = Column(Integer, default=0)


# id: (author, board, title, extra)
POSTS = {
    1: (A, PROFILE, "Sent to a guild", {}),
    2: (A, PETS, "Sent to a guild", {"repost_id": 1}),                 # 1's copy in +pets
    3: (B, PROFILE, "Which is best?", {}),                             # a poll, and a link
    4: (B, PROFILE, "Its copy was deleted", {}),
    5: (B, NEWS, "Its copy was deleted", {"repost_id": 4, "deleted_utc": NOW}),
    6: (A, PROFILE, "Written together", {}),                           # an accepted co-author
    7: (A, PROFILE, "Asked, not answered", {}),                        # a pending one
    8: (A, PROFILE, "A shared draft", {}),                             # collaborative (its authors are co-authors too)
    9: (A, PROFILE, "Nobody knows", {"is_anonymous": True}),
    10: (A, PROFILE, "For my circles", {"audience": 4}),
    11: (B, PROFILE, "Live now", {}),
    12: (B, PROFILE, "Was live long ago", {}),
    13: (B, PROFILE, "Coming up", {}),
    14: (B, PROFILE, "Recorded", {}),
    15: (B, PROFILE, "Recorded, never uploaded", {}),
    16: (B, PETS, "Live now", {"repost_id": 11}),                     # 11's copy in +pets
    17: (A, PROFILE, "Sent somewhere private", {}),
    18: (A, SECRET, "Sent somewhere private", {"repost_id": 17}),     # 17's copy in a private guild
}


@pytest.fixture
def db(monkeypatch):
    classes = types.ModuleType("ruqqus.classes")
    for model in (Submission, SubmissionAux, Poll, MediaAsset, Comment, Board):
        setattr(classes, model.__name__, model)
    monkeypatch.setitem(sys.modules, "ruqqus.classes", classes)

    engine = create_engine("sqlite://")
    Base.metadata.create_all(engine)
    session = Session(engine)
    for pid, (author, board, title, extra) in POSTS.items():
        session.add(Submission(id=pid, author_id=author, board_id=board, **extra))
        session.add(SubmissionAux(id=pid, title=title, url="https://example.com/a" if pid == 3 else None))
    session.add_all([Board(id=PROFILE, name=sql.PROFILE_BOARD_NAME), Board(id=PETS, name="pets", subcat_id=5),
                     Board(id=NEWS, name="news", subcat_id=6), Board(id=SECRET, name="secret", subcat_id=5, is_private=True)])
    session.add_all([Forward(primary_submission_id=1, forward_submission_id=2, board_id=PETS),
                     Forward(primary_submission_id=4, forward_submission_id=5, board_id=NEWS),
                     Forward(primary_submission_id=11, forward_submission_id=16, board_id=PETS),
                     Forward(primary_submission_id=17, forward_submission_id=18, board_id=SECRET)])
    session.add(Poll(id=1, post_id=3))
    session.add_all([Coauthor(post_id=6, status="accepted"), Coauthor(post_id=7, status="pending"),
                     Coauthor(post_id=8, status="accepted"), CollabDoc(post_id=8)])
    session.add_all([Space(post_id=11, status="live", started_utc=NOW - 600),
                     Space(post_id=12, status="live", started_utc=NOW - 5 * 3600),
                     Space(post_id=13, status="scheduled"),
                     Space(post_id=14, status="ended", record=True, replay_asset_id=7),
                     Space(post_id=15, status="ended", record=True)])
    session.commit()
    yield session
    session.close()


def shown(db, feed_filter, viewer=None):
    """What a feed shows with the filter: the feed's own rules (here only "not deleted") come first."""
    posts = db.query(Submission.id).join(SubmissionAux, SubmissionAux.id == Submission.id).filter(
        Submission.deleted_utc == 0)
    return sorted(x[0] for x in sql.apply(posts, feed_filter, viewer, now=NOW).all())


def of_type(db, *keys):
    return shown(db, sql.FeedFilter(types=keys))


def test_no_filter_is_every_post(db):
    assert shown(db, None) == [p for p in POSTS if p != 5]


def test_sent_to_guilds_is_a_guild_copy_or_a_post_with_a_live_one(db):
    # 4's only copy was deleted; 17's only copy is in a private guild, which must never make the public post match
    # (18 itself is in the guild: who may see it is the feed's business)
    assert of_type(db, "guilds") == [1, 2, 11, 16, 18]


def test_in_a_guild_finds_the_original_of_a_copy_there_since_a_feed_shows_one_card_per_post(db):
    in_pets = sq.Part("in", "pets", ref=PETS)
    assert shown(db, sql.FeedFilter(query=sq.Query(parts=(in_pets,)))) == [1, 2, 11, 16]
    out_of_pets = sq.Part("in", "pets", negated=True, ref=PETS)
    assert not {1, 2, 11, 16} & set(shown(db, sql.FeedFilter(query=sq.Query(parts=(out_of_pets,)))))
    # a guild the viewer named (and so may see) counts even when private
    in_secret = sq.Part("in", "secret", ref=SECRET)
    assert shown(db, sql.FeedFilter(query=sq.Query(parts=(in_secret,)))) == [17, 18]


def test_a_topic_finds_originals_only_through_open_guilds(db):
    animals = sq.Part("in", "#animals", refs=(5,))          # +pets and the private +secret
    assert shown(db, sql.FeedFilter(query=sq.Query(parts=(animals,)))) == [1, 2, 11, 16, 18]   # never 17


def test_polls_and_links(db):
    assert of_type(db, "poll") == [3]
    assert of_type(db, "link") == [3]


def test_coauthored_and_collaborative_do_not_overlap(db):
    assert of_type(db, "coauthored") == [6]          # not 7 (pending), not 8 (a shared draft)
    assert of_type(db, "collab") == [8]


def test_anonymous_and_circles(db):
    assert of_type(db, "anonymous") == [9]
    assert of_type(db, "circles") == [10]


def test_spaces_by_state_and_a_guild_copy_is_its_originals_space(db):
    assert of_type(db, "space_live") == [11, 16]     # 12 went past its four hours
    assert of_type(db, "space_upcoming") == [13]
    assert of_type(db, "space_recording") == [14]    # 15's replay never arrived


def test_several_types_show_posts_of_any_of_them(db):
    assert of_type(db, "poll", "space_upcoming", "collab") == [3, 8, 13]


def test_the_advanced_filter_uses_searchs_conditions(db):
    query = sq.Query(parts=(sq.Part("from", "a", ref=A),))
    assert shown(db, sql.FeedFilter(query=query), NS(id=99, admin_level=0)) == [1, 2, 6, 7, 8, 10, 17, 18]   # never 9


def test_minus_from_never_hides_an_anonymous_post(db):
    query = sq.Query(parts=(sq.Part("from", "a", negated=True, ref=A),))
    assert 9 in shown(db, sql.FeedFilter(query=query), NS(id=99, admin_level=0))


def test_any_of_these_words(db):
    assert shown(db, sql.FeedFilter(any_words=("circles", "recorded"))) == [10, 14, 15]


def test_types_and_the_advanced_filter_together_both_narrow(db):
    query = sq.Query(parts=(sq.Part("from", "b", ref=B),))
    assert shown(db, sql.FeedFilter(types=("guilds", "poll"), query=query)) == [3, 11, 16]


def test_which_filters_need_submission_aux():
    assert not sql.needs_aux(None)
    assert not sql.needs_aux(sql.FeedFilter(types=("poll", "space_live", "guilds")))
    assert sql.needs_aux(sql.FeedFilter(types=("link",)))
    assert sql.needs_aux(sql.FeedFilter(any_words=("x",)))
    assert sql.needs_aux(sql.FeedFilter(query=sq.parse("cats")))
    assert not sql.needs_aux(sql.FeedFilter(query=sq.parse("min_votes:3")))


# --- prepare: names as the viewer may see them ----------------------------------------------------------------------


@pytest.fixture
def names(monkeypatch):
    accounts = {"alice": NS(id=A, username="alice")}
    guilds = {"pets": NS(id=PETS, name="pets")}
    monkeypatch.setattr(search_filters, "find_account", lambda name, v: accounts.get(name.lower()))
    monkeypatch.setattr(search_filters, "find_guild", lambda name, v: guilds.get(name.lower()))
    monkeypatch.setattr(search_filters, "find_topic", lambda name: None)
    monkeypatch.setattr(search_filters, "find_curation", lambda slug, v: None)


def test_nothing_set_is_no_filter(names):
    assert sql.prepare({}, [], None) == (None, [])


def test_a_name_nobody_may_see_is_left_out_with_the_notice_of_one_that_does_not_exist(names):
    feed_filter, notices = sql.prepare({"from": "@alice @ghost", "in_guilds": "+pets +secret"}, ["poll"], None)
    assert [(p.kind, p.value, p.ref) for p in feed_filter.query.parts] == [("from", "alice", A), ("in", "pets", PETS)]
    assert notices == [sq.NO_ACCOUNT.format("ghost") + " It is left out of the filter.",
                       sq.NO_GUILD.format("secret") + " It is left out of the filter."]
    assert feed_filter.types == ("poll",)


def test_dates_that_leave_no_days_are_left_out(names):
    feed_filter, notices = sql.prepare({"since": "2026-02-01", "until": "2026-01-01", "min_votes": "3"}, [], None)
    assert [p.kind for p in feed_filter.query.parts] == ["min_votes"]
    assert notices == [sql.LEFT_OUT.format(sql.DATES_CLASH)]


def test_a_prepared_filter_is_a_stable_cache_key(names):
    one, _ = sql.prepare(FULL | {"from": "@alice", "in_guilds": "+pets"}, ["poll", "guilds"], None)
    two, _ = sql.prepare(FULL | {"from": "@alice", "in_guilds": "+pets"}, ["guilds", "poll"], None)
    assert one == two and repr(one) == repr(two) and hash(one) == hash(two)


def test_the_notice_chips_name_what_is_applied(names):
    feed_filter, _ = sql.prepare({"from": "@alice", "any_words": "red blue", "mentioning": "@carol"}, [], None)
    assert ff.chips(feed_filter.query, {"from": "@alice", "any_words": "red blue", "mentioning": "@carol"}) == [
        "From @alice", 'Any of "red", "blue"', "Mentioning @carol"]


# --- a member's saved default ----------------------------------------------------------------------------------------

KNOWN = {"known_regions": {"british_isles", "iberia"}, "known_languages": {"en", "fr"},
         "known_categories": {0, 5, 6}}


def test_a_saved_default_reads_back_as_it_was_saved():
    text = ff.defaults_payload(["british_isles"], ["en"], [5, 0], ["space_live", "poll"], {"from": "@alice"})
    assert ff.read_defaults(text, **KNOWN) == {"regions": ["british_isles"], "languages": ["en"], "categories": [5, 0],
                                               "types": ["poll", "space_live"], "advanced": {"from": ["alice"]}}


def test_nothing_saved_or_unreadable_is_no_default():
    for text in ("", None, "not json", "[]", "3", '"x"'):
        assert ff.read_defaults(text, **KNOWN) is None


def test_a_saved_default_is_checked_again_when_read():
    import json
    hostile = json.dumps({"regions": ["british_isles", "atlantis", 7], "languages": ["en", "la", None],
                          "categories": [5, 99, True, "6"], "types": ["poll", "everything"],
                          "advanced": {"from": "@ok @not!ok", "evil": "x", "ai": "maybe"}, "extra": 1})
    assert ff.read_defaults(hostile, **KNOWN) == {"regions": ["british_isles"], "languages": ["en"], "categories": [5],
                                                  "types": ["poll"], "advanced": {"from": ["ok"]}}
    assert ff.read_defaults(json.dumps({"advanced": "nope", "types": "poll"}), **KNOWN) == {
        "regions": [], "languages": [], "categories": [], "types": [], "advanced": {}}


def test_an_empty_default_is_still_a_default():
    assert ff.read_defaults(ff.defaults_payload([], [], [], [], {}), **KNOWN) == {
        "regions": [], "languages": [], "categories": [], "types": [], "advanced": {}}
