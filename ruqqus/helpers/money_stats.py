"""The admins' money page (/admin/money, routes/admin.py, templates/admin/money.html): the arithmetic and what each figure
is called. No Flask, and no I/O: the route counts, this names and writes.

A refunded order is money that arrived and then went back: it counts once in what came in and once in what went back.
The page took it out of what came in (its status leaves "captured" when it is reversed) AND counted it as a loss, so a
refund lowered the year's revenue twice (bug hunt 2026-10, M6).

**A label says what its query counts and no more** (the page's labels once said "coins redeemed" of a count of accounts, "written
off" of debts nothing writes off, and "vanished" of coins still in their balances). Change a query, change its note.
Ad income is in none of this: the site holds no figure of it (`ADSENSE_REPORTS` is where it is), and it never reaches the
creator fund, the Studio or members (tests/test_ad_income_stays_out_assets.py).
"""
from sqlalchemy import and_, func, or_

FEE_RATE = 0.029
FEE_FIXED_CENTS = 30
PAID = (3, -2)                 # captured, and captured then reversed

# a link for a person who signs in there, nothing the site calls. A browser that isn't signed in is sent to Google's sign-in
ADSENSE_REPORTS = "https://adsense.google.com/adsense/u/0/reporting"

USD, COUNT = "usd", "count"

# (heading, figures). A figure is (the route's key, how it is written, its label, what it really is)
SECTIONS = (
    ("Money in, through PayPal", (
        ("received_cents_24h", USD, "Received, last 24 hours",
         "Before PayPal's fees. An order is dated when it was paid: when the buyer came back from PayPal or, for a payment PayPal held "
         "for checking, when PayPal told the site it cleared. Only orders still standing: one reversed since, even in part, drops out "
         "of this figure. Debts paid off are included, so it isn't only coin sales."),
        ("received_cents_7d", USD, "Received, last 7 days", "The same, over 7 days."),
        ("paid_orders_24h", COUNT, "Orders behind that, last 24 hours", "Coin orders and debts paid off, not reversed since."),
        ("paid_orders_7d", COUNT, "Orders behind that, last 7 days", "The same, over 7 days."),
        ("net_cents_ytd", USD, "Net of this year's orders",
         "Every order paid since 1 January UTC, reversed later or not, less PayPal's fee as this page assumes it (2.9% + 30 cents an "
         "order, not what PayPal really charged), less what went back on those orders. A refund counts against the year its order "
         "was paid in, whenever the refund came."),
    )),
    ("Coins", (
        ("coins_sold_ytd", COUNT, "Coins sold this year",
         "On orders paid since 1 January UTC and not reversed since. A reversed order's coins are not taken back (the account owes "
         "the money instead), so more coins than this may have gone out. Creator fund coins are not sold and are not in this."),
        ("coins_held_open", COUNT, "Coins in the balances of open accounts",
         "Right now, on accounts not closed and not permanently banned; a temporary ban is still open. Coin sales and the creator fund "
         "add coins; Premium weeks, name changes and tips on a site bot's post take them out; other tips and circle payments only "
         "move them."),
        ("coins_held_gone", COUNT, "Coins in the balances of closed or permanently banned accounts",
         "Right now. They are still there: nothing removes them, and lifting a ban gives them back."),
    )),
    ("Owed to the site", (
        ("owed_cents_open", USD, "Owed by open accounts",
         "After a refund or a chargeback: the money that went back, grossed up so that paying it through PayPal covers that money and "
         "PayPal's assumed fee: (money + 30 cents) / (1 - 2.9%), a little more than the money plus the 2.9% + 30 cents the Net figure "
         "assumes. Such an account can't post, comment, vote or spend until it pays."),
        ("owed_cents_gone", USD, "Owed by closed or permanently banned accounts",
         "Not written off: nothing on the site writes a debt off. It is only what those accounts owe right now. A temporary ban is "
         "in the row above."),
    )),
    ("Premium", (
        ("premium_ending_7_days", COUNT, "Accounts whose Premium ends in the next 7 days",
         "Not coins redeemed: the site keeps no record of those. A week redeemed long ago counts here once it is the last week left, "
         "and an account counts once however many coins it spent. Closed and banned accounts are included."),
        ("premium_ending_6_to_7_days", COUNT, "Accounts whose Premium ends 6 to 7 days from now",
         "A rough guide to how many accounts redeemed or renewed in the last 24 hours: a week added on top of time already running "
         "can fall outside this, and older weeks stacked up can fall inside. It counts accounts, not coins."),
    )),
)


def gone_accounts(is_deleted, is_banned, unban_utc):
    """The condition for an account that is gone for good: closed, or banned with no end. Never NULL, even on a row with an
    empty column, so an account is always in this or in its NOT, never in neither."""
    return or_(is_deleted.is_(True), and_(func.coalesce(is_banned, 0) > 0, func.coalesce(unban_utc, 0) == 0))


def net_of_fees(cents):
    """What PayPal paid out for an order of `cents`, as the page has always counted it."""
    return int(cents - cents * FEE_RATE - FEE_FIXED_CENTS)


def revenue_cents(paid_orders, refunds):
    """`paid_orders`: the cents of every order that was paid (reversed later or not); `refunds`: the cents that went
    back. The year's revenue in cents."""
    return sum(net_of_fees(c) for c in paid_orders if c is not None) - sum(r or 0 for r in refunds)


def dollars(cents):
    """12345 -> "123.45", 5 -> "0.05", -250 -> "-2.50"."""
    sign = "-" if cents < 0 else ""
    whole, part = divmod(abs(int(cents)), 100)
    return f"{sign}{whole}.{part:02d}"


def written(kind, value):
    """A figure as the page shows it. A sum over no rows comes back from the database as None, which is 0 of whatever it counts."""
    value = int(value or 0)
    if kind == USD:
        sign = "-" if value < 0 else ""
        whole, part = divmod(abs(value), 100)
        return f"{sign}${whole:,}.{part:02d}"
    return f"{value:,}"


def sections(figures):
    """The page's sections, drawn from the route's counts. A key the route didn't count fails here rather than showing nothing."""
    return [{"title": title,
             "rows": [{"key": key, "label": label, "note": note, "value": written(kind, figures[key])}
                      for key, kind, label, note in rows]}
            for title, rows in SECTIONS]
