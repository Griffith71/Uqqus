"""Circles, the database side (ruqqus/helpers/circle_store.py): the real SQL on in-memory SQLite (tables as in
schema.sql, tests/circle_tables.py). The promises under test: only an owner changes their own circles, coins move
exactly and never below zero, a subscriber keeps what they paid for, who is in a circle is worked out the same way
everywhere (custom members, paid-up subscribers, mutual follows for Friends), and a block ends it all both ways."""
import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.orm import Session

from circle_tables import add_users, block, create, follow
from ruqqus.helpers import circle_store as store
from ruqqus.helpers import circles as c

NOW = 2_000_000_000
DAY = 86400
OWNER, FAN, POOR, FRIEND, BANNED, DELETED, STRANGER, OTHER = 1, 2, 3, 4, 5, 6, 7, 8


@pytest.fixture
def db():
    engine = create_engine("sqlite://")
    with engine.begin() as conn:
        create(conn)
        add_users(conn, {OWNER: ("owner", 0), FAN: ("fan", 100), POOR: ("poor", 5), FRIEND: ("friend", 0), BANNED: ("banned", 50),
                         DELETED: ("deleted", 50), STRANGER: ("stranger", 50), OTHER: ("other", 50)})
        conn.execute(text("UPDATE users SET is_banned = 1 WHERE id = :i"), {"i": BANNED})
        conn.execute(text("UPDATE users SET is_deleted = 1 WHERE id = :i"), {"i": DELETED})
        conn.execute(text("INSERT INTO boards (id, name, creator_id, is_circle) VALUES (50, 'guild', :o, 1)"), {"o": OTHER})
    session = Session(engine)
    yield session
    session.close()


def coins(db, uid):
    return db.execute(text("SELECT coin_balance FROM users WHERE id = :i"), {"i": uid}).scalar()


def paid(db, price=10, name="Supporters", owner=OWNER):
    cid, error = store.create(db, owner, c.PAID, name, price, NOW)
    assert error is None
    return cid


def custom(db, name="Family", owner=OWNER):
    cid, error = store.create(db, owner, c.CUSTOM, name, 0, NOW)
    assert error is None
    return cid


def tier(db, cid, uid, now=NOW):
    row = store.subscriber_row(db, cid, uid)
    return c.tier_of(row.tier, row.status, row.renews_utc, now) if row else None


def payments(db):
    return db.execute(text("SELECT payer_id, owner_id, coins, kind FROM circle_payments ORDER BY id")).fetchall()


# --- an account's circles -------------------------------------------------------------------------------

def test_friends_is_made_once_the_first_time_it_is_needed(db):
    assert store.friends_circle(db, OWNER) is None
    first = store.ensure_friends(db, OWNER, NOW)
    assert store.ensure_friends(db, OWNER, NOW) == first
    assert store.get(db, first).kind == c.FRIENDS
    assert db.execute(text("SELECT COUNT(*) FROM circles WHERE user_id = :o"), {"o": OWNER}).scalar() == 1


def test_circles_are_listed_friends_first_then_ones_own_then_paid_each_by_name(db):
    paid(db, name="Zoo")
    custom(db, name="work")
    custom(db, name="Art")
    paid(db, name="backers")
    store.ensure_friends(db, OWNER, NOW)
    assert [(r.kind, r.name) for r in store.circles_of(db, OWNER)] == [
        ("friends", ""), ("custom", "Art"), ("custom", "work"), ("paid", "backers"), ("paid", "Zoo")]
    assert [r.name for r in store.paid_circles(db, OWNER)] == ["backers", "Zoo"]
    assert store.circles_of(db, FAN) == []


def test_a_name_is_one_per_owner_ignoring_case(db):
    family = custom(db, name="Family")
    assert store.create(db, OWNER, c.CUSTOM, "FAMILY", 0, NOW) == (None, "You already have a circle with that name.")
    assert store.create(db, OWNER, c.PAID, "family", 5, NOW)[1] == "You already have a circle with that name."
    assert store.create(db, FAN, c.CUSTOM, "Family", 0, NOW)[1] is None                  # someone else's is theirs
    work = custom(db, name="Work")
    assert store.rename(db, OWNER, work, "family") == "You already have a circle with that name."
    assert store.rename(db, OWNER, family, "FAMILY") is None                              # its own name, new case
    assert store.get(db, family).name == "FAMILY"


