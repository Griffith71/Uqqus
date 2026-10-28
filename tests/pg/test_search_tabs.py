"""The search tabs on the real app and a real Postgres (helpers/search_tabs.py, routes/search.py, templates/partials/search_tabs.html):
every results page has the bar with the right tab active, `?type=` runs the search its symbol stands for, a typed symbol still
wins, a tab with nothing typed lists nothing, the sort dropdowns belong to the posts search, a guild's own search has two tabs,
and the JSON says what it is."""
import re
import secrets

import pytest

BASE = "http://localhost"


def addr():
    return f"10.{secrets.randbelow(250)}.{secrets.randbelow(250)}.{secrets.randbelow(250) + 1}"


def go(client, path, **query):
    return client.get(path, base_url=BASE, query_string=query, headers={"User-Agent": "ci-search-tabs"}, environ_base={"REMOTE_ADDR": addr()})


def tabs(page):
    """[(label, active, href)] of the bar."""
    found = re.findall(r'<a class="search-tab( active)?" href="([^"]*)"[^>]*><i[^>]*></i><span>([^<]*)</span></a>', page)
    return [(label, bool(active), href.replace("&amp;", "&")) for active, href, label in found]


@pytest.fixture
def visitor(real_app):
    app, _ = real_app
    return app.test_client()


@pytest.fixture
def guild(real_app):
    """A public guild with a name nothing else has."""
    from ruqqus.classes import Board
    _, db_session = real_app
    db = db_session()
    name = "ci_tabs_" + secrets.token_hex(3)
    db.add(Board(name=name, description="a guild for the search tabs", description_html="<p>a guild for the search tabs</p>",
                 creator_id=1, subcat_id=0))
    db.commit()
    return name


def test_every_kind_has_the_bar_with_its_own_tab_active(visitor, guild):
    cases = {"Posts": go(visitor, "/search", q="zzz"), "Comments": go(visitor, "/search", q="zzzzz", type="comments"),
             "Guilds": go(visitor, "/search", q=guild[:9], type="guilds"), "Accounts": go(visitor, "/search", q="ci_", type="accounts"),
             "Curations": go(visitor, "/search", q="zzz", type="curations")}
    for label, answer in cases.items():
        assert answer.status_code == 200, label
        bar = tabs(answer.get_data(as_text=True))
        assert [t[0] for t in bar] == ["Posts", "Comments", "Guilds", "Accounts", "Curations"], label
        assert [t[0] for t in bar if t[1]] == [label], label


def test_a_tab_runs_the_search_its_symbol_stands_for(visitor, guild):
    by_type = go(visitor, "/search", q=guild, type="guilds").get_data(as_text=True)
    typed = go(visitor, "/search", q="+" + guild).get_data(as_text=True)
    assert f'href="/+{guild}"' in by_type and f'href="/+{guild}"' in typed
    plain = go(visitor, "/search", q=guild).get_data(as_text=True)
    assert f'href="/+{guild}"' not in plain, "plain words still search posts only"


def test_a_typed_symbol_beats_the_tab_and_old_urls_keep_their_meaning(visitor, guild):
    page = go(visitor, "/search", q="+" + guild, type="comments").get_data(as_text=True)
    assert [t[0] for t in tabs(page) if t[1]] == ["Guilds"] and f'href="/+{guild}"' in page
    page = go(visitor, "/search", q="#food").get_data(as_text=True)
    assert [t[0] for t in tabs(page) if t[1]] == ["Guilds"]
    for label, href in [(t[0], t[2]) for t in tabs(page)]:
        if label == "Posts":
            assert href == "/search?q=food", href


def test_the_other_tabs_start_from_the_query_without_its_symbol(visitor, guild):
    page = go(visitor, "/search", q="+" + guild).get_data(as_text=True)
    hrefs = {t[0]: t[2] for t in tabs(page)}
    assert hrefs["Posts"] == f"/search?q={guild}" and hrefs["Comments"] == f"/search?q={guild}&type=comments"
    assert hrefs["Accounts"] == f"/search?q={guild}&type=accounts"
    page = go(visitor, "/search", q="film from:@ci_x").get_data(as_text=True)
    hrefs = {t[0]: t[2] for t in tabs(page)}
    assert hrefs["Guilds"] == "/search?q=film&type=guilds" and "from%3A%40ci_x" in hrefs["Comments"]
    named = go(visitor, "/search", q="film from:@ci_x", type="guilds").get_data(as_text=True)
    assert "only apply to posts and comments" in named


def test_a_tab_with_nothing_typed_lists_nothing_but_a_bare_symbol_still_does(visitor):
    for kind in ("guilds", "accounts", "curations"):
        page = go(visitor, "/search", q="", type=kind).get_data(as_text=True)
        assert "Nothing was searched." in page and [t[0] for t in tabs(page) if t[1]] == [kind.capitalize()], kind
    assert "Nothing was searched." not in go(visitor, "/search", q="+").get_data(as_text=True)


def test_the_sort_and_time_dropdowns_are_the_posts_searchs_and_account_results_page(visitor, make_member):
    assert "Filter by" in go(visitor, "/search", q="zzz").get_data(as_text=True)
    for kind in ("guilds", "accounts", "curations"):
        assert "Filter by" not in go(visitor, "/search", q="zzz", type=kind).get_data(as_text=True), kind
    members = [make_member("ci_pager_" + secrets.token_hex(3) + str(i)) for i in range(27)]
    page = go(visitor, "/search", q="@ci_pager_").get_data(as_text=True)
    assert "Page navigation" in page and "rel=\"next\"" in page, "account results page now"
    assert members


def test_the_navbar_box_keeps_the_tab(visitor, guild):
    page = go(visitor, "/search", q=guild, type="guilds").get_data(as_text=True)
    assert '<input type="hidden" name="type" value="guilds">' in page
    assert 'name="type" value=' not in go(visitor, "/search", q=guild).get_data(as_text=True)
    assert 'name="type" value=' not in go(visitor, "/browse").get_data(as_text=True)


def test_a_guilds_own_search_has_two_tabs_and_hands_the_sites_kinds_to_the_site(visitor, guild):
    page = go(visitor, f"/+{guild}/search", q="film").get_data(as_text=True)
    assert [t[0] for t in tabs(page)] == ["Posts", "Comments"]
    assert all(t[2].startswith(f"/+{guild}/search") for t in tabs(page))
    for typed in ("+x", "@x", "#x", "&x"):
        answer = go(visitor, f"/+{guild}/search", q=typed)
        assert answer.status_code == 302 and answer.headers["Location"].startswith("/search?q="), typed


def test_the_json_says_what_it_is(visitor, guild):
    """/api/vue/search answers without a token (the parked web app's); /api/v1/search needs one."""
    for kind, q in (("guilds", guild), ("accounts", "ci_"), ("curations", "zzz")):
        data = go(visitor, "/api/vue/search", q=q, type=kind)
        assert data.status_code == 200, (kind, data.status_code)
        body = data.get_json()
        assert body["type"] == kind and "next_exists" in body and "data" in body, (kind, list(body))
    assert go(visitor, "/api/vue/search", q="", type="guilds").get_json() == {"data": [], "next_exists": False, "type": "guilds"}
    assert "type" not in go(visitor, "/api/vue/search", q="zzz").get_json()                       # the post search's shape is as it was
