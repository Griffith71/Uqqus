"""What a person is told when a decision is made about them, and how an appeal ends (the Log, slice D2: the owner's decisions 13 and 26).

Every hide, removal, exile, suspension and ban reaches the person it is about as a notification with **the rule, the reason, a link to
the entry in the Log and an Appeal link** (`/log/<id>` and `/log/<id>/appeal`), and sets the entry's `notice_sent`. An appeal's outcome
is a notification too. The words are marked with `N_` (stored in English with their values, drawn in each reader's language: helpers/
alerts.py `notify`). **A notice is markdown, and a guildmaster's reason is free text** that would be turned into links on its way (the site
links every address it finds in a message): a reason goes inside a fenced block, the one place that linking skips, and `plain` takes the
backticks out so it can't end the block. A decision a member didn't like can't put a link of their choosing into a message that arrives
as the site's.

**What is lifted is told too** (the owner, 2026-11-14: "if the limitation is removed/lifted the user should be informed"): an unexile, an unmute, an
unhide, a restored post or comment, a suspension that ended and a reinstated guild each send a short notice (`tell_lifted`), after the lifting commits.
A lift notice has no link: the entry that lifted it may be a private guild's, and nothing in it needs one. It is never sent to whoever lifted it, or about an
account the lift doesn't concern, and **never names an alias** (an exile or a mute made by alias is lifted from a list that draws the number; the notice goes
to the account behind it and says only what it can do again). A chat mute and a guild ban are told when imposed (`tell_muted`, `tell_guild_banned`) with the
rule, the reason, the Log and the Appeal link, as a hide is.

**A quarantine an automatic check placed** (helpers/auto_quarantine.py) is told to the person with the rule, the generic reason ("Automatic spam check": never what was
matched) and the way to ask for a review (`/appeal/quarantine`: the staff's entry about it is not theirs to open, so the page finds it for them), and its lifting is
told without a link (`tell_quarantined`, `tell_quarantine_lifted`).

Not here: the decision's own routes (they call these after they commit: `notify` commits on its own) and the pure rules of
appeals (helpers/log_appeals.py)."""
import re

from ruqqus.helpers.action_log import RULES
from ruqqus.helpers.i18n_runtime import N_

_TICKS = re.compile(r"`+")

WHY = 300                       # how much of a reason a notice carries; the entry has all of it

# --- what was decided ----------------------------------------------------------------------------------------------------
HID_POST = N_("%(post_link)s was hidden from +%(guild)s by a guildmaster.\n\n%(why_block)s\n\n%(log_link)s · %(appeal_link)s")
HID_COMMENT = N_("%(comment_link)s was hidden from +%(guild)s by a guildmaster.\n\n%(why_block)s\n\n%(log_link)s · %(appeal_link)s")
REMOVED_POST = N_("%(post_link)s was removed.\n\n%(why_block)s\n\n%(log_link)s · %(appeal_link)s")
REMOVED_COMMENT = N_("%(comment_link)s was removed.\n\n%(why_block)s\n\n%(log_link)s · %(appeal_link)s")
AUTHOR_HID_COMMENT = N_("%(comment_link)s was hidden by the author of the post it is under.\n\n%(why_block)s\n\n%(log_link)s · %(appeal_link)s")
# an exile and a ban each have a notice of their own already (with the admin's own words): this one only carries the way to the Log
# and the appeal
DECISION = N_("This decision is in the Log, and you can appeal it: %(log_link)s · %(appeal_link)s")

# a chat mute and a guild ban, when they are made (a mute by alias too: the notice says nothing of the number)
MUTED = N_("You can no longer write in +%(guild)s's channels, but you can still read them.\n\n%(why_block)s\n\n%(log_link)s · %(appeal_link)s")
GUILD_BANNED = N_("+%(guild)s was banned from %(site)s.\n\n%(why_block)s\n\n%(log_link)s · %(appeal_link)s")
# a ban that cites anonymous items of the account says how many and where to see which (the appeal page lists them to the account alone: a notice is read by
# whoever is signed in, a relayed QR code included, and the ids of anonymous items are the account's to know)
BAN_CITES = N_("The ban relies on %(num)s of your anonymous posts and comments. If a second administrator approves (or, on a site with a single "
               "administrator, its owner), the Log will name your account beside them. %(which_link)s")

