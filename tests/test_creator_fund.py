"""The creator fund's rules (ruqqus/helpers/creator_fund.py): months, budgets, the split and its cap."""
import pytest

from ruqqus.helpers import creator_fund as rules

SEP_1 = 1788220800                # 2026-09-01 00:00 UTC
OCT_1 = 1790812800


def test_months():
    assert rules.month_bounds("2026-09") == (SEP_1, OCT_1)
    assert rules.month_bounds("2024-02")[1] - rules.month_bounds("2024-02")[0] == 29 * 86400
    assert rules.month_of(OCT_1 - 1) == "2026-09" and rules.month_of(OCT_1) == "2026-10"
    assert rules.last_month(OCT_1 + 5) == "2026-09" and rules.last_month(SEP_1) == "2026-08"
    assert rules.last_month(1767225600) == "2025-12"          # across a new year
    assert rules.is_over("2026-09", OCT_1) and not rules.is_over("2026-09", OCT_1 - 1)
    assert rules.month_name("2026-09") == "September 2026"


@pytest.mark.parametrize("text", ["2026-13", "2026-00", "1999-05", "2026-9", "26-09", "2026-09-01", "", None, "abcd-ef"])
def test_a_bad_month_is_none(text):
    assert rules.parse_month(text) is None


@pytest.mark.parametrize("text, value", [("1", 1), ("500", 500), (" 7 ", 7), ("0", None), ("-3", None), ("1.5", None),
                                         ("٣", None), (str(rules.MAX_BUDGET + 1), None), ("", None), (None, None)])
def test_budgets_are_whole_coins_in_range(text, value):
    assert rules.parse_budget(text) == value


def test_the_minimum_is_configurable_but_never_tiny():
    assert rules.min_points({}) == 50
    assert rules.min_points({"CREATOR_FUND_MIN_POINTS": "120"}) == 120
    assert rules.min_points({"CREATOR_FUND_MIN_POINTS": "2"}) == 10
    assert rules.min_points({"CREATOR_FUND_MIN_POINTS": "lots"}) == 50


def test_the_split_follows_points_and_rounds_down():
    assert rules.split(100, {1: 50, 2: 50, 3: 100}, 10) == {1: 25, 2: 25, 3: 25}      # 50 capped at 25
    assert rules.split(1000, {1: 60, 2: 30, 3: 9}, 10) == {1: 250, 2: 250}          # 3 is under the minimum
    assert rules.split(1000, {1: 10, 2: 10, 3: 10, 4: 10, 5: 10, 6: 10, 7: 10}, 10) == {i: 142 for i in range(1, 8)}


def test_nobody_gets_more_than_a_quarter_and_leftovers_are_not_paid():
    coins = rules.split(1000, {1: 1000, 2: 10}, 10)
    assert coins[1] == 250 and coins[2] == 9 and sum(coins.values()) <= 1000


def test_nothing_is_paid_from_nothing_and_no_share_under_a_coin():
    assert rules.split(0, {1: 100}, 10) == {} and rules.split(100, {}, 10) == {} and rules.split(100, {1: 5}, 10) == {}
    assert rules.split(3, {1: 10, 2: 1000}, 10) == {}           # 3 * 10 // 1010 is 0; 2 is capped at 0 coins


def test_the_accounts_that_count_were_made_thirty_days_before_the_month_ended():
    assert rules.established_before(OCT_1) == OCT_1 - 30 * 86400


def test_flags_for_a_closer_look():
    assert rules.flags(200, 40, 35) == []
    assert "points from only 4 accounts" in rules.flags(60, 4, 4)
    assert "30 accounts on 3 networks" in rules.flags(100, 30, 3)
    assert any("close to the most" in f for f in rules.flags(30 * 18, 30, 30))
