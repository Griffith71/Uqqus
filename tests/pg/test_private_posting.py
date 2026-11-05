"""Private posting on the real app and a real Postgres (helpers/private_posting.py): every post and story goes only to the Friends circle
while it is on, nothing can carry a post past that circle, custom circles hold only mutual followers, a follow that ends takes the member
out, and the switch is the member's own (a sub-account's is its parent's)."""
import secrets

import pytest
from sqlalchemy import text

BASE = "http://localhost"
AGENT = "Mozilla/5.0 (Macintosh; Intel Mac OS X 14_5) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.5 Safari/605.1.15"


def address():
    return f"10.{secrets.randbelow(250)}.{secrets.randbelow(250)}.{secrets.randbelow(250) + 1}"


def get(client, path):
    return client.get(path, base_url=BASE, headers={"User-Agent": AGENT}, environ_base={"REMOTE_ADDR": address()})


def post(client, path, **form):
    return client.post(path, base_url=BASE, headers={"User-Agent": AGENT}, environ_base={"REMOTE_ADDR": address()}, data=form)


def rows(member, sql, **params):
    db = member.db_session()
    db.rollback()
    return db.execute(text(sql), dict(params, u=member.id)).fetchall()


def set_columns(member, **values):
    db = member.db_session()
    db.execute(text("UPDATE users SET " + ", ".join(f"{k} = :{k}" for k in values) + " WHERE id = :u"), dict(values, u=member.id))
    db.commit()


def private(make_member, **extra):
    member = make_member()
    set_columns(member, age_declared=1, age_declared_utc=5, private_posting=True, **extra)
    return member


def publish(member, client, **extra):
    return post(client, "/api/vue/submit", formkey=member.formkey(client), title=f"Private {secrets.token_hex(3)}", body="words",
                post_to_profile="1", **extra)


def stored(member, post_id):
    from ruqqus.helpers.base36 import base36decode
    return rows(member, "SELECT audience, post_public FROM submissions WHERE id = :p", p=base36decode(post_id))[0]


def follow(a, b):
    db = a.db_session()
    db.execute(text("INSERT INTO follows (user_id, target_id, created_utc) VALUES (:a, :b, 1)"), {"a": a.id, "b": b.id})
    db.commit()


@pytest.mark.parametrize("extra", [{}, {"audience": "0"}])
def test_a_public_post_is_stored_for_the_friends_circle_while_it_is_on(make_member, extra):
    member = private(make_member)
    client = member.browser()
    made = publish(member, client, **extra)
    assert made.status_code == 200, made.get_data(as_text=True)[:300]
    audience, public = stored(member, made.get_json()["id"])
    assert audience == 4 and public is False
    friends = rows(member, "SELECT COUNT(*) FROM post_circles pc JOIN circles c ON c.id = pc.circle_id WHERE c.user_id = :u AND c.kind = 'friends'")
    assert friends[0][0] >= 1


def test_it_off_the_post_is_public_as_before(make_member):
    member = make_member()
    set_columns(member, age_declared=1, age_declared_utc=5)
    made = publish(member, member.browser())
    assert made.status_code == 200 and stored(member, made.get_json()["id"])[0] == 0


def test_a_post_for_guilds_is_refused_with_the_reason(make_member):
    member = private(make_member)
    refused = publish(member, member.browser(), forward_guilds="general")
    assert refused.status_code == 400 and "Private posting is on" in refused.get_data(as_text=True)


def test_a_story_is_for_friends_never_public(make_member):
    member = private(make_member)
    client = member.browser()
    made = post(client, "/api/stories", formkey=member.formkey(client), kind="text", text="a story", audience="0", background="sky")
    assert made.status_code in (200, 201), made.get_data(as_text=True)[:200]
    assert rows(member, "SELECT audience FROM stories WHERE user_id = :u ORDER BY id DESC LIMIT 1")[0][0] == 4


def test_it_cant_be_widened_to_public_afterwards_while_it_is_on(make_member):
    member = private(make_member)
    client = member.browser()
    b36 = publish(member, client).get_json()["id"]
    refused = post(client, f"/api/post/{b36}/audience", formkey=member.formkey(client), audience="0", confirm="1")
    assert refused.status_code == 403 and "Private posting is on" in refused.get_data(as_text=True)
    assert stored(member, b36)[0] == 4


