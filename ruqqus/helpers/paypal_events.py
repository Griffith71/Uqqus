"""What a PayPal webhook event says about a coin order: which order, how much went back (bug hunt 2026-10, H3). Pure, no
Flask, no network.

A coin order is a v2 Checkout order: `paypal_txns.paypal_id` is the ORDER id, and capturing it makes a capture (its id
is kept in `paypal_capture_id`). A refund or a chargeback arrives as an event whose `resource` is the refund or the
capture, never the order, so looking the order up by `resource.id` (as the handler did) found nothing: every reversal
was a 404 that PayPal retried for days, and the coins stayed with the buyer. Shapes as PayPal documents them for the v2
Payments API (developer.paypal.com/api/rest/webhooks/event-names/); the v1 `PAYMENT.SALE.*` events are kept as
before."""

REVERSALS_V2 = ("PAYMENT.CAPTURE.REFUNDED", "PAYMENT.CAPTURE.REVERSED")
REVERSALS_V1 = ("PAYMENT.SALE.REFUNDED", "PAYMENT.SALE.REVERSED")
# a capture PayPal was still checking (PENDING when the order was paid): its coins wait for one of these
SETTLEMENTS = ("PAYMENT.CAPTURE.COMPLETED", "PAYMENT.CAPTURE.DENIED")
# events whose resource is the capture itself
_CAPTURE_RESOURCE = ("PAYMENT.CAPTURE.REVERSED",) + SETTLEMENTS

# what a reversal costs the site on top of the money itself: PayPal's fee for the order, added to what the account owes
FEE_FIXED_CENTS = 30
FEE_RATE = 0.029


def is_reversal(event):
    return isinstance(event, dict) and event.get("event_type") in REVERSALS_V2 + REVERSALS_V1


def is_settlement(event):
    return isinstance(event, dict) and event.get("event_type") in SETTLEMENTS


def _capture_from_links(resource):
    """A refund's links name the capture it refunds (`rel: up`, .../v2/payments/captures/<id>)."""
    links = resource.get("links")
    for link in links if isinstance(links, list) else []:
        if not isinstance(link, dict):
            continue
        href = str(link.get("href") or "")
        if link.get("rel") == "up" and "/v2/payments/captures/" in href:
            return href.rstrip("/").rsplit("/", 1)[-1] or None
    return None


def keys(event):
    """(order id, capture id, v1 id) the event may name; any of them may be None."""
    resource = event.get("resource") if isinstance(event, dict) else None
    if not isinstance(resource, dict):
        return None, None, None
    if event.get("event_type") in REVERSALS_V1:
        return None, None, resource.get("id") or resource.get("parent_payment")
    supplementary = resource.get("supplementary_data")
    related = supplementary.get("related_ids") if isinstance(supplementary, dict) else None
    related = related if isinstance(related, dict) else {}
    order = related.get("order_id")
    capture = _capture_from_links(resource) or related.get("capture_id")
    if event.get("event_type") in _CAPTURE_RESOURCE and not capture:
        capture = resource.get("id")                  # these events' resource is the capture itself
    return (str(order) if order else None), (str(capture) if capture else None), None


def order_of_capture(capture):
    """The order id PayPal's own record of a capture names (GET /v2/payments/captures/<id>), or None."""
    if not isinstance(capture, dict):
        return None
    supplementary = capture.get("supplementary_data")
    related = supplementary.get("related_ids") if isinstance(supplementary, dict) else None
    order = related.get("order_id") if isinstance(related, dict) else None
    return str(order) if order else None


def amount_cents(event):
    """The money that went back, in cents (USD only, the shop's currency), or None when it can't be read."""
    resource = event.get("resource") if isinstance(event, dict) else None
    if not isinstance(resource, dict):
        return None
    amount = resource.get("amount") or {}
    if not isinstance(amount, dict):
        return None
    value = amount.get("value", amount.get("total"))
    currency = amount.get("currency_code", amount.get("currency", "USD"))
    if currency != "USD":
        return None
    try:
        cents = round(float(str(value)) * 100)
    except (TypeError, ValueError):
        return None
    return cents if cents > 0 else None


def chargeable_cents(refund, already_refunded, order_cents):
    """How much of `refund` still counts: never more than the order cost in all, whatever events arrive twice or
    in different names (a chargeback and a refund of the same money)."""
    room = max(0, (order_cents or 0) - (already_refunded or 0))
    return max(0, min(refund or 0, room))


def owed_for(refunded_cents):
    """What the account owes for money that went back: the money and PayPal's fee on it."""
    if refunded_cents <= 0:
        return 0
    return int((refunded_cents + FEE_FIXED_CENTS) / (1 - FEE_RATE))
