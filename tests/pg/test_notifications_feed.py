"""The notifications page on the real app and Postgres: bundled rows named by who and where, the "..." menu's controls, the tabs, what
is new, muted and anonymous people, pages of bundles, and the API's unchanged list (helpers/notification_feed.py, notification_groups.py)."""
import re
import secrets
import time

from sqlalchemy import text

BASE = "http://localhost"


def address():
    return f"10.{secrets.randbelow(250)}.{secrets.randbelow(250)}.{secrets.randbelow(250) + 1}"


def call(client, method, path, **kw):
    return getattr(client, method)(path, base_url=BASE, headers={"User-Agent": "ci-notifications"},
                                   environ_base={"REMOTE_ADDR": address()}, **kw)


class Person:
    def __init__(self, member):
        self.m, self.here = member, member.browser()
        self.name = member.username

    def send(self, path, **data):
        data["formkey"] = self.m.formkey(self.here)
        return call(self.here, "post", path, data=data)

    def post(self, title):
        answer = self.send("/api/vue/submit", title=title, body="Some words.", post_to_profile="1")
        assert answer.status_code == 200, answer.get_data(as_text=True)[:200]
        return answer.get_json()["id"]

    def comment(self, post, parent, body=None, anonymous=False):
        data = {"parent_fullname": parent, "submission": post, "body": body or "Comment " + secrets.token_hex(5)}
        if anonymous:
            data["anonymous"] = "true"
        answer = self.send("/api/comment", **data)
        assert answer.status_code == 200, answer.get_data(as_text=True)[:200]
        html = answer.get_json()["html"]
        start = html.index('<div id="comment-') + len('<div id="comment-')
        return html[start:html.index('"', start)]

    def get(self, path):
        answer = call(self.here, "get", path)
        assert answer.status_code == 200, (path, answer.status_code)
        return answer.get_data(as_text=True)


def person(make_member):
    member = make_member()
    while member.id == 1:                       # id 1 is the site's own account, whose notices are the System tab's
        member = make_member()
    return Person(member)


def rows(html):
    """[(kind, segment)] for each row of the page."""
    parts = html.split('<article class="notif notif-kind-')[1:]
    return [(p.split(" ", 1)[0].split('"', 1)[0], p.split("</article>", 1)[0]) for p in parts]


def kinds(html):
    return sorted(k for k, _ in rows(html))


def one(html, kind, needle):
    found = [seg for k, seg in rows(html) if k == kind and needle in seg]
    assert len(found) == 1, (kind, needle, [k for k, _ in rows(html)])
    return found[0]


def actions(segment):
    return re.findall(r'data-notif-action="([a-z-]+)" data-target="([^"]+)" data-on="([01])"', segment)


def world(make_member):
    """me; a, b, c, d and an anonymous commenter; e, whose posts I follow and whose bell I rang."""
    me, a, b, c, d, anon, e = (person(make_member) for _ in range(7))
    mine = me.post("My own post " + secrets.token_hex(3))
    for who in (a, b, c):
        who.comment(mine, f"t2_{mine}")
    anon.comment(mine, f"t2_{mine}", anonymous=True)                       # the newest on my post: nobody may be told who
    theirs = e.post("Garden club plans " + secrets.token_hex(3))
    me.send(f"/api/post/{theirs}/follow")
    for who in (a, b):
        who.comment(theirs, f"t2_{theirs}")
    starter = me.comment(theirs, f"t2_{theirs}", "I can bring starter.")
    for who in (c, d):
        who.comment(theirs, f"t3_{starter}")
    other = e.post("Compost questions " + secrets.token_hex(3))
    d.comment(other, f"t2_{other}", f"Hey @{me.name}, any advice on compost?")      # reaches me only by naming me
    me.send(f"/api/follow/{e.name}")
    me.send(f"/@{e.name}/toggle_bell")
    e.post("Seedlings are up " + secrets.token_hex(3))
    e.post("Tomato update " + secrets.token_hex(3))
    return me, a, b, c, d, anon, e, mine, theirs


