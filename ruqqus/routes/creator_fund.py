"""The creator fund: a member's page (Settings > Creator fund) and the admins' page that pays a month.
The rules are helpers/creator_fund.py, the SQL helpers/creator_fund_store.py. Nothing here reads anything about ads."""
import time
from types import SimpleNamespace
from urllib.parse import quote

from flask import g, redirect, render_template, request

from ruqqus.helpers import creator_fund as rules
from ruqqus.helpers import creator_fund_store as store
from ruqqus.helpers.alerts import send_notification
from ruqqus.helpers.wrappers import admin_level_required, auth_required, is_not_banned, validate_formkey
from ruqqus.__main__ import app, cache

ADMIN_LEVEL = 4             # coins are money: as /admin/money's most powerful tools


@cache.memoize(600)
def _points_so_far(user_id, month):
    """A member's points this month so far, as their own page shows them (10 minutes old at most)."""
    rows = store.points(g.db, month, member=user_id, eligible_only=False)
    return rows[0] if rows else (user_id, 0, 0, 0)


@app.get("/settings/creator_fund")
@auth_required
def settings_creator_fund(v):
    now = int(time.time())
    month = rules.month_of(now)
    _, points, accounts, _ = _points_so_far(v.id, month)
    return render_template(
        "settings_creator_fund.html", v=v,
        taking_part=bool(v.creator_fund_opt_in_utc),
        month_name=rules.month_name(month),
        points=points, accounts=accounts, min_points=rules.min_points(),
        payouts=[(rules.month_name(m), p, c, s) for m, p, c, s in store.payouts_of_member(g.db, v.id)],
        rules=rules,
        msg=request.args.get("msg"), error=request.args.get("error"),
    )


@app.post("/settings/creator_fund")
@is_not_banned
@validate_formkey
def settings_creator_fund_save(v):
    if request.form.get("action") == "leave":
        store.set_opt_in(g.db, v.id, False)
        g.db.commit()
        return redirect("/settings/creator_fund?msg=" + quote("You have left the creator fund."))
    if request.form.get("agree") != "1":
        return redirect("/settings/creator_fund?error=" + quote("Tick the box to agree to the rules first."))
    store.set_opt_in(g.db, v.id, True)
    g.db.commit()
    return redirect("/settings/creator_fund?msg=" + quote("You are taking part in the creator fund."))


# --- admin ---------------------------------------------------------------------------------

def _admin_redirect(month, msg=None, error=None, budget=None):
    url = "/admin/creator_fund?month=" + quote(month)
    if budget:
        url += "&budget=" + quote(str(budget))
    if error:
        return redirect(url + "&error=" + quote(error))
    return redirect(url + "&msg=" + quote(msg or "Saved."))


@app.get("/admin/creator_fund")
@admin_level_required(ADMIN_LEVEL)
def admin_creator_fund(v):
    now = int(time.time())
    month = rules.parse_month(request.args.get("month")) or rules.last_month(now)
    budget = rules.parse_budget(request.args.get("budget"))
    paid = store.is_paid(g.db, month)
    over = rules.is_over(month, now)
    lines = store.preview(g.db, month, budget, now) if (budget and over and not paid) else []
    return render_template(
        "admin/creator_fund.html", v=v,
        month=month, month_name=rules.month_name(month), budget=budget, paid=paid, over=over,
        lines=lines, payouts=store.payouts_of_month(g.db, month) if paid else [],
        months=store.paid_months(g.db), min_points=rules.min_points(), rules=rules,
        msg=request.args.get("msg"), error=request.args.get("error"),
    )


@app.post("/admin/creator_fund/pay")
@admin_level_required(ADMIN_LEVEL)
@validate_formkey
def admin_creator_fund_pay(v):
    now = int(time.time())
    month = rules.parse_month(request.form.get("month"))
    budget = rules.parse_budget(request.form.get("budget"))
    if not month or not budget:
        return _admin_redirect(month or rules.last_month(now), error="Choose a month and a budget of 1 or more coins.")
    if not rules.is_over(month, now):
        return _admin_redirect(month, error="That month is not over yet.", budget=budget)
    held = {int(x) for x in request.form.getlist("hold") if x.isascii() and x.isdigit()}

    g.db.flush()                 # nothing pending on a user row may be written back over the new balances
    try:
        result = store.pay(g.db, month, budget, held, v.id, now)
    except store.AlreadyPaid:
        g.db.rollback()
        return _admin_redirect(month, error=f"{rules.month_name(month)} has been paid already.")
    g.db.commit()
    g.db.expire_all()

    # each notification commits on its own (helpers/alerts.py), so they go out after the payment is safe
    for user_id, coins, status in result:
        if status == "paid":
            send_notification(SimpleNamespace(id=user_id),
                              f"You earned {coins} coins from the creator fund for {rules.month_name(month)}. "
                              f"They are in your coin balance. [Creator fund](/settings/creator_fund)")
    g.db.commit()
    paid = sum(c for _, c, s in result if s == "paid")
    return _admin_redirect(month, msg=f"Paid {paid} coins to {sum(1 for r in result if r[2] == 'paid')} members"
                                      f"{f', {len(held & {r[0] for r in result})} held' if held else ''}.")


@app.post("/admin/creator_fund/release/<int:payout_id>")
@admin_level_required(ADMIN_LEVEL)
@validate_formkey
def admin_creator_fund_release(payout_id, v):
    now = int(time.time())
    g.db.flush()
    done = store.release(g.db, payout_id, now)
    if done is None:
        g.db.rollback()
        return _admin_redirect(rules.parse_month(request.form.get("month")) or rules.last_month(now),
                               error="That payout is not held (it may have been paid already).")
    user_id, coins, month = done
    g.db.commit()
    g.db.expire_all()
    send_notification(SimpleNamespace(id=user_id),
                      f"You earned {coins} coins from the creator fund for {rules.month_name(month)}. "
                      f"They are in your coin balance. [Creator fund](/settings/creator_fund)")
    g.db.commit()
    return _admin_redirect(month, msg=f"Paid {coins} coins.")
