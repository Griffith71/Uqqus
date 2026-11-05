"""Private posting (the owner, 2026-11-05): a setting for a member who must not make a public post by accident. While it is on,

* every post the account makes goes **only to its Friends circle** (mutual followers): a Public choice is stored as Friends, in the
  composers, drafts, scheduled posts and stories alike (the audience choice is drawn locked to Friends);
* what would carry a post past that circle is unavailable: forwarding or reposting, posting to guilds, a shared draft (always public),
  and making a post Public afterwards;
* its custom circles may hold only mutual followers: adding anyone else is refused, and a member who stops being a mutual follower is
  taken out when the follow ends (`circle_store.drop_non_mutual`).

Comments on other people's public posts are still public; this is about the member's own posts. `users.private_posting` starts on for every
account under the child lock (`age_declaration.fields_for`), and a standalone member may turn it off in Settings > Content (a sub-account only
with its parent's consent, Stage 2). Lifting the child lock turns it off in the same statement (`age_declaration.lift_fields`). It is not the
older, all-or-nothing "Private mode". Rules here are pure; tests/test_private_posting.py."""
from ruqqus.helpers.i18n_runtime import N_

FRIENDS = "friends"

REFUSAL = N_("Private posting is on, so this can only go to your Friends circle. You can change that in Settings > Content.")
NOT_MUTUAL = N_("With Private posting on, a circle can only hold people who follow you and whom you follow.")
CAN_T_TURN_OFF = N_("Your parent or guardian has to agree before Private posting can be turned off.")


def on(v):
    return bool(v is not None and getattr(v, "private_posting", False))


def refusal(v):
    """The message when this account may not do something that would take a post beyond its Friends circle, else None."""
    return REFUSAL if on(v) else None


def circles_chosen(v, audience, tokens, circles_audience):
    """The audience a post is stored with: a Public choice becomes the Friends circle while Private posting is on. A choice of the
    account's own circles is kept (they can only hold mutual followers)."""
    if on(v) and audience != circles_audience:
        return circles_audience, [FRIENDS]
    return audience, tokens


def may_turn_off(v):
    """Can this member switch it off themselves? Everyone but a sub-account, whose parent decides."""
    from ruqqus.helpers import age_declaration
    return getattr(v, "age_lock", None) != age_declaration.PARENT_LOCK


def member_refusal(owner, mutual):
    """The message when `owner` may not put this account in one of their custom circles, else None."""
    return NOT_MUTUAL if on(owner) and not mutual else None
