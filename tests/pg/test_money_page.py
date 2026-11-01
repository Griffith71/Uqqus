"""The admins' money page on the real app: who may open it, nothing reads None, a figure counts what its label says, and the ad status
in its three states, with no id printed (helpers/money_stats.py, helpers/ads.py status)."""
import calendar
import re
import secrets
import time

import pytest
from sqlalchemy import text

from .test_characterization import fake_paypal

# a fixture: importing it here makes it available to this file's tests
assert fake_paypal

BASE = "https://localhost"
CLIENT = "ca-pub-9876543210987654"
SLOTS = {"ADSENSE_SLOT_FEED": "7777777771", "ADSENSE_SLOT_POST": "7777777772", "ADSENSE_SLOT_SIDEBAR": "7777777773"}
ENV = ("ADSENSE_CLIENT", "ADSENSE_FEED_LAYOUT_KEY", *SLOTS)


@pytest.fixture
def no_ads(monkeypatch):
    for name in ENV:
        monkeypatch.delenv(name, raising=False)
    return monkeypatch


def _admin(make_member, level=2):
    admin = make_member()
    db = admin.db_session()
    db.execute(text("UPDATE users SET admin_level = :l WHERE id = :u"), {"l": level, "u": admin.id})
    db.commit()
    db.close()
    return admin


def _page(admin):
    answer = admin.browser().get("/admin/money", base_url=BASE)
    assert answer.status_code == 200
    return answer.get_data(as_text=True)


def _figures(page):
    return dict(re.findall(r'data-stat="(\w+)".*?<td[^>]*>([^<]*)</td>', page, re.S))


def test_only_an_admin_opens_it(make_member, no_ads):
    assert make_member().browser().get("/admin/money", base_url=BASE).status_code == 403
    assert "Financial Statistics" in _page(_admin(make_member))


def test_every_figure_is_drawn_and_none_reads_none(make_member, no_ads):
    page = _page(_admin(make_member))
    figures = _figures(page)
    assert len(figures) == 12
    assert "None" not in figures.values()
    assert ">None<" not in page
    assert not re.search(r"vanished|in circulation|receivable", page, re.I)


def _grew(before, after, key):
    number = (lambda v: round(float(v.replace("$", "").replace(",", "")) * (100 if "$" in v else 1)))
    return number(after[key]) - number(before[key])


