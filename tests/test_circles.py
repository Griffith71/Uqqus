"""Circles' rules (ruqqus/helpers/circles.py): who sees what, what a price or an audience may be, who may be added or
subscribe, and what a renewal does. No I/O."""
import itertools

import pytest

from ruqqus.helpers import circles as c

NOW = 2_000_000_000
DAY = 86400
ARABIC_INDIC_FIVE = chr(0x665)


# --- the numbers a form may send ----------------------------------------------------------------------

@pytest.mark.parametrize("raw, value", [("0", 0), ("1", 1), ("30", 30), ("100", 100), (" 7 ", 7), (5, 5), ("007", 7)])
def test_a_price_is_a_whole_number_of_coins_up_to_the_limit(raw, value):
    assert c.parse_price(raw) == (value, None)


@pytest.mark.parametrize("raw", [None, "", "  ", "-1", "1.5", "101", "1000", "abc", "10 coins", ARABIC_INDIC_FIVE, "1e2", "0x10"])
def test_anything_else_is_refused_with_a_message(raw):
    value, message = c.parse_price(raw)
    assert value is None and "coins" in message


def test_an_audience_is_public_unless_one_of_the_three_is_chosen():
    assert c.parse_audience(None) == (c.PUBLIC, None)
    assert c.parse_audience("") == (c.PUBLIC, None)
    assert c.parse_audience("0") == (c.PUBLIC, None)
    assert c.parse_audience("1") == (c.SUBSCRIBERS, None)
    assert c.parse_audience(2) == (c.FRIENDS, None)
    for raw in ("3", "4", "-1", "x", "1.0", ARABIC_INDIC_FIVE, "99"):
        value, message = c.parse_audience(raw)
        assert value is None and "Close Friends" in message, raw        # 3 (a Circle guild) is not a choice on a profile


# --- who counts as in the Circle right now ------------------------------------------------------------

def test_tier_of_follows_status_and_the_paid_to_date():
    assert c.tier_of("friend", "active", 0, NOW) == c.FRIEND
    assert c.tier_of("friend", "ended", 0, NOW) is None
    assert c.tier_of("subscriber", "active", NOW + 1, NOW) == c.SUBSCRIBER
    assert c.tier_of("subscriber", "active", NOW, NOW) is None            # paid to now: out (strictly ahead is in)
    assert c.tier_of("subscriber", "active", NOW - DAY, NOW) is None      # the renewal job is late: still out
    assert c.tier_of("subscriber", "ended", NOW + DAY, NOW) is None
    assert c.tier_of("stranger", "active", NOW + DAY, NOW) is None


# --- who sees what ------------------------------------------------------------------------------------

@pytest.mark.parametrize("audience, tier, seen", [
    (c.PUBLIC, None, True), (c.PUBLIC, c.FRIEND, True), (c.PUBLIC, c.SUBSCRIBER, True),
    (c.SUBSCRIBERS, None, False), (c.SUBSCRIBERS, c.SUBSCRIBER, True), (c.SUBSCRIBERS, c.FRIEND, True),
    (c.FRIENDS, None, False), (c.FRIENDS, c.SUBSCRIBER, False), (c.FRIENDS, c.FRIEND, True),
    (c.GUILD, None, False), (c.GUILD, c.SUBSCRIBER, True), (c.GUILD, c.FRIEND, True),
])
def test_what_each_kind_of_member_sees(audience, tier, seen):
    assert c.can_see(audience, is_author=False, tier=tier) is seen


def test_the_author_always_sees_their_own():
    for audience in (c.PUBLIC, c.SUBSCRIBERS, c.FRIENDS, c.GUILD):
        assert c.can_see(audience, is_author=True, tier=None)


def test_an_audience_nobody_knows_is_seen_by_nobody_but_the_author():
    for audience, tier in itertools.product((9, -1, None, "1", 4), (None, c.FRIEND, c.SUBSCRIBER, "other")):
        assert not c.can_see(audience, is_author=False, tier=tier), (audience, tier)
    assert c.can_see(9, is_author=True, tier=None)


def test_a_subscriber_never_sees_close_friends_only_things():
    assert not c.can_see(c.FRIENDS, is_author=False, tier=c.SUBSCRIBER)


# --- adding a close friend ----------------------------------------------------------------------------

def friend(**kw):
    args = dict(owner_id=1, target_id=2, blocked=False, target_gone=False, friends=0, already=False)
    args.update(kw)
    return c.refusal_to_add_friend(**args)


def test_a_close_friend_can_be_added_unless_there_is_a_reason():
    assert friend() is None
    assert "own Circle" in friend(target_id=1)
    assert friend(target_gone=True) == "That account isn't available."
    assert friend(blocked=True) == "You can't add that account."
    assert "already" in friend(already=True)
    assert friend(friends=c.FRIEND_LIMIT - 1) is None
    assert str(c.FRIEND_LIMIT) in friend(friends=c.FRIEND_LIMIT)


