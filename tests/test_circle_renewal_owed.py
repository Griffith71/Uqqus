"""A paid circle's renewal never takes coins from an account that owes after a chargeback (bug hunt): it lapses, as
Premium's renewals and subscribing already refuse such an account."""
from sqlalchemy import text

import test_circle_store as base
from ruqqus.helpers import circle_store as store
from ruqqus.helpers import circles as c

db = base.db            # the same in-memory database as tests/test_circle_store.py


def test_an_account_that_owes_lapses_instead_of_paying(db):
    fans = base.paid(db, 10)
    store.subscribe(db, fans, base.FAN, base.NOW)
    db.execute(text("UPDATE users SET negative_balance_cents = 500 WHERE id = :i"), {"i": base.FAN})
    db.commit()
    assert store.renew_due(db, base.NOW + c.PERIOD) == [("lapsed", base.FAN, base.OWNER, 10, None, "Supporters")]
    assert (base.coins(db, base.FAN), base.coins(db, base.OWNER)) == (90, 10)
