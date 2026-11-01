"""The left sidebar on the real app and Postgres (templates/sidebar-left.html; the owner, 2026-10-30): a long list stops at five rows in a
name order a member can predict and ends with one "See all", and a filter that is narrowing the feed is marked, only where its notice
is and only for what the member chose. The look itself (40px, the ring's centre, the wash) is measured in a browser by
scripts/ui/sidebar.spec.ts."""
import re
import secrets
import time

import pytest
from sqlalchemy import text

BASE = "http://localhost"


def addr():
    return f"10.{secrets.randbelow(250)}.{secrets.randbelow(250)}.{secrets.randbelow(250) + 1}"


def call(client, path):
    return client.get(path, base_url=BASE, headers={"User-Agent": "ci-sidebar"}, environ_base={"REMOTE_ADDR": addr()})


def page(client, path="/all"):
    answer = call(client, path)
    assert answer.status_code == 200, (path, answer.status_code)
    return answer.get_data(as_text=True)


def sidebar(html):
    """The left sidebar's markup, and nothing of the page around it (the main column follows it)."""
    start = html.index('id="sidebar-left"')
    return html[start:html.index('id="main-content-col"', start)]


def open_lists(side):
    """The sidebar as an open one draws it: the rail's twin lists (`sidebar-collapsed-visible`) are cut out."""
    return re.sub(r'<ul class="no-bullets guild-recommendations-list pl-0 sidebar-collapsed-visible">.*?</ul>', "", side, flags=re.S)


def rail_lists(side):
    return "".join(re.findall(r'<ul class="no-bullets guild-recommendations-list pl-0 sidebar-collapsed-visible">.*?</ul>', side, flags=re.S))


def guilds_in(html, tag):
    return re.findall(r'sidebar-row-label" dir="auto" title="\+(ci_sb_' + tag + r'_\w+)"', html)


@pytest.fixture(autouse=True)
def forget_led_guilds(real_app):
    """The site limits how many guilds the accounts on one network may lead between them (helpers/guild_limits.py), and every test account
    here has the same address: a test that makes someone lead nine guilds would refuse every later test that appoints a guildmaster, in this
    database and in the next run on it. So what these tests make a member lead is taken back when each is done."""
    yield
    _, db_session = real_app
    db = db_session()
    db.execute(text("DELETE FROM mods WHERE board_id IN (SELECT id FROM boards WHERE name LIKE 'ci\\_sb\\_%')"))
    db.commit()


@pytest.fixture
def make_guilds(real_app):
    """`make_guilds(member, n)`: n public guilds the member has joined, named so their order is not their creation order."""
    from ruqqus.classes import Board
    _, db_session = real_app

    def make(member, n):
        tag = secrets.token_hex(3)
        db = db_session()
        names = [f"ci_sb_{tag}_{chr(97 + (i * 7) % 26)}{i:02d}" for i in range(n)]
        for name in names:
            db.add(Board(name=name, description="a guild for the sidebar test", description_html="<p>a guild for the sidebar test</p>",
                         creator_id=1, subcat_id=0))
        db.commit()
        now = int(time.time())
        for name in names:
            board = db.query(Board).filter_by(name=name).first()
            db.execute(text("INSERT INTO subscriptions (user_id, board_id, created_utc, is_active) VALUES (:u, :b, :t, true)"),
                       {"u": member.id, "b": board.id, "t": now})
        db.commit()
        return tag, sorted(names, key=str.lower)
    return make


def test_a_long_guild_list_shows_five_in_name_order_and_one_see_all(make_member, make_guilds):
    member = make_member()
    tag, names = make_guilds(member, 12)
    html = page(member.browser())
    side = sidebar(html)
    assert guilds_in(open_lists(side), tag) == names[:5]                 # alphabetical, whatever order they were joined in
    assert guilds_in(rail_lists(side), tag) == []                          # (the rail's rows are avatars alone: no label to read)
    assert open_lists(side).count('href="/mine/guilds"') == 1             # one row at the end of the list
    assert rail_lists(side).count('href="/mine/guilds"') == 1
    assert "View more" not in side
    # a second load gives the same five (the order is the name's, not whatever the database hands back)
    assert guilds_in(open_lists(sidebar(page(member.browser()))), tag) == names[:5]