def test_a_forward_and_a_repost_are_refused(make_member, real_app):
    app, _ = real_app
    member, other = private(make_member), make_member()
    set_columns(other, age_declared=1, age_declared_utc=5)
    b36 = publish(other, other.browser()).get_json()["id"]
    client = member.browser()
    for endpoint in ("forward_post", "repost_post"):
        path = app.url_map.bind("localhost").build(endpoint, {"pid": b36} if endpoint == "forward_post" else {"base36id": b36})
        served = post(client, path, formkey=member.formkey(client), board="general")
        assert served.status_code == 403 and "Private posting is on" in served.get_data(as_text=True), endpoint


def test_a_shared_draft_is_refused(make_member):
    member = private(make_member)
    client = member.browser()
    served = post(client, "/collab/new", formkey=member.formkey(client))
    assert served.status_code == 403 and "Private posting is on" in served.get_data(as_text=True)
    free = make_member()
    set_columns(free, age_declared=1, age_declared_utc=5)
    assert post(free.browser(), "/collab/new", formkey=free.formkey(free.browser())).status_code != 403


def test_a_custom_circle_holds_only_mutual_followers_and_a_broken_follow_takes_them_out(make_member):
    owner, friend, stranger = private(make_member), make_member(), make_member()
    client = owner.browser()
    made = post(client, "/api/circles", formkey=owner.formkey(client), name="Family", kind="custom")
    assert made.status_code == 200, made.get_data(as_text=True)[:200]
    cid = rows(owner, "SELECT id FROM circles WHERE user_id = :u AND kind = 'custom' ORDER BY id DESC LIMIT 1")[0][0]
    refused = post(client, f"/api/circles/{cid}/add", formkey=owner.formkey(client), username=stranger.username)
    assert refused.status_code == 400 and "follow you and whom you follow" in refused.get_data(as_text=True).replace("&#39;", "'") or "only hold people" in refused.get_data(as_text=True)
    follow(owner, friend)
    follow(friend, owner)
    assert post(client, f"/api/circles/{cid}/add", formkey=owner.formkey(client), username=friend.username).status_code == 200
    # the friend stops following: they leave the circle without anybody being told
    theirs = friend.browser()
    assert post(theirs, f"/api/unfollow/{owner.username}", formkey=friend.formkey(theirs)).status_code == 204
    assert rows(owner, "SELECT COUNT(*) FROM circle_members WHERE circle_id = :c AND user_id = :f", c=cid, f=friend.id)[0][0] == 0


def test_the_member_turns_it_off_and_on_in_settings(make_member):
    member = private(make_member)
    client = member.browser()
    page = get(client, "/settings/content").get_data(as_text=True)
    assert 'id="private-posting-switch"' in page and " checked" in page.split('id="private-posting-switch"', 1)[1].split(">", 1)[0]
    assert post(client, "/settings/private_posting", formkey=member.formkey(client), on="false").status_code == 200
    assert rows(member, "SELECT private_posting FROM users WHERE id = :u")[0][0] is False
    assert post(client, "/settings/private_posting", formkey=member.formkey(client), on="true").status_code == 200
    assert rows(member, "SELECT private_posting FROM users WHERE id = :u")[0][0] is True


def test_a_standalone_locked_member_may_turn_it_off_a_sub_account_may_not(make_member):
    child = private(make_member)
    set_columns(child, age_declared=2, filter_level=2, age_lock=1)
    client = child.browser()
    assert post(client, "/settings/private_posting", formkey=child.formkey(client), on="false").status_code == 200
    sub = private(make_member)
    set_columns(sub, age_declared=2, filter_level=2, age_lock=2)
    sub_client = sub.browser()
    refused = post(sub_client, "/settings/private_posting", formkey=sub.formkey(sub_client), on="false")
    assert refused.status_code == 403 and rows(sub, "SELECT private_posting FROM users WHERE id = :u")[0][0] is True


def test_the_composer_draws_the_audience_locked_to_friends(make_member):
    member = private(make_member)
    page = get(member.browser(), "/submit").get_data(as_text=True)
    assert "Private posting is on, so this goes only to your Friends circle" in page
    public = page.split('id="post-audience-0"', 1)[1].split(">", 1)[0]
    assert " disabled" in public
