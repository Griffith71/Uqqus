"""Credential events: ending a member's other sign-ins, and their chat devices with them (`revoke`, the ONE place a credential event
happens; tests/test_revocation_assets.py fails on any other write of `login_nonce`).

A sign-in is a cookie holding the account's `login_nonce` as it was when the member signed in; a request is refused
once the account's nonce is higher (`wrappers.get_logged_in_user`). So raising the nonce signs out every other browser
at once, with no list of sessions to keep. Form keys are made from the nonce too, so a page open elsewhere stops
working the same moment.

It is raised when a credential changes, so that whoever held the old one is out: a new password, two-factor
authentication switched on or off, a reset by email (which also makes that reset link usable once: its token holds the
nonce), a sign-in with the two-factor recovery code, a parent's new password for a child, "log out all other devices" and
closing an account. The browser that made the change keeps its own sign-in by taking the new number into its cookie.

Each also queues a chat sign-out (helpers/chat_signouts.py), worked after the commit and retried for ever. With
`spare_this_sign_in` (Log out everywhere, a password change, two-factor on or off: the owner's decision 2) the site chat devices
this cookie's sign-in started are kept, at most one chat and one call device, found here on the server; everything else goes, the
site's own browsers included (a browser keeps its locked crypto store when its device is signed out, so nothing is lost there).
"""
from ruqqus.helpers import chat_signouts
from ruqqus.helpers.i18n_runtime import tr_lazy


def _end_other_sessions(user, session=None):
    """Raises the account's nonce; `session` (the request's) is given the new number so this browser stays signed in.
    The caller saves the user. Returns the new nonce. Private to `revoke`: never change only one half of a credential event."""
    user.login_nonce = (user.login_nonce or 0) + 1
    if session is not None:
        session["login_nonce"] = user.login_nonce
    return user.login_nonce


def revoke(db, user, reason, *, session=None, spare_this_sign_in=False, now_ms=None):
    """The one place a credential event happens. Raises login_nonce (form keys, reset links, pending QR approvals) and gives
    `session` (this request's cookie) the new number so this browser stays signed in. With spare_this_sign_in, the site chat
    devices whose sign_in is sha256(session["session_id"]) are spared (decision 2): found here, on the server, never from the
    browser, and at most one chat and one call device. Queues the chat sign-out of everything else (chat_signouts.queue) in this
    transaction; its Synapse calls run after the commit (g.after_commit), never before. A database without the migration still gets
    the new nonce (the chat sign-out is only logged). Returns the new nonce."""
    if spare_this_sign_in and session is None:
        raise ValueError("spare_this_sign_in needs the request's session")
    nonce = _end_other_sessions(user, session)
    spared = ()
    if spare_this_sign_in:
        session_id = session.get("session_id")

        def spared():
            # found by queue once it holds the mint lock: read before it, a device this sign-in made in between would be revoked
            return chat_signouts.this_sign_in(db, user.id, session_id)
    chat_signouts.queue(db, user.id, reason, "all", spared=spared, now_ms=now_ms)
    return nonce


OTHERS_SIGNED_OUT = tr_lazy("Your other devices have been signed out.")
