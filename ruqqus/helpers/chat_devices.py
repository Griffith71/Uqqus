"""A member's chat name and chat devices, kept in step with their site account (Settings > Chat apps,
routes/chat_apps.py). Best effort: the chat server being down never stops a name change, a password change or a
deletion, and each failure is only logged.

- The chat name an outside Matrix app shows is the @handle (members can't set one: synapse/modules/uqqus_rules.py),
  set when the chat account is made, when the handle changes, and "Deleted account" when the account goes.
- Signing chat devices out is helpers/chat_signouts.py's durable queue (account_sessions.revoke and the other events that ask for
  it): `is_site_device` is the fallback it and Settings > Chat apps use for a device made before `chat_site_devices` had a row.
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


def mxid_of(db, user_id):
    """The Matrix id a member's browser will get from /api/chat/token, for the call and Space pages to compare with the
    session this browser has stored (assets/chat_src/src/token.js)."""
    from ruqqus.helpers import matrix_client as matrix
    return _mxid(db, user_id) or matrix.mxid_for(user_id)


def closing_with(db, user_id):
    """The Matrix ids a closure ends, for its answer to clear from this browser (account_deleted.html, forgetChatDevices): the account's
    own and those of the child accounts closed with it (routes/settings.close_own_account closes every child of a parent, and a family
    shares a browser). Read before the accounts close."""
    from ruqqus.helpers import child_store
    return mxid_of(db, user_id), [mxid_of(db, child) for child in child_store.child_ids_safely(db, user_id)]


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
