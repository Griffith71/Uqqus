from os import environ
import requests
import time
from flask import (
    request, render_template, g, abort, jsonify, session
)
from urllib.parse import quote

from ruqqus.helpers.security import *
from ruqqus.helpers.wrappers import *
from ruqqus.classes import *
from ruqqus.__main__ import app


def mail_settings():
    """(Mailgun key, sending domain, From line) from the environment: `MAILGUN_KEY`, `MAILGUN_DOMAIN` (default
    `mail.<the site's name>`) and `MAIL_FROM` (default "<SITE_NAME> <noreply@domain>"). The key may be empty."""
    key = (environ.get("MAILGUN_KEY") or "").strip()
    domain = (environ.get("MAILGUN_DOMAIN") or "").strip() or f"mail.{app.config['SERVER_NAME']}"
    sender = (environ.get("MAIL_FROM") or "").strip() or f"{app.config['SITE_NAME']} <noreply@{domain}>"
    return key, domain, sender


def send_mail(to_address, subject, html, plaintext=None, files={}, from_address=None):
    """Sends one email through Mailgun; None when no key is set (nothing is sent, and nothing crashes)."""
    key, domain, sender = mail_settings()
    if not key:
        print("mail not sent: MAILGUN_KEY is not set", flush=True)
        return None

    data = {"from": from_address or sender,
            "to": [to_address],
            "subject": subject,
            "text": plaintext,
            "html": html,
            }

    return requests.post(f"https://api.mailgun.net/v3/{domain}/messages",
                         auth=("api", key),
                         data=data,
                         files=[("attachment", (k, files[k])) for k in files],
                         timeout=15
                         )


def send_mail_quietly(to_address, subject, html):
    """`send_mail` for the emails nobody waits for (a sign-in alert): never raises, so a mail outage can't touch the
    request or the greenlet that sent it."""
    try:
        send_mail(to_address, subject, html)
    except Exception as e:
        print(f"mail not sent: {type(e).__name__}", flush=True)


def send_verification_email(user, email=None):

    if not email:
        email = user.email

    url = f"https://{app.config['SERVER_NAME']}/activate"
    now = int(time.time())

    token = generate_hash(f"{email}+{user.id}+{now}")
    params = f"?email={quote(email)}&id={user.id}&time={now}&token={token}"

    link = url + params

    send_mail(to_address=email,
              html=render_template("email/email_verify.html",
                                   action_url=link,
                                   v=user),
              subject=f"Validate your {app.config['SITE_NAME']} account email."
              )


@app.route("/api/verify_email", methods=["POST"])
@is_not_banned
@validate_formkey
def api_verify_email(v):

    send_verification_email(v)

    return "", 204


@app.route("/activate", methods=["GET"])
@auth_desired
def activate(v):

    email = request.args.get("email", "")
    id = request.args.get("id", "")
    timestamp = request.args.get("time", "") or ""
    # a link cut short or altered is a 400 (bug hunt L3: it was a 500)
    if not (timestamp.isascii() and timestamp.isdigit()):
        abort(400)
    timestamp = int(timestamp)
    token = request.args.get("token", "")

    if int(time.time()) - timestamp > 3600:
        return render_template("message.html", v=v, title="Verification link expired.",
                               message="That link has expired. Visit your settings to send yourself another verification email."), 410

    if not validate_hash(f"{email}+{id}+{timestamp}", token):
        abort(403)

    user = g.db.query(User).filter_by(id=id).first()
    if not user:
        abort(404)

    if user.is_activated and user.email == email:
        return render_template("message_success.html", v=v,
                               title="Email already verified.", message="Email already verified."), 404

    # still free: the check when the change was asked for is up to an hour old (bug hunt: another account could take
    # the address meanwhile, and two accounts then shared it: sign-in by email and resets pick one)
    if g.db.query(User).filter(User.id != user.id, func.lower(User.email) == email.lower()).first():
        return render_template("message.html", v=v, title="That email address is already in use.",
                               message="Another account uses that address now. Visit your settings to use another."), 409

    user.email = email
    user.is_activated = True

    if not any([b.badge_id == 2 for b in user.badges]):
        mail_badge = Badge(user_id=user.id,
                           badge_id=2,
                           created_utc=time.time())
        g.db.add(mail_badge)

    g.db.add(user)

    return render_template("message_success.html", v=v, title="Email verified.", message=f"Your email {email} has been verified. Thank you.")
