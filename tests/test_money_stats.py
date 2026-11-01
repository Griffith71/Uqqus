"""The admins' money page arithmetic (ruqqus/helpers/money_stats.py, bug hunt 2026-10, M6)."""
import ast
from pathlib import Path

import pytest
from sqlalchemy import Boolean, Column, Integer, MetaData, Table, create_engine, not_, select

from ruqqus.helpers import money_stats as ms
from ruqqus.helpers import paypal_events


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


def _every_figure(value):
    return {key: value for _, rows in ms.SECTIONS for key, *_ in rows}


def test_a_sum_over_no_rows_reads_as_zero_never_none():
    drawn = ms.sections(_every_figure(None))
    values = {row["key"]: row["value"] for section in drawn for row in section["rows"]}
    assert "None" not in values.values() and set(values.values()) == {"$0.00", "0"}
    assert values["received_cents_24h"] == "$0.00" and values["coins_sold_ytd"] == "0"
    assert ms.revenue_cents([None], [None]) == 0


def test_money_and_counts_are_written_out():
    assert ms.written(ms.USD, 123456789) == "$1,234,567.89"
    assert ms.written(ms.USD, 5) == "$0.05"
    assert ms.written(ms.USD, -250) == "-$2.50"
    assert ms.written(ms.COUNT, 1234567) == "1,234,567"


def test_a_figure_the_route_did_not_count_fails_rather_than_showing_nothing():
    figures = _every_figure(1)
    figures.popitem()
    with pytest.raises(KeyError):
        ms.sections(figures)


def _route_keys():
    """The keys the route writes in its figures, read from its source: a key written twice is lost silently in a running dict."""
    tree = ast.parse(Path(__file__).resolve().parent.parent.joinpath("ruqqus", "routes", "admin.py").read_text(encoding="utf-8"))
    view = next(node for node in ast.walk(tree) if isinstance(node, ast.FunctionDef) and node.name == "money_stats")
    target = next(node for node in ast.walk(view) if isinstance(node, ast.Assign) and getattr(node.targets[0], "id", "") == "figures")
    return [key.value for key in target.value.keys]


def test_the_route_counts_each_figure_once_and_counts_exactly_what_the_page_names():
    keys = _route_keys()
    assert len(keys) == len(set(keys)), "a key written twice"
    named = [key for _, rows in ms.SECTIONS for key, *_ in rows]
    assert len(named) == len(set(named))
    assert set(keys) == set(named)


def test_no_label_says_more_than_its_query_counts():
    labels = " ".join(label for _, rows in ms.SECTIONS for _, _, label, _ in rows).lower()
    for claim in ("redeemed", "vanished", "written off", "in circulation", "receivable", "revenue"):
        assert claim not in labels, claim
    notes = {key: note for _, rows in ms.SECTIONS for key, _, _, note in rows}
    assert "not coins redeemed" in notes["premium_ending_7_days"].lower()         # a count of accounts, by when their Premium ends
    assert "not written off" in notes["owed_cents_gone"].lower()                  # nothing on the site writes a debt off
    assert "nothing removes them" in notes["coins_held_gone"]                     # still in those balances
    assert "2.9% + 30 cents" in notes["net_cents_ytd"]                            # the fee is assumed, not what PayPal charged
    assert ms.FEE_RATE == 0.029 and ms.FEE_FIXED_CENTS == 30                      # or that sentence is wrong
    assert "debts paid off" in notes["received_cents_24h"].lower()                # status 3 holds them too, coin_count 0
    assert "when paypal told the site it cleared" in notes["received_cents_24h"].lower()     # routes/paypal.py dates a held payment then


def test_the_note_on_a_debt_is_the_sum_the_site_stores():
    notes = {key: note for _, rows in ms.SECTIONS for key, _, _, note in rows}
    assert "(money + 30 cents) / (1 - 2.9%)" in notes["owed_cents_open"]
    assert "plus paypal's fee" not in notes["owed_cents_open"].lower()            # it is not the money + the fee the Net figure assumes
    assert (paypal_events.FEE_RATE, paypal_events.FEE_FIXED_CENTS) == (ms.FEE_RATE, ms.FEE_FIXED_CENTS) == (0.029, 30)
    assert paypal_events.owed_for(100000) == int((100000 + 30) / (1 - 0.029)) == 103017
    assert 100000 + (100000 - ms.net_of_fees(100000)) == 102930                   # that fee: 87 cents less


def test_every_account_is_open_or_gone_exactly_once():
    table = Table("users", MetaData(), Column("id", Integer), Column("is_deleted", Boolean), Column("is_banned", Integer), Column("unban_utc", Integer))
    engine = create_engine("sqlite://")
    table.create(engine)
    rows = {1: (False, 0, 0, False),      # open
            2: (False, 3, 999, False),    # a temporary ban still owes and holds
            3: (False, 3, 0, True),       # a permanent ban
            4: (True, 0, 0, True),        # closed
            5: (True, 3, 999, True),      # closed, while on a temporary ban
            6: (None, None, None, False)}  # empty columns land somewhere
    with engine.begin() as conn:
        for uid, (deleted, banned, unban, _) in rows.items():
            conn.execute(table.insert().values(id=uid, is_deleted=deleted, is_banned=banned, unban_utc=unban))
        gone = ms.gone_accounts(table.c.is_deleted, table.c.is_banned, table.c.unban_utc)
        found_gone = {r[0] for r in conn.execute(select(table.c.id).where(gone))}
        found_open = {r[0] for r in conn.execute(select(table.c.id).where(not_(gone)))}
    assert found_gone == {uid for uid, row in rows.items() if row[3]}
    assert found_open == {uid for uid, row in rows.items() if not row[3]}
