from os import environ
import requests
import urllib
import hmac
import pprint
import time
import pprint

from flask import request, jsonify, redirect, render_template, abort
from sqlalchemy import text
import logging as _logging       # a later star import rebinds `logging` to flask.logging

from ruqqus.classes import *
from ruqqus.helpers.wrappers import *
from ruqqus.helpers.security import *
from ruqqus.helpers.alerts import send_notification
from ruqqus.helpers.base36 import *
from ruqqus.helpers.get import *
from ruqqus.__main__ import app
from ruqqus.helpers import tips, tips_store
from ruqqus.helpers import coins as coins_store, premium
from ruqqus.helpers import coin_shop, paypal_events
from ruqqus.helpers.discord import add_role

CLIENT=PayPalClient()
log = _logging.getLogger(__name__)

def coins_to_price_cents(n, code=None):

    if n>=52:
        price= 100*n - 1001
    elif n>=26:
        price= 100*n-401
    elif n>=12:
        price= 100*n-101
    elif n >=4:
        price= 100*n-1
    else:
        price= 100*n+49

    if code:
        if isinstance(code, str):
            promo=get_promocode(code)
        else:
            promo=code

        if promo:
            price=promo.adjust_price(price)

    return price

@app.route("/shop/get_price", methods=["GET"])
def shop_get_price():

    coins=request.args.get("coins", "")
    coins=int(coins) if coins.isascii() and coins.isdigit() else 0

    if not coins>=1:
        return jsonify({"error": "Must attempt to buy at least one coin"}), 400

    code=request.args.get("promo","")
    promo=get_promocode(code)

    data={
        "price":coins_to_price_cents(coins, code=promo)/100,
        "promo": promo.promo_text if promo and promo.is_active else ''
        }

    return jsonify(data)

@app.route("/shop/coin_balance", methods=["GET"])
@auth_required
def shop_coin_balance(v):
    return jsonify({"balance":v.coin_balance})


@app.route("/shop/buy_coins", methods=["POST"])
@no_sanctions
@is_not_banned
@no_negative_balance("html")
@validate_formkey
def shop_buy_coins(v):

    # 1 to 10,000 coins, digits only (bug hunt H4: 0 was priced at 49 cents and then "paid off" a debt that wasn't
    # there, which locked the account; a negative or unreadable count was a 500)
    coin_count=coin_shop.parse_coin_count(request.form.get("coin_count", "1"))
    if coin_count is None:
        return jsonify({"error": coin_shop.BAD_COUNT}), 400

    code=request.form.get("promo","")
    promo=get_promocode(code)

    price=coins_to_price_cents(coin_count, code=promo)
    if price <= 0:
        return jsonify({"error": coin_shop.FREE}), 400

    new_txn=PayPalTxn(
        user_id=v.id,
        created_utc=int(time.time()),
        coin_count=coin_count,
        usd_cents=price,
        promo_id= promo.id if promo else None
        )

    return _start_order(new_txn)


def _start_order(new_txn):
    """Open the PayPal order for `new_txn` and send the browser to approve it; nothing is kept when PayPal refuses."""
    g.db.add(new_txn)
    g.db.flush()

    CLIENT.create(new_txn)

    if not new_txn.paypal_id:
        g.db.rollback()
        return jsonify({"error": "PayPal couldn't start this order. Please try again later."}), 502

    g.db.add(new_txn)
    g.db.commit()

    return redirect(new_txn.approve_url)


@app.route("/shop/negative_balance", methods=["POST"])
@no_sanctions
@is_not_banned
@validate_formkey
def shop_negative_balance(v):

    if (v.negative_balance_cents or 0) <= 0:
        return jsonify({"error": coin_shop.NOTHING_OWED}), 400

    new_txn=PayPalTxn(
        user_id=v.id,
        created_utc=int(time.time()),
        coin_count=0,
        usd_cents=v.negative_balance_cents
        )

    return _start_order(new_txn)

