"""A member's own recent sign-ins, for Settings > Security: when, roughly where, and with what browser. Plain `text()`
SQL, tested on SQLite (tests/test_sign_in_history.py).

Only the account's own rows are read, and the address is never selected: what is shown is the time, the country
(Cloudflare's `cf_country`) and the browser label (helpers/user_agents.py). The address and the browser text are erased
from the table after 90 days and the row itself after 410 (helpers/retention.py).
"""
import time

from sqlalchemy import text

from ruqqus.helpers import user_agents

SHOWN = 10
TOR = "T1"                          # Cloudflare's code for the Tor network


def recent(db, user_id, limit=SHOWN):
    """The account's latest sign-ins, newest first: [(created_utc, country code or None, user agent or None)]."""
    rows = db.execute(text("SELECT created_utc, cf_country, user_agent FROM login_events WHERE user_id = :u "
                           "ORDER BY created_utc DESC, id DESC LIMIT :n"), {"u": user_id, "n": limit}).all()
    return [(row[0], row[1], row[2]) for row in rows]


def place(code, names):
    """A country as words: "United Kingdom", "Tor network", or "Unknown location"."""
    code = (code or "").strip().upper()
    if code == TOR:
        return "Tor network"
    return names.get(code) or "Unknown location"


def label(created_utc):
    return time.strftime("%d %b %Y, %H:%M UTC", time.gmtime(created_utc or 0)).lstrip("0")


def entries(rows, names):
    """The rows as the page draws them: {"when", "iso", "place", "browser"}."""
    return [{"when": label(created), "iso": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(created or 0)),
             "place": place(country, names), "browser": user_agents.describe(agent) if agent else "Browser not recorded"}
            for created, country, agent in rows]


def page_context(db, user, names):
    """What Settings > Security needs to draw the list."""
    return {"sign_ins": entries(recent(db, user.id), names)}
