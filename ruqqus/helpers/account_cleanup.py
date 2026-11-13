"""What deleting an account takes with it (audit I-05: `delete_account` predated every feature since October).

`close_account(db, user_id, now)` ends or removes, for one account, everything that would otherwise keep running or
keep personal data after it is gone. Plain `text()` SQL, each step in its own savepoint so a table a database doesn't
have yet never stops the rest (tests/test_account_cleanup.py). Nothing here commits. A new feature that keeps
something per account adds its step here.

Posts and comments stay (they are part of other people's threads; the account shows as deleted). What a member wrote
in a shared draft stays credited to them, as when anyone leaves one.
"""
from sqlalchemy import text

from ruqqus.helpers.age_declaration import ADMIN_NOTICES

# the notices an admin's change of the age answer sent (helpers/age_declaration.py); they say what the answer became
_AGE_NOTICES = {f"age_notice_{n}": message for n, message in enumerate(ADMIN_NOTICES.values())}
_AGE_NOTICE_MARKS = ", ".join(f":{name}" for name in _AGE_NOTICES)

STEPS = (
    # paid and custom circles: nothing renews, nobody is charged, memberships of others' custom circles go
    ("end their subscriptions", "UPDATE circle_members SET status = 'ended', cancelled = :yes "
                                "WHERE user_id = :u AND tier = 'subscriber' AND status = 'active'"),
    ("leave custom circles", "DELETE FROM circle_members WHERE user_id = :u AND tier = 'member'"),
    ("stop their circles renewing", "UPDATE circle_members SET cancelled = :yes WHERE circle_id IN "
                                    "(SELECT id FROM circles WHERE user_id = :u)"),
    # co-authorships, stories, Highlights
    ("end co-authorships", "DELETE FROM post_coauthors WHERE user_id = :u"),
    ("remove stories", "UPDATE stories SET deleted_utc = :now WHERE user_id = :u AND deleted_utc = 0"),
    ("remove Highlights", "DELETE FROM highlights WHERE user_id = :u"),
    # channels they own close; memberships go
    ("close their channels", "UPDATE broadcasts SET deleted_utc = :now WHERE owner_id = :u AND deleted_utc = 0"),
    ("leave channels", "DELETE FROM broadcast_members WHERE user_id = :u"),
    # shared drafts: they stop being an author (their text stays credited)
    ("leave shared drafts", "UPDATE collab_authors SET left_utc = :now WHERE user_id = :u AND left_utc = 0"),
    # a draft they were the last author of closes, so its post is an ordinary post again (audit C-01)
    ("close drafts left empty", "UPDATE collab_docs SET deleted_utc = :now WHERE deleted_utc = 0 AND id IN "
                                "(SELECT doc_id FROM collab_authors WHERE user_id = :u) AND NOT EXISTS "
                                "(SELECT 1 FROM collab_authors a WHERE a.doc_id = collab_docs.id AND a.left_utc = 0)"),
    # answers, reminders, follows, private lists
    ("remove event answers", "DELETE FROM guild_event_rsvps WHERE user_id = :u"),
    ("remove Space reminders", "DELETE FROM space_reminders WHERE user_id = :u"),
    ("remove followed posts", "DELETE FROM post_follows WHERE user_id = :u"),
    ("remove fewer-like-this", "DELETE FROM post_feedback WHERE user_id = :u"),
    # feedback sent to the admins (helpers/site_feedback.py): the screenshots first, they are found through the items
    ("remove feedback screenshots", "DELETE FROM site_feedback_images WHERE feedback_id IN "
                                    "(SELECT id FROM site_feedback WHERE user_id = :u)"),
    ("remove feedback", "DELETE FROM site_feedback WHERE user_id = :u"),
    # what the signup guard thought of the sign-up (helpers/signup_review_store.py)
    ("remove sign-up review", "DELETE FROM signup_reviews WHERE user_id = :u"),
    ("remove bookmarks", "DELETE FROM save_relationship WHERE user_id = :u"),
    ("remove comment bookmarks", "DELETE FROM comment_save_relationship WHERE user_id = :u"),
    ("remove bookmark folders", "DELETE FROM bookmark_folders WHERE user_id = :u"),
    ("remove drafts", "DELETE FROM post_drafts WHERE user_id = :u"),
    ("remove templates", "DELETE FROM post_templates WHERE user_id = :u"),
    ("remove mutes", "DELETE FROM usermutes WHERE user_id = :u"),
    # linked storage: the site lets go of their accounts (the files stay in the member's own storage)
    ("unlink storage", "UPDATE media_accounts SET status = 'removed', refresh_token_encrypted = '' WHERE user_id = :u"),
    # chat: no copy of the key, locked or not, stays
    ("forget the chat key", "UPDATE chat_identities SET recovery_key_encrypted = NULL, key_blob_password = NULL, "
                            "key_blob_recovery = NULL WHERE user_id = :u"),
    # the chat devices the site made (helpers/chat_signouts.py): the record goes, with anything sealed in it. The queued sign-out of
    # the account's devices (chat_device_signouts) is never touched here: it must still finish, and needs nothing from these rows
    ("forget the site's chat devices", "DELETE FROM chat_site_devices WHERE user_id = :u"),
    # apps the member let act for them: their tokens and refresh tokens stop working (bug hunt)
    ("revoke app grants", "DELETE FROM client_auths WHERE user_id = :u"),
    # two-factor backup codes
    ("remove backup codes", "DELETE FROM mfa_backup_codes WHERE user_id = :u"),
    # the browsers notifications went to (helpers/push_store.py)
    ("remove push subscriptions", "DELETE FROM push_subscriptions WHERE user_id = :u"),
    # their own account's automated posting (helpers/bots.py SELF): its feeds stop, what it saw goes. A bot ACCOUNT's
    # row is not theirs to end here (a bot is never signed in to, so never deleted this way)
    ("forget what automated posting saw", "DELETE FROM bot_seen WHERE source_id IN (SELECT s.id FROM bot_sources s "
                                          "JOIN bots b ON b.user_id = s.bot_id WHERE b.user_id = :u AND b.kind = 'self')"),
    ("stop automated posting's feeds", "DELETE FROM bot_sources WHERE bot_id = :u AND EXISTS "
                                       "(SELECT 1 FROM bots b WHERE b.user_id = :u AND b.kind = 'self')"),
    ("stop automated posting", "DELETE FROM bots WHERE user_id = :u AND kind = 'self'"),
    # where they signed in from, and what they wrote about themselves
    ("forget sign-in addresses", "DELETE FROM login_events WHERE user_id = :u"),
    # a parent's sub-accounts (helpers/child_accounts.py): the account's own link and controls as a child, and the links of any children it still had
    ("forget a parent's switches for its children", "DELETE FROM child_controls WHERE child_id = :u OR child_id IN "
                                                    "(SELECT child_id FROM account_links WHERE parent_id = :u)"),
    ("forget the parent and child links", "DELETE FROM account_links WHERE child_id = :u OR parent_id = :u"),
    ("forget the family ledger of a closing child", "UPDATE child_family SET child_id = NULL WHERE child_id = :u"),
    ("forget the family ledger of a closing parent", "DELETE FROM child_family WHERE parent_id = :u"),
    ("forget the age answer", "UPDATE users SET age_declared = NULL, age_declared_utc = 0, age_lock = NULL, age_assured_utc = 0, "
                              "age_under_utc = 0, private_posting = FALSE WHERE id = :u"),
    # two more copies of it, where an admin changed it: the Log's row stays (an entry is never deleted, helpers/action_log.py)
    # and says only that a change was made, and the notice's own words go (each one is that member's own message, notify)
    ("forget an admin's age note", "UPDATE log_entries SET reason = '', staff_note = 'removed when the account closed' "
                                   "WHERE target_user_id = :u AND action = 'age_declaration'"),
    ("empty the age notices", "UPDATE comments_aux SET body = '', body_html = '', notice_params = NULL, notice_msgid = '' "
                              f"WHERE notice_msgid IN ({_AGE_NOTICE_MARKS}) AND EXISTS (SELECT 1 FROM notifications n "
                              "WHERE n.comment_id = comments_aux.id AND n.user_id = :u)"),
    ("remove the age notices", "DELETE FROM notifications WHERE user_id = :u AND EXISTS (SELECT 1 FROM comments_aux a "
                               "WHERE a.id = notifications.comment_id AND a.notice_msgid = '')"),
    ("clear the profile", "UPDATE users SET display_name = '', bio = '', bio_html = '', profile_job = '', "
                          "profile_location = '', profile_website = '', email = NULL, premium_auto_renew = :no, "
                          "creator_fund_opt_in_utc = 0 WHERE id = :u"),
)


# Postgres's codes for a table or a column that doesn't exist (a database a migration hasn't reached yet)
_MISSING = ("42P01", "42703")


def _missing_table(error):
    """Is this the database saying a table or column doesn't exist? Anything else is a real failure."""
    original = getattr(error, "orig", None)
    if getattr(original, "pgcode", None) in _MISSING:
        return True
    message = str(original or error).lower()          # SQLite, which the unit tests use
    return "no such table" in message or "no such column" in message


def close_account(db, user_id, now):
    """Runs every step for `user_id`; returns the names of the steps that couldn't run (a missing table). Any other
    error is raised, so the deletion fails as a whole (bug hunt M7: a lock timeout was skipped like a missing table,
    and a deleted account kept its subscriptions renewing)."""
    skipped = []
    for name, sql in STEPS:
        savepoint = db.begin_nested()
        try:
            db.execute(text(sql), {"u": user_id, "now": now, "yes": True, "no": False, **_AGE_NOTICES})
            savepoint.commit()
        except Exception as error:
            savepoint.rollback()
            if not _missing_table(error):
                raise
            skipped.append(name)
    return skipped
