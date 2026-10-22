"""The admins' money page, the arithmetic (bug hunt 2026-10, M6). Pure, no Flask.

A refunded order is money that arrived and then went back: it counts once in what came in and once in what went back.
The page took it out of what came in (its status leaves "captured" when it is reversed) AND counted it as a loss, so a
refund lowered the year's revenue twice."""

FEE_RATE = 0.029
FEE_FIXED_CENTS = 30
PAID = (3, -2)                 # captured, and captured then reversed


def net_of_fees(cents):
    """What PayPal paid out for an order of `cents`, as the page has always counted it."""
    return int(cents - cents * FEE_RATE - FEE_FIXED_CENTS)


def revenue_cents(paid_orders, refunds):
    """`paid_orders`: the cents of every order that was paid (reversed later or not); `refunds`: the cents that went
    back. The year's revenue in cents."""
    return sum(net_of_fees(c) for c in paid_orders) - sum(refunds)


def dollars(cents):
    """12345 -> "123.45", 5 -> "0.05", -250 -> "-2.50"."""
    sign = "-" if cents < 0 else ""
    whole, part = divmod(abs(int(cents)), 100)
    return f"{sign}{whole}.{part:02d}"