def test_only_custom_and_paid_circles_are_made_by_hand(db):
    for kind in (c.FRIENDS, c.GUILD_KIND):
        assert store.create(db, OWNER, kind, "x", 0, NOW) == (None, "That kind of circle can't be made.")


def test_a_custom_circle_has_no_price_whatever_is_sent(db):
    cid, _ = store.create(db, OWNER, c.CUSTOM, "Family", 50, NOW)
    assert store.get(db, cid).price_coins == 0


def test_only_the_owner_renames_deletes_or_prices_a_circle(db):
    family, fans = custom(db), paid(db)
    assert store.rename(db, OTHER, family, "Mine") == "That circle isn't yours."
    assert store.delete(db, OTHER, family) == "That circle isn't yours."
    assert store.set_price(db, OTHER, fans, 1) == "That circle isn't yours."
    assert store.set_price(db, OWNER, family, 5) == "That circle isn't yours."            # a custom circle has no price
    assert store.get(db, family).name == "Family" and store.get(db, fans).price_coins == 10
    assert store.owned(db, OTHER, family) is None and store.owned(db, OWNER, family).id == family


def test_friends_keeps_its_name_and_cannot_be_deleted(db):
    friends = store.ensure_friends(db, OWNER, NOW)
    assert store.rename(db, OWNER, friends, "Pals") == "That circle isn't yours."
    assert store.delete(db, OWNER, friends) == "That circle isn't yours."
    assert store.get(db, friends) is not None


def test_a_guild_circle_is_nobodys_to_change_here(db):
    guild = store.set_board_price(db, 50, 5, NOW)
    assert store.owned(db, OTHER, guild) is None
    assert store.delete(db, OTHER, guild) == "That circle isn't yours."


def test_deleting_a_custom_circle_takes_its_people_and_leaves_posts_their_other_circles(db):
    family, work = custom(db, "Family"), custom(db, "Work")
    store.add_member(db, OWNER, family, FAN, NOW)
    store.set_post_circles(db, 100, [family, work])
    store.set_story_circles(db, 7, [family])
    assert store.delete(db, OWNER, family) is None
    assert store.get(db, family) is None
    assert store.post_circle_ids(db, [100]) == {100: {work}}
    assert store.story_circle_ids(db, [7]) == {7: set()}                                    # only its author sees it now
    assert db.execute(text("SELECT COUNT(*) FROM circle_members WHERE circle_id = :c"), {"c": family}).scalar() == 0


def test_a_paid_circle_goes_only_once_nobody_has_paid_days_left_and_its_payments_stay(db):
    fans = paid(db, 10)
    store.subscribe(db, fans, FAN, NOW)
    refusal = store.delete(db, OWNER, fans, NOW + DAY)
    assert refusal and "paid for this circle" in refusal and store.get(db, fans) is not None
    assert store.delete(db, OWNER, fans, NOW + c.PERIOD) is None                            # their paid days are over
    assert store.get(db, fans) is None
    assert payments(db) == [(FAN, OWNER, 10, "subscribe")]
    assert db.execute(text("SELECT circle_id FROM circle_payments")).scalar() is None
    assert store.earnings(db, OWNER) == 10


# --- what a form chose ------------------------------------------------------------------------------------

def test_resolve_takes_friends_and_the_owners_own_circles(db):
    family, fans = custom(db), paid(db)
    ids, error = store.resolve(db, OWNER, ["friends", str(fans), str(family)], NOW)
    assert error is None and ids == sorted([store.friends_circle(db, OWNER).id, fans, family])
    assert store.resolve(db, OWNER, [str(family), str(family)], NOW) == ([family], None)


def test_resolve_refuses_anyone_elses_circle_like_a_made_up_one(db):
    theirs = custom(db, owner=FAN)
    guild = store.set_board_price(db, 50, 5, NOW)
    for token in (str(theirs), str(guild), "999"):
        assert store.resolve(db, OWNER, [token], NOW) == (None, "One of those circles isn't yours any more. Choose again.")


# --- people in a custom circle ---------------------------------------------------------------------------

