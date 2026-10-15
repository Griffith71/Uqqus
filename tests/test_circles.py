"""Circles' rules (ruqqus/helpers/circles.py): who sees what, what a price, a name or an audience may be, who may be
added, subscribe or make a circle, and what a renewal does. No I/O."""
import pytest

from ruqqus.helpers import circles as c

NOW = 2_000_000_000
DAY = 86400
ARABIC_INDIC_FIVE = chr(0x665)
RTL_OVERRIDE = chr(0x202E)
ZERO_WIDTH = chr(0x200B)
JOINER = chr(0x200D)


# --- the numbers and names a form may send ----------------------------------------------------------------

@pytest.mark.parametrize("raw, value", [("0", 0), ("1", 1), ("30", 30), ("100", 100), (" 7 ", 7), (5, 5), ("007", 7)])
def test_a_price_is_a_whole_number_of_coins_up_to_the_limit(raw, value):
    assert c.parse_price(raw) == (value, None)


@pytest.mark.parametrize("raw", [None, "", "  ", "-1", "1.5", "101", "1000", "abc", "10 coins", ARABIC_INDIC_FIVE, "1e2", "0x10"])
def test_anything_else_is_refused_with_a_message(raw):
    value, error = c.parse_price(raw)
    assert value is None and error


def test_a_circle_name_is_plain_text_with_spaces_collapsed():
    assert c.parse_name("  Uni\tfriends   in Spain ") == ("Uni friends in Spain", None)
    assert c.parse_name("Family " + chr(0x1F468) + JOINER + chr(0x1F469)) == ("Family " + chr(0x1F468) + JOINER + chr(0x1F469), None)
    assert c.parse_name("a" + RTL_OVERRIDE + "b" + ZERO_WIDTH + "c") == ("abc", None)
    assert c.parse_name("x" * c.NAME_MAX) == ("x" * c.NAME_MAX, None)


@pytest.mark.parametrize("raw, word", [(None, "Give"), ("   ", "Give"), (RTL_OVERRIDE, "Give"), ("x" * (c.NAME_MAX + 1), "at most"),
                                       ("Friends", "automatic"), ("  FRIENDS ", "automatic")])
def test_a_name_that_cannot_be_used_is_refused(raw, word):
    name, error = c.parse_name(raw)
    assert name is None and word in error


def test_a_name_in_a_notification_cannot_make_a_link():
    assert c.plain_name("Fans [click](javascript:x) *now*") == "Fans clickjavascript:x now"
    assert c.plain_name("[]()") == "a paid circle"


# --- the audience a form chooses ----------------------------------------------------------------------------

def test_public_is_the_default_and_an_explicit_choice():
    assert c.parse_audience(None, []) == (c.PUBLIC, (), None)
    assert c.parse_audience("", []) == (c.PUBLIC, (), None)
    assert c.parse_audience("0", ["friends", "3"]) == (c.PUBLIC, (), None)          # Public wins: the ticks are not sent


def test_circles_are_friends_or_ids_each_once():
    assert c.parse_audience("4", ["friends", "12", " 12 ", "FRIENDS"]) == (c.CIRCLES, ("friends", "12"), None)
    assert c.parse_audience(None, ["7"]) == (c.CIRCLES, ("7",), None)                # circles chosen without the radio


@pytest.mark.parametrize("audience, chosen, word", [
    ("4", [], "at least one"), ("4", [""], "at least one"),                       # never made public by mistake
    ("1", [], "Public or some"), ("2", [], "Public or some"), ("3", [], "Public or some"), ("8", [], "Public or some"),
    ("4", ["12x"], "from your circles"), ("4", ["-1"], "from your circles"), (None, [ARABIC_INDIC_FIVE], "from your circles"),
])
def test_what_is_refused(audience, chosen, word):
    value, tokens, error = c.parse_audience(audience, chosen)
    assert value is None and tokens == () and word in error


# --- who sees what ------------------------------------------------------------------------------------------

def test_tier_of_follows_status_and_the_paid_to_date():
    assert c.tier_of(c.MEMBER, c.ACTIVE, 0, NOW) == c.MEMBER
    assert c.tier_of(c.SUBSCRIBER, c.ACTIVE, NOW + 1, NOW) == c.SUBSCRIBER
    assert c.tier_of(c.SUBSCRIBER, c.ACTIVE, NOW, NOW) is None                      # the paid days are over
    assert c.tier_of(c.MEMBER, c.ENDED, 0, NOW) is None
    assert c.tier_of("friend", c.ACTIVE, 0, NOW) is None                            # the old tier means nothing now


def test_a_row_counts_in_its_own_kind_of_circle_only():
    assert c.counts_in(c.CUSTOM, c.MEMBER) and not c.counts_in(c.CUSTOM, c.SUBSCRIBER)
    assert c.counts_in(c.PAID, c.SUBSCRIBER) and not c.counts_in(c.PAID, c.MEMBER)  # nobody is in a paid circle free
    assert c.counts_in(c.GUILD_KIND, c.SUBSCRIBER)
    assert not c.counts_in(c.FRIENDS, c.MEMBER)                                     # Friends is the follows, never rows


def test_a_post_for_circles_is_seen_by_anyone_in_any_of_them():
    assert c.can_see(c.CIRCLES, is_author=False, viewer_circles={2, 9}, item_circles={1, 2})
    assert not c.can_see(c.CIRCLES, is_author=False, viewer_circles={9}, item_circles={1, 2})
    assert not c.can_see(c.CIRCLES, is_author=False, viewer_circles=set(), item_circles={1})
    assert not c.can_see(c.CIRCLES, is_author=False, viewer_circles={1}, item_circles=set())   # its circles were deleted


