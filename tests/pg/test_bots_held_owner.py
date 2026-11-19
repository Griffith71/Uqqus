"""A member whose account is held (the signup guard's hold, or an automatic check's: helpers/quarantine.py, helpers/auto_quarantine.py) can't run a member bot or their
own account's automated posting, on the real app and Postgres, through the runner's real submit (scripts/run_bots.py). A bot account is exempt from a hold
(`User.is_quarantined`), so the member's is what stops it:

- the runner skips the source (`owner_ok`), so nothing is submitted and **no item is marked seen**: the feed's items are posted once the hold is lifted;
- if the hold lands after that check, the route's own refusal is read as a wait (a member's own account: `@not_quarantined`; a bot account: `bots.post_refusal`), never as
  a refusal that burns the item, and nothing is paused."""
import importlib.util
import email.utils
import secrets
import time
from pathlib import Path

import pytest
from sqlalchemy import text

ROOT = Path(__file__).resolve().parents[2]
FEED = "https://example.org/feed.xml"


@pytest.fixture(scope="module")
def runner(real_app):
    spec = importlib.util.spec_from_file_location("run_bots_held_owner", ROOT / "scripts" / "run_bots.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def cool_down(r):
    """The posting throttle escalates per account and address; these tests post a few times in a row."""
    for key in r.scan_iter("throttle:*"):
        r.delete(key)


def approved(db, account_id, owner_id, username, kind):
    from ruqqus.helpers import bot_store, bots
    now = int(time.time())
    bot_store.add_bot(db, account_id, owner_id, username, "A feed of things.", [], now, kind=kind, proposed_by=owner_id)
    bot_store.add_source(db, account_id, FEED, now)
    bot_store.review_bot(db, account_id, bots.APPROVED, 1, "", now)
    db.commit()
    assert bot_store.bot_of(db, account_id).status == bots.APPROVED


@pytest.fixture
def own_account(make_member, real_app):
    """A member whose own account posts from a feed (SELF): (the member, the account that posts)."""
    from ruqqus.helpers import bots
    member = make_member()
    approved(member.db_session(), member.id, member.id, member.username, bots.SELF)
    return member, member


@pytest.fixture
def member_bot(make_member, real_app):
    """A member bot: (the member who runs it, its own bot account)."""
    from ruqqus.helpers import bots
    owner, account = make_member(), make_member()
    db = owner.db_session()
    db.execute(text("UPDATE users SET is_bot = true, bot_owner_id = :o WHERE id = :u"), {"o": owner.id, "u": account.id})
    db.commit()
    approved(db, account.id, owner.id, account.username, bots.MEMBER)
    return owner, account


def hold(real_app, member, kind):
    """`automatic`: an automatic check's hold (kind 1, only an admin or an appeal lifts it); `signup`: the signup guard's (kind 0)."""
    from ruqqus.classes import User
    from ruqqus.helpers import auto_quarantine
    _, db_session = real_app
    db = db_session()
    db.rollback()
    if kind == "automatic":
        assert auto_quarantine.hold(db, db.get(User, member.id), "spam", detail="six similar posts in a minute", notify=False) is not None
    else:
        db.execute(text("UPDATE users SET quarantined_utc = :now, quarantine_kind = 0 WHERE id = :u"), {"now": int(time.time()), "u": member.id})
    db.commit()
    db.expire_all()
    assert member.user().is_quarantined


def lift(real_app, member, kind):
    from ruqqus.helpers import auto_quarantine
    _, db_session = real_app
    db = db_session()
    if kind == "automatic":
        assert auto_quarantine.release(db, member.id, admin_id=None, notify=False)
    else:
        db.execute(text("UPDATE users SET quarantined_utc = 0 WHERE id = :u"), {"u": member.id})
    db.commit()
    db.expire_all()
    assert not member.user().is_quarantined


def source_of(db, account_id):
    return db.execute(text("SELECT id FROM bot_sources WHERE bot_id = :u"), {"u": account_id}).scalar()


def seen(db, source_id):
    return db.execute(text("SELECT COUNT(*) FROM bot_seen WHERE source_id = :s"), {"s": source_id}).scalar()


def posts_by(db, account_id):
    return db.execute(text("SELECT COUNT(*) FROM submissions WHERE author_id = :u AND COALESCE(is_banned, false) = false AND deleted_utc = 0"), {"u": account_id}).scalar()


def fetcher(*titles):
    """A fetch that answers one RSS feed with these items, all published a moment ago."""
    from ruqqus.helpers.feed_fetch import Answer
    now = int(time.time())
    items = "".join(f"<item><title>{title}</title><link>https://example.org/{secrets.token_hex(8)}</link><guid>{secrets.token_hex(8)}</guid>"
                    f"<pubDate>{email.utils.formatdate(now - n, usegmt=True)}</pubDate></item>" for n, title in enumerate(titles))
    body = f"<rss><channel><title>Things</title>{items}</channel></rss>".encode()
    return lambda url, etag, last_modified: Answer(True, body, "application/rss+xml", '"e"', "", url)


def run(runner, real_app, account, fetch, owner_ok=None):
    from ruqqus.helpers import bot_runner
    _, db_session = real_app
    db = db_session()
    return bot_runner.run_source(db, source_of(db, account.id), int(time.time()), fetch, runner.submit, runner.notify, owner_ok or runner.owner_ok)


def row(real_app, account):
    from ruqqus.helpers import bot_store
    _, db_session = real_app
    db = db_session()
    db.expire_all()
    return bot_store.bot_of(db, account.id)


def item():
    from types import SimpleNamespace
    return SimpleNamespace(title=f"Feed item {secrets.token_hex(6)}", link=f"https://example.org/{secrets.token_hex(8)}")


@pytest.mark.parametrize("kind", ["automatic", "signup"])
@pytest.mark.parametrize("which", ["own_account", "member_bot"])
def test_a_held_owner_stops_the_feeds_posting_and_no_item_is_lost(which, kind, request, runner, real_app):
    from ruqqus.__main__ import r
    owner, account = request.getfixturevalue(which)
    _, db_session = real_app
    db = db_session()
    cool_down(r)
    assert runner.owner_ok(owner.id)

    hold(real_app, owner, kind)
    assert not runner.owner_ok(owner.id), "the runner asks for the hold too"
    assert run(runner, real_app, account, fetcher("First", "Second")) == "owner"
    db.expire_all()
    assert seen(db, source_of(db, account.id)) == 0, "no item was marked seen: the feed's items wait for the hold to be lifted"
    assert posts_by(db, account.id) == 0
    assert row(real_app, account).status == "approved" and row(real_app, account).posts_today == 0
    error = db.execute(text("SELECT last_error, fail_count, read_once FROM bot_sources WHERE bot_id = :u"), {"u": account.id}).one()
    assert error.last_error == "The bot's owner can't run it right now." and error.fail_count == 0 and not error.read_once

    lift(real_app, owner, kind)
    cool_down(r)
    assert runner.owner_ok(owner.id)
    done = run(runner, real_app, account, fetcher("First", "Second"))
    assert done.startswith("read") and done.endswith("2 posted"), done
    db.expire_all()
    assert posts_by(db, account.id) == 2


@pytest.mark.parametrize("kind", ["automatic", "signup"])
def test_a_hold_that_lands_after_the_runners_check_is_a_wait_for_an_own_account(kind, own_account, runner, real_app):
    from ruqqus.__main__ import r
    from ruqqus.helpers import quarantine
    member, account = own_account
    _, db_session = real_app
    db = db_session()
    cool_down(r)
    hold(real_app, member, kind)

    answer = runner.submit(row(real_app, account), item(), [])
    assert answer.kind == "wait", answer
    assert answer.message == (quarantine.HELD_AUTOMATIC if kind == "automatic" else quarantine.HELD)
    assert posts_by(db, account.id) == 0

    # the whole run, as if the check had passed just before the hold: the item is not burned and the automated posting is not paused
    assert run(runner, real_app, account, fetcher("Kept"), owner_ok=lambda _: True).startswith("read")
    db.expire_all()
    assert seen(db, source_of(db, account.id)) == 0
    assert row(real_app, account).status == "approved"
    assert db.execute(text("SELECT COUNT(*) FROM notifications WHERE user_id = :u"), {"u": member.id}).scalar() == 0, "nobody is told it was paused"

    lift(real_app, member, kind)
    cool_down(r)
    assert runner.submit(row(real_app, account), item(), []).kind == "posted"


@pytest.mark.parametrize("kind", ["automatic", "signup"])
def test_a_member_bots_own_account_is_refused_while_its_owner_is_held_and_that_is_a_wait(kind, member_bot, runner, real_app):
    from ruqqus.__main__ import r
    from ruqqus.helpers import bots
    owner, account = member_bot
    _, db_session = real_app
    db = db_session()
    cool_down(r)
    assert account.user().is_quarantined is False, "a bot account is exempt from a hold"
    hold(real_app, owner, kind)
    assert account.user().is_quarantined is False

    answer = runner.submit(row(real_app, account), item(), [])
    assert answer.kind == "wait" and answer.message == bots.OWNER_HELD, answer
    assert posts_by(db, account.id) == 0
    assert run(runner, real_app, account, fetcher("Kept"), owner_ok=lambda _: True).startswith("read")
    db.expire_all()
    assert seen(db, source_of(db, account.id)) == 0 and row(real_app, account).status == "approved"

    lift(real_app, owner, kind)
    cool_down(r)
    assert runner.submit(row(real_app, account), item(), []).kind == "posted"


def test_an_owner_who_is_not_held_is_not_stopped_and_an_admin_is_never_held(own_account, runner, real_app):
    member, account = own_account
    assert runner.owner_ok(member.id)
    _, db_session = real_app
    db = db_session()
    db.execute(text("UPDATE users SET quarantined_utc = :now, admin_level = 3 WHERE id = :u"), {"now": int(time.time()), "u": member.id})
    db.commit()
    db.expire_all()
    assert not member.user().is_quarantined and runner.owner_ok(member.id)


def test_an_owner_under_the_age_lock_is_skipped_by_the_runner_as_the_posting_rule_refuses_them(own_account, runner, real_app):
    """`bots.post_refusal` refuses a locked owner; the runner asks the same before it posts, so the source waits (its items stay unseen) instead of being tried and refused."""
    member, account = own_account
    assert runner.owner_ok(member.id)
    _, db_session = real_app
    db = db_session()
    db.execute(text("UPDATE users SET age_declared = 2, age_lock = 1, filter_level = 2, hide_offensive = true, all_shows_sensitive = false WHERE id = :u"), {"u": member.id})
    db.commit()
    db.expire_all()
    assert not runner.owner_ok(member.id)
    assert run(runner, real_app, account, fetcher("Kept")) == "owner"
    db.expire_all()
    assert seen(db, source_of(db, account.id)) == 0
