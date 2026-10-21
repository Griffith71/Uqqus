"""A member's own account that also posts automatically (helpers/bots.py SELF), on the real app and a real Postgres,
through the runner's real submit (scripts/run_bots.py): automated posts are marked and held to the bot rules, the
member keeps using the account as themselves, their feeds can never get them suspended, and switching it off or a
rejection never touches the account."""
import importlib.util
import secrets
import time
from pathlib import Path
from types import SimpleNamespace

import pytest
from sqlalchemy import text

BASE = "http://localhost"
ROOT = Path(__file__).resolve().parents[2]


def addr():
    return f"10.{secrets.randbelow(250)}.{secrets.randbelow(250)}.{secrets.randbelow(250) + 1}"


@pytest.fixture(scope="module")
def runner(real_app):
    spec = importlib.util.spec_from_file_location("run_bots_under_test", ROOT / "scripts" / "run_bots.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def cool_down(r):
    """The posting throttle escalates per account and address; these tests post many times in a row on purpose."""
    for key in r.scan_iter("throttle:*"):
        r.delete(key)


@pytest.fixture
def automating(make_member, real_app):
    """A member whose own account posts from a feed, approved."""
    from ruqqus.helpers import bot_store, bots
    member = make_member()
    db = member.db_session()
    bot_store.add_bot(db, member.id, member.id, member.username, "My blog.", [], int(time.time()), kind=bots.SELF,
                      proposed_by=member.id)
    bot_store.add_source(db, member.id, "https://example.org/feed.xml", int(time.time()))
    bot_store.review_bot(db, member.id, bots.APPROVED, 1, "", int(time.time()))
    db.commit()
    return member


def row(member):
    from ruqqus.helpers import bot_store
    db = member.db_session()
    db.expire_all()
    return bot_store.bot_of(db, member.id)


def item(title=None):
    return SimpleNamespace(title=title or f"Feed item {secrets.token_hex(6)}", link=f"https://example.org/{secrets.token_hex(8)}")


def post_of(member, post_id):
    from ruqqus.classes import Submission
    db = member.db_session()
    db.expire_all()
    return db.query(Submission).filter_by(id=post_id).first()


def by_hand(member, title=None):
    here = member.browser()
    return here.post("/api/vue/submit", base_url=BASE, headers={"User-Agent": "ci"}, environ_base={"REMOTE_ADDR": addr()},
                     data={"title": title or f"My own words {secrets.token_hex(4)}", "body": "Written by me.",
                           "formkey": member.formkey(here), "post_to_profile": "1"})


def test_a_feed_item_is_posted_as_the_member_and_marked_automated(automating, runner, real_app):
    from ruqqus.__main__ import r
    cool_down(r)
    answer = runner.submit(row(automating), item(), [])
    assert answer.kind == "posted", answer
    post = post_of(automating, answer.post_id)
    assert post.author_id == automating.id and post.is_bot is True
    assert automating.user().is_bot is False, "the account stays a person's account"
    assert row(automating).posts_today == 1


def test_the_member_still_posts_comments_and_votes_as_themselves(automating, runner, real_app):
    from ruqqus.__main__ import r
    from ruqqus.helpers.base36 import base36decode, base36encode
    cool_down(r)
    mine = by_hand(automating)
    assert mine.status_code == 200, mine.get_data(as_text=True)[:300]
    own_id = base36decode(mine.get_json()["id"])
    assert post_of(automating, own_id).is_bot is False, "a post made by hand carries no mark"
    assert row(automating).posts_today == 0, "the member's own posts never count towards the automated cap"
    cool_down(r)
    here = automating.browser()
    said = here.post("/api/comment", base_url=BASE, headers={"User-Agent": "ci"}, environ_base={"REMOTE_ADDR": addr()},
                     data={"parent_fullname": f"t2_{base36encode(own_id)}", "body": "A comment of mine.",
                           "formkey": automating.formkey(here)})
    assert said.status_code == 200, "a bot account may not comment; this person may"
    voted = here.post(f"/api/vote/post/{base36encode(own_id)}/0", base_url=BASE,
                      environ_base={"REMOTE_ADDR": addr()}, data={"formkey": automating.formkey(here)})
    assert voted.status_code < 400, "a bot account may not vote; this person may"


def test_automated_posts_are_held_to_the_cap_and_a_header_alone_changes_nothing(automating, runner, make_member, real_app):
    from ruqqus.__main__ import r
    db = automating.db_session()
    from ruqqus.helpers import bots
    db.execute(text("UPDATE bots SET day = :d, posts_today = :n WHERE user_id = :u"),
               {"d": bots.utc_day(), "n": bots.DAILY_CAP, "u": automating.id})
    db.commit()
    cool_down(r)
    assert runner.submit(row(automating), item(), []).kind == "wait"
    cool_down(r)
    assert by_hand(automating).status_code == 200, "the cap is the feeds', never the person's"

    # someone with no automated posting who sends the header: only marked, as before, and no cap or approval asked
    other = make_member()
    here = other.browser()
    cool_down(r)
    sent = here.post("/api/vue/submit", base_url=BASE, headers={"User-Agent": "ci", "X-User-Type": "Bot"},
                     environ_base={"REMOTE_ADDR": addr()},
                     data={"title": f"Scripted {secrets.token_hex(4)}", "body": "x", "formkey": other.formkey(here),
                           "post_to_profile": "1"})
    assert sent.status_code == 200


def test_a_repeating_feed_is_refused_and_never_suspends_the_member(automating, runner, real_app):
    from ruqqus.__main__ import r
    outcomes = []
    for n in range(30):
        cool_down(r)
        outcomes.append(runner.submit(row(automating), item("Weekly update from the blog number 1"), []).kind)
        if outcomes[-1] != "posted":
            break
    assert outcomes[-1] == "duplicate", outcomes          # the item is marked seen and never tried again
    assert outcomes.count("posted") >= 5
    member = automating.user()
    assert member.is_banned == 0 and not member.is_suspended
    from ruqqus.classes import Submission
    db = automating.db_session()
    assert db.query(Submission).filter_by(author_id=automating.id, is_banned=True).count() == 0, "nothing was removed"
    cool_down(r)
    assert by_hand(automating, "Weekly update from the blog number 1").status_code == 200, \
        "the member's own posts are judged on their own: the feed's posts don't count against them"


def test_the_members_own_similar_posts_still_trip_the_guard(automating, real_app):
    from ruqqus.__main__ import r
    for n in range(40):
        cool_down(r)
        answer = by_hand(automating, "Buy my thing now at the shop number 7")
        if answer.status_code != 200:
            break
    assert automating.user().is_suspended, "the old rule still holds for what the person posts by hand"


def test_switching_it_off_leaves_the_account_signed_in_and_its_posts(automating, runner, real_app):
    from ruqqus.__main__ import r
    cool_down(r)
    posted = runner.submit(row(automating), item(), [])
    here = automating.browser()
    page = here.get(f"/settings/bots/{automating.username}", base_url=BASE)
    assert page.status_code == 200 and "Stop posting automatically" in page.get_data(as_text=True)
    assert "Picture and banner" not in page.get_data(as_text=True)
    for path in (f"/settings/bots/{automating.username}/image/picture", f"/settings/bots/{automating.username}/keys/1/revoke"):
        assert here.post(path, base_url=BASE, data={"formkey": automating.formkey(here)}).status_code == 404, path
    gone = here.post(f"/settings/bots/{automating.username}/delete", base_url=BASE, data={"formkey": automating.formkey(here)})
    assert gone.status_code == 302
    assert row(automating) is None
    user = automating.user()
    assert user.is_deleted is False and user.is_bot is False
    assert here.get("/settings/bots", base_url=BASE).status_code == 200, "still signed in"
    assert post_of(automating, posted.post_id).deleted_utc == 0, "its posts stay"


def test_a_rejection_never_deletes_the_account(automating, make_member, real_app):
    from ruqqus.helpers import bots
    db = automating.db_session()
    db.execute(text("UPDATE bots SET status = :p WHERE user_id = :u"), {"p": bots.PENDING, "u": automating.id})
    db.commit()
    admin = make_member()
    adb = admin.db_session()
    adb.execute(text("UPDATE users SET admin_level = 3 WHERE id = :u"), {"u": admin.id})
    adb.commit()
    here = admin.browser()
    answer = here.post(f"/admin/bots/{automating.id}/reject", base_url=BASE,
                       data={"formkey": admin.formkey(here), "note": "Not now."})
    assert answer.status_code == 302
    user = automating.user()
    assert user is not None and user.is_deleted is False
    assert row(automating).status == bots.REJECTED
    for kind in ("member", "site", "self"):
        changed = here.post(f"/admin/bots/{automating.id}/kind", base_url=BASE, data={"formkey": admin.formkey(here), "kind": kind})
        assert changed.status_code == 404, kind


def test_deleting_the_account_ends_it(automating, real_app):
    from ruqqus.helpers import account_cleanup
    db = automating.db_session()
    assert account_cleanup.close_account(db, automating.id, int(time.time())) == []
    db.commit()
    assert row(automating) is None
    assert db.execute(text("SELECT COUNT(*) FROM bot_sources WHERE bot_id = :u"), {"u": automating.id}).scalar() == 0