@app.route("/shop/buy_coins_completed", methods=["GET"])
@no_sanctions
@is_not_banned
def shop_buy_coins_completed(v):

    #look up the txn
    id=request.args.get("txid")
    if not id:
        abort(400)
    id=base36decode(id)
    txn=g.db.query(PayPalTxn
        #).with_for_update(
        ).filter_by(user_id=v.id, id=id, status=1).first()
    #v=g.db.query(User).with_for_update().options(lazyload('*')).filter_by(id=v.id).first()

    if not txn:
        abort(400)

    if txn.promo and not txn.promo.promo_is_active:
        return jsonify({"error":f"The promo code `{txn.promo.code}` is not currently valid. Please begin a new transaction."}), 422

    if not CLIENT.capture(txn):
        abort(402)

    txn.created_utc=int(time.time())

    g.db.add(txn)
    g.db.flush()

    #successful payment - award coins

    if txn.coin_count:
        g.db.flush()
        coins_store.give(g.db, v.id, txn.coin_count)
        g.db.refresh(v)
        # buying coins without a running week starts one, as the purchase form says (helpers/premium.py)
        if not v.has_premium_no_renew and premium.redeem_week(g.db, v, int(time.time())):
            add_role(v, "premium")
    else:
        # paying off what the account owes: never below 0 (helpers/coin_shop.py)
        v.negative_balance_cents = coin_shop.owed_after_payment(v.negative_balance_cents, txn.usd_cents)

    g.db.add(v)
    g.db.commit()

    return render_template(
        "single_txn.html", 
        v=v, 
        txns=[txn],
        msg="The Royal Bank has minted your Coins. Here is a copy of your order."
        )

@app.route("/shop/paypal_webhook", methods=["POST"])
def paypal_webhook_handler():
    """PayPal's word that money went back (a refund or a chargeback): what the account owes grows by it. Each event
    is handled once, and an order never counts for more than it cost (helpers/paypal_events.py, bug hunt H3)."""

    event=request.get_json(silent=True)
    if not isinstance(event, dict):
        abort(400)

    #Verify paypal signature
    data={
        "auth_algo":request.headers.get("PAYPAL-AUTH-ALGO"),
        "cert_url":request.headers.get("PAYPAL-CERT-URL"),
        "transmission_id":request.headers.get("PAYPAL-TRANSMISSION-ID"),
        "transmission_sig":request.headers.get("PAYPAL-TRANSMISSION-SIG"),
        "transmission_time":request.headers.get("PAYPAL-TRANSMISSION-TIME"),
        "webhook_id":CLIENT.webhook_id,
        "webhook_event":event
        }


    x=CLIENT._post("/v1/notifications/verify-webhook-signature", data=data)

    if x.json().get("verification_status") != "SUCCESS":
        abort(403)

    if not event.get("id") or not event.get("event_type"):
        abort(400)

    if not paypal_events.is_reversal(event):
        return "", 204

    event_id=str(event["id"])[:64]
    # once per event: PayPal sends one again when it isn't sure it arrived
    first=g.db.execute(text("INSERT INTO paypal_events (event_id, event_type, created_utc) VALUES (:e, :t, :now) "
                            "ON CONFLICT (event_id) DO NOTHING RETURNING event_id"),
                       {"e": event_id, "t": str(event["event_type"])[:64], "now": int(time.time())}).first()
    if not first:
        g.db.rollback()
        return "", 204

    txn=_reversed_txn(event)
    if not txn:
        # not one of this site's orders (or one it can't name): kept as handled, so PayPal stops sending it
        log.warning("PayPal %s %s names no order of this site", event["event_type"], event_id)
        g.db.commit()
        return "", 204

    refund=paypal_events.amount_cents(event)
    counts=paypal_events.chargeable_cents(refund, txn.refunded_cents, txn.usd_cents)
    g.db.execute(text("UPDATE paypal_events SET txn_id = :t WHERE event_id = :e"), {"t": txn.id, "e": event_id})
    if counts:
        txn.refunded_cents=(txn.refunded_cents or 0) + counts
        txn.user.negative_balance_cents=(txn.user.negative_balance_cents or 0) + paypal_events.owed_for(counts)
        g.db.add(txn.user)
    txn.status=-2

    g.db.add(txn)
    g.db.commit()

    return "", 204


