"""Buying coins and paying off a debt (ruqqus/helpers/coin_shop.py, bug hunt 2026-10, H4)."""
import pytest

from ruqqus.helpers import coin_shop


@pytest.mark.parametrize("value, count", [("1", 1), ("4", 4), ("52", 52), (" 12 ", 12), ("10000", 10000)])
def test_a_count_of_one_to_ten_thousand_is_read(value, count):
    assert coin_shop.parse_coin_count(value) == count


@pytest.mark.parametrize("value", ["0", "00", "-5", "10001", "999999999", "1.5", "1e3", "abc", "", None, "٣", "４", " ", 5])
def test_anything_else_is_not_a_count(value):
    assert coin_shop.parse_coin_count(value) is None


def test_paying_off_a_debt_never_leaves_it_below_zero():
    assert coin_shop.owed_after_payment(300, 300) == 0
    assert coin_shop.owed_after_payment(300, 100) == 200
    assert coin_shop.owed_after_payment(0, 49) == 0, "the 0-coin order that locked accounts"
    assert coin_shop.owed_after_payment(100, 300) == 0
    assert coin_shop.owed_after_payment(None, 10) == 0
    assert coin_shop.owed_after_payment(100, -50) == 100
