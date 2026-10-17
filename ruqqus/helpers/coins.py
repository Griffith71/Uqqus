"""Moving coins (tips, the paid username change, Premium). Plain `text()` SQL, tested on SQLite (tests/test_coins.py).

**A coin moves in one statement that checks and charges at once**:
    UPDATE users SET coin_balance = coin_balance - :c WHERE id = :p AND coin_balance >= :c
so a balance can never go negative and two requests can never spend the same coin. Reading a balance into Python,
comparing and writing it back (what tips did: audit $-01) lets parallel requests each write a total worked out from
the same old balance, and coins get created. The callers flush their pending ORM changes first and refresh the
`User` objects after, so a stale `coin_balance` held by the session is never written back. Nothing here commits.
"""
from sqlalchemy import text


def spend(db, user_id, amount):
    """Takes `amount` coins from one account. True when it had them (and they're gone); False and nothing changed
    otherwise."""
    amount = int(amount)
    if amount <= 0:
        return False
    result = db.execute(text("UPDATE users SET coin_balance = coin_balance - :c WHERE id = :u AND coin_balance >= :c"),
                        {"c": amount, "u": user_id})
    return result.rowcount == 1


def give(db, user_id, amount):
    amount = int(amount)
    if amount > 0:
        db.execute(text("UPDATE users SET coin_balance = coin_balance + :c WHERE id = :u"), {"c": amount, "u": user_id})


def move(db, payer_id, payee_id, amount):
    """Moves `amount` coins from the payer to the payee, both or neither. True when it happened."""
    if payer_id == payee_id or not spend(db, payer_id, amount):
        return False
    give(db, payee_id, amount)
    return True


def balance(db, user_id):
    return db.execute(text("SELECT coin_balance FROM users WHERE id = :u"), {"u": user_id}).scalar() or 0
