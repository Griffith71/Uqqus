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

Not here: the decision's own routes (they call these after they commit: `notify` commits on its own) and the pure rules of
appeals (helpers/log_appeals.py)."""
import re

from ruqqus.helpers.action_log import RULES
from ruqqus.helpers.i18n_runtime import N_

_TICKS = re.compile(r"`+")

WHY = 300                       # how much of a reason a notice carries; the entry has all of it

# --- what was decided ----------------------------------------------------------------------------------------------------
HID_POST = N_("[Your post](%(item)s) was hidden from +%(guild)s by a guildmaster.\n\n```\n%(why)s\n```\n\n"
              "[See it in the Log](%(log)s) · [Appeal this decision](%(appeal)s)")
HID_COMMENT = N_("[Your comment](%(item)s) was hidden from +%(guild)s by a guildmaster.\n\n```\n%(why)s\n```\n\n"
                 "[See it in the Log](%(log)s) · [Appeal this decision](%(appeal)s)")
REMOVED_POST = N_("[Your post](%(item)s) was removed.\n\n```\n%(why)s\n```\n\n"
                  "[See it in the Log](%(log)s) · [Appeal this decision](%(appeal)s)")
REMOVED_COMMENT = N_("[Your comment](%(item)s) was removed.\n\n```\n%(why)s\n```\n\n"
                     "[See it in the Log](%(log)s) · [Appeal this decision](%(appeal)s)")
AUTHOR_HID_COMMENT = N_("[Your comment](%(item)s) was hidden by the author of the post it is under.\n\n```\n%(why)s\n```\n\n"
                        "[See it in the Log](%(log)s) · [Appeal this decision](%(appeal)s)")
# an exile and a ban each have a notice of their own already (with the admin's own words): this one only carries the way to the Log
# and the appeal
DECISION = N_("This decision is in the Log, and you can appeal it: [See it in the Log](%(log)s) · [Appeal this decision](%(appeal)s)")

# a chat mute and a guild ban, when they are made (a mute by alias too: the notice says nothing of the number)
MUTED = N_("You can no longer write in +%(guild)s's channels, but you can still read them.\n\n```\n%(why)s\n```\n\n"
           "[See it in the Log](%(log)s) · [Appeal this decision](%(appeal)s)")
GUILD_BANNED = N_("+%(guild)s was banned from %(site)s.\n\n```\n%(why)s\n```\n\n"
                  "[See it in the Log](%(log)s) · [Appeal this decision](%(appeal)s)")
# a ban that cites anonymous items of the account says which, and that a second administrator's approval would name the account beside them
BAN_CITES = N_("The ban relies on these of your anonymous posts and comments: %(items)s\n\nIf a second administrator approves, the Log will name "
               "your account beside them. [See it in the Log](%(log)s)")

# --- what was lifted ---------------------------------------------------------------------------------------------------------
EXILE_LIFTED = N_("You are no longer exiled from +%(guild)s. You can forward posts to it and comment in it again.")
MUTE_LIFTED = N_("You can write in +%(guild)s's channels again.")
POST_SHOWN = N_("[Your post](%(item)s) is shown in +%(guild)s again.")
COMMENT_SHOWN = N_("[Your comment](%(item)s) is shown in +%(guild)s again.")
POST_RESTORED = N_("[Your post](%(item)s) was restored.")
COMMENT_RESTORED = N_("[Your comment](%(item)s) was restored.")
COMMENT_SHOWN_AGAIN = N_("[Your comment](%(item)s) is shown again under the post it is on.")
SUSPENSION_ENDED = N_("Your suspension has ended. You can use %(site)s as you did before.")
GUILD_REINSTATED = N_("+%(guild)s was reinstated, and its page is open again.")

# --- how an appeal ended -------------------------------------------------------------------------------------------------
UPHELD = N_("Your appeal of [this decision](%(log)s) was looked at, and the decision stands.\n\n```\n%(note)s\n```")
REVERSED = N_("Your appeal of [this decision](%(log)s) was accepted, and the decision was reversed.\n\n```\n%(note)s\n```")
DISMISSED = N_("Your appeal of [this decision](%(log)s) was not taken further.\n\n```\n%(note)s\n```")
OUTCOME_NOTICES = {"upheld": UPHELD, "reversed": REVERSED, "dismissed": DISMISSED}

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


def outcome(appeal_outcome, *, entry_id, note):
    """(message, values) for the notice that tells a person how their appeal ended."""
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


def tell_heads(db, board_id, message, *, entry_id=None, actor_id=None, **values):
    """Tell the people who answer for a guild (its founder and its full guildmasters: `log_appeals.heads_of`) what was decided about it or lifted from it. With
    an `entry_id` the notice is the imposed kind (rule, reason, the Log and the Appeal link: pass `rule_key` and `reason`), else a lift notice. Returns how many were told."""
    from ruqqus.helpers import log_appeals

    told = 0
    for user_id in log_appeals.heads_of(db, board_id):
        if entry_id:
            sent = tell_imposed(db, user_id, message, entry_id, actor_id=actor_id, **values)
        else:
            sent = tell_lifted(db, user_id, message, actor_id=actor_id, **values)
        told += bool(sent)
    return told


def tell_ban_cites(db, entry_id, user_id, items):
    """Tell a banned account which of its anonymous posts and comments the ban relies on (`items` are `("post" | "comment", id)` that are anonymous now),
    and that a second administrator's approval would name its account beside them in the Log (the owner, 2026-11-14). Each is a link to the item as its own
    author sees it. Returns whether a notice went."""
    import logging

    from ruqqus.helpers.base36 import base36encode

    try:
        user = _person(db, user_id)
        if user is None or not entry_id or not items:
            return False
        links = ", ".join(f"[#{base36encode(i)}](/{kind}/{base36encode(i)})" for kind, i in items)
        send(user, (BAN_CITES, {"items": links, "log": link_of(entry_id)}))
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
