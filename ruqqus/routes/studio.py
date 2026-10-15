"""Creator Studio (helpers/studio.py rules, helpers/studio_store.py counting): `/studio`, a Premium member's earnings and
how their posts are doing, with the way to their paid circles and the creator fund; `/studio/insights` is post insights
(routes/insights.py). Anyone else gets a page that explains it. Only counts, never who, and nothing about ad money
(helpers/studio.py says why)."""
import time

from flask import g, render_template

from ruqqus.helpers import circle_store, insights_store
from ruqqus.helpers import studio as rules
from ruqqus.helpers import studio_store
from ruqqus.helpers import creator_fund as fund_rules
from ruqqus.helpers.wrappers import auth_required
from ruqqus.__main__ import app


@app.route("/studio", methods=["GET"])
@auth_required
def studio(v):
    if not v.has_premium_no_renew:                     # never has_premium: reading it spends a coin to renew
        return render_template("studio_locked.html", v=v)
    from ruqqus.routes.creator_fund import _points_so_far

    now = int(time.time())
    rows, recent, total = rules.summary(studio_store.earnings(g.db, v.id, now - rules.WINDOW_DAYS * rules.DAY),
                                        studio_store.earnings(g.db, v.id))
    paid = circle_store.paid_circles(g.db, v.id)
    month = fund_rules.month_of(now)
    _, points, _, _ = _points_so_far(v.id, month)
    posts = insights_store.overview(g.db, v, now)
    return render_template(
        "studio.html", v=v, studio_tab="overview",
        rows=rows, recent=recent, total=total, coins=rules.coins, window=rules.WINDOW_DAYS,
        paid_circles=len(paid), subscribers=studio_store.subscribers_now(g.db, v.id, now),
        taking_part=bool(v.creator_fund_opt_in_utc), month_name=fund_rules.month_name(month), points=points,
        min_points=fund_rules.min_points(),
        post_count=len(posts), views=sum(row["views"] for row in posts),
    )
