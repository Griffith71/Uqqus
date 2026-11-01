"""`GET /admin/traffic` (level 3): the site's own count of requests for its pages (helpers/traffic.py) beside the sign-ups,
posts and comments of the same days. English, as every admin page. Not `traffic.py`: a route module named like a helper
would be replaced by it in files that star-import both."""
import time

from flask import g, render_template, request

from ruqqus.helpers import traffic as rules
from ruqqus.helpers import traffic_store as store
from ruqqus.helpers.regions import ENGLISH_COUNTRY_NAMES
from ruqqus.helpers.wrappers import admin_level_required
from ruqqus.__main__ import app

TOP = 15
REFERRER_LABELS = {rules.DIRECT: "None (typed, a bookmark, or the browser didn't say)", rules.INTERNAL: "This site",
                   rules.OTHER: "Everything else"}


def _labelled(rows, label):
    for row in rows:
        row["label"] = label(row)
    return rows


def _country_label(row):
    if row["value"] == rules.OTHER:
        return "1 more country" if row["more"] == 1 else f"{row['more']} more countries"
    return "Not known" if row["value"] == rules.UNKNOWN else ENGLISH_COUNTRY_NAMES.get(row["value"], row["value"])


@app.get("/admin/traffic")
@admin_level_required(3)
def admin_traffic(v):
    data = store.report(g.db, int(time.time()), rules.parse_days(request.args.get("days")))
    total = data["requests"]
    kinds = _labelled(rules.ranked(data["kinds"], total), lambda row: rules.KIND_LABELS.get(row["value"], row["value"]))
    referrers = _labelled(rules.ranked_top(data["referrers"], total, TOP, always=(rules.DIRECT, rules.INTERNAL)),
                          lambda row: REFERRER_LABELS.get(row["value"], row["value"]))
    countries = _labelled(rules.ranked_top(data["countries"], total, TOP, always=(rules.UNKNOWN,)), _country_label)
    last = time.strftime("%d %b %Y, %H:%M UTC", time.gmtime(data["last_flush_utc"])) if data["last_flush_utc"] else ""
    return render_template("admin/traffic.html", v=v, data=data, windows=rules.WINDOWS, kinds=kinds, referrers=referrers,
                           countries=countries, last_flush=last, keep_days=rules.KEEP_DAYS, daily_hosts=rules.DAILY_HOSTS, top=TOP)
