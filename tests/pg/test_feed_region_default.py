"""No region is applied unless the member chose it, on the real app and Postgres (routes/front.py `_resolve_active_filters`; the owner,
2026-10-30: "If no filter is selected under region, it should show all"). An account's own region decides which posts a Regional filter
matches, never that one is on."""
import secrets
import sys

import pytest
from sqlalchemy import text

BASE = "http://localhost"


def addr():
    return f"10.{secrets.randbelow(250)}.{secrets.randbelow(250)}.{secrets.randbelow(250) + 1}"


def call(client, method, path, **kw):
    return getattr(client, method)(path, base_url=BASE, headers={"User-Agent": "ci-region-default"},
                                   environ_base={"REMOTE_ADDR": addr()}, **kw)


def based_in(member, code):
    db = member.db_session()
    db.execute(text("UPDATE users SET display_region = :r WHERE id = :u"), {"r": code, "u": member.id})
    db.commit()


def write(member, title):
    here = member.browser()
    answer = call(here, "post", "/api/vue/submit", data={"title": title, "body": "Some words.", "post_to_profile": "1",
                                                          "formkey": member.formkey(here)})
    assert answer.status_code == 200, answer.get_data(as_text=True)[:200]


def page(client, path="/all?sort=new"):
    answer = call(client, "get", path)
    assert answer.status_code == 200, (path, answer.status_code)
    return answer.get_data(as_text=True)


@pytest.fixture
def scene(make_member, real_app):
    """Two authors in two regions with one post each, and a reader based in the first region who has chosen nothing."""
    front = sys.modules["ruqqus.routes.front"]
    here, there = sorted(front.REGION_CENTROIDS)[:2]
    tag = secrets.token_hex(4)
    near, far, reader = make_member(), make_member(), make_member()
    based_in(near, here)
    based_in(far, there)
    based_in(reader, here)
    write(near, f"Near post {tag}")
    write(far, f"Far post {tag}")
    return {"here": here, "there": there, "near": f"Near post {tag}", "far": f"Far post {tag}", "reader": reader,
            "near_id": near.id, "far_id": far.id}


def test_a_member_with_a_home_region_and_no_choice_sees_every_region(scene):
    html = page(scene["reader"].browser())
    assert scene["near"] in html and scene["far"] in html
    assert "Showing posts from" not in html


def test_choosing_a_region_narrows_and_clearing_it_shows_everything_again(scene):
    here = scene["reader"].browser()
    call(here, "get", f"/regional?region={scene['there']}")
    narrowed = page(here)
    assert scene["far"] in narrowed and scene["near"] not in narrowed and "Showing posts from" in narrowed
    call(here, "get", "/regional?region=")             # Deselect all + Apply: this used to put the home region back
    cleared = page(here)
    assert scene["near"] in cleared and scene["far"] in cleared and "Showing posts from" not in cleared


def test_choosing_the_home_region_itself_still_narrows_to_it(scene):
    here = scene["reader"].browser()
    call(here, "get", f"/regional?region={scene['here']}")
    html = page(here)
    assert scene["near"] in html and scene["far"] not in html


def test_a_saved_default_region_applies_to_a_new_session_and_saving_none_means_all(scene):
    reader = scene["reader"]
    here = reader.browser()
    page(here)                                          # opens the session; any saved default is loaded once
    call(here, "get", f"/regional?region={scene['there']}")
    saved = call(here, "post", "/settings/feed_defaults", data={"action": "save", "formkey": reader.formkey(here)})
    assert saved.status_code in (200, 302)
    fresh = page(reader.browser())                      # a new session: the default is loaded into it
    assert scene["far"] in fresh and scene["near"] not in fresh
    again = reader.browser()
    page(again)
    call(again, "get", "/regional?region=")
    call(again, "post", "/settings/feed_defaults", data={"action": "save", "formkey": reader.formkey(again)})
    everything = page(reader.browser())                 # saved with no region: all regions, not "my home region"
    assert scene["near"] in everything and scene["far"] in everything


def test_following_is_not_narrowed_to_the_home_region_either(scene):
    reader = scene["reader"]
    db = reader.db_session()
    for author in (scene["near_id"], scene["far_id"]):
        db.execute(text("INSERT INTO follows (user_id, target_id, created_utc) VALUES (:a, :b, 1)"), {"a": reader.id, "b": author})
    db.commit()
    html = page(reader.browser(), "/following?sort=new")
    assert scene["near"] in html and scene["far"] in html
