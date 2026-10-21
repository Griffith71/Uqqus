"""New-sign-in emails: the rules (what counts as new, who is told, how often). No I/O except the two small queries and the
Redis key, so it is tested on SQLite with a fake Redis (tests/test_signin_alerts.py). Sending is `mail.send_mail_quietly`.

An account with a **verified email** that has the setting on is emailed when a sign-in comes from a country, or a
browser and system, that it has not used in the last 90 days. Not for an account's first sign-in (nothing to compare), at
most once a day per account, and never on the failure of anything: the sign-in has already succeeded and the email is
best effort. The email names the browser, the country and the time, never the address, and says nothing about which
password was typed (it was the right one, or there would be no sign-in).

Sign-ins recorded before the browser text was kept have no browser, so they say nothing about browsers: while an account
has no recorded browser in the window only a new country counts (otherwise every account would be emailed on its first
sign-in after this shipped).
"""
from sqlalchemy import text

from ruqqus.helpers import sign_in_history, user_agents

WINDOW = 90 * 86400
ONE_A_DAY = 86400


def previous(db, user_id, now):
    """[(country, user agent)] of the account's sign-ins in the last 90 days, before the one being made."""
    rows = db.execute(text("SELECT cf_country, user_agent FROM login_events WHERE user_id = :u AND created_utc >= :since"),
                      {"u": user_id, "since": now - WINDOW}).all()
    return [(row[0], row[1]) for row in rows]


def reasons(before, country, user_agent):
    """Why this sign-in is new: [] (it is not), or any of "country", "browser"."""
    if not before:
        return []                                   # the account's first sign-in has nothing to be new against
    found = []
    countries = {(c or "").upper() for c, _ in before}
    if (country or "").upper() not in countries:
        found.append("country")
    seen = {user_agents.parse(a) for _, a in before if a}
    if seen and user_agents.parse(user_agent) not in seen:
        found.append("browser")
    return found


def wants_email(user):
    """Verified email, and the setting on."""
    return bool(getattr(user, "signin_alerts", True) and getattr(user, "is_activated", False) and getattr(user, "email", None))


def claim_today(redis, user_id):
    """True the first time in a day. With no Redis it allows it (one email less is better than a crash, but so is one
    more than none)."""
    if redis is None:
        return True
    try:
        return bool(redis.set(f"signin_alert:{user_id}", 1, nx=True, ex=ONE_A_DAY))
    except Exception:
        return False                                # unsure: don't email


def check(db, user, country, user_agent, now, redis, names):
    """None, or what the email says ({"browser", "place", "when", "why"}), for a sign-in about to be recorded.
    Call it BEFORE the sign-in is added to `login_events`, or it would be compared with itself."""
    if not wants_email(user):
        return None
    why = reasons(previous(db, user.id, now), country, user_agent)
    if not why or not claim_today(redis, user.id):
        return None
    return {"browser": user_agents.describe(user_agent), "place": sign_in_history.place(country, names),
            "when": sign_in_history.label(now), "why": why}


def subject(site_name):
    return f"New sign-in to your {site_name} account"
