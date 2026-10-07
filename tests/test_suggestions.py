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
    base = dict(id=id, name=f"g{id}", is_banned=False, is_private=False, all_opt_out=False, subcat_id=5)
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


def test_hidden_categories_follow_the_word_filter():
    for subcat in sg.SPECIAL_SUBCATS:
        assert not sg.guild_eligible(guild(1, subcat_id=subcat), set(), set(), False)
    for subcat in sg.CHILD_HIDDEN_SUBCATS:
        assert sg.guild_eligible(guild(1, subcat_id=subcat), set(), set(), False)
        assert not sg.guild_eligible(guild(1, subcat_id=subcat), set(), set(), True)


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
    reasons = re.findall(r'(?:s\.add\([^\n]*?,\s*)(f?"[^"\n]+")', source)
    assert reasons
    for text in reasons:
        assert "@" not in text and "author" not in text.lower(), text


def test_the_sidebar_loads_lazily_and_the_page_is_for_members():
    routes = (ROOT / "routes" / "suggestions.py").read_text(encoding="utf-8")
    assert routes.count("@auth_required") == 2
    assert "/inpage/who_to_follow" in routes and "/who_to_follow" in routes
    for sidebar in ("home.html", "default.html"):
        html = (ROOT / "templates" / sidebar).read_text(encoding="utf-8")
        assert 'id="who-to-follow" data-src="/inpage/who_to_follow"' in html, sidebar
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
        assert 'id="who-to-follow"' in (ROOT / "templates" / sidebar).read_text(encoding="utf-8"), sidebar