# what an exile by alias tells its account: the item it cites is anonymous, and the Log entry it links names that item by id (helpers/alias_actions.py)
ALIAS_EXILED = N_("You have been exiled from +%(guild)s.\n\nNone of your existing posts or comments have been removed, however, you will not be able to forward "
                  "posts to +%(guild)s or comment in it.\n\nYou were exiled for %(this_item_link)s.")

# an automatic check put the account on hold (helpers/auto_quarantine.py): it can read the site and change its own settings, and nothing else, until an admin looks
QUARANTINED = N_("An automatic check put your account on hold. You can read %(site)s and change your settings, but you can't post, comment or vote until a "
                 "member of the staff has looked at it.\n\n%(why_block)s\n\n%(review_link)s")
QUARANTINE_APPEAL = "/appeal/quarantine"

# what a level-4 admin's use of guild powers tells the people it touches (the owner, 2026-11-19: every such act says why)
ADMIN_JOINED = N_("A site administrator made themselves a guildmaster of +%(guild)s.\n\n%(why_block)s\n\n%(log_link)s")
ADMIN_REMOVED_MOD = N_("A site administrator removed you as a guildmaster of +%(guild)s.\n\n%(why_block)s\n\n%(log_link)s")

# --- what was lifted ---------------------------------------------------------------------------------------------------------
EXILE_LIFTED = N_("You are no longer exiled from +%(guild)s. You can forward posts to it and comment in it again.")
MUTE_LIFTED = N_("You can write in +%(guild)s's channels again.")
POST_SHOWN = N_("%(post_link)s is shown in +%(guild)s again.")
COMMENT_SHOWN = N_("%(comment_link)s is shown in +%(guild)s again.")
POST_RESTORED = N_("%(post_link)s was restored.")
COMMENT_RESTORED = N_("%(comment_link)s was restored.")
COMMENT_SHOWN_AGAIN = N_("%(comment_link)s is shown again under the post it is on.")
SUSPENSION_ENDED = N_("Your suspension has ended. You can use %(site)s as you did before.")
GUILD_REINSTATED = N_("+%(guild)s was reinstated, and its page is open again.")
QUARANTINE_LIFTED = N_("The hold on your account was lifted. You can post, comment and vote again.")
# the same three for an account that is ON HOLD still (helpers/quarantine.py): a lift that says "you can post again" is untrue to it, so it says what is still so
EXILE_LIFTED_HELD = N_("You are no longer exiled from +%(guild)s. Your account is still on hold, so it can't forward posts or comment until a member of the staff has looked at it.")
MUTE_LIFTED_HELD = N_("You are no longer muted in +%(guild)s's channels. Your account is still on hold, so it can't write there until a member of the staff has looked at it.")
SUSPENSION_ENDED_HELD = N_("Your suspension has ended. Your account is still on hold, so it can't post, comment or vote until a member of the staff has looked at it.")
HELD_VARIANTS = {}      # filled below, once the plain ones are all defined

# --- how an appeal ended -------------------------------------------------------------------------------------------------
UPHELD = N_("Your appeal of %(decision_link)s was looked at, and the decision stands.\n\n%(note_block)s")
REVERSED = N_("Your appeal of %(decision_link)s was accepted, and the decision was reversed.\n\n%(note_block)s")
DISMISSED = N_("Your appeal of %(decision_link)s was not taken further.\n\n%(note_block)s")
OUTCOME_NOTICES = {"upheld": UPHELD, "reversed": REVERSED, "dismissed": DISMISSED}
# the same for an appeal of an automatic quarantine: the staff's entry about it is not the person's to open, so these carry no link to the Log (`outcome(..., action=)`)
QUARANTINE_UPHELD = N_("Your appeal of the hold on your account was looked at, and the hold stands.\n\n%(note_block)s")
QUARANTINE_REVERSED = N_("Your appeal of the hold on your account was accepted, and the hold was lifted. You can post, comment and vote again.\n\n%(note_block)s")
QUARANTINE_DISMISSED = N_("Your appeal of the hold on your account was not taken further.\n\n%(note_block)s")
QUARANTINE_OUTCOME_NOTICES = {"upheld": QUARANTINE_UPHELD, "reversed": QUARANTINE_REVERSED, "dismissed": QUARANTINE_DISMISSED}

