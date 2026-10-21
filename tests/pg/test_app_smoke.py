"""The real Flask app against a real Postgres: the pages that matter answer without a server error, for a visitor and for
a member. It catches a crash in a template or a query that only Postgres rejects, on pages the SQLite tests never draw.
Nothing is stubbed: the app, the models, the templates, Redis and the database are the real ones; only Matrix and the
object store are never called by a page that is merely read."""
import secrets
import time

import pytest


@pytest.fixture(scope="session")
def site(real_app):
    app, db_session = real_app
    from ruqqus.classes import User
    from ruqqus.helpers.security import generate_hash

    db = db_session()
    now = int(time.time())
    member = db.query(User).filter_by(username="ci_member").first()          # a database used twice keeps its member
    if member is None:
        member = User(username="ci_member", original_username="ci_member", password="ci-password-" + secrets.token_hex(4),
                      email="ci_member@example.com", created_utc=now - 90 * 86400, creation_ip="127.0.0.1",
                      tos_agreed_utc=now - 90 * 86400, following_visibility=0, followers_visibility=0,
                      subscribers_visibility=0, subscriptions_visibility=0)
        db.add(member)
        db.commit()
    member_id, nonce = member.id, member.login_nonce

    def as_member():
        client = app.test_client()
        session_id = secrets.token_hex(16)
        with client.session_transaction() as session:
            session["user_id"] = member_id
            session["login_nonce"] = nonce
            session["session_id"] = session_id
        client.formkey = generate_hash(f"{session_id}+{member_id}+{nonce}")
        return client

    poster = as_member()
    answer = poster.post("/api/vue/submit", base_url="http://localhost", environ_base={"REMOTE_ADDR": "203.0.113.7"},
                         headers={"User-Agent": "ci-smoke"},
                         data={"title": "A post made by the smoke test", "body": "Some text.", "formkey": poster.formkey,
                               "post_to_profile": "1"})
    assert answer.status_code == 200, (answer.status_code, answer.get_data(as_text=True)[:300])
    post_id = answer.get_json()["id"]

    from ruqqus.classes import Comment
    from ruqqus.helpers.base36 import base36decode, base36encode

    def comment(text):
        said = poster.post("/api/comment", base_url="http://localhost", environ_base={"REMOTE_ADDR": "203.0.113.7"},
                           headers={"User-Agent": "ci-smoke"},
                           data={"parent_fullname": f"t2_{post_id}", "body": text, "formkey": poster.formkey})
        assert said.status_code == 200, (said.status_code, said.get_data(as_text=True)[:300])   # the answer is drawn HTML
        return db_session().query(Comment).filter_by(parent_submission=base36decode(post_id)).order_by(Comment.id.desc()).first()

    comment_id, gone = base36encode(comment("A comment made by the smoke test").id), comment("A comment about to be deleted")
    gone.deleted_utc = now
    removed_id = base36encode(gone.id)
    db_session().commit()

    class Site:
        pass
    s = Site()
    s.app, s.post_id, s.comment_id, s.removed_id, s.as_member = app, post_id, comment_id, removed_id, as_member
    s.visitor = app.test_client
    return s


def get(client, path):
    return client.get(path, base_url="http://localhost", headers={"User-Agent": "ci-smoke"})


PUBLIC_PAGES = ["/", "/all", "/forgot", "/login", "/signup", "/robots.txt", "/spaces", "/browse", "/curations", "/search?q=smoke", "/help/terms",
                "/help/rules", "/+general", "/@ci_member", "/log/deleted", "/trending"]


# Not listed: /chat and the call and Space windows, which talk to Matrix (Synapse) while they draw.
def test_the_health_checks_answer(site):
    client = site.visitor()
    assert get(client, "/healthz").status_code == 200
    ready = get(client, "/readyz")
    assert ready.status_code == 200, ready.get_data(as_text=True)


@pytest.mark.parametrize("path", PUBLIC_PAGES)
def test_a_visitor_can_open(site, path):
    response = get(site.visitor(), path)
    assert response.status_code < 500, (path, response.status_code)


@pytest.mark.parametrize("path", PUBLIC_PAGES + ["/settings/profile", "/settings/security", "/settings/content",
                                                  "/settings/blocks", "/circles", "/studio", "/submit",
                                                  "/notifications", "/history"])
def test_a_member_can_open(site, path):
    response = get(site.as_member(), path)
    assert response.status_code < 500, (path, response.status_code)


def test_a_post_and_its_embeds_render(site):
    for client in (site.visitor(), site.as_member()):
        for path in (f"/post/{site.post_id}", f"/embed/post/{site.post_id}"):
            response = get(client, path)
            assert response.status_code < 500, (path, response.status_code)
    page = get(site.visitor(), f"/post/{site.post_id}")
    assert page.status_code in (200, 301, 302), page.status_code


def test_comment_embeds_render_for_anyone_and_a_deleted_one_is_gone(site):
    """Embeds are drawn for no viewer at all (v=None); they once crashed on a name the template expected."""
    visitor = site.visitor()
    for path in (f"/embed/comment/{site.comment_id}", f"/embed/post/{site.post_id}/comment/{site.comment_id}"):
        assert get(visitor, path).status_code == 200, path
    assert get(visitor, f"/embed/comment/{site.removed_id}").status_code == 410


def test_name_lookups_match_only_the_name_typed(site):
    """get_user / get_guild use ILIKE: an underscore in a real name must still resolve (it is escaped), and a wildcard
    typed into the address must not match somebody."""
    visitor = site.visitor()
    assert get(visitor, "/@ci_member").status_code == 200
    assert get(visitor, "/@CI_MEMBER").status_code == 200                    # any case
    for path in ("/@ci%25", "/@ci_memb_r", "/@%25", "/+gener%25", "/+gener_l"):
        assert get(visitor, path).status_code == 404, path
