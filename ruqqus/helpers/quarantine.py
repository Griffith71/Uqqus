"""A held account (the signup guard: helpers/signup_risk.py decided its sign-up looked like a bot) can read the site and
change its own settings, but can't act on anyone else: post, comment, vote, forward, repost, tip, follow, join a guild,
start a chat or a group, report or vote in a poll. Each of those routes carries `@not_quarantined("<action>")`
(helpers/wrappers.py) right above its `@validate_formkey`; tests/test_quarantine_assets.py lists them, so a new route
that acts on others has to decide.

* **Never in `is_not_banned`**: that wrapper also guards Settings, email verification and deleting the account, which
  a held person needs.
* **What a held account tried is counted** (`signup_signals.note_attempt`), so /admin/signups shows what held
  accounts go on to attempt. Counts per action only.
* **Released** by an admin at /admin/signups, or by verifying an email that isn't a throwaway (`/activate`,
  ruqqus/mail/mail.py). Never by time: accounts kept dormant to be sold later are one of the things it is for.
* Admins and bot accounts are never held (`User.is_quarantined`).
tests/test_quarantine.py.
"""
from ruqqus.helpers.i18n_runtime import N_, msg

ACTIONS = ("post", "edit_post", "comment", "vote", "forward", "repost", "tip", "follow", "join_guild", "chat",
           "group", "report", "poll_vote", "story")

HELD = N_("Your account is waiting for a quick check before it can do this. To skip the wait, verify an email "
          "address in Settings > Security.")
HELD_TITLE = N_("Your account is being checked")


def refusal(v, action, r=None):
    """The message for a held account (and the attempt counted), or None."""
    if v is None or not getattr(v, "is_quarantined", False):
        return None
    if r is not None:
        from ruqqus.helpers import signup_signals
        signup_signals.note_attempt(r, v.id, action if action in ACTIONS else "other")
    return msg(HELD)
