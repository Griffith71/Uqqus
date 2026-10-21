"""Moving coins (helpers/coins.py) and Premium bought with them (helpers/premium.py): audit $-01, $-04, $-05.
The promises: a coin moves in one conditional statement, so a balance never goes negative and parallel spends can't
create coins; Premium is bought only when asked and "Premium only" needs a running week, never just a coin."""
import threading
from pathlib import Path
from types import SimpleNamespace as NS

import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.orm import Session

from ruqqus.helpers import coins, premium

ROOT = Path(__file__).resolve().parent.parent
NOW = 1_800_000_000


def read(*parts):
    return ROOT.joinpath(*parts).read_text(encoding="utf-8")


@pytest.fixture
def engine(tmp_path):
    engine = create_engine(f"sqlite:///{tmp_path / 'coins.db'}", connect_args={"check_same_thread": False})
    with engine.begin() as conn:
        conn.execute(text("""CREATE TABLE users (id INTEGER PRIMARY KEY, coin_balance INTEGER DEFAULT 0,
                             premium_auto_renew BOOLEAN DEFAULT 0, premium_expires_utc INTEGER DEFAULT 0,
                             negative_balance_cents INTEGER DEFAULT 0, is_deleted BOOLEAN DEFAULT 0)"""))
        conn.execute(text("INSERT INTO users (id, coin_balance) VALUES (1, 499), (2, 0)"))
    return engine


def test_spend_and_move_are_all_or_nothing(engine):
    with Session(engine) as db:
        assert coins.move(db, 1, 2, 400) is True
        assert coins.move(db, 1, 2, 400) is False                 # 99 left: refused, nothing moves
        assert (coins.balance(db, 1), coins.balance(db, 2)) == (99, 400)
        assert coins.spend(db, 1, 0) is False and coins.spend(db, 1, -5) is False
        assert coins.move(db, 1, 1, 10) is False                  # never to oneself
        db.commit()


def test_parallel_spends_never_create_coins(engine):
    """Five tips of 400 from 499 coins, at once, each in its own session: exactly one goes through."""
    results = []

    def tip():
        with Session(engine) as db:
            ok = coins.move(db, 1, 2, 400)
            db.commit()
            results.append(ok)

    threads = [threading.Thread(target=tip) for _ in range(5)]
    [t.start() for t in threads]
    [t.join() for t in threads]
    with Session(engine) as db:
        assert results.count(True) == 1
        assert coins.balance(db, 1) + coins.balance(db, 2) == 499     # no coin created or lost


def test_premium_is_bought_only_when_asked(engine):
    with Session(engine) as db:
        db.execute(text("UPDATE users SET coin_balance = 3 WHERE id = 1"))
        user = NS(id=1, premium_expires_utc=0, premium_first_purchased_utc=0)
        db.refresh = lambda obj: None
        db.add = lambda obj: None
        assert premium.redeem_week(db, user, NOW) is True
        assert user.premium_expires_utc == NOW + premium.WEEK and coins.balance(db, 1) == 2
        assert premium.redeem_week(db, user, NOW) is True             # a second week is added after the first
        assert user.premium_expires_utc == NOW + 2 * premium.WEEK
        db.execute(text("UPDATE users SET premium_auto_renew = 1, premium_expires_utc = :e WHERE id = 1"), {"e": NOW + 60})
        assert premium.due_ids(db, NOW) == [1]
        db.execute(text("UPDATE users SET premium_auto_renew = 0 WHERE id = 1"))
        assert premium.due_ids(db, NOW) == []                          # off: never renewed


def test_the_routes_use_the_one_statement_and_reading_premium_spends_nothing():
    paypal = read("ruqqus", "routes", "paypal.py")
    # a post's tip goes to its author, a member bot's owner, or (a site bot's) to nobody: helpers/tips.py recipient_id
    for name, moves in (("gift_post_pid", ("coins_store.move(g.db, v.id, receiver.id, coins)",
                                           "coins_store.spend(g.db, v.id, coins)")),
                        ("gift_comment_pid", ("coins_store.move(g.db, v.id, u.id, coins)",))):
        body = paypal.split(f"def {name}(", 1)[1].split("\n@app", 1)[0]
        assert all(m in body for m in moves) and "coin_balance -=" not in body, name
    settings = read("ruqqus", "routes", "settings.py")
    assert "coins_store.spend(g.db, v.id, 20)" in settings and "coin_balance-=20" not in settings
    user = read("ruqqus", "classes", "user.py")
    prop = user.split("def has_premium(self):", 1)[1].split("@property", 1)[0]
    assert "coin_balance" not in prop                                  # $-04: reading it never spends
    no_renew = user.split("def has_premium_no_renew(self):", 1)[1].split("@property", 1)[0]
    assert "coin_balance" not in no_renew                              # $-05: a coin isn't Premium
    assert "renew_premium(now)" in read("scripts", "renew_circles.py")