def test_someone_is_added_and_listed_newest_first(db):
    family = custom(db)
    assert store.add_member(db, OWNER, family, FRIEND, NOW) is None
    assert store.add_member(db, OWNER, family, STRANGER, NOW + 5) is None
    assert [m.username for m in store.members(db, family)] == ["stranger", "friend"]
    assert store.member_counts(db, [family]) == {family: 2}
    assert store.custom_circles_with(db, OWNER, FRIEND) == {family}


def test_adding_twice_or_yourself_or_a_gone_account_is_refused(db):
    family = custom(db)
    assert store.add_member(db, OWNER, family, FRIEND, NOW) is None
    assert "already" in store.add_member(db, OWNER, family, FRIEND, NOW)
    assert "own posts" in store.add_member(db, OWNER, family, OWNER, NOW)
    for gone in (BANNED, DELETED, 999):
        assert "isn't available" in store.add_member(db, OWNER, family, gone, NOW)
    assert len(store.members(db, family)) == 1


def test_a_temporary_ban_is_not_a_gone_account(db):
    db.execute(text("UPDATE users SET is_banned = 1, unban_utc = :u WHERE id = :i"), {"u": NOW + DAY, "i": STRANGER})
    assert store.add_member(db, OWNER, custom(db), STRANGER, NOW) is None


def test_a_block_either_way_stops_someone_being_added(db):
    family = custom(db)
    block(db, OWNER, OTHER)
    block(db, STRANGER, OWNER)
    assert store.add_member(db, OWNER, family, OTHER, NOW) == "You can't add that account."
    assert store.add_member(db, OWNER, family, STRANGER, NOW) == "You can't add that account."


def test_there_is_a_limit_on_people_in_one_circle(db, monkeypatch):
    monkeypatch.setattr(c, "MEMBER_LIMIT", 2)
    family = custom(db)
    assert store.add_member(db, OWNER, family, FRIEND, NOW) is None
    assert store.add_member(db, OWNER, family, STRANGER, NOW) is None
    assert "2 people at most" in store.add_member(db, OWNER, family, OTHER, NOW)
    assert store.add_member(db, OWNER, custom(db, "Work"), OTHER, NOW) is None              # the limit is per circle


def test_only_the_owner_fills_their_own_custom_circles(db):
    family, fans = custom(db), paid(db)
    assert store.add_member(db, OTHER, family, FAN, NOW) == "That circle isn't yours."
    assert store.add_member(db, OWNER, fans, FAN, NOW) == "That circle isn't yours."         # nobody is in a paid one free
    store.add_member(db, OWNER, family, FRIEND, NOW)
    assert store.remove_member(db, OTHER, family, FRIEND) is False
    assert store.remove_member(db, OWNER, family, FRIEND) is True
    assert store.remove_member(db, OWNER, family, FRIEND) is False


def test_removing_from_a_custom_circle_never_touches_a_paying_subscriber(db):
    fans = paid(db)
    store.subscribe(db, fans, FAN, NOW)
    assert store.remove_member(db, OWNER, fans, FAN) is False
    assert tier(db, fans, FAN) == c.SUBSCRIBER


# --- who is in which circle -------------------------------------------------------------------------------

def test_a_custom_member_a_paid_up_subscriber_and_a_mutual_follow_are_in_their_circles(db):
    family, fans = custom(db), paid(db)
    friends = store.ensure_friends(db, OWNER, NOW)
    store.add_member(db, OWNER, family, FRIEND, NOW)
    store.subscribe(db, fans, FAN, NOW)
    follow(db, OWNER, STRANGER)
    follow(db, STRANGER, OWNER)
    follow(db, OWNER, OTHER)                                                                 # one way only
    assert store.viewer_circle_ids(db, OWNER, FRIEND, NOW) == {family}
    assert store.viewer_circle_ids(db, OWNER, FAN, NOW) == {fans}
    assert store.viewer_circle_ids(db, OWNER, FAN, NOW + c.PERIOD) == set()                  # the paid days ran out
    assert store.viewer_circle_ids(db, OWNER, STRANGER, NOW) == {friends}
    assert store.viewer_circle_ids(db, OWNER, OTHER, NOW) == set()
    assert store.viewer_circle_ids(db, OWNER, OWNER, NOW) == set()
    assert store.viewer_circle_ids(db, OWNER, None, NOW) == set()