def _reversed_txn(event):
    """The coin order a reversal is about, locked for this transaction, or None."""
    order, capture, legacy = paypal_events.keys(event)
    query=g.db.query(PayPalTxn).with_for_update(of=PayPalTxn)      # the order row only, not its eager joins
    for column, value in ((PayPalTxn.paypal_id, order), (PayPalTxn.paypal_capture_id, capture), (PayPalTxn.paypal_id, legacy)):
        if value:
            txn=query.filter(column == str(value)[:64]).first()
            if txn:
                return txn
    return None


def _tip_notification(kind, permalink, coins, recipient):
    """What the author is told of a tip. Tips are anonymous: never who sent it."""
    text = f"Someone liked [your {kind}]({permalink}) and has tipped you {coins} coin{'s' if coins != 1 else ''}!\n\n"
    # coins buy Premium only when the member asks, or with auto-renew switched on (helpers/premium.py, audit $-04)
    text += (f"The coins are in your balance: keep them, tip someone, or redeem a week of "
             f"[{app.config['SITE_NAME']} Premium](/settings/premium) per coin.")
    return text


@app.route("/gift_post/<pid>", methods=["POST"])
@no_sanctions
@is_not_banned
@no_negative_balance("toast")
@validate_formkey
def gift_post_pid(pid, v):

    post=get_post(pid, v=v)

    if post.in_private_guild_for(v):
        abort(403)

    if post.author_id==v.id:
        return jsonify({"error":"You can't give awards to yourself."}), 403   

    if post.is_anonymous:
        return jsonify({"error":"You can't give awards to anonymous posts."}), 403

    if post.deleted_utc > 0:
        return jsonify({"error":"You can't give awards to deleted posts"}), 403

    if post.is_banned:
        return jsonify({"error":"You can't give awards to removed posts"}), 403

    if post.author.is_deleted:
        return jsonify({"error":"You can't give awards to deleted accounts"}), 403

    if post.author.is_banned and not post.author.unban_utc:
        return jsonify({"error":"You can't give awards to banned accounts"}), 403

    u=get_user(post.author.username, v=v)

    if u.is_blocking:
        return jsonify({"error":"You can't give awards to someone you're blocking."}), 403

    if u.is_blocked:
        return jsonify({"error":"You can't give awards to someone that's blocking you."}), 403

    # a bot's post takes tips too (the owner, 2026-10-20): a member bot's coins go to the member who runs it, a site
    # bot's to nobody, spent only to lift the post (helpers/tips.py recipient_id)
    receiver = u
    if u.is_bot:
        owner_id = tips.recipient_id(u.id, True, u.bot_owner_id)
        receiver = g.db.query(User).filter_by(id=owner_id).first() if owner_id else None
        if owner_id and receiver is None:
            return jsonify({"error":"This bot can't take tips right now."}), 403
        if receiver is not None:
            if receiver.id == v.id:
                return jsonify({"error":"You can't tip your own bot: the coins would come back to you."}), 403
            if receiver.is_deleted or (receiver.is_banned and not receiver.unban_utc) or v.any_block_exists(receiver):
                return jsonify({"error":"This bot can't take tips right now."}), 403

    coins = tips.parse_coins(request.args.get("coins"))
    if coins is None:
        return jsonify({"error": f"A tip is a whole number of coins, from 1 to {tips.MAX_TIP}."}), 400

    # one conditional statement checks and moves the coins (helpers/coins.py): parallel tips can't create coins
    g.db.flush()
    moved = (coins_store.move(g.db, v.id, receiver.id, coins) if receiver is not None
             else coins_store.spend(g.db, v.id, coins))
    if not moved:
        g.db.rollback()
        return jsonify({"error":"You don't have that many coins to give!"}), 403
    g.db.refresh(v)
    if receiver is not None:
        g.db.refresh(receiver)

    # the tip, the post's totals and its place in the ranked sorts move with the coins, in one transaction
    # (helpers/tips.py: the boost counts in rank_hot / rank_best, never in the score or the Top sort)
    tips_store.record(g.db, v.id, coins, post_id=post.id)
    tips_store.recompute_post(g.db, post.id, post.author_id)
    g.db.flush()
    g.db.refresh(post, attribute_names=["tip_coins", "tip_boost", "rank_hot", "rank_best"])
    post.score_hot = post.rank_hot
    post.score_best = post.rank_best
    g.db.add(post)
    g.db.commit()

    # send_notification commits on its own (helpers/alerts.py): only once the tip is safe; a site bot's tip tells nobody
    if receiver is not None:
        kind = "bot's post" if u.is_bot else "post"
        send_notification(receiver, _tip_notification(kind, post.permalink, coins, receiver))
        g.db.commit()

    return jsonify({"message":"Tip Successful!"})

