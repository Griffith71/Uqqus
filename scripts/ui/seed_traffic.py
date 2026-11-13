"""Made-up numbers for /admin/traffic, for scripts/ui/traffic.spec.ts to look at: 90 days of requests, kinds, countries and
referrers, one with the longest name the column allows, and an admin account (`ui_traffic_admin`, level 3, password
ui-Password-1). Dev only: it refuses any database but a scratch one (`ui_preview`, `ci_*` or `test*`).

    PYTHONPATH=. python scripts/ui/seed_traffic.py            put them in (again: it replaces what it put in)
    PYTHONPATH=. python scripts/ui/seed_traffic.py --remove   take every count out of site_traffic (the account stays)
"""
import os
import random
import re
import sys
import time

DB_NAME = re.compile(r"^(ui_preview|ci_\w+|test\w*)$")
ADMIN = ("ui_traffic_admin", 90790)
KINDS = {"for_you": 30, "all": 22, "post": 28, "profile": 9, "guild": 6, "search": 3, "chat": 2, "settings": 1, "help": 1, "sign_in": 2}
COUNTRIES = {"GB": 30, "US": 25, "DE": 9, "FR": 6, "IN": 5, "BR": 4, "JP": 3, "AU": 3, "unknown": 8,
             **{code: 1 for code in ("ES", "IT", "NL", "SE", "PL", "CA", "MX", "NG", "ZA", "KR", "AR", "IE")}}     # more than the table's 15
HOSTS = {"direct": 40, "internal": 35, "other": 3, "news.ycombinator.com": 6, "reddit.com": 5, "google.com": 4, "bsky.app": 2, "t.co": 2,
         "a-very-long-subdomain-of-a-site-that-nobody-would-ever-type-out.and-another-long-label.example.co.uk": 1,
         **{f"small-site-{i}.example": 1 for i in range(12)}}                                                       # more than the table's 15


def refuse_unless_scratch():
    name = os.environ.get("DATABASE_CONNECTION_POOL_URL", os.environ.get("DATABASE_URL", "")).rsplit("/", 1)[-1].split("?", 1)[0]
    if not DB_NAME.match(name):
        sys.exit(f"Refusing to seed database {name!r}: only ui_preview, ci_* or test*.")


def main():
    refuse_unless_scratch()
    from sqlalchemy import text
    from ruqqus.__main__ import app, db_session
    from ruqqus.classes import User
    with app.test_request_context("/", environ_base={"REMOTE_ADDR": "127.0.0.1"}):
        db = db_session()
        db.execute(text("DELETE FROM site_traffic"))
        if "--remove" in sys.argv:
            db.commit()
            return
        today = int(time.time()) // 86400
        rng = random.Random(7)
        statement = text("INSERT INTO site_traffic (day, dimension, value, requests) VALUES (:d, :x, :v, :n)")
        for back in range(90):
            total = int((900 + 500 * (back % 7 < 5)) * (1 + (90 - back) / 60) * rng.uniform(.85, 1.15))
            if back in (40, 41):
                total = 0       # a day with nothing: the job or the site was down
            for dimension, shares in (("kind", KINDS), ("country", COUNTRIES), ("referrer", HOSTS)):
                weight = sum(shares.values())
                for value, share in shares.items():
                    n = total * share // weight
                    if n:
                        db.execute(statement, {"d": today - back, "x": dimension, "v": value, "n": n})
        name, uid = ADMIN
        if not db.query(User).filter_by(username=name).first():
            now = int(time.time())
            db.add(User(id=uid, age_declared=1, username=name, original_username=name, password="ui-Password-1", email=f"{name}@example.com",
                        created_utc=now - 120 * 86400, creation_ip="127.0.0.1", tos_agreed_utc=now - 120 * 86400, admin_level=3))
        db.commit()
        print("Seeded site_traffic.")


if __name__ == "__main__":
    main()
