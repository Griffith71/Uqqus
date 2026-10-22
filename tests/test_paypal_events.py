"""What a PayPal webhook event says about a coin order (ruqqus/helpers/paypal_events.py, bug hunt 2026-10, H3).

The events below follow the shapes PayPal documents for the v2 Payments API (a refund resource links the capture it
refunds as `rel: up`; a capture resource carries `supplementary_data.related_ids.order_id`). They are written from the
published schema, not recorded from PayPal: confirm against PayPal's sample payloads before relying on a new field."""
import pytest

from ruqqus.helpers import paypal_events as pe

REFUND = {
    "id": "WH-1", "event_type": "PAYMENT.CAPTURE.REFUNDED",
    "resource": {
        "id": "REFUND-9", "status": "COMPLETED", "amount": {"currency_code": "USD", "value": "4.99"},
        "links": [{"href": "https://api.paypal.com/v2/payments/refunds/REFUND-9", "rel": "self", "method": "GET"},
                  {"href": "https://api.paypal.com/v2/payments/captures/CAPTURE-3", "rel": "up", "method": "GET"}],
    },
}
REVERSAL = {
    "id": "WH-2", "event_type": "PAYMENT.CAPTURE.REVERSED",
    "resource": {
        "id": "CAPTURE-3", "status": "REVERSED", "amount": {"currency_code": "USD", "value": "4.99"},
        "supplementary_data": {"related_ids": {"order_id": "ORDER-7"}},
    },
}
SALE = {"id": "WH-3", "event_type": "PAYMENT.SALE.REFUNDED",
        "resource": {"id": "PAYID-OLD", "amount": {"total": "4.99", "currency": "USD"}}}


def test_a_refund_names_the_capture_it_refunds():
    assert pe.is_reversal(REFUND)
    assert pe.keys(REFUND) == (None, "CAPTURE-3", None)
    assert pe.amount_cents(REFUND) == 499


def test_a_reversal_names_the_order_and_the_capture():
    assert pe.keys(REVERSAL) == ("ORDER-7", "CAPTURE-3", None)
    assert pe.amount_cents(REVERSAL) == 499


def test_the_old_v1_sale_events_keep_their_own_id():
    assert pe.is_reversal(SALE) and pe.keys(SALE) == (None, None, "PAYID-OLD") and pe.amount_cents(SALE) == 499


@pytest.mark.parametrize("event", [
    {}, None, "x", {"event_type": "CHECKOUT.ORDER.APPROVED", "resource": {}},
    {"event_type": "PAYMENT.CAPTURE.REFUNDED"}, {"event_type": "PAYMENT.CAPTURE.REFUNDED", "resource": "x"},
])
def test_anything_else_names_nothing(event):
    assert pe.keys(event) == (None, None, None) or not pe.is_reversal(event)


@pytest.mark.parametrize("amount, cents", [
    ({"currency_code": "USD", "value": "10.00"}, 1000), ({"currency_code": "USD", "value": "0.10"}, 10),
    ({"currency_code": "EUR", "value": "10.00"}, None), ({"currency_code": "USD", "value": "abc"}, None),
    ({"currency_code": "USD", "value": "-5"}, None), ({"currency_code": "USD", "value": "0"}, None), ("x", None),
])
def test_only_a_positive_amount_in_dollars_counts(amount, cents):
    assert pe.amount_cents({"resource": {"amount": amount}}) == cents


def test_an_order_never_counts_for_more_than_it_cost():
    assert pe.chargeable_cents(499, 0, 499) == 499
    assert pe.chargeable_cents(499, 499, 499) == 0, "a chargeback and a refund of the same money"
    assert pe.chargeable_cents(300, 299, 499) == 200, "partial refunds add up to the order at most"
    assert pe.chargeable_cents(None, 0, 499) == 0


def test_what_is_owed_adds_paypals_fee_as_before():
    assert pe.owed_for(499) == int((499 + 30) / (1 - 0.029))
    assert pe.owed_for(0) == 0


def test_a_settlement_names_its_capture_and_order():
    cleared = {"id": "WH-4", "event_type": "PAYMENT.CAPTURE.COMPLETED",
               "resource": {"id": "CAPTURE-5", "status": "COMPLETED", "supplementary_data": {"related_ids": {"order_id": "ORDER-8"}}}}
    assert pe.is_settlement(cleared) and not pe.is_reversal(cleared)
    assert pe.keys(cleared) == ("ORDER-8", "CAPTURE-5", None)
    assert pe.keys({**cleared, "event_type": "PAYMENT.CAPTURE.DENIED"}) == ("ORDER-8", "CAPTURE-5", None)


def test_paypals_record_of_a_capture_names_its_order():
    assert pe.order_of_capture({"supplementary_data": {"related_ids": {"order_id": "ORDER-1"}}}) == "ORDER-1"
    for record in ({}, None, {"supplementary_data": []}, {"supplementary_data": {"related_ids": "x"}}):
        assert pe.order_of_capture(record) is None


def test_odd_shapes_in_a_signed_event_name_nothing_rather_than_fail():
    for resource in ({"supplementary_data": ["x"]}, {"links": "x"}, {"links": [1, None]}, {"supplementary_data": {"related_ids": []}}):
        assert pe.keys({"event_type": "PAYMENT.CAPTURE.REFUNDED", "resource": resource}) == (None, None, None)
