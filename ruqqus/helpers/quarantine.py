"""A held account (the signup guard: helpers/signup_risk.py decided its sign-up looked like a bot, or one of the site's own
automatic checks fired: helpers/auto_quarantine.py) can read the site and change its own settings, but can't act on anyone
else: post, comment, vote, forward, repost, tip, follow, join a guild, report or vote in a poll, and **has no chat at all** (the owner, 2026-11-19: the chat server's session and keys, every chat page and list, a call, a call Space, pushes, outside chat apps; a new hold also signs its chat devices out, `auto_quarantine._cut_off_chat`, with a cycle backstop `cut_off_held_chats`). Each of those routes carries `@not_quarantined("<action>")`
(helpers/wrappers.py) right above its `@validate_formkey`. **A suspension stopped every route through `is_not_banned`; a hold stops only what
carries the wrapper**, so tests/test_signup_guard_assets.py lists the routes that carry it (`HELD_OUT`) and the ones a held account may still use,
each with its reason (`OPEN_TO_HELD`), and fails on a route that is in neither: a new route that changes something has to decide. A guildmaster's powers
are held too (`@is_guildmaster` refuses, and `Board.has_mod` says no; `Board.leads` is what it may still read).

* **Never in `is_not_banned`**: that wrapper also guards Settings, email verification and deleting the account, which
  a held person needs.
* **What a held account tried is counted** (`signup_signals.note_attempt`), so /admin/signups shows what held
  accounts go on to attempt. Counts per action only.
* **Released** by an admin at /admin/signups, or (the signup guard's hold only, `users.quarantine_kind` 0) by verifying an
  email that isn't a throwaway (`/activate`, ruqqus/mail/mail.py). Never by time: accounts kept dormant to be sold later are
  one of the things it is for. **A hold an automatic check placed (kind 1) is released only by an admin or a successful
  appeal, never by an email**: its refusal says so and links /appeal/quarantine.
* **A hold an automatic check placed also closes the profile picture and the banner** (`picture_refusal`, `User.can_upload_avatar` / `can_upload_banner`):
  a picture is public and was what the image checks looked at, and the suspension these checks used to apply closed those routes through `is_not_banned`.
  Removing a picture and every text field of the profile stay open. The signup guard's hold (kind 0) keeps both: it waits for a person to look at the account.
* Nothing public names a quarantined account: no banner on the profile, nothing on a page (the Log's public line names nobody).
* Admins and bot accounts are never held (`User.is_quarantined`).
tests/test_quarantine.py.
"""
from ruqqus.helpers.i18n_runtime import N_, msg

ACTIONS = ("post", "edit_post", "comment", "vote", "forward", "repost", "tip", "follow", "join_guild", "chat",
           "group", "report", "poll_vote", "story", "edit_comment", "moderate", "wiki", "event", "space", "live_update",
           "channel", "collab", "coauthor", "guild", "curation", "region", "circle")

HELD = N_("Your account is waiting for a quick check before it can do this. To skip the wait, verify an email "
          "address in Settings > Security.")
HELD_TITLE = N_("Your account is being checked")
# an automatic check's hold (kind 1): no email releases it, so the way out is a review (the page finds the account's own entry: /appeal/quarantine)
HELD_AUTOMATIC = N_("An automatic check put your account on hold, so you can't do this for now. A member of the staff will look at it. If you think the "
                    "check was wrong, you can ask for a review.")
HELD_AUTOMATIC_TITLE = N_("Your account is on hold")
APPEAL_LINK = "/appeal/quarantine"


def is_automatic(v):
    """Is this account held by an automatic check (kind 1)? An object that doesn't say is the signup guard's."""
    return getattr(v, "quarantine_kind", 0) == 1


def refusal(v, action, r=None):
    """The message for a held account (and the attempt counted), or None."""
    if v is None or not getattr(v, "is_quarantined", False):
        return None
    if r is not None:
        from ruqqus.helpers import signup_signals
        signup_signals.note_attempt(r, v.id, action if action in ACTIONS else "other")
    return msg(HELD_AUTOMATIC if is_automatic(v) else HELD)


def picture_refusal(v, r=None):
    """The message for an account an AUTOMATIC check holds (kind 1) that tries to upload a profile picture or a banner, and the attempt counted (under "other": the
    admin page labels only the actions of `ACTIONS`), or None: nobody else is refused here, the signup guard's hold included. Taking a picture down is never asked."""
    if v is None or not getattr(v, "is_quarantined", False) or not is_automatic(v):
        return None
    return refusal(v, "picture", r)
