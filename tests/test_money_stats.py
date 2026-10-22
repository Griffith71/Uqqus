"""The admins' money page arithmetic (ruqqus/helpers/money_stats.py, bug hunt 2026-10, M6)."""
from ruqqus.helpers import money_stats as ms


def test_a_refunded_order_counts_once_in_and_once_out():
    paid = [499, 499]                         # one kept, one refunded later
    assert ms.revenue_cents(paid, [499]) == 2 * ms.net_of_fees(499) - 499
    assert ms.revenue_cents([], []) == 0


def test_the_sum_is_written_as_dollars_and_cents():
    assert ms.dollars(12345) == "123.45"
    assert ms.dollars(5) == "0.05"
    assert ms.dollars(0) == "0.00"
    assert ms.dollars(-250) == "-2.50"
    assert ms.dollars(100) == "1.00"