def test_friends_counts_only_once_its_row_exists_and_follows_both_ways(db):
    follow(db, OWNER, STRANGER)
    follow(db, STRANGER, OWNER)
    assert store.viewer_circle_ids(db, OWNER, STRANGER, NOW) == set()                        # nothing was ever made for it
    friends = store.ensure_friends(db, OWNER, NOW)
    assert store.viewer_circle_ids(db, OWNER, STRANGER, NOW) == {friends}
    assert store.mutual_ids(db, OWNER) == {STRANGER} and store.mutual_count(db, OWNER) == 1


def test_a_block_either_way_takes_someone_out_of_every_circle(db):
    family, fans = custom(db), paid(db)
    store.ensure_friends(db, OWNER, NOW)
    store.add_member(db, OWNER, family, FAN, NOW)
    store.subscribe(db, fans, FAN, NOW)
    follow(db, OWNER, FAN)
    follow(db, FAN, OWNER)
    assert len(store.viewer_circle_ids(db, OWNER, FAN, NOW)) == 3
    block(db, FAN, OWNER)
    assert store.viewer_circle_ids(db, OWNER, FAN, NOW) == set()


def test_who_is_told_is_everyone_in_any_of_the_circles_and_nobody_else(db):
    family, fans = custom(db), paid(db)
    friends = store.ensure_friends(db, OWNER, NOW)
    store.add_member(db, OWNER, family, FRIEND, NOW)
    store.subscribe(db, fans, FAN, NOW)
    follow(db, OWNER, STRANGER)
    follow(db, STRANGER, OWNER)
    assert store.eligible_ids(db, OWNER, [family, fans, friends], NOW) == {FRIEND, FAN, STRANGER}
    assert store.eligible_ids(db, OWNER, [family], NOW) == {FRIEND}
    assert store.eligible_ids(db, OWNER, [fans], NOW + c.PERIOD) == set()
    theirs = custom(db, owner=FAN)
    store.add_member(db, FAN, theirs, OTHER, NOW)
    assert store.eligible_ids(db, OWNER, [theirs], NOW) == set()                             # only the owner's own circles count
    block(db, OWNER, FRIEND)
    assert store.eligible_ids(db, OWNER, [family], NOW) == set()
    assert store.reaches_nobody(db, OWNER, [family], NOW) and not store.reaches_nobody(db, OWNER, [fans], NOW)


def test_what_a_post_or_a_story_is_for_is_kept_and_replaced(db):
    family, work = custom(db, "Family"), custom(db, "Work")
    store.set_post_circles(db, 100, [work, family, family])
    assert store.post_circle_ids(db, [100, 101]) == {100: {family, work}, 101: set()}
    store.set_post_circles(db, 100, [work])
    assert store.post_circle_ids(db, [100]) == {100: {work}}
    store.set_post_circles(db, 100, [])
    assert store.post_circle_ids(db, [100]) == {100: set()}
    store.set_story_circles(db, 9, [family])
    assert store.story_circle_ids(db, [9]) == {9: {family}}


def test_names_put_friends_first_and_call_it_friends(db):
    family, fans = custom(db, "Family"), paid(db, name="Backers")
    friends = store.ensure_friends(db, OWNER, NOW)
    assert store.names_of(db, [fans, family, friends]) == [(friends, c.FRIENDS, "Friends"), (family, c.CUSTOM, "Family"), (fans, c.PAID, "Backers")]
    assert store.names_of(db, [999]) == []


# --- subscribing ------------------------------------------------------------------------------------------

def test_subscribing_moves_exactly_the_price_and_records_it(db):
    fans = paid(db, 10)
    outcome, message = store.subscribe(db, fans, FAN, NOW)
    assert outcome == "subscribed" and "10 coins" in message
    assert (coins(db, FAN), coins(db, OWNER)) == (90, 10)
    row = store.subscriber_row(db, fans, FAN)
    assert (row.tier, row.status, row.renews_utc, row.price_coins, bool(row.cancelled)) == ("subscriber", "active", NOW + c.PERIOD, 10, False)
    assert payments(db) == [(FAN, OWNER, 10, "subscribe")]
    assert db.execute(text("SELECT circle_id FROM circle_payments")).scalar() == fans


def test_coins_are_conserved_across_the_whole_site(db):
    fans = paid(db, 17)
    total = db.execute(text("SELECT SUM(coin_balance) FROM users")).scalar()
    store.subscribe(db, fans, FAN, NOW)
    assert db.execute(text("SELECT SUM(coin_balance) FROM users")).scalar() == total


