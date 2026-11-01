"""Who to follow (ruqqus/helpers/suggestions.py): the scoring and the rules for who
may be suggested. The queries themselves are exercised against the real database in
the end-to-end run; the rules that decide what a member must never be shown are here."""
import inspect
import re
from pathlib import Path
from types import SimpleNamespace as NS

from ruqqus.helpers import suggestions as sg

ROOT = Path(__file__).resolve().parent.parent / "ruqqus"


def user(id, **kw):
    base = dict(id=id, is_private=False, is_nofollow=False, is_banned=0, is_deleted=False)
    base.update(kw)
    return NS(**base)


def guild(id, **kw):
    base = dict(id=id, name=f"g{id}", is_banned=False, is_private=False, all_opt_out=False, subcat_id=5, cat_id=None)
    base.update(kw)
    return NS(**base)


def curation(id, **kw):
    base = dict(id=id, owner_id=99, is_private=False)
    base.update(kw)
    return NS(**base)


# --- scoring ---------------------------------------------------------------------

def test_reasons_add_up_and_the_strongest_one_is_the_label():
    s = sg.Scores()
    s.add(7, 3, "Followed by 1 person you follow")
    s.add(7, 2, "Follows you")
    s.add(8, 1, "Popular on the site")
    assert s.rows() == [(7, 5.0, "Followed by 1 person you follow"), (8, 1.0, "Popular on the site")]


def test_equal_scores_keep_a_stable_order():
    s = sg.Scores()
    for uid in (9, 3, 6):
        s.add(uid, 1, "x")
    assert [r[0] for r in s.rows()] == [3, 6, 9]


def test_popularity_only_breaks_ties():
    assert sg.popularity(0) == 0
    assert sg.popularity(10) < sg.popularity(1000)
    assert sg.popularity(10**12) <= 2.5 < 3        # however big, one mutual follow (3) still wins
    assert sg.popularity(-5) == 0


def test_top_skips_the_ineligible_and_stops_at_the_limit():
    rows = [(1, 9, "a"), (2, 8, "b"), (3, 7, "c"), (4, 6, "d")]
    assert sg.top(rows, lambda i: i != 2, 2) == [(1, 9, "a"), (3, 7, "c")]
    assert sg.top(rows, lambda i: False, 3) == []


def test_plural():
    assert sg.plural(1, "person", "people") == "1 person"
    assert sg.plural(3, "person", "people") == "3 people"


# --- who may be suggested --------------------------------------------------------

def test_an_account_is_not_suggested_to_itself_or_to_who_already_follows_it():
    assert sg.user_eligible(user(5), 1, set(), set())
    assert not sg.user_eligible(user(1), 1, set(), set())
    assert not sg.user_eligible(user(5), 1, {5}, set())
    assert not sg.user_eligible(None, 1, set(), set())


def test_blocked_private_unfollowable_banned_and_deleted_accounts_are_never_suggested():
    assert not sg.user_eligible(user(5), 1, set(), {5})                 # blocked either way
    assert not sg.user_eligible(user(5, is_private=True), 1, set(), set())
    assert not sg.user_eligible(user(5, is_nofollow=True), 1, set(), set())
    assert not sg.user_eligible(user(5, is_banned=3), 1, set(), set())
    assert not sg.user_eligible(user(5, is_deleted=True), 1, set(), set())


def test_a_guild_you_joined_or_blocked_is_not_suggested():
    assert sg.guild_eligible(guild(1), set(), set(), False)
    assert not sg.guild_eligible(guild(1), {1}, set(), False)
    assert not sg.guild_eligible(guild(1), set(), {1}, False)
    assert not sg.guild_eligible(None, set(), set(), False)


def test_banned_private_opted_out_and_profile_guilds_are_never_suggested():
    assert not sg.guild_eligible(guild(1, is_banned=True), set(), set(), False)
    assert not sg.guild_eligible(guild(1, is_private=True), set(), set(), False)
    assert not sg.guild_eligible(guild(1, all_opt_out=True), set(), set(), False)
    assert not sg.guild_eligible(guild(1, name=sg.PROFILE_BOARD), set(), set(), False)


def similar(id, **kw):
    base = dict(is_circle=False, is_nsfl=False, is_sensitive=False)
    base.update(kw)
    return guild(id, **base)


