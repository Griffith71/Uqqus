"""What a person is told when a decision is made about them, and how an appeal ends (the Log, slice D2: the owner's decisions 13 and 26).

Every hide, removal, exile, suspension and ban reaches the person it is about as a notification with **the rule, the reason, a link to
the entry in the Log and an Appeal link** (`/log/<id>` and `/log/<id>/appeal`), and sets the entry's `notice_sent`. An appeal's outcome
is a notification too. The words are marked with `N_` (stored in English with their values, drawn in each reader's language: helpers/
alerts.py `notify`). **A notice is markdown, and a guildmaster's reason is free text** that would be turned into links on its way (the site
links every address it finds in a message): a reason goes inside a fenced block, the one place that linking skips, and `plain` takes the
backticks out so it can't end the block. A decision a member didn't like can't put a link of their choosing into a message that arrives
as the site's.

Not here: the decision's own routes (they call `tell_decision` after they commit: `notify` commits on its own) and the pure rules of
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