def test_the_author_always_sees_their_own_and_public_is_everyone():
    assert c.can_see(c.CIRCLES, is_author=True, viewer_circles=(), item_circles=())
    assert c.can_see(c.PUBLIC, is_author=False)


@pytest.mark.parametrize("audience", [1, 2, 5, 7, 99, c.GUILD])
def test_an_audience_this_rule_does_not_know_is_seen_by_nobody_but_the_author(audience):
    assert not c.can_see(audience, is_author=False, viewer_circles={1}, item_circles={1})


# --- making circles and filling them -------------------------------------------------------------------------

def test_anyone_makes_circles_of_their_own_up_to_the_limit():
    assert c.refusal_to_make(c.CUSTOM, count=0, has_premium=False) is None
    assert c.refusal_to_make(c.CUSTOM, count=c.CUSTOM_LIMIT - 1, has_premium=False) is None
    assert "at most" in c.refusal_to_make(c.CUSTOM, count=c.CUSTOM_LIMIT, has_premium=True)


def test_a_paid_circle_needs_premium():
    assert "Premium" in c.refusal_to_make(c.PAID, count=0, has_premium=False)
    assert c.refusal_to_make(c.PAID, count=c.PAID_LIMIT - 1, has_premium=True) is None
    assert "at most" in c.refusal_to_make(c.PAID, count=c.PAID_LIMIT, has_premium=True)


@pytest.mark.parametrize("kind", [c.FRIENDS, c.GUILD_KIND, "secret"])
def test_friends_and_guild_circles_are_never_made_by_hand(kind):
    assert c.refusal_to_make(kind, count=0, has_premium=True)


def member(**kw):
    args = dict(owner_id=1, target_id=2, blocked=False, target_gone=False, members=0, already=False)
    args.update(kw)
    return c.refusal_to_add_member(**args)


def test_someone_can_be_added_unless_there_is_a_reason():
    assert member() is None
    assert "own posts" in member(target_id=1)
    assert "isn't available" in member(target_gone=True)
    assert "already" in member(already=True)
    assert "at most" in member(members=c.MEMBER_LIMIT)


def test_the_refusal_to_add_never_says_who_blocked_whom():
    assert member(blocked=True) == "You can't add that account."


# --- subscribing --------------------------------------------------------------------------------------

def sub(**kw):
    args = dict(owner_id=1, viewer_id=2, price=10, blocked=False, owner_gone=False, tier=None, cancelled=False)
    args.update(kw)
    return c.refusal_to_subscribe(**args)


def test_someone_may_subscribe_to_a_paid_circle_with_a_price():
    assert sub() is None


@pytest.mark.parametrize("kw, word", [
    (dict(viewer_id=1), "your own circle"), (dict(owner_gone=True), "isn't available"), (dict(blocked=True), "can't subscribe"),
    (dict(tier=c.SUBSCRIBER), "already subscribed"), (dict(price=0), "isn't taking"),
])
def test_the_reasons_not_to_subscribe(kw, word):
    assert word in sub(**kw)


def test_a_cancelled_subscriber_who_still_has_access_is_not_refused_so_they_can_resume():
    assert sub(tier=c.SUBSCRIBER, cancelled=True) is None


def test_being_in_a_custom_circle_is_no_reason_not_to_subscribe():
    assert sub(tier=c.MEMBER) is None


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


def test_the_limits():
    assert c.PERIOD == 30 * DAY and c.PRICE_MAX == 100 and c.MEMBER_LIMIT == 500
    assert c.CUSTOM_LIMIT == 20 and c.PAID_LIMIT == 5 and c.NAME_MAX == 40


def test_a_price_is_described_in_words():
    assert c.describe_price(0) == "Not taking subscribers"
    assert c.describe_price(1) == "1 coin every 30 days"
    assert c.describe_price(25) == "25 coins every 30 days"


# --- what people are told a post is for -----------------------------------------------------------------------

def test_the_author_is_told_which_circles():
    assert c.shared_label([]) == "Only you"
    assert c.shared_label(["Friends"]) == "Friends"
    assert c.shared_label(["Friends", "Family"]) == "Friends and Family"
    assert c.shared_label(["Friends", "Family", "Fans"]) == "Friends, Family and Fans"
    assert c.shared_label(["A", "B", "C", "D", "E"]) == "A, B and 3 more"


def test_anyone_else_is_never_told_the_name_of_a_circle_they_were_put_in():
    assert c.viewer_label([]) == "Shared with a circle"
    assert c.viewer_label(["Supporters"]) == "For subscribers of Supporters"     # a paid circle: they chose to join


# --- making a post for an audience ----------------------------------------------------------------------

def test_a_public_post_is_never_refused_for_its_audience():
    assert c.post_refusal(c.PUBLIC, anonymous=True, coauthors=True, forwards=True, own_video=True) is None


def test_what_a_post_for_circles_cannot_be():
    assert c.post_refusal(c.CIRCLES) is None
    assert "anonymous" in c.post_refusal(c.CIRCLES, anonymous=True)
    assert "co-authors" in c.post_refusal(c.CIRCLES, coauthors=True)
    assert "forwarded to a guild" in c.post_refusal(c.CIRCLES, forwards=True)
    assert "YouTube" in c.post_refusal(c.CIRCLES, own_video=True)


def test_the_first_problem_is_the_one_said():
    assert "anonymous" in c.post_refusal(c.CIRCLES, anonymous=True, coauthors=True, forwards=True, own_video=True)
    assert "co-authors" in c.post_refusal(c.CIRCLES, coauthors=True, forwards=True, own_video=True)


def test_the_date_is_written_the_way_the_site_writes_it():
    assert c.date_text(0) == "01 January 1970"
    assert c.date_text(NOW) == "18 May 2033"
    assert c.date_text(None) == "01 January 1970"