@pytest.mark.parametrize("count, see_all", [(1, False), (5, False), (6, True)])
def test_see_all_is_drawn_only_when_there_is_more_than_the_sidebar_shows(make_member, make_guilds, count, see_all):
    member = make_member()
    tag, names = make_guilds(member, count)
    side = sidebar(page(member.browser()))
    assert guilds_in(open_lists(side), tag) == names[:5]
    assert (open_lists(side).count('href="/mine/guilds"') == 1) is see_all
    assert (rail_lists(side).count('href="/mine/guilds"') == 1) is see_all
    assert "See all" not in side or see_all


def test_a_member_with_no_guilds_still_sees_the_invitation_and_no_see_all(make_member):
    side = sidebar(page(make_member().browser()))
    assert "My guilds" in side and "See all" not in side


def test_curations_stop_at_five_each_and_share_one_see_all(make_member, real_app):
    from ruqqus.classes import Curation, CurationFollow
    _, db_session = real_app
    member = make_member()
    db = db_session()
    tag = secrets.token_hex(3)
    now = int(time.time())
    owned = [Curation(owner_id=member.id, name=f"Mine {tag} {i:02d}", slug=f"m{tag}{i:02d}", is_private=True, created_utc=now - i) for i in range(2)]
    other = make_member()
    theirs = [Curation(owner_id=other.id, name=f"Theirs {chr(104 - i)} {tag}", slug=f"t{tag}{i:02d}", is_private=False, created_utc=now - i)
              for i in range(8)]
    names = sorted(c.name for c in theirs)
    db.add_all(owned + theirs)
    db.flush()
    for c in theirs:
        db.add(CurationFollow(user_id=member.id, curation_id=c.id, created_utc=now))
    db.commit()
    side = sidebar(page(member.browser()))
    shown = re.findall(r'sidebar-row-label" dir="auto" title="(Theirs [a-z] ' + tag + ')"', open_lists(side))
    assert shown == names[:5]                                             # followed ones by name, not by when they were followed
    assert len(re.findall(r'sidebar-row-label" dir="auto" title="Mine ' + tag, open_lists(side))) == 2
    assert open_lists(side).count('href="/curations"') >= 1
    assert open_lists(side).count("sidebar-see-all") == 1                  # one row, not one per list
    assert rail_lists(side).count("sidebar-see-all") == 1


def test_the_selected_feed_is_marked_on_the_feed_and_never_a_filter(make_member):
    member = make_member()
    side = sidebar(page(member.browser(), "/following"))
    assert side.count('aria-current="page"') == 2                          # the open row and its rail twin
    assert re.search(r'<a href="/following" aria-current="page"', side)
    assert "is-filtering" not in side


@pytest.fixture
def chosen(make_member, real_app):
    """A member who has chosen a region, a language, a post type and an Advanced filter, in the session of the returned browser."""
    import sys
    front = sys.modules["ruqqus.routes.front"]
    code = sorted(front.REGION_CENTROIDS)[0]
    member = make_member()
    here = member.browser()
    for path in (f"/regional?region={code}", "/language?codes=en", "/post_type?types=poll", "/feed_filter?min_votes=3"):
        assert call(here, path).status_code == 302
    return member, here


def marked(side):
    """The names of the rows carrying the ring: from their aria-label or visible label."""
    out = []
    for tag in re.findall(r'<a [^>]*class="is-filtering"[^>]*>.*?</a>', side, flags=re.S):
        label = re.search(r'aria-label="([^"]+)"', tag)
        text_ = re.search(r'sidebar-row-label"(?: dir="auto")? title="([^"]+)"', tag)
        out.append((label or text_).group(1).split(" · ")[0])
    return sorted(out)


def test_a_chosen_filter_is_marked_on_the_open_row_and_on_the_rail(chosen):
    _, here = chosen
    side = sidebar(page(here))
    assert marked(open_lists(side)) == ["Advanced filter", "Language", "Post type", "Regional"]
    assert marked(rail_lists(side)) == ["Advanced filter", "Language", "Post type", "Regional"]
    assert "Categorical" not in marked(side)                               # nothing chosen there
    # said in words as well: the rail's names carry the state, the open rows' read it out
    assert "Regional · 1 selected" in side and "Language · 1 selected" in side
    assert '<span class="sr-only"> 1 selected</span>' in side