def test_a_figure_counts_what_its_label_says(make_member, no_ads):
    from ruqqus.helpers import money_stats as ms
    admin = _admin(make_member)
    closed, debtor, buyer, premium, ending_soon, ending_later = (make_member() for _ in range(6))
    before = _figures(_page(admin))
    now = int(time.time())
    day = 86400
    year_start = calendar.timegm((time.gmtime(now).tm_year, 1, 1, 0, 0, 0))
    last_year = year_start - day
    db = admin.db_session()
    db.execute(text("UPDATE users SET coin_balance = 7, is_deleted = true WHERE id = :u"), {"u": closed.id})
    db.execute(text("UPDATE users SET coin_balance = 5, negative_balance_cents = 300, is_banned = 1, unban_utc = :t WHERE id = :u"),
               {"t": now + day, "u": debtor.id})
    for member, ends in ((premium, now + 2 * day), (ending_soon, now + 6 * day + day // 2), (ending_later, now + 8 * day)):
        db.execute(text("UPDATE users SET premium_expires_utc = :t WHERE id = :u"), {"t": ends, "u": member.id})
    orders = [(3, 1000, 10, 0, now), (3, 500, 0, 0, now), (-2, 400, 4, 400, now), (1, 900, 9, 0, now), (4, 800, 8, 0, now),   # paid, a debt paid, reversed, unpaid, pending
              (3, 2000, 20, 0, now - 3 * day),                                                                                # in the week, not the day
              (3, 4000, 40, 0, now - 8 * day),                                                                                # in neither, in the year unless it is early January
              (3, 8000, 80, 0, last_year), (-2, 16000, 160, 16000, last_year)]                                                # last year's: in no figure here, kept or reversed
    for status, cents, coins, refunded, when in orders:
        db.execute(text("INSERT INTO paypal_txns (user_id, created_utc, coin_count, usd_cents, status, paypal_id, refunded_cents) "
                        "VALUES (:u, :t, :c, :m, :s, :o, :r)"),
                   {"u": buyer.id, "t": when, "c": coins, "m": cents, "s": status, "o": "MONEY-" + secrets.token_hex(5), "r": refunded})
    db.commit()
    db.close()
    try:
        after = _figures(_page(admin))

        def grew(key):
            return _grew(before, after, key)

        assert grew("received_cents_24h") == 1500 and grew("paid_orders_24h") == 2           # the debt paid off is one; the reversed, unpaid and pending ones aren't
        assert grew("received_cents_7d") == 1500 + 2000 and grew("paid_orders_7d") == 3      # the 3-day-old order, not the 8-day-old one
        in_year = [o for o in orders if o[4] > year_start]                                   # the 3- and 8-day-old orders may be last year's in early January
        assert grew("coins_sold_ytd") == sum(o[2] for o in in_year if o[0] == 3)
        assert grew("net_cents_ytd") == sum(ms.net_of_fees(o[1]) for o in in_year if o[0] in ms.PAID) - sum(o[3] for o in in_year)
        assert grew("coins_held_gone") == 7 and grew("coins_held_open") == 5                 # a temporary ban still holds its coins
        assert grew("owed_cents_open") == 300 and grew("owed_cents_gone") == 0
        assert grew("premium_ending_7_days") == 2 and grew("premium_ending_6_to_7_days") == 1     # 2 days and 6.5 days, and 6.5 days; never the 8
    finally:
        db = admin.db_session()
        db.execute(text("DELETE FROM paypal_txns WHERE user_id = :u"), {"u": buyer.id})
        db.execute(text("UPDATE users SET negative_balance_cents = 0, coin_balance = 0 WHERE id IN (:a, :b)"), {"a": closed.id, "b": debtor.id})
        db.commit()
        db.close()


def test_a_payment_paypal_held_counts_from_when_it_cleared(make_member, real_app, fake_paypal, no_ads):
    app, _ = real_app
    admin, buyer = _admin(make_member), make_member()
    capture, order = "CAPTURE-" + secrets.token_hex(5), "ORDER-" + secrets.token_hex(5)
    came_back = int(time.time()) - 9 * 86400
    db = admin.db_session()
    txn = db.execute(text("INSERT INTO paypal_txns (user_id, created_utc, coin_count, usd_cents, status, paypal_id, paypal_capture_id) "
                          "VALUES (:u, :t, 0, 5000, 4, :o, :c) RETURNING id"),
                     {"u": buyer.id, "t": came_back, "o": order, "c": capture}).scalar()
    db.commit()
    db.close()
    event = {"event_type": "PAYMENT.CAPTURE.COMPLETED",
             "resource": {"id": capture, "status": "COMPLETED", "supplementary_data": {"related_ids": {"order_id": order}}}}

    def send():
        answer = app.test_client().post("/shop/paypal_webhook", base_url=BASE, json={**event, "id": "WH-" + secrets.token_hex(6)})
        assert answer.status_code == 204

    def date():
        db = admin.db_session()
        when = db.execute(text("SELECT created_utc FROM paypal_txns WHERE id = :t"), {"t": txn}).scalar()
        db.close()
        return when

    try:
        before = _figures(_page(admin))
        send()
        after = _figures(_page(admin))
        assert _grew(before, after, "received_cents_24h") == 5000 and _grew(before, after, "paid_orders_24h") == 1
        assert _grew(before, after, "received_cents_7d") == 5000
        cleared = date()
        assert cleared > came_back + 8 * 86400

        db = admin.db_session()
        db.execute(text("UPDATE paypal_txns SET created_utc = :t WHERE id = :i"), {"t": cleared - 100, "i": txn})
        db.commit()
        db.close()
        send()
        assert date() == cleared - 100, "a second copy of the event, under a new id, doesn't date a settled order again"
    finally:
        db = admin.db_session()
        db.execute(text("DELETE FROM paypal_events WHERE txn_id = :t"), {"t": txn})
        db.execute(text("DELETE FROM paypal_txns WHERE id = :t"), {"t": txn})
        db.commit()
        db.close()


def _state(page):
    return re.search(r'data-ad-state="(\w+)"', page).group(1)


def test_the_ad_status_in_its_three_states_prints_no_id(make_member, no_ads):
    admin = _admin(make_member)

    page = _page(admin)
    assert _state(page) == "off" and "/ads.txt is a 404" in page
    assert 'href="/ads.txt"' not in page

    no_ads.setenv("ADSENSE_CLIENT", CLIENT)
    page = _page(admin)
    assert _state(page) == "client_only" and 'href="/ads.txt"' in page
    assert "/ads.txt is a 404" not in page

    no_ads.setenv("ADSENSE_SLOT_FEED", SLOTS["ADSENSE_SLOT_FEED"])
    no_ads.setenv("ADSENSE_SLOT_POST", SLOTS["ADSENSE_SLOT_POST"])
    page = _page(admin)
    assert _state(page) == "live" and "Feed, Under a post." in page and "No slot set: Right sidebar." in page
    assert "no layout key" in page

    no_ads.setenv("ADSENSE_SLOT_SIDEBAR", SLOTS["ADSENSE_SLOT_SIDEBAR"])
    no_ads.setenv("ADSENSE_FEED_LAYOUT_KEY", "-fb+5w+4e-db+86")
    page = _page(admin)
    assert _state(page) == "live" and "No slot set" not in page and "no layout key" not in page

    assert CLIENT not in page and CLIENT[len("ca-"):] not in page
    assert not any(slot in page for slot in SLOTS.values()) and "-fb+5w+4e-db+86" not in page


def test_the_reports_link_is_there_in_every_state_and_no_ad_figure(make_member, no_ads):
    admin = _admin(make_member)
    for client in (None, CLIENT):
        if client:
            no_ads.setenv("ADSENSE_CLIENT", client)
        page = _page(admin)
        assert 'href="https://adsense.google.com/adsense/u/0/reporting"' in page
        assert "never reaches the creator fund, the Creator Studio or members" in page
