"""The age declaration: a member says once whether they are 18 or over, or whether a parent or guardian is setting the
account up for their child (13 to 17). Under-13s may not have an account (help/terms.html "Age"). **This is what someone
said, never a check**: a child can sign up and say they are an adult, and anyone can read the site signed out.

`users.age_declared` is NULL (not asked yet), ADULT or CHILD, `users.age_declared_utc` when. Sign-up asks and refuses
without an answer (routes/login.py). A member from before this is asked by a notice (partials/age_notice.html, on
default.html) and in Settings > Content, until they answer; until then they are treated as they always were. Bot
accounts and the site's own account are never asked: nobody signs in to them.

**A child account stays on the Child word filter** and never shows sensitive content in All or For You. Three things hold
it: `fields_for` sets both whenever an account becomes a child account (sign-up, the notice, an admin: nothing else writes
the answer); `locked_filter` / `locked_sensitive` refuse the two settings routes; and the database's own constraint
(`users_age_declared_rule`) fails anything else that tries, so a route added later can't undo it by mistake.

**An answer is once only.** The member can't change it: `POST /settings/age_declaration` writes only where the column is
still NULL. An admin (level 3) can (`POST /admin/age_declaration`), written to the Log. An admin taking an account off
CHILD does not lower its filter: the member decides, with their password, as anyone on the Child filter does. Closing an
account takes the answer, the note and reason on the admin's Log entry and the notices (`ADMIN_NOTICES`) with it
(helpers/account_cleanup.py), which finds those notices by their message: **rewording one strands the ones already sent**,
so keep the old wording in a list there.

What else a child account can't do (chats with strangers, coins, tips, anonymous posts) is not decided and not built.
Rules here need no database except `terms_notice_ids`. tests/test_age_declaration.py, tests/pg/test_age_declaration.py.
"""
from sqlalchemy import text

from ruqqus.helpers.i18n_runtime import N_
from ruqqus.helpers.visibility import CHILD as CHILD_FILTER

ADULT, CHILD = 1, 2

FILTER_LOCKED = N_("This is a child account, so its word filter stays on Child. Only an admin can change that.")
SENSITIVE_LOCKED = N_("This is a child account, so sensitive content stays hidden. Only an admin can change that.")
ALREADY_ANSWERED = N_("You've already answered this. Only an admin can change your answer.")
NO_ANSWER = N_("Please choose one of the two answers.")
SIGN_UP_NO_ANSWER = N_("Please say how old you are.")

ANSWER_TITLE = N_("Your age")
CONFIRM_TITLE = N_("Is this a child's account?")
BACK = N_("Go back")

# told once, by scripts/notify_terms_age.py, in the words the Terms now use. Stored in English like every notice.
TERMS_NOTICE = N_("We've changed the Age section of our [Terms](/help/terms): you must be at least 18, or at least 13 "
                  "with an account made for you by a parent or guardian. If you haven't told us which applies to you "
                  "yet, you'll see a question at the top of the site.")

# an admin changed the answer: the member is told what that does to their account
ADMIN_NOTICES = {
    CHILD: N_("An admin marked this as a child account, one set up by a parent or guardian. It stays on the Child word "
              "filter, which hides profanity and anything marked sensitive. Only an admin can change this."),
    ADULT: N_("An admin marked this account as belonging to someone 18 or over. Your word filter hasn't changed: you "
              "can change it in Settings > Content, and turning the Child filter down needs your password."),
    None: N_("An admin cleared the age answer on your account. You'll be asked again."),
}

# the admin panel and the Log (admin pages stay English)
LABELS = {None: "not asked", ADULT: "18 or over", CHILD: "child account"}


def parse(value):
    """ADULT or CHILD from a form's value, else None. Only the two exact strings count."""
    if value == "1":
        return ADULT
    if value == "2":
        return CHILD
    return None


def is_child(v):
    return getattr(v, "age_declared", None) == CHILD


def is_asked(v):
    """Does this signed-in member still have to answer? Never a bot account: nobody signs in to one."""
    return v is not None and getattr(v, "age_declared", 0) is None and not getattr(v, "is_bot", False)


def fields_for(answer, now):
    """The columns an answer writes, in one statement so the constraint is never broken in between. The only place a
    filter level and the sensitive choice are set for being a child account. `answer` None clears it (an admin only)."""
    fields = {"age_declared": answer, "age_declared_utc": now if answer else 0}
    if answer == CHILD:
        fields.update(filter_level=CHILD_FILTER, hide_offensive=True, all_shows_sensitive=False)
    return fields


def locked_filter(v, new_level):
    """The message when this account may not move to `new_level`, or None."""
    return FILTER_LOCKED if is_child(v) and new_level != CHILD_FILTER else None


def locked_sensitive(v, wanted):
    """The message when this account may not turn on sensitive content in All and For You, or None."""
    return SENSITIVE_LOCKED if is_child(v) and wanted else None


def terms_notice_ids(db):
    """Who is told the Terms changed: live people's accounts that agreed to the old Terms, not told yet. An account that
    signed up since answered on the page that links the new ones (its answer is as old as the account: the model stamps its
    own created_utc, so they can be a moment apart), and looking for the notice itself makes the script safe to run again."""
    rows = db.execute(text(
        "SELECT u.id FROM users u WHERE NOT u.is_deleted AND NOT u.is_bot AND u.id <> 1 "
        "AND (u.age_declared IS NULL OR u.age_declared_utc > u.created_utc + 60) "
        "AND NOT EXISTS (SELECT 1 FROM notifications n JOIN comments_aux a ON a.id = n.comment_id "
        "                WHERE n.user_id = u.id AND a.notice_msgid = :msgid) ORDER BY u.id"),
        {"msgid": TERMS_NOTICE})
    return [row[0] for row in rows]