# **What a limited QR sign-in is not shown** (the second review, 2026-11-17, C14). A notice that links the member's item, or the Log's entry about it
# (which names the item by id), tells whoever reads it which item is the member's: for an anonymous one that is the pairing anonymity exists to keep, and
# a limited sign-in is a stranger to it (`anonymity.is_author`). These are held back, still new, until the password is given (`User._without_own_anonymous`),
# the way the comments on the member's anonymous posts already are. A notice of an item that is not anonymous waits too: telling which is which is the leak.
# The two quarantine notices (helpers/auto_quarantine.py) wait as well, by the owner's instruction: they name no item, but they are about the account's standing, and the
# first links the review page, which a limited sign-in cannot open until the password is given.
HELD_FROM_LIMITED = (HID_POST, HID_COMMENT, REMOVED_POST, REMOVED_COMMENT, AUTHOR_HID_COMMENT, DECISION, MUTED, GUILD_BANNED, ALIAS_EXILED,
                     POST_SHOWN, COMMENT_SHOWN, POST_RESTORED, COMMENT_RESTORED, COMMENT_SHOWN_AGAIN, UPHELD, REVERSED, DISMISSED, QUARANTINED, QUARANTINE_LIFTED,
                     QUARANTINE_UPHELD, QUARANTINE_REVERSED, QUARANTINE_DISMISSED)

HELD_VARIANTS.update({EXILE_LIFTED: EXILE_LIFTED_HELD, MUTE_LIFTED: MUTE_LIFTED_HELD, SUSPENSION_ENDED: SUSPENSION_ENDED_HELD})
# an exile or a mute lifted by ALIAS is told to the account behind it, and names the guild: a relayed code reads that too (the review of 2026-11-19)
HELD_FROM_LIMITED = HELD_FROM_LIMITED + (EXILE_LIFTED, MUTE_LIFTED, EXILE_LIFTED_HELD, MUTE_LIFTED_HELD, ADMIN_JOINED, ADMIN_REMOVED_MOD)

# what the Log's entries say a decision is, to choose the notice
HIDES = {"hide_post_from_guild": HID_POST, "hide_comment_from_guild": HID_COMMENT}
REMOVALS = {"ban_post": REMOVED_POST, "ban_comment": REMOVED_COMMENT}


def plain(value, limit=WHY):
    """A person's words inside a notice's fenced block: one line, without a backtick (which could end the block), cut to `limit`."""
    return _TICKS.sub("'", " ".join(str(value or "").split()))[:limit].strip()


def why_of(rule_key, reason):
    """"Content standards: Spamming links", or whichever of the two there is, as plain text for a notice's fenced block. The rule's name
    is the English one, as the notice is stored (a reader's own language draws the message around it)."""
    rule = str(RULES.get(rule_key, "")) if rule_key else ""
    reason = plain(reason)
    if rule and reason:
        return f"{rule}: {reason}"
    return rule or reason or "No reason was given."


def link_of(entry_id):
    return f"/log/{int(entry_id)}"


def appeal_link_of(entry_id):
    return f"/log/{int(entry_id)}/appeal"


def decision(action, *, entry_id, item, guild="", rule_key="", reason=""):
    """(message, values) for the notice of a hide, a removal or an author's hide of a comment; None for an action that has none.
    `item` is the site address of the post or comment, `guild` the guild's name for a hide."""
    if action in HIDES:
        message = HIDES[action]
    elif action in REMOVALS:
        message = REMOVALS[action]
    elif action == "author_hide_comment":
        message = AUTHOR_HID_COMMENT
    else:
        return None
    return message, {"item": item, "guild": plain(guild, 64), "why": why_of(rule_key, reason),
                     "log": link_of(entry_id), "appeal": appeal_link_of(entry_id)}


def outcome(appeal_outcome, *, entry_id, note, action=None):
    """(message, values) for the notice that tells a person how their appeal ended. **An appeal of an automatic quarantine** (`action` "quarantine_user") has its own
    words and no link: its entry is the staff's alone, so a link to it would be a 404 to the person."""
    if action == "quarantine_user":
        return QUARANTINE_OUTCOME_NOTICES[appeal_outcome], {"note": plain(note, 500) or "-"}
    return OUTCOME_NOTICES[appeal_outcome], {"log": link_of(entry_id), "note": plain(note, 500) or "-"}