def test_someone_who_cannot_pay_is_refused_and_nothing_changes(db):
    fans = paid(db, 10)
    outcome, message = store.subscribe(db, fans, POOR, NOW)
    assert outcome == "refused" and "10 coins" in message and "don't have enough" in message
    assert (coins(db, POOR), coins(db, OWNER)) == (5, 0)
    assert store.subscriber_row(db, fans, POOR) is None and payments(db) == []


def test_a_balance_exactly_the_price_is_enough_and_ends_at_zero_never_below(db):
    fans = paid(db, 5)
    assert store.subscribe(db, fans, POOR, NOW)[0] == "subscribed"
    assert coins(db, POOR) == 0
    assert store.subscribe(db, fans, POOR, NOW + 2 * c.PERIOD)[0] == "refused"
    assert coins(db, POOR) == 0


def test_two_paid_circles_are_two_subscriptions(db):
    fans, backers = paid(db, 10, "Fans"), paid(db, 20, "Backers")
    store.subscribe(db, fans, FAN, NOW)
    store.subscribe(db, backers, FAN, NOW)
    assert coins(db, FAN) == 70 and coins(db, OWNER) == 30
    assert store.cancel(db, fans, FAN) is True
    assert tier(db, backers, FAN) == c.SUBSCRIBER and not store.subscriber_row(db, backers, FAN).cancelled


def test_you_cannot_subscribe_to_your_own_or_a_gone_owners_circle(db):
    fans = paid(db)
    outcome, message = store.subscribe(db, fans, OWNER, NOW)
    assert outcome == "refused" and "your own circle" in message
    for gone in (BANNED, DELETED):
        theirs = paid(db, name=f"g{gone}", owner=gone)
        outcome, message = store.subscribe(db, theirs, FAN, NOW)
        assert outcome == "refused" and "isn't available" in message and coins(db, FAN) == 100


def test_only_a_paid_circle_with_a_price_takes_subscribers(db):
    for cid in (paid(db, 0), custom(db), store.ensure_friends(db, OWNER, NOW), 999, store.set_board_price(db, 50, 5, NOW)):
        outcome, message = store.subscribe(db, cid, FAN, NOW)
        assert outcome == "refused" and "isn't taking subscribers" in message
    assert coins(db, FAN) == 100 and payments(db) == []


def test_a_block_either_way_refuses_a_subscription_and_charges_nothing(db):
    fans = paid(db)
    block(db, FAN, OWNER)
    assert store.subscribe(db, fans, FAN, NOW) == ("refused", "You can't subscribe to this circle.")
    assert coins(db, FAN) == 100


def test_subscribing_twice_charges_once(db):
    fans = paid(db)
    store.subscribe(db, fans, FAN, NOW)
    assert store.subscribe(db, fans, FAN, NOW + DAY) == ("refused", "You are already subscribed.")
    assert coins(db, FAN) == 90 and len(payments(db)) == 1


def test_being_in_a_custom_circle_does_not_stop_a_subscription(db):
    family, fans = custom(db), paid(db)
    store.add_member(db, OWNER, family, FAN, NOW)
    assert store.subscribe(db, fans, FAN, NOW)[0] == "subscribed"


# --- cancelling and the paid-to date ------------------------------------------------------------------------

def test_a_cancelled_subscriber_keeps_access_until_the_date_and_is_not_charged_again(db):
    fans = paid(db)
    store.subscribe(db, fans, FAN, NOW)
    assert store.cancel(db, fans, FAN) is True
    assert tier(db, fans, FAN, NOW + 29 * DAY) == c.SUBSCRIBER
    assert tier(db, fans, FAN, NOW + c.PERIOD) is None
    assert store.renew_due(db, NOW + c.PERIOD) == [("ended", FAN, OWNER, 10, None, "Supporters")]
    assert coins(db, FAN) == 90 and len(payments(db)) == 1


def test_cancelling_when_there_is_nothing_to_cancel_does_nothing(db):
    fans, family = paid(db), custom(db)
    assert store.cancel(db, fans, FAN) is False
    store.add_member(db, OWNER, family, FRIEND, NOW)
    assert store.cancel(db, family, FRIEND) is False