def test_the_ring_is_only_where_the_filters_apply(chosen, make_member):
    _, here = chosen
    assert "is-filtering" in page(here, "/following") and "is-filtering" in page(here, "/for_you")
    # a guild or a profile is not narrowed by them (the notices are not there either), so neither is the sidebar marked
    assert "is-filtering" not in page(here, "/browse")
    assert "is-filtering" not in page(here, f"/@{make_member().username}")
    assert "is-filtering" not in page(here, "/spaces")


def test_a_region_the_member_never_chose_is_not_marked(make_member, real_app):
    import sys
    from sqlalchemy import text as sql
    front = sys.modules["ruqqus.routes.front"]
    member = make_member()
    db = member.db_session()
    db.execute(sql("UPDATE users SET display_region = :r WHERE id = :u"), {"r": sorted(front.REGION_CENTROIDS)[0], "u": member.id})
    db.commit()
    html = page(member.browser())
    assert "is-filtering" not in html and "Showing posts from" not in html


def test_clearing_a_filter_takes_its_mark_away(chosen):
    _, here = chosen
    assert "Regional" in marked(open_lists(sidebar(page(here))))
    call(here, "/regional?region=")
    call(here, "/feed_filter?clear=1")
    assert marked(open_lists(sidebar(page(here)))) == ["Language", "Post type"]


def test_a_visitor_gets_the_sidebar_without_a_ring_or_a_list(real_app):
    app, _ = real_app
    side = sidebar(page(app.test_client()))
    assert "is-filtering" not in side and "See all" not in side and "My guilds" not in side


# --- what the independent review (2026-10-30) showed the tests above would not catch -----------------------------------------------------

def make_boards(real_app, tag, names, **columns):
    from ruqqus.classes import Board
    _, db_session = real_app
    db = db_session()
    ids = []
    for name in names:
        board = Board(name=f"ci_sb_{tag}_{name}", description="a guild for the sidebar test", description_html="<p>a guild for the sidebar test</p>",
                      creator_id=1, subcat_id=0, **columns)
        db.add(board)
        db.flush()
        ids.append(board.id)
    db.commit()
    return ids


def lead(real_app, member, board_ids):
    from ruqqus.classes import ModRelationship
    _, db_session = real_app
    db = db_session()
    for board_id in board_ids:
        db.add(ModRelationship(user_id=member.id, board_id=board_id, accepted=True, invite_rescinded=False, perm_full=True))
    db.commit()


def join(real_app, member, board_ids):
    _, db_session = real_app
    db = db_session()
    for board_id in board_ids:
        db.execute(text("INSERT INTO subscriptions (user_id, board_id, created_utc, is_active) VALUES (:u, :b, :t, true)"),
                   {"u": member.id, "b": board_id, "t": int(time.time())})
    db.commit()


def section(side, heading):
    """The open sidebar's block under a heading: from the heading to the next block."""
    html = open_lists(side)
    start = html.index(f'>{heading}</div>')
    nxt = html.find('class="sidebar-block', start)
    return html[start:nxt if nxt != -1 else len(html)]


def test_guildmaster_of_stops_at_five_in_name_order_whatever_the_member_has_joined(make_member, real_app):
    member = make_member()
    tag = secrets.token_hex(3)
    names = ["Zebra", "apple", "Mango", "banana", "Cherry", "date", "Elder", "fig", "Grape"]
    lead(real_app, member, make_boards(real_app, tag, names))                          # leads nine guilds and has joined none
    side = sidebar(page(member.browser()))
    shown = [n.split("_", 3)[3] for n in guilds_in(section(side, "Guildmaster of"), tag)]
    assert shown == ["apple", "banana", "Cherry", "date", "Elder"]                      # capitals do not sort before every lowercase name
    assert section(side, "Guildmaster of").count("sidebar-see-all") == 1 and 'href="/mine/guilds"' in section(side, "Guildmaster of")
    assert "You are not a member of any guilds" in side                               # leading is not joining: My guilds says so