def test_a_row_for_each_place_with_the_people_named_and_the_right_controls(make_member, real_app):
    me, a, b, c, d, anon, e, mine, theirs = world(make_member)
    html = me.get("/notifications/all")
    assert kinds(html) == ["comment-on-comment", "comment-on-post", "comment-on-post", "mention", "post"]

    own = one(html, "comment-on-post", "your post")
    assert "and 2 others commented on your post" in own and "Anonymous" in own      # the newest is the anonymous one
    assert anon.name not in html                                                      # and who they are is never in the page
    assert actions(own) == []                                                         # the person responsible cannot unfollow; many people: no Mute

    followed = one(html, "comment-on-post", "a post you follow")
    assert "commented on a post you follow" in followed and "Garden club plans" in followed
    assert actions(followed) == [("post-follow", theirs, "1")]

    under = one(html, "comment-on-comment", "commented on your comment")
    assert d.name in under and c.name in under and actions(under) == []

    mention = one(html, "mention", "mentioned you")
    assert actions(mention) == [("user-mute", d.name, "0")] and "any advice on compost" in mention

    posts = one(html, "post", "posted 2 new posts")
    assert actions(posts) == [("user-bell", e.name, "1"), ("user-follow", e.name, "1"), ("user-mute", e.name, "0")]


def test_the_tabs_and_their_empty_states_are_right(make_member, real_app):
    me, a, b, c, d, anon, e, mine, theirs = world(make_member)
    seen = me.get("/notifications/all")                                               # marks everything on it read
    assert len(rows(seen)) == 5
    assert "all caught up" in me.get("/notifications")                                # nothing unread any more
    assert kinds(me.get("/notifications/replies")) == ["comment-on-comment", "comment-on-post", "comment-on-post"]
    assert kinds(me.get("/notifications/posts")) == ["post"]
    assert kinds(me.get("/notifications/mentions")) == ["mention"]                    # a top-level mention was lost to NULL NOT IN
    system = me.get("/notifications/system")
    assert rows(system) == [] and "Nothing here yet" in system


def test_the_first_view_tints_what_is_new(make_member, real_app):
    me, *_ = world(make_member)
    first = me.get("/notifications/all")
    assert first.count(" is-new") == 5
    assert " is-new" not in me.get("/notifications/all")


def test_unfollowing_a_post_keeps_its_notifications_and_offers_to_follow_it_again(make_member, real_app):
    me, a, b, c, d, anon, e, mine, theirs = world(make_member)
    assert me.send(f"/api/post/{theirs}/unfollow").status_code == 200
    html = me.get("/notifications/all")
    row = one(html, "comment-on-post", "Garden club plans")
    assert "commented on" in row and "you follow" not in row                           # still a comment on the post, not a "mention"
    assert actions(row) == [("post-follow", theirs, "0")]
    assert kinds(html).count("mention") == 1                                           # and the one real mention is still a mention


def test_a_muted_account_leaves_the_bundle(make_member, real_app):
    me, a, b, c, d, anon, e, mine, theirs = world(make_member)
    assert me.send("/settings/mute", username=a.name).status_code == 200
    html = me.get("/notifications/all")
    assert f'/@{a.name}"' not in html and f'/@{b.name}"' in html                       # a is gone from every row, b is not


def test_a_page_is_a_page_of_rows_and_a_bundle_never_straddles_two(make_member, real_app, monkeypatch):
    from ruqqus.helpers import notification_feed
    monkeypatch.setattr(notification_feed, "PER_PAGE", 2)
    me, *_ = world(make_member)
    seen = []
    for number, expected, more in ((1, 2, True), (2, 2, True), (3, 1, False)):
        html = me.get(f"/notifications/all?page={number}")
        found = re.findall(r'data-notification="(\d+)"', html)
        assert len(found) == expected, number
        assert ('rel="next"' in html) is more, number
        seen += found
    assert len(set(seen)) == 5                                                       # five rows in all, none twice


def test_the_api_list_is_the_comments_it_always_was(make_member, real_app):
    me, a, b, c, d, anon, e, mine, theirs = world(make_member)
    db = me.m.db_session()
    app_id = db.execute(text("INSERT INTO oauth_apps (client_id, client_secret, app_name, redirect_uri, author_id, is_banned) "
                             "VALUES (:c, :s, 'ci app', 'https://example.org/cb', :u, false) RETURNING id"),
                        {"c": secrets.token_hex(16), "s": secrets.token_hex(32), "u": me.m.id}).scalar()
    token = secrets.token_hex(32)
    db.execute(text("INSERT INTO client_auths (user_id, oauth_client, access_token, access_token_expire_utc, scope_identity, scope_read) "
                    "VALUES (:u, :a, :t, :e, true, true)"), {"u": me.m.id, "a": app_id, "t": token, "e": int(time.time()) + 3600})
    db.commit()
    answer = real_app[0].test_client().get("/api/v1/notifications", base_url=BASE, environ_base={"REMOTE_ADDR": address()},
                                           headers={"Authorization": f"Bearer {token}", "User-Agent": "ci"})
    assert answer.status_code == 200, answer.get_data(as_text=True)[:200]
    data = answer.get_json()["data"]
    assert data and all("id" in x for x in data) and "notif-kind" not in answer.get_data(as_text=True)