def test_subscribing_again_after_cancelling_resumes_without_a_charge(db):
    fans = paid(db)
    store.subscribe(db, fans, FAN, NOW)
    store.cancel(db, fans, FAN)
    assert store.subscribe(db, fans, FAN, NOW + 5 * DAY)[0] == "resumed"
    assert coins(db, FAN) == 90 and len(payments(db)) == 1
    assert not store.subscriber_row(db, fans, FAN).cancelled
    assert store.renew_due(db, NOW + c.PERIOD)[0][0] == "renewed"


def test_a_block_after_cancelling_stops_a_resume(db):
    fans = paid(db)
    store.subscribe(db, fans, FAN, NOW)
    store.cancel(db, fans, FAN)
    block(db, OWNER, FAN)
    assert store.subscribe(db, fans, FAN, NOW + DAY)[0] == "refused"
    assert store.subscriber_row(db, fans, FAN).cancelled


def test_access_ends_at_the_date_even_if_the_renewal_job_never_ran(db):
    fans = paid(db)
    store.subscribe(db, fans, FAN, NOW)
    assert tier(db, fans, FAN, NOW + c.PERIOD - 1) == c.SUBSCRIBER
    assert tier(db, fans, FAN, NOW + c.PERIOD) is None
    assert store.subscriber_row(db, fans, FAN).status == "active"


def test_subscribing_again_after_the_subscription_ended_charges_again(db):
    fans = paid(db)
    store.subscribe(db, fans, FAN, NOW)
    store.cancel(db, fans, FAN)
    store.renew_due(db, NOW + c.PERIOD)
    assert store.subscribe(db, fans, FAN, NOW + 40 * DAY)[0] == "subscribed"
    assert coins(db, FAN) == 80
    assert store.subscriber_row(db, fans, FAN).renews_utc == NOW + 40 * DAY + c.PERIOD


# --- renewals -----------------------------------------------------------------------------------------------

def test_a_due_subscriber_is_renewed_from_their_coins(db):
    fans = paid(db, 10)
    store.subscribe(db, fans, FAN, NOW)
    assert store.renew_due(db, NOW + c.PERIOD - 1) == []
    assert store.renew_due(db, NOW + c.PERIOD) == [("renewed", FAN, OWNER, 10, None, "Supporters")]
    assert (coins(db, FAN), coins(db, OWNER)) == (80, 20)
    assert store.subscriber_row(db, fans, FAN).renews_utc == NOW + 2 * c.PERIOD
    assert [p[3] for p in payments(db)] == ["subscribe", "renew"]


def test_a_subscriber_renews_at_the_price_they_signed_up_for(db):
    fans = paid(db, 10)
    store.subscribe(db, fans, FAN, NOW)
    store.set_price(db, OWNER, fans, 90)                             # the owner raises the price
    store.renew_due(db, NOW + c.PERIOD)
    assert coins(db, FAN) == 80                                       # still 10
    assert store.subscriber_row(db, fans, FAN).price_coins == 10


def test_a_subscriber_who_cannot_pay_lapses_and_is_not_charged(db):
    fans = paid(db, 5)
    store.subscribe(db, fans, POOR, NOW)                              # 5 -> 0
    assert store.renew_due(db, NOW + c.PERIOD) == [("lapsed", POOR, OWNER, 5, None, "Supporters")]
    assert coins(db, POOR) == 0 and tier(db, fans, POOR, NOW + c.PERIOD) is None
    assert store.subscriber_row(db, fans, POOR).status == "ended"


def test_a_block_ends_a_renewal_instead_of_charging(db):
    fans = paid(db)
    store.subscribe(db, fans, FAN, NOW)
    block(db, OWNER, FAN)
    assert store.renew_due(db, NOW + c.PERIOD) == [("ended", FAN, OWNER, 10, None, "Supporters")]
    assert coins(db, FAN) == 90


