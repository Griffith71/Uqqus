"""Tips (ruqqus/helpers/tips.py): what a tip's coins add to a post's ranking, and what a tip may be."""
import pytest

from ruqqus.helpers import tips


@pytest.mark.parametrize("coins, boost", [(0, 0), (1, 10), (2, 12), (3, 12), (4, 14), (8, 16), (16, 18), (31, 18),
                                          (32, 20), (1000, 20)])
def test_one_persons_boost_grows_with_each_doubling_up_to_twenty(coins, boost):
    assert tips.tipper_boost(coins) == boost


def test_a_posts_boost_is_counted_per_person():
    assert tips.post_boost({}) == 0
    assert tips.post_boost({1: 1, 2: 1, 3: 1}) == 30          # three people: three times the boost
    assert tips.post_boost({1: 3}) == 12                      # one person tipping 1 then 2 coins: once, on 3 coins
    assert tips.post_boost({1: 10 ** 6}) == tips.PER_TIPPER_MAX


@pytest.mark.parametrize("raw, coins", [(None, 1), ("1", 1), (" 5 ", 5), ("1000", 1000), (7, 7)])
def test_a_tip_is_whole_coins(raw, coins):
    assert tips.parse_coins(raw) == coins


@pytest.mark.parametrize("raw", ["0", "-1", "1.5", "", "abc", "٣", "1001", "1e3", "0x10"])
def test_anything_else_is_refused_not_a_crash(raw):
    assert tips.parse_coins(raw) is None


def test_labels_and_the_age_a_tipper_needs():
    assert tips.label(1) == "1 coin tipped" and tips.label(12) == "12 coins tipped"
    assert tips.established_before(100 * 86400) == 70 * 86400


def test_the_coins_of_a_tip_go_to_the_author_a_bot_s_owner_or_nobody():
    from ruqqus.helpers import tips as rules
    assert rules.recipient_id(1) == 1
    assert rules.recipient_id(5, is_bot=True, bot_owner_id=1) == 1      # a member bot: the member who runs it
    assert rules.recipient_id(5, is_bot=True, bot_owner_id=None) is None  # a site bot: spent, only the lift
    assert rules.answerable_id(5, is_bot=True, bot_owner_id=1) == 1
    assert rules.answerable_id(5, is_bot=True, bot_owner_id=None) == 5
    assert rules.answerable_id(1) == 1