def test_similar_guilds_add_to_the_guild_rules():
    # what guild_eligible refuses stays refused, and Similar guilds also refuses a Circle guild, an NSFL guild, one the viewer
    # is exiled from, and a sensitive one unless the guild the box is on is sensitive or the viewer chose to see them
    assert sg.similar_guild_eligible(similar(1), set(), set(), set(), False, False)
    assert not sg.similar_guild_eligible(similar(1), {1}, set(), set(), False, False)       # joined
    assert not sg.similar_guild_eligible(similar(1), set(), {1}, set(), False, False)       # muted
    assert not sg.similar_guild_eligible(similar(1), set(), set(), {1}, False, False)       # exiled
    assert not sg.similar_guild_eligible(similar(1, is_circle=True), set(), set(), set(), False, False)
    assert not sg.similar_guild_eligible(similar(1, is_nsfl=True), set(), set(), set(), False, False)
    assert not sg.similar_guild_eligible(similar(1, is_private=True), set(), set(), set(), False, False)
    assert not sg.similar_guild_eligible(similar(1, is_sensitive=True), set(), set(), set(), False, False)
    assert sg.similar_guild_eligible(similar(1, is_sensitive=True), set(), set(), set(), False, True)


def test_similar_guilds_reasons_name_no_person_and_no_count():
    here = sg.reason_text((sg.SHARES_MEMBERS, "cats"))
    assert here == "Shares members with +cats" and not any(ch.isdigit() for ch in here)
    assert sg.reason_text((sg.SAME_TOPIC, "Food")) == "Also about Food"


def test_hidden_categories_follow_the_word_filter():
    # no subcategory is left out of the suggestions any more (108 was the unlisted bucket until 2026-10-30)
    assert not hasattr(sg, "SPECIAL_SUBCATS")
    assert sg.guild_eligible(guild(1, subcat_id=108), set(), set(), False)
    for subcat in sg.CHILD_HIDDEN_SUBCATS:
        assert sg.guild_eligible(guild(1, subcat_id=subcat), set(), set(), False)
        assert not sg.guild_eligible(guild(1, subcat_id=subcat), set(), set(), True)


def test_a_guild_filed_under_a_category_alone_is_not_one_of_the_hidden_subcategories():
    alone = guild(1, subcat_id=0, cat_id=7)
    assert sg.guild_eligible(alone, set(), set(), False) and sg.guild_eligible(alone, set(), set(), True)


def test_the_interest_ids_may_be_a_category_alone_in_both_candidate_queries():
    guilds = inspect.getsource(sg.guild_candidates.uncached)
    assert "category_filter.board_clause([subcat], Board.subcat_id, Board.cat_id)" in guilds
    assert "Board.subcat_id == subcat" not in guilds
    curations = inspect.getsource(sg.curation_candidates.uncached)
    assert "interests = set(viewer.interest_subcats() if viewer else [])" in curations
    assert "interests & set(c.category_filter_list)" in curations
    assert "expand" not in curations


def test_a_curation_matches_an_interest_only_through_an_id_both_name(monkeypatch):
    # curation_candidates run for real on SQLite: -N is the guilds filed under category N alone, so it is no stand-in for
    # N's subcategories (nor they for it); a token equal on both sides overlaps
    import sys
    import types

    import flask
    from sqlalchemy import Boolean, Column, Integer, String, create_engine
    from sqlalchemy.orm import Session, declarative_base

    Base = declarative_base()
    interests = []

    class Follow(Base):
        __tablename__ = "follows"
        id = Column(Integer, primary_key=True)
        user_id = Column(Integer)
        target_id = Column(Integer)

    class Subscription(Base):
        __tablename__ = "subscriptions"
        id = Column(Integer, primary_key=True)
        user_id = Column(Integer)
        board_id = Column(Integer)
        is_active = Column(Boolean)

    class User(Base):
        __tablename__ = "users"
        id = Column(Integer, primary_key=True)

        def interest_subcats(self):
            return interests

    class Curation(Base):
        __tablename__ = "curations"
        id = Column(Integer, primary_key=True)
        is_private = Column(Boolean, default=False)
        category_filter = Column(String)

        @property
        def category_filter_list(self):
            return [int(x) for x in self.category_filter.split(",") if x] if self.category_filter else []

    class CurationGuild(Base):
        __tablename__ = "curation_guilds"
        id = Column(Integer, primary_key=True)
        curation_id = Column(Integer)
        board_id = Column(Integer)

    class CurationUser(Base):
        __tablename__ = "curation_users"
        id = Column(Integer, primary_key=True)
        curation_id = Column(Integer)
        target_user_id = Column(Integer)

    class CurationFollow(Base):
        __tablename__ = "curation_follows"
        id = Column(Integer, primary_key=True)
        curation_id = Column(Integer)

    classes = types.ModuleType("ruqqus.classes")
    classes.Follow, classes.Subscription, classes.User = Follow, Subscription, User
    curations = types.ModuleType("ruqqus.classes.curations")
    curations.Curation, curations.CurationGuild = Curation, CurationGuild
    curations.CurationUser, curations.CurationFollow = CurationUser, CurationFollow
    monkeypatch.setitem(sys.modules, "ruqqus.classes", classes)
    monkeypatch.setitem(sys.modules, "ruqqus.classes.curations", curations)

    engine = create_engine("sqlite://")
    Base.metadata.create_all(engine)
    db = Session(engine)
    db.add(User(id=5))
    # 1: the subcategories of Arts; 2: Arts alone; 3: both; 4: another category's rows
    db.add_all([Curation(id=1, category_filter="7,8"), Curation(id=2, category_filter="-1"),
                Curation(id=3, category_filter="-1,7,8"), Curation(id=4, category_filter="9"), Curation(id=5)])
    db.commit()

    def matching(own_interests):
        interests[:] = own_interests
        with flask.Flask("test").app_context():
            flask.g.db = db
            rows = sg.curation_candidates.uncached(5)
        return sorted(row[0] for row in rows if row[2] == "Matches your interests")

    assert matching([7]) == [1, 3]                  # a subcategory is not the category alone
    assert matching([-1]) == [2, 3]                 # the category alone is not its subcategories
    assert matching([-1, 7]) == [1, 2, 3]
    assert matching([9]) == [4]
    assert matching([-2]) == [] and matching([]) == []
    db.close()


