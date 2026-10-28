"""The notifications page's rows (ruqqus/helpers/notification_groups.py): what each notification is, what bundles, who is named,
and the controls offered beside a row."""
from ruqqus.helpers import bell
from ruqqus.helpers import notification_groups as ng
from ruqqus.helpers.notification_groups import Actor, Item

ALEX, SAM, JO, KIM = (Actor(n, link=f"/@{n}") for n in ("alex", "sam", "jo", "kim"))
ANON = Actor("", anonymous=True)
REPOST = next(iter(sorted(m for m in bell.ACCOUNT_NOTICE_IDS if "reposted [a post]" in m)))
HERALD = next(iter(sorted(m for m in bell.GUILD_NOTICE_IDS if "announcement" in m)))


def item(i, kind, key, actor=None, read=False, **extra):
    return Item(id=i, read=read, kind=kind, key=key, actor=actor, created_utc=1000 + i, **extra)


def post_comment(i, actor, post=7, read=False, state="following"):
    return item(i, ng.COMMENT_ON_POST, ("post", post), actor, read, post_id=post, post_ref="b7", post_state=state)


def test_each_kind_and_what_it_bundles_by():
    c = lambda **k: ng.classify(source="comment", item_id=9, **k)
    assert c(post_id=7, post_state="own") == (ng.COMMENT_ON_POST, ("post", 7))
    assert c(post_id=7, post_state="following") == (ng.COMMENT_ON_POST, ("post", 7))
    assert c(level=2, parent_id=5, parent_by_viewer=True, post_id=7) == (ng.COMMENT_ON_COMMENT, ("comment", 5))
    # a comment on a post you do not follow is still a comment on that post (you may have unfollowed it from the menu), unless it
    # names you, which is how it reached you; a deep comment under someone else's reached you by a mention
    assert c(post_id=7, post_state="can_follow") == (ng.COMMENT_ON_POST, ("post", 7))
    assert c(post_id=7, post_state="can_follow", mentions_viewer=True) == (ng.MENTION, ("row", 9))
    assert c(post_id=7, post_state="following", mentions_viewer=True) == (ng.COMMENT_ON_POST, ("post", 7))      # the same row either way
    assert c(level=3, parent_id=5, parent_by_viewer=False, post_id=7, post_state="following") == (ng.MENTION, ("row", 9))
    assert ng.classify(source="post", author_id=4) == (ng.POST, ("account", 4))
    assert c(system=True, msgid=REPOST, params={"name": "Nu_Lee"}) == (ng.BELL_ACCOUNT, ("account", "nu_lee"))
    assert c(system=True, msgid=HERALD, params={"guild": "General"}) == (ng.BELL_GUILD, ("guild", "general"))
    assert c(system=True, msgid="Welcome to %(site)s.", params={}) == (ng.SYSTEM, ("row", 9))
    assert c(system=True, msgid=REPOST, params={}) == (ng.SYSTEM, ("row", 9))          # a bell notice with no account is not bundled


def test_comments_on_one_post_are_one_row_with_the_newest_first_and_the_actors_counted():
    rows = ng.group([post_comment(9, ALEX), post_comment(8, SAM), post_comment(7, ALEX), post_comment(6, JO), post_comment(5, KIM)])
    assert len(rows) == 1
    row = rows[0]
    assert (row.kind, row.count) == (ng.COMMENT_ON_POST, 5) and row.newest.id == 9
    assert [a.name for a in row.actors] == ["alex", "sam", "jo", "kim"]               # alex once, in the order they last wrote
    assert [a.name for a in row.shown] == ["alex", "sam", "jo"] and [a.name for a in row.names] == ["alex", "sam"] and row.rest == 2
    assert row.sole is None


def test_one_actor_is_named_and_sole():
    row = ng.group([post_comment(9, ALEX), post_comment(8, ALEX)])[0]
    assert row.count == 2 and row.sole == ALEX and row.rest == 0


def test_rows_stay_in_the_order_of_their_newest_item_and_other_places_are_other_rows():
    rows = ng.group([post_comment(9, ALEX, post=7), post_comment(8, SAM, post=8), post_comment(7, JO, post=7)])
    assert [(r.key, r.count) for r in rows] == [(("post", 7), 2), (("post", 8), 1)]


def test_new_and_read_notifications_never_bundle_together():
    rows = ng.group([post_comment(9, ALEX), post_comment(8, SAM), post_comment(4, JO, read=True), post_comment(3, KIM, read=True)])
    assert [(r.count, r.unread) for r in rows] == [(2, True), (2, False)]
    mixed = ng.Group(ng.COMMENT_ON_POST, ("post", 7), [post_comment(9, ALEX), post_comment(8, SAM, read=True)])
    assert mixed.unread is True                                                       # unread = any, for a row built by hand


def test_mentions_tips_and_other_notices_are_each_their_own_row():
    items = [item(9, ng.MENTION, ("row", 9), ALEX), item(8, ng.MENTION, ("row", 8), ALEX), item(7, ng.SYSTEM, ("row", 7)), item(6, ng.SYSTEM, ("row", 6))]
    assert [r.count for r in ng.group(items)] == [1, 1, 1, 1]


def test_posts_and_bell_notices_bundle_by_account_and_guild():
    items = [item(9, ng.POST, ("account", 4), ALEX), item(8, ng.BELL_ACCOUNT, ("account", "alex"), ALEX), item(7, ng.POST, ("account", 4), ALEX),
             item(6, ng.BELL_GUILD, ("guild", "general")), item(5, ng.BELL_GUILD, ("guild", "general")), item(4, ng.BELL_GUILD, ("guild", "other"))]
    assert [(r.kind, r.count) for r in ng.group(items)] == [(ng.POST, 2), (ng.BELL_ACCOUNT, 1), (ng.BELL_GUILD, 2), (ng.BELL_GUILD, 1)]