def test_a_deleted_or_banned_owner_or_payer_ends_a_renewal_instead_of_charging(db):
    """Audit $-03: a deleted owner's circle kept charging its subscribers."""
    from sqlalchemy import text as sql
    for who, column in ((OWNER, "is_deleted"), (FAN, "is_deleted"), (OWNER, "is_banned")):
        fans = paid(db, 10, name=f"Circle {who} {column}")
        store.subscribe(db, fans, FAN, NOW)
        db.execute(sql(f"UPDATE users SET {column} = 1 WHERE id = :u"), {"u": who})
        before = coins(db, FAN)
        events = store.renew_due(db, NOW + c.PERIOD)
        assert events and all(e[0] == "ended" for e in events), (who, column, events)
        assert coins(db, FAN) == before
        db.execute(sql(f"UPDATE users SET {column} = 0 WHERE id = :u"), {"u": who})
        db.execute(sql("DELETE FROM circle_members"))


def test_a_late_renewal_runs_from_now(db):
    fans = paid(db)
    store.subscribe(db, fans, FAN, NOW)
    late = NOW + 10 * c.PERIOD
    store.renew_due(db, late)
    assert store.subscriber_row(db, fans, FAN).renews_utc == late + c.PERIOD


def test_renewals_settle_one_by_one_and_never_touch_custom_members(db):
    fans, family = paid(db, 5), custom(db)
    store.subscribe(db, fans, POOR, NOW)
    store.subscribe(db, fans, FAN, NOW)
    store.add_member(db, OWNER, family, FRIEND, NOW)
    events = store.renew_due(db, NOW + c.PERIOD)
    assert sorted(e[0] for e in events) == ["lapsed", "renewed"]
    assert store.viewer_circle_ids(db, OWNER, FRIEND, NOW + 10 * c.PERIOD) == {family}


def test_the_renewal_job_stops_at_its_limit(db):
    fans = paid(db, 1)
    for uid in (FAN, STRANGER, OTHER):
        store.subscribe(db, fans, uid, NOW)
    assert len(store.renew_due(db, NOW + c.PERIOD, limit=2)) == 2
    assert len(store.renew_due(db, NOW + c.PERIOD, limit=2)) == 1


# --- blocks -------------------------------------------------------------------------------------------------

def test_a_block_takes_each_account_out_of_the_others_circles_with_no_refund(db):
    family, work, fans = custom(db, "Family"), custom(db, "Work"), paid(db)
    store.add_member(db, OWNER, family, FAN, NOW)
    store.add_member(db, OWNER, work, FAN, NOW)
    store.add_member(db, OWNER, family, FRIEND, NOW)
    store.subscribe(db, fans, FAN, NOW)
    theirs = custom(db, "Mates", owner=FAN)                                 # and the other way round
    store.add_member(db, FAN, theirs, OWNER, NOW)
    assert store.end_between(db, OWNER, FAN) == 4
    assert store.viewer_circle_ids(db, OWNER, FAN, NOW) == set() and store.viewer_circle_ids(db, FAN, OWNER, NOW) == set()
    assert store.viewer_circle_ids(db, OWNER, FRIEND, NOW) == {family}     # nobody else is touched
    assert coins(db, FAN) == 90 and coins(db, OWNER) == 10                 # the coins stay where they went


# --- the lists ----------------------------------------------------------------------------------------------

def test_subscribers_and_memberships_list_only_paid_circles_with_access_now(db):
    fans, family = paid(db), custom(db)
    store.add_member(db, OWNER, family, FAN, NOW)
    store.subscribe(db, fans, FAN, NOW)
    store.subscribe(db, fans, STRANGER, NOW - 29 * DAY)
    assert [s.username for s in store.subscribers(db, fans, NOW)] == ["stranger", "fan"]
    assert [s.username for s in store.subscribers(db, fans, NOW + 2 * DAY)] == ["fan"]
    rows = store.memberships(db, FAN, NOW)
    assert [(m.circle_id, m.owner_id, m.username, m.name) for m in rows] == [(fans, OWNER, "owner", "Supporters")]  # never Family
    assert store.memberships(db, FAN, NOW + c.PERIOD) == []


def test_earnings_add_up_what_came_in_since_a_date(db):
    fans, backers = paid(db, 10, "Fans"), paid(db, 3, "Backers")
    store.subscribe(db, fans, FAN, NOW)
    store.subscribe(db, fans, STRANGER, NOW + 5 * DAY)
    store.subscribe(db, backers, FAN, NOW)
    assert store.earnings(db, OWNER) == 23
    assert store.earnings(db, OWNER, since=NOW + DAY) == 10
    assert store.earnings_of(db, backers) == 3
    assert store.earnings(db, FAN) == 0