def way_to_appeal(entry_id):
    """(message, values) for the short notice that sits beside an exile's or a ban's own."""
    return DECISION, {"log": link_of(entry_id), "appeal": appeal_link_of(entry_id)}


def send(user, message_and_values):
    """Send one of the above to `user` (an account object) and say so on its entry is the caller's (`mark_sent`). `notify` commits."""
    from ruqqus.helpers.alerts import notify

    message, values = message_and_values
    notify(user, message, **values)


def mark_sent(db, entry_id):
    """Record on the entry that the person was told (the staff page shows it)."""
    from sqlalchemy import text

    if entry_id:
        db.execute(text("UPDATE log_entries SET notice_sent = :yes WHERE id = :e"), {"yes": True, "e": entry_id})


def tell_decision(db, entry_id, action, *, post=None, comment=None, guild="", rule_key="", reason="", actor_id=None):
    """Tell the person a hide or a removal is about (the account responsible for a post: its forwarder for a guild's copy, else its
    author; a comment's author) what was decided: the rule, the reason, the Log and the way to appeal. Best effort and after the decision
    is committed (`notify` commits on its own): a failure here is logged and never undoes the decision. Not told: whoever made the
    decision, or an account that is gone. Sets the entry's `notice_sent`. Returns whether a notice went."""
    import logging

    from ruqqus.helpers import post_follows

    try:
        if comment is not None:
            person_id, item = comment.author_id, comment.permalink
        elif post is not None:
            person_id, item = post_follows.responsible_id(db, post), post.permalink
        else:
            return False
        if not person_id or person_id == actor_id:
            return False
        from sqlalchemy import text
        row = db.execute(text("SELECT id, is_deleted FROM users WHERE id = :u"), {"u": person_id}).first()
        if row is None or row[1]:
            return False
        built = decision(action, entry_id=entry_id, item=item, guild=guild, rule_key=rule_key, reason=reason)
        if built is None:
            return False
        from ruqqus.classes import User
        send(db.query(User).filter_by(id=person_id).first(), built)
        mark_sent(db, entry_id)
        db.commit()
        return True
    except Exception:
        logging.getLogger(__name__).exception("the notice for log entry %s was not sent", entry_id)
        db.rollback()
        return False


def tell_appeal_way(db, entry_id, user):
    """The short notice beside an exile's or a ban's own: the Log and the Appeal link. Best effort, as `tell_decision`."""
    import logging

    try:
        send(user, way_to_appeal(entry_id))
        return True
    except Exception:
        logging.getLogger(__name__).exception("the appeal notice for log entry %s was not sent", entry_id)
        db.rollback()
        return False


# --- lifting and imposing ------------------------------------------------------------------------------------------------------

def _person(db, user_id, actor_id=None):
    """The account to tell, or None: not whoever acted, not an account that is gone."""
    if not user_id or user_id == actor_id:
        return None
    from ruqqus.classes import User
    user = db.query(User).filter_by(id=user_id).first()
    return None if user is None or user.is_deleted else user


def tell_lifted(db, user_id, message, *, actor_id=None, **values):
    """Tell one account a limitation was lifted (`message` is one of the lift notices above). Best effort and AFTER the lifting is committed (`notify`
    commits on its own): a failure here is logged and never undoes it. Returns whether a notice went."""
    import logging

    try:
        user = _person(db, user_id, actor_id)
        if user is None:
            return False
        if getattr(user, "is_quarantined", False):
            message = HELD_VARIANTS.get(message, message)       # an account on hold is told what is still so
        send(user, (message, {k: plain(v, 128) if k == "guild" else v for k, v in values.items()}))
        return True
    except Exception:
        logging.getLogger(__name__).exception("a lift notice was not sent")
        db.rollback()
        return False


def tell_imposed(db, user_id, message, entry_id, *, guild="", rule_key="", reason="", actor_id=None):
    """Tell one account a decision was made, with the rule, the reason, the Log and the way to appeal (`MUTED`, `GUILD_BANNED`); sets the entry's `notice_sent`."""
    import logging

    try:
        user = _person(db, user_id, actor_id)
        if user is None or not entry_id:
            return False
        send(user, (message, {"guild": plain(guild, 64), "why": why_of(rule_key, reason), "log": link_of(entry_id), "appeal": appeal_link_of(entry_id)}))
        mark_sent(db, entry_id)
        db.commit()
        return True
    except Exception:
        logging.getLogger(__name__).exception("the notice for log entry %s was not sent", entry_id)
        db.rollback()
        return False


