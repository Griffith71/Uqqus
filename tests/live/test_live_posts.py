"""Characterisation of posting, editing and tipping on the running site (audit T-01, T-02). See conftest.py."""
import concurrent.futures
import re
import time
import uuid

import pytest

from tests.live.conftest import BASE


def _coins(member):
    page = member.get("/settings/premium").text
    return int(re.search(r"Current balance: <span[^>]*>(\d+) Coin", page).group(1))


def _post(member, title, body):
    r = member.post("/api/vue/submit", {"title": title, "body": body})
    assert r.status_code == 200, r.text[:300]
    data = r.json()
    return data.get("post_id") or data.get("id") or data["redirect"].rstrip("/").split("/")[-2]


def _page(member, pid):
    r = member.s.get(f"{BASE}/post/{pid}")                       # follows the redirect to the post's own address
    assert r.status_code == 200, r.status_code
    return r.text


def test_an_edit_changes_the_text_marks_it_edited_and_keeps_history(alice, bob):
    marker = uuid.uuid4().hex[:8]
    pid = _post(alice, f"Live edit check {marker}", "First text.")
    assert "First text." in _page(alice, pid)

    r = alice.post(f"/edit_post/{pid}", {"title": f"Live edit check {marker} edited", "body": "Second text."})
    assert r.status_code in (200, 302), r.text[:300]                # JSON for scripts, a redirect for the form
    page = _page(bob, pid)
    assert f"Live edit check {marker} edited" in page and "Second text." in page and "First text." not in page

    # an edit in the first 90 seconds is part of writing the post: no history row, not marked edited
    history = alice.get(f"/post/{pid}/history")
    assert history.status_code == 200 and "First text." not in history.text

    # a later edit keeps the text it replaced, publicly (CLAUDE.md "Logs")
    time.sleep(92)
    r = alice.post(f"/edit_post/{pid}", {"body": "Third text."})
    assert r.status_code in (200, 302), r.text[:300]
    history = bob.get(f"/post/{pid}/history")
    assert history.status_code == 200 and "Second text." in history.text
    assert "Third text." in _page(bob, pid)

    # nobody else edits it
    assert bob.post(f"/edit_post/{pid}", {"body": "Not mine."}).status_code in (403, 404)
    assert "Not mine." not in _page(alice, pid) and "Third text." in _page(alice, pid)


def test_an_edit_that_breaks_a_rule_changes_nothing(alice):
    pid = _post(alice, f"Live edit refusal {uuid.uuid4().hex[:8]}", "Kept.")
    r = alice.post(f"/edit_post/{pid}", {"title": "", "body": "Changed."})
    assert r.status_code >= 400
    page = _page(alice, pid)
    assert "Kept." in page and "Changed." not in page


def test_parallel_tips_never_spend_coins_twice(alice, bob):
    """Six tips at once, each of the whole balance: one can go through, never two ($-01: they all did, creating
    coins). Few requests, so the tip rate limit and the address ban (30 refusals in a row) are never reached."""
    pid = _post(alice, f"Live tip check {uuid.uuid4().hex[:8]}", "Tip me.")
    start = _coins(bob)
    if start < 1:
        pytest.skip("user B needs at least one coin")
    amount = min(start, 1000)
    with concurrent.futures.ThreadPoolExecutor(max_workers=6) as pool:
        codes = list(pool.map(lambda _: bob.post(f"/gift_post/{pid}?coins={amount}").status_code, range(6)))
    time.sleep(1)
    end = _coins(bob)
    assert end >= 0
    assert codes.count(200) <= start // amount
    assert end == start - codes.count(200) * amount         # every coin that left was one tip that answered 200