def test_curations_must_be_public_someone_elses_and_not_followed():
    assert sg.curation_eligible(curation(1), 5, set(), set(), True)
    assert not sg.curation_eligible(curation(1, is_private=True), 5, set(), set(), True)
    assert not sg.curation_eligible(curation(1, owner_id=5), 5, set(), set(), True)      # your own
    assert not sg.curation_eligible(curation(1), 5, {1}, set(), True)                    # already followed
    assert not sg.curation_eligible(None, 5, set(), set(), True)


def test_a_curation_is_hidden_with_its_owner():
    assert not sg.curation_eligible(curation(1), 5, set(), set(), False)    # owner banned, deleted or filtered
    assert not sg.curation_eligible(curation(1, owner_id=99), 5, set(), {99}, True)      # owner blocked


# --- the rules in CLAUDE.md ------------------------------------------------------

def test_the_posting_signal_leaves_anonymous_posts_out():
    source = inspect.getsource(sg.user_candidates.uncached)
    assert "Submission.is_anonymous == False" in source, "anonymous posts must not point at their author"


def test_reasons_name_no_individual():
    source = inspect.getsource(sg)
    reasons = re.findall(r'(?:s\.add\([^\n]*?,\s*)(?:N_\()?(f?"[^"\n]+")', source)
    reasons += re.findall(r'trn\(("[^"\n]+")', inspect.getsource(sg.reason_text))      # the reasons that count
    assert reasons
    for text in reasons:
        assert "@" not in text and "author" not in text.lower(), text


def test_the_sidebar_loads_lazily_and_the_page_is_for_members():
    routes = (ROOT / "routes" / "suggestions.py").read_text(encoding="utf-8")
    assert routes.count("@auth_required") == 2
    assert "/inpage/who_to_follow" in routes and "/who_to_follow" in routes
    # both page shells include the placeholders (partials/late_boxes.html), which load after the page
    for sidebar in ("home.html", "default.html"):
        html = (ROOT / "templates" / sidebar).read_text(encoding="utf-8")
        assert '{% include "partials/sidebar_common.html" %}' in html, sidebar     # which includes the late boxes
    late = (ROOT / "templates" / "partials" / "late_boxes.html").read_text(encoding="utf-8")
    # (a profile has Similar accounts in its place: tests/test_profile_sidebar_assets.py)
    assert '{% elif v %}<div id="who-to-follow" class="late-box" data-src="/inpage/who_to_follow"></div>{% endif %}' in late
    box = (ROOT / "templates" / "partials" / "who_to_follow_box.html").read_text(encoding="utf-8")
    assert "{% if picks %}" in box and 'href="/who_to_follow"' in box


def test_every_kind_has_a_row_and_a_button_route_that_exists():
    row = (ROOT / "templates" / "partials" / "suggestion_row.html").read_text(encoding="utf-8")
    routes = "".join((ROOT / "routes" / name).read_text(encoding="utf-8")
                     for name in ("users.py", "boards.py", "curations.py"))
    for url, route in (("/api/follow/", '"/api/follow/<username>"'), ("/api/subscribe/", '"/api/subscribe/<guildname>"'),
                       ("/api/follow_curation/", '"/api/follow_curation/<slug>"')):
        assert url in row, url
        assert route in routes, route


def test_who_to_follow_is_only_in_the_right_sidebar():
    # the box in the right sidebar links to the full page; the left navigation does not
    left = (ROOT / "templates" / "sidebar-left.html").read_text(encoding="utf-8")
    assert "who_to_follow" not in left and "Who to follow" not in left
    for sidebar in ("home.html", "default.html"):
        assert "partials/sidebar_common.html" in (ROOT / "templates" / sidebar).read_text(encoding="utf-8"), sidebar
    assert "partials/late_boxes.html" in (ROOT / "templates" / "partials" / "sidebar_common.html").read_text(encoding="utf-8")
    assert 'id="who-to-follow"' in (ROOT / "templates" / "partials" / "late_boxes.html").read_text(encoding="utf-8")