@app.route("/gift_comment/<cid>", methods=["POST"])
@no_sanctions
@is_not_banned
@no_negative_balance("toast")
@validate_formkey
def gift_comment_pid(cid, v):

    comment=get_comment(cid, v=v)

    if comment.post and comment.post.in_private_guild_for(v):
        abort(403)

    if comment.author_id==v.id:
        return jsonify({"error":"You can't give awards to yourself."}), 403      

    if comment.is_anonymous:
        return jsonify({"error":"You can't give awards to anonymous comments."}), 403

    if comment.author.is_bot:
        return jsonify({"error":"Bots don't take tips."}), 403

    if comment.deleted_utc > 0:
        return jsonify({"error":"You can't give awards to deleted posts"}), 403

    if comment.is_banned:
        return jsonify({"error":"You can't give awards to removed posts"}), 403

    if comment.author.is_deleted:
        return jsonify({"error":"You can't give awards to deleted accounts"}), 403

    if comment.author.is_banned and not comment.author.unban_utc:
        return jsonify({"error":"You can't give awards to banned accounts"}), 403

    u=get_user(comment.author.username, v=v)

    if u.is_blocking:
        return jsonify({"error":"You can't give awards to someone you're blocking."}), 403

    if u.is_blocked:
        return jsonify({"error":"You can't give awards to someone that's blocking you."}), 403

    coins = tips.parse_coins(request.args.get("coins"))
    if coins is None:
        return jsonify({"error": f"A tip is a whole number of coins, from 1 to {tips.MAX_TIP}."}), 400

    # one conditional statement checks and moves the coins (helpers/coins.py): parallel tips can't create coins
    g.db.flush()
    if not coins_store.move(g.db, v.id, u.id, coins):
        g.db.rollback()
        return jsonify({"error":"You don't have that many coins to give!"}), 403
    g.db.refresh(v)
    g.db.refresh(u)

    tips_store.record(g.db, v.id, coins, comment_id=comment.id)
    g.db.commit()

    # send_notification commits on its own (helpers/alerts.py): only once the tip is safe
    send_notification(u, _tip_notification("comment", comment.permalink, coins, u))
    g.db.commit()

    return jsonify({"message":"Tip Successful!"})


@app.route("/paypaltxn/<txid>")
@auth_required
def paypaltxn_txid(txid, v):

    txn = get_txid(txid)

    if txn.user_id != v.id and v.admin_level<4:
        abort(403)

    return render_template(
        "single_txn.html", 
        v=v, 
        txns=[txn]
        )


@app.route("/settings/premium/redeem", methods=["POST"])
@is_not_banned
@validate_formkey
def premium_redeem(v):
    """Spend one coin on a week of Premium, added after any time left (helpers/premium.py). Only when asked."""
    if v.negative_balance_cents:
        return jsonify({"error": "Settle your balance first."}), 402
    if not premium.redeem_week(g.db, v, int(time.time())):
        g.db.rollback()
        return jsonify({"error": "You don't have a coin to redeem."}), 403
    add_role(v, "premium")
    g.db.commit()
    return jsonify({"message": f"Premium until {v.renew_premium_time} UTC."})


@app.route("/settings/premium/auto_renew", methods=["POST"])
@auth_required
@validate_formkey
def premium_auto_renew(v):
    """Whether a coin is spent on the next week when one runs out (`setting=true|false`)."""
    v.premium_auto_renew = request.values.get("setting") == "true"
    g.db.add(v)
    g.db.commit()
    return jsonify({"premium_auto_renew": v.premium_auto_renew})