def test_the_refusal_to_add_never_says_who_blocked_whom():
    assert "block" not in friend(blocked=True).lower()
    assert "block" not in sub(blocked=True).lower()
    # the same words whoever blocked, and nothing about which account it was
    assert friend(blocked=True) == "You can't add that account."
    assert sub(blocked=True) == "You can't subscribe to this Circle."


# --- subscribing --------------------------------------------------------------------------------------

def sub(**kw):
    args = dict(owner_id=1, viewer_id=2, price=10, blocked=False, owner_gone=False, tier=None, cancelled=False)
    args.update(kw)
    return c.refusal_to_subscribe(**args)


def test_someone_may_subscribe_to_a_circle_with_a_price():
    assert sub() is None


@pytest.mark.parametrize("kw, word", [
    (dict(viewer_id=1), "own Circle"), (dict(owner_gone=True), "isn't available"), (dict(blocked=True), "can't subscribe"),
    (dict(tier=c.FRIEND), "Close Friends"), (dict(tier=c.SUBSCRIBER), "already subscribed"), (dict(price=0), "isn't taking"),
])
def test_the_reasons_not_to_subscribe(kw, word):
    assert word in sub(**kw)


def test_a_cancelled_subscriber_who_still_has_access_is_not_refused_so_they_can_resume():
    assert sub(tier=c.SUBSCRIBER, cancelled=True) is None


def test_a_circle_with_no_price_refuses_even_after_the_owner_changed_it():
    assert sub(price=0, tier=None) == "This Circle isn't taking subscribers."


# --- renewals -----------------------------------------------------------------------------------------

@pytest.mark.parametrize("renews, cancelled, balance, price, expected", [
    (NOW + 1, False, 0, 10, c.KEEP), (NOW + DAY, True, 0, 10, c.KEEP),
    (NOW, False, 10, 10, c.RENEW), (NOW - DAY, False, 99, 10, c.RENEW),
    (NOW, False, 9, 10, c.LAPSE), (NOW, False, 0, 10, c.LAPSE),
    (NOW, True, 99, 10, c.END), (NOW - 5, True, 0, 10, c.END),
    (NOW, False, 99, 0, c.END),
])
def test_what_a_renewal_does(renews, cancelled, balance, price, expected):
    assert c.renewal(NOW, renews, cancelled, balance, price) == expected


def test_a_late_renewal_counts_from_now_and_an_early_one_from_the_paid_to_date():
    assert c.next_renewal(NOW, NOW - 10 * DAY) == NOW + c.PERIOD
    assert c.next_renewal(NOW, NOW + DAY) == NOW + DAY + c.PERIOD
    assert c.next_renewal(NOW, 0) == NOW + c.PERIOD


def test_the_period_is_thirty_days_and_the_price_cap_is_a_hundred():
    assert c.PERIOD == 30 * DAY and c.PRICE_MAX == 100 and c.FRIEND_LIMIT == 500


def test_a_price_is_described_in_words():
    assert c.describe_price(0) == "Close Friends only"
    assert c.describe_price(1) == "1 coin every 30 days"
    assert c.describe_price(25) == "25 coins every 30 days"


# --- making a post for an audience ----------------------------------------------------------------------

def test_a_public_post_is_never_refused_for_its_audience():
    assert c.post_refusal(c.PUBLIC, anonymous=True, coauthors=True, forwards=True, own_video=True, shared=True) is None


@pytest.mark.parametrize("audience", [c.SUBSCRIBERS, c.FRIENDS])
def test_what_a_circle_post_cannot_be(audience):
    assert c.post_refusal(audience) is None
    assert "anonymous" in c.post_refusal(audience, anonymous=True)
    assert "co-authors" in c.post_refusal(audience, coauthors=True)
    assert "forwarded to a guild" in c.post_refusal(audience, forwards=True)
    assert "already been forwarded or reposted" in c.post_refusal(audience, shared=True)
    assert "YouTube" in c.post_refusal(audience, own_video=True)


def test_the_first_problem_is_the_one_said():
    assert "anonymous" in c.post_refusal(c.FRIENDS, anonymous=True, coauthors=True, forwards=True, own_video=True, shared=True)
    assert "co-authors" in c.post_refusal(c.FRIENDS, coauthors=True, forwards=True, own_video=True)


def test_the_date_is_written_the_way_the_site_writes_it():
    assert c.date_text(0) == "01 January 1970"
    assert c.date_text(NOW) == "18 May 2033"
    assert c.date_text(None) == "01 January 1970"
