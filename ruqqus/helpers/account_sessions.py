"""Ending a member's other sign-ins.

A sign-in is a cookie holding the account's `login_nonce` as it was when the member signed in; a request is refused
once the account's nonce is higher (`wrappers.get_logged_in_user`). So raising the nonce signs out every other browser
at once, with no list of sessions to keep. Form keys are made from the nonce too, so a page open elsewhere stops
working the same moment.

It is raised when a credential changes, so that whoever held the old one is out: a new password, two-factor
authentication switched on or off, a reset by email (which also makes that reset link usable once: its token holds the
nonce), and "log out all other devices". The browser that made the change keeps its own sign-in by taking the new
number into its cookie.
"""


def end_other_sessions(user, session=None):
    """Raises the account's nonce; `session` (the request's) is given the new number so this browser stays signed in.
    The caller saves the user. Returns the new nonce."""
    user.login_nonce = (user.login_nonce or 0) + 1
    if session is not None:
        session["login_nonce"] = user.login_nonce
    return user.login_nonce


OTHERS_SIGNED_OUT = "Your other devices have been signed out."
