"""A member's chat name and chat devices, kept in step with their site account (Settings > Chat apps,
routes/chat_apps.py). Best effort: the chat server being down never stops a name change, a password change or a
deletion, and each failure is only logged.

- The chat name an outside Matrix app shows is the @handle (members can't set one: synapse/modules/uqqus_rules.py),
  set when the chat account is made, when the handle changes, and "Deleted account" when the account goes.
- An outside app signed in with the account ends wherever the site ends other sign-ins (a password change or reset,
  "log out other sessions"); deleting the account signs out every chat device, the site's own browsers too.
"""
from sqlalchemy import text

DELETED = "Deleted account"


def name_for(user):
    return DELETED if getattr(user, "is_deleted", False) else user.username


def is_site_device(name, site_name):
    """The site's own browsers are named "<site> chat" when they sign in (matrix_client.login_as); devices made before
    that have no name, and only the site made devices then."""
    return not name or name.startswith(f"{site_name} ")


def _mxid(db, user_id):
    row = db.execute(text("SELECT matrix_user_id FROM chat_identities WHERE user_id = :u"), {"u": user_id}).first()
    return row[0] if row else None


def _log(what, user_id, e):
    print(f"chat {what} for {user_id} not done: {type(e).__name__}", flush=True)


def sync_name(db, user, mxid=None):
    from ruqqus.helpers import matrix_client as matrix
    try:
        mxid = mxid or _mxid(db, user.id)
        if mxid:
            matrix.set_display_name(mxid, name_for(user))
    except Exception as e:
        _log("name", user.id, e)


def sign_out(db, user_id, site_name, apps_only=True):
    """Signs out the account's outside apps (or, with apps_only=False, every chat device). Returns how many."""
    from ruqqus.helpers import matrix_client as matrix
    done = 0
    try:
        mxid = _mxid(db, user_id)
        if not mxid:
            return 0
        for device in matrix.list_devices(mxid):
            if apps_only and is_site_device(device["name"], site_name):
                continue
            try:
                matrix.delete_device(mxid, device["device_id"])
                done += 1
            except Exception as e:
                _log("device sign-out", user_id, e)
    except Exception as e:
        _log("device list", user_id, e)
    return done