def tell_quarantined(db, user_id, entry_id, *, rule_key="", reason=""):
    """Tell an account an automatic check put it on hold (`QUARANTINED`): the rule (only when `rule_key` is given: ban-evasion detection names none), the generic
    reason and the way to ask for a review. AFTER the hold is committed; best effort, as `tell_imposed`. Sets the staff entry's `notice_sent`. Returns whether a
    notice went."""
    import logging

    try:
        user = _person(db, user_id)
        if user is None:
            return False
        send(user, (QUARANTINED, {"why": why_of(rule_key, reason), "appeal": QUARANTINE_APPEAL}))
        mark_sent(db, entry_id)
        db.commit()
        return True
    except Exception:
        logging.getLogger(__name__).exception("the quarantine notice for log entry %s was not sent", entry_id)
        db.rollback()
        return False


def tell_quarantine_lifted(db, user_id):
    """Tell an account the hold on it was lifted (a lift notice: no link). AFTER the lifting is committed; best effort."""
    return tell_lifted(db, user_id, QUARANTINE_LIFTED)


def tell_heads(db, board_id, message, *, entry_id=None, actor_id=None, skip=(), **values):
    """Tell the people who answer for a guild (its founder and its full guildmasters: `log_appeals.heads_of`) what was decided about it or lifted from it. With
    an `entry_id` the notice is the imposed kind (rule, reason, the Log and the Appeal link: pass `rule_key` and `reason`), else a lift notice. Returns how many were told."""
    from ruqqus.helpers import log_appeals

    told = 0
    for user_id in log_appeals.heads_of(db, board_id):
        if user_id in skip:          # already told by another notice (the one who appealed has the outcome's own)
            continue
        if entry_id:
            sent = tell_imposed(db, user_id, message, entry_id, actor_id=actor_id, **values)
        else:
            sent = tell_lifted(db, user_id, message, actor_id=actor_id, **values)
        told += bool(sent)
    return told


def tell_ban_cites(db, entry_id, user_id, items):
    """Tell a banned account how many of its anonymous posts and comments the ban relies on (`items` are `("post" | "comment", id)` that are anonymous now),
    and that a second administrator's approval (or, on a site with a single administrator, its owner's: decision 27) would name its account beside them in the
    Log (the owner, 2026-11-14). The link is to the appeal page, which lists
    them to the account alone. Returns whether a notice went."""
    import logging

    try:
        user = _person(db, user_id)
        if user is None or not entry_id or not items:
            return False
        send(user, (BAN_CITES, {"num": len(items), "appeal": appeal_link_of(entry_id)}))
        return True
    except Exception:
        logging.getLogger(__name__).exception("the evidence notice for log entry %s was not sent", entry_id)
        db.rollback()
        return False


def tell_item_lifted(db, message, *, post=None, comment=None, guild="", actor_id=None):
    """Tell whoever answers for a post (its forwarder for a guild's copy, else its author) or a comment (its author) that it was shown again or restored
    (`POST_SHOWN`, `POST_RESTORED`, `COMMENT_SHOWN`, `COMMENT_RESTORED`, `COMMENT_SHOWN_AGAIN`). Best effort, after the lifting committed."""
    import logging

    from ruqqus.helpers import post_follows

    try:
        if comment is not None:
            person, item = comment.author_id, comment.permalink
        elif post is not None:
            person, item = post_follows.responsible_id(db, post), post.permalink
        else:
            return False
        return tell_lifted(db, person, message, actor_id=actor_id, item=item, guild=guild)
    except Exception:
        logging.getLogger(__name__).exception("a lift notice for an item was not sent")
        db.rollback()
        return False


def tell_lapsed(db_session, user_ids):
    """Tell each account its timed suspension has ended (`SUSPENSION_ENDED`): the step of `scripts/recomputes.py` that writes the lapse entries, in a
    request context of its own (`notify` needs one). Returns how many were told."""
    from flask import g

    from ruqqus.__main__ import app

    told = 0
    with app.test_request_context():
        g.db = db_session()
        try:
            for user_id in user_ids:
                told += bool(tell_lifted(g.db, user_id, SUSPENSION_ENDED))
        finally:
            g.db.close()
    return told