def test_the_guilds_a_member_has_joined_are_in_name_order_ignoring_case(make_member, real_app):
    member = make_member()
    tag = secrets.token_hex(3)
    join(real_app, member, make_boards(real_app, tag, ["Zebra", "apple", "Mango", "banana", "Cherry", "date"]))
    side = sidebar(page(member.browser()))
    shown = [n.split("_", 3)[3] for n in guilds_in(section(side, "My guilds"), tag)]
    assert shown == ["apple", "banana", "Cherry", "date", "Mango"] and "sidebar-see-all" in section(side, "My guilds")


def test_a_banned_guild_is_not_a_heading_with_nothing_under_it(make_member, real_app):
    member = make_member()
    tag = secrets.token_hex(3)
    banned = make_boards(real_app, tag, ["gone"], is_banned=True)
    join(real_app, member, banned)
    lead(real_app, member, banned)
    side = sidebar(page(member.browser()))
    assert "Guildmaster of" not in side and "Mod Queue" not in side                    # nothing to lead
    assert "You are not a member of any guilds" in side                               # and the invitation, not an empty list
    assert "ci_sb_" + tag not in side


def curations(real_app, member, owned, followed):
    from ruqqus.classes import Curation, CurationFollow
    _, db_session = real_app
    db = db_session()
    tag = secrets.token_hex(3)
    now = int(time.time())
    mine = [Curation(owner_id=member.id, name=f"Own {tag} {i}", slug=f"o{tag}{i}", is_private=True, created_utc=now - 100 + i) for i in range(owned)]
    other = db.query(Curation).first()
    theirs = [Curation(owner_id=1, name=f"Fol {tag} {chr(97 + i)}", slug=f"f{tag}{i}", is_private=False, created_utc=now) for i in range(followed)]
    db.add_all(mine + theirs)
    db.flush()
    for c in theirs:
        db.add(CurationFollow(user_id=member.id, curation_id=c.id, created_utc=now))
    db.commit()
    return tag


def test_the_curations_see_all_follows_the_longer_list_and_the_rail_the_two_together(make_member, real_app):
    # 8 owned and 2 followed: the open lists show five and two, and the one See all is there for the eight (it once counted only the followed)
    member = make_member()
    tag = curations(real_app, member, owned=8, followed=2)
    side = sidebar(page(member.browser()))
    owned = re.findall(r'sidebar-row-label" dir="auto" title="Own ' + tag + r' (\d)"', open_lists(side))
    assert owned == ["7", "6", "5", "4", "3"]                                          # owned ones newest first, as they always were
    assert open_lists(side).count("sidebar-see-all") == 1 and rail_lists(side).count("sidebar-see-all") == 1
    # 3 owned and 3 followed: neither list is over five, but the rail holds them together and six is over
    other = make_member()
    curations(real_app, other, owned=3, followed=3)
    side = sidebar(page(other.browser()))
    assert open_lists(side).count("sidebar-see-all") == 0
    assert rail_lists(side).count("sidebar-see-all") == 1
    # and five between them is not over
    third = make_member()
    curations(real_app, third, owned=3, followed=2)
    side = sidebar(page(third.browser()))
    assert open_lists(side).count("sidebar-see-all") == 0 and rail_lists(side).count("sidebar-see-all") == 0


def test_every_see_all_names_its_list_open_and_on_the_rail(make_member, real_app, make_guilds):
    member = make_member()
    make_guilds(member, 7)
    lead(real_app, member, make_boards(real_app, secrets.token_hex(3), ["a", "b", "c", "d", "e", "f"]))
    curations(real_app, member, owned=6, followed=0)
    side = sidebar(page(member.browser()))
    names = re.findall(r'<a href="(/mine/guilds|/curations)"[^>]*aria-label="([^"]+)"', side)
    assert sorted(names) == sorted([("/mine/guilds", "My guilds · See all"), ("/mine/guilds", "Guildmaster of · See all"), ("/curations", "My Curations · See all")] * 2)


def test_the_rails_expand_button_has_a_name(make_member):
    member = make_member()
    here = member.browser()
    here.set_cookie("sidebar_pref", "collapsed", domain="localhost")
    side = sidebar(page(here))
    assert re.search(r'<button class="btn mb-3" aria-label="[^"]+" aria-expanded="false" onclick="toggle_sidebar_expand\(\);">', side)
