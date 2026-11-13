"""Settings > Chat (/settings/chat): the member's chat recovery code (when they said they kept it, and making a new one) and a suggestion to
switch two-factor on. The home for chat's own settings to come. Named `chat_settings_pages.py`: `from ruqqus.routes import *` would put a
module named like a helper where the helper is expected."""
from flask import g, redirect, render_template, request

from ruqqus.classes import ChatIdentity
from ruqqus.helpers.wrappers import auth_required, not_child_locked, not_qr_limited, validate_formkey
from ruqqus.__main__ import app


@app.get("/settings/chat")
@auth_required
@not_qr_limited
@not_child_locked("chat")
def settings_chat(v):
    identity = g.db.query(ChatIdentity).filter_by(user_id=v.id).first()
    set_up = bool(identity and identity.key_blob_password and identity.key_blob_recovery)
    from ruqqus.helpers import push_send
    return render_template("settings_chat.html", v=v, set_up=set_up,
                           code_saved_utc=(identity.code_saved_utc or 0) if identity else 0, mfa_on=bool(v.mfa_secret),
                           push_offered=push_send.enabled())


@app.post("/settings/chat/receipts")
@auth_required
@validate_formkey
def settings_chat_receipts(v):
    """Whether others see when this member has read their messages and is typing ("Seen"). Off, the member's chat sends a
    private read receipt (the server still counts what was read) and no typing, and shows them neither, as WhatsApp does."""
    v.chat_receipts = request.values.get("on") == "1"
    g.db.add(v)
    g.db.commit()
    return redirect("/settings/chat#seen")