def test_an_anonymous_author_is_never_named_or_given_controls_and_each_one_counts():
    rows = ng.group([post_comment(9, ANON), post_comment(8, ANON), post_comment(7, ALEX)])
    row = rows[0]
    assert len(row.actors) == 3 and row.sole is None                                  # two unknown people and alex
    only = ng.group([post_comment(9, ANON)])[0]
    assert only.sole is None
    assert ng.controls(only, {"follow": {}, "mute": set(), "guild": {}}) == [ng.Control("post-follow", "b7", True, "/api/post/b7/unfollow", "/api/post/b7/follow")]
    anon_mention = ng.group([item(9, ng.MENTION, ("row", 9), ANON)])[0]
    assert ng.controls(anon_mention, {}) == []


def actions(grp, **state):
    return [(c.action, c.target, c.on) for c in ng.controls(grp, state)]


def test_once_unfollowed_the_menu_offers_follow_again_so_it_can_be_undone():
    stopped = ng.group([post_comment(9, ALEX, state="can_follow")])[0]
    assert actions(stopped) == [("post-follow", "b7", False), ("user-mute", "alex", False)]
    assert [c.url_off for c in ng.controls(stopped, {}) if c.action == "post-follow"] == ["/api/post/b7/follow"]


def test_a_comment_on_a_post_you_follow_offers_unfollow_but_on_your_own_post_it_does_not():
    followed = ng.group([post_comment(9, ALEX), post_comment(8, SAM)])[0]
    assert actions(followed) == [("post-follow", "b7", True)]                         # two people: no Mute
    own = ng.group([post_comment(9, ALEX, state="own")])[0]
    assert actions(own) == [("user-mute", "alex", False)]                             # the person responsible cannot unfollow
    assert actions(own, mute={"alex"}) == [("user-mute", "alex", True)]               # and a mute can be undone from the same menu
    both = ng.group([post_comment(9, ALEX)])[0]
    assert actions(both) == [("post-follow", "b7", True), ("user-mute", "alex", False)]


def test_a_comment_on_your_comment_or_a_mention_offers_mute_only():
    assert actions(ng.group([item(9, ng.COMMENT_ON_COMMENT, ("comment", 5), JO)])[0]) == [("user-mute", "jo", False)]
    assert actions(ng.group([item(9, ng.MENTION, ("row", 9), KIM)])[0], follow={"kim": {"following": True, "bell": True}}) == [("user-mute", "kim", False)]


def test_a_post_or_bell_notice_from_an_account_offers_bell_unfollow_and_mute_when_you_follow_them():
    posts = ng.group([item(9, ng.POST, ("account", 4), ALEX, account="alex")])[0]
    assert actions(posts, follow={"alex": {"following": True, "bell": True}}) == [("user-bell", "alex", True), ("user-follow", "alex", True), ("user-mute", "alex", False)]
    assert actions(posts, follow={"alex": {"following": True, "bell": False}})[0] == ("user-bell", "alex", False)
    assert actions(posts) == [("user-mute", "alex", False)]                           # not following: nothing to turn off
    bell_row = ng.group([item(9, ng.BELL_ACCOUNT, ("account", "alex"), ALEX, account="alex")])[0]
    assert [a[0] for a in actions(bell_row, follow={"alex": {"following": True, "bell": True}})] == ["user-bell", "user-follow", "user-mute"]


def test_a_guild_bell_notice_offers_the_guilds_bell_leave_and_mute():
    row = ng.group([item(9, ng.BELL_GUILD, ("guild", "general"), guild="General")])[0]
    member = {"general": {"member": True, "bell": True, "mute": False}}
    assert actions(row, guild=member) == [("guild-bell", "General", True), ("guild-member", "General", True), ("guild-mute", "General", False)]
    assert actions(row, guild={"general": {"member": False}}) == [("guild-mute", "General", False)]


def test_other_notices_have_no_menu_and_the_urls_and_fields_are_the_routes_the_site_has():
    assert ng.controls(ng.group([item(9, ng.SYSTEM, ("row", 9))])[0], {}) == []
    follow = {"alex": {"following": True, "bell": True}}
    post_row = ng.group([item(9, ng.POST, ("account", 4), ALEX, account="alex")])[0]
    bell_c, unfollow_c, mute_c = ng.controls(post_row, {"follow": follow})
    assert bell_c.toggles and bell_c.url_on == "/@alex/toggle_bell"
    assert (unfollow_c.url_on, unfollow_c.url_off) == ("/api/unfollow/alex", "/api/follow/alex")
    assert (mute_c.url_on, mute_c.url_off, mute_c.field) == ("/settings/unmute", "/settings/mute", "username")
    guild_row = ng.group([item(9, ng.BELL_GUILD, ("guild", "general"), guild="general")])[0]
    g_bell, g_member, g_mute = ng.controls(guild_row, {"guild": {"general": {"member": True, "bell": False}}})
    assert g_bell.url_on == "/+general/toggle_bell" and (g_member.url_on, g_member.url_off) == ("/api/unsubscribe/general", "/api/subscribe/general")
    assert (g_mute.url_on, g_mute.url_off, g_mute.field) == ("/settings/unmute_guild", "/settings/mute_guild", "board")


def test_a_name_that_does_not_look_like_a_name_never_reaches_a_url():
    odd = Actor("a/../b", link="/x")
    assert ng.controls(ng.group([item(9, ng.MENTION, ("row", 9), odd)])[0], {}) == []
    assert ng.controls(ng.group([item(9, ng.BELL_GUILD, ("guild", "x"), guild="a b")])[0], {"guild": {"a b": {"member": True}}}) == []
