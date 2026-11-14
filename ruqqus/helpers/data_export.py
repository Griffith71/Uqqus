"""Download your data (audit I-06): one JSON file of what the site keeps about an account, built per table like
helpers/account_cleanup.py. Plain `text()` SQL, each table in its own savepoint so one a database lacks is just left
out (tests/test_data_export.py). Chats aren't in it: they are end-to-end encrypted and the site can't read them
(each member's chat page can export its own history).

Only the account's own rows and columns are read; nothing about who else voted, viewed or reported.
"""
import json

from sqlalchemy import text

from ruqqus.helpers.i18n_runtime import tr

SECTIONS = (
    ("account", "SELECT id, username, display_name, email, bio, profile_job, profile_location, profile_website, "
                "created_utc, coin_balance, premium_expires_utc, filter_level, signin_alerts, qr_login_off, languages, feed_defaults, "
                # held by the signup guard since (0: not held); its score and reasons are the admins' and stay out
                "quarantined_utc FROM users WHERE id = :u"),
    # what the member said about their age, and when (helpers/age_declaration.py)
    ("age", "SELECT CASE age_declared WHEN 1 THEN '18 or over' WHEN 2 THEN 'a child account' END AS age_declared, age_declared_utc, "
            "CASE WHEN age_lock IS NULL THEN 'none' WHEN age_lock = 2 THEN 'set by a parent' ELSE 'child lock' END AS age_lock, "
            "age_assured_utc AS age_confirmed_utc, age_under_utc AS age_check_found_under_18_utc "
            "FROM users WHERE id = :u"),
    ("posts", "SELECT s.id, s.created_utc, s.edited_utc, s.deleted_utc, a.title, a.url, a.body, s.board_id, "
              "s.is_anonymous FROM submissions s JOIN submissions_aux a ON a.id = s.id "
              "WHERE s.author_id = :u ORDER BY s.id"),
    ("comments", "SELECT c.id, c.created_utc, c.edited_utc, c.deleted_utc, c.parent_submission, a.body, c.is_anonymous "
                 "FROM comments c JOIN comments_aux a ON a.id = c.id WHERE c.author_id = :u ORDER BY c.id"),
    ("post_votes", "SELECT submission_id, vote_type, created_utc FROM votes WHERE user_id = :u"),
    ("comment_votes", "SELECT comment_id, vote_type, created_utc FROM commentvotes WHERE user_id = :u"),
    ("following", "SELECT target_id, created_utc FROM follows WHERE user_id = :u"),
    ("blocked", "SELECT target_id, created_utc FROM userblocks WHERE user_id = :u"),
    ("muted", "SELECT target_id, created_utc FROM usermutes WHERE user_id = :u"),
    ("guilds_joined", "SELECT board_id, created_utc FROM subscriptions WHERE user_id = :u AND is_active = :yes"),
    ("bookmarks", "SELECT submission_id, folder_id, created_utc FROM save_relationship WHERE user_id = :u"),
    ("comment_bookmarks", "SELECT comment_id, folder_id, created_utc FROM comment_save_relationship WHERE user_id = :u"),
    ("bookmark_folders", "SELECT id, name, created_utc FROM bookmark_folders WHERE user_id = :u"),
    ("drafts", "SELECT id, title, url, body, status, publish_utc, created_utc FROM post_drafts WHERE user_id = :u"),
    ("templates", "SELECT id, name, body, created_utc FROM post_templates WHERE user_id = :u"),
    ("circles", "SELECT id, name, kind, price_coins, created_utc FROM circles WHERE user_id = :u"),
    ("circle_memberships", "SELECT circle_id, tier, status, renews_utc, price_coins FROM circle_members WHERE user_id = :u"),
    ("stories", "SELECT id, kind, body, audience, created_utc, deleted_utc FROM stories WHERE user_id = :u"),
    ("shared_drafts", "SELECT d.id, d.title, d.body, d.post_id, a.joined_utc, a.left_utc FROM collab_docs d "
                      "JOIN collab_authors a ON a.doc_id = d.id WHERE a.user_id = :u"),
    ("event_answers", "SELECT event_id, status, created_utc FROM guild_event_rsvps WHERE user_id = :u"),
    ("tips_given", "SELECT submission_id, comment_id, coins, created_utc FROM award_relationships WHERE user_id = :u"),
    ("sign_ins", "SELECT created_utc, ip, cf_country, user_agent, method FROM login_events WHERE user_id = :u ORDER BY created_utc DESC"),
    # the codes themselves are never exported (the site keeps only a hash); this is how many are left
    ("two_factor", "SELECT (SELECT count(*) FROM mfa_backup_codes WHERE user_id = :u AND used_utc = 0) AS unused_backup_codes, "
                   "(mfa_secret IS NOT NULL) AS two_factor_on FROM users WHERE id = :u"),
    # how many chat devices the site made for the account, and how many are still signed in: never their ids
    ("chat_devices", "SELECT count(*) AS chat_devices, COALESCE(SUM(CASE WHEN revoked_utc = 0 THEN 1 ELSE 0 END), 0) AS signed_in "
                     "FROM chat_site_devices WHERE user_id = :u"),
    ("linked_storage", "SELECT provider, status, created_utc FROM media_accounts WHERE user_id = :u"),
    # the browsers notifications go to (their push service, never the address or keys), and whether they name who wrote
    ("push_notifications", "SELECT created_utc, last_ok_utc, CASE WHEN endpoint LIKE 'https://fcm.googleapis.com/%' THEN 'Google' "
                           "WHEN endpoint LIKE '%.mozilla.com/%' THEN 'Mozilla' WHEN endpoint LIKE '%apple.com/%' THEN 'Apple' "
                           "WHEN endpoint LIKE '%.windows.com/%' THEN 'Microsoft' ELSE 'other' END AS push_service "
                           "FROM push_subscriptions WHERE user_id = :u"),
    # what the member sent the admins, with the facts the page sent along; a screenshot is not exported
    ("feedback", "SELECT kind, message, page, context, status, response, created_utc FROM site_feedback "
                 "WHERE user_id = :u ORDER BY id"),
    # the appeals they made against a decision in the Log, with how each ended (the Log's own entries about them are public or staff's)
    ("appeals", "SELECT a.entry_id, a.reason, a.status, a.decision_note, a.created_utc, a.decided_utc FROM log_appeals a "
                "WHERE a.user_id = :u ORDER BY a.id"),
    # what the Log says about them that anyone could read (a sanction, an exile, one made by alias too: it names the number and is theirs to be given, never a
    # staff-only entry, and never a reason an owner hid), and the private
    # copies of their own words that the site still holds (what they erased themselves, what was hidden): never a copy kept for a legal duty
    ("log_entries_about_you", "SELECT e.id, e.created_utc, e.action, e.rule_key, CASE WHEN e.redacted_utc > 0 THEN '' ELSE e.reason END AS reason, "
                              "e.board_id, e.until_utc, e.alias_n FROM log_entries e WHERE (e.target_user_id = :u OR e.subject_user_id = :u) AND e.audience = 0 ORDER BY e.id"),
    ("private_copies", "SELECT s.id, s.kind, s.created_utc, s.expires_utc, s.title, s.url, s.body FROM log_snapshots s "
                       "WHERE s.author_id = :u AND s.scrubbed_utc = 0 AND NOT s.legal_hold ORDER BY s.id"),
    ("automated_posting", "SELECT b.status, b.purpose, b.created_utc, s.url, s.status AS feed_status FROM bots b "
                          "LEFT JOIN bot_sources s ON s.bot_id = b.user_id WHERE b.user_id = :u AND b.kind = 'self'"),
)


def build(db, user_id):
    """{section: [row dicts]} for one account; sections whose table is missing are left out."""
    out = {"note": tr("Chats are end-to-end encrypted and not included: the site can't read them.")}
    for name, sql in SECTIONS:
        savepoint = db.begin_nested()
        try:
            result = db.execute(text(sql), {"u": user_id, "yes": True})
            out[name] = [dict(row._mapping) for row in result]
            savepoint.commit()
        except Exception:
            savepoint.rollback()
    return out


# What a PARENT may download of a child's account (routes/children_pages.py): exactly what the parent's activity page shows (helpers/child_activity.py), in full
# instead of the newest few, and nothing else the member's own export holds: no sign-in addresses, no messages to the site's admins, no quiet blocks or mutes, no
# drafts or shared drafts (other members' unpublished words), no email, no private circles or stories, no feed settings, **and none of the words the activity page
# leaves out**: deleted items, a comment's text on a post other members kept private, the title of such a post, a private or Circle guild's name, other members'
# reasons for reporting. It is built from the same statements as the page, so the two can't drift apart.
PARENT_ACCOUNT = "SELECT id, username, display_name, created_utc FROM users WHERE id = :u"
PARENT_LIMIT = 1000


def build_for_parent(db, user_id):
    """{section: [row dicts]} of one child, for their parent: a short account row and the sections of the parent's activity page."""
    from ruqqus.helpers import child_activity
    out = {"note": tr("Chats are end-to-end encrypted and not included: the site can't read them.")}
    wanted = [("account", lambda: [dict(row._mapping) for row in db.execute(text(PARENT_ACCOUNT), {"u": user_id})]),
              ("posts", lambda: child_activity.posts(db, user_id, PARENT_LIMIT)),
              ("comments", lambda: child_activity.comments(db, user_id, PARENT_LIMIT)),
              ("votes", lambda: child_activity.votes(db, user_id, PARENT_LIMIT)),
              ("bookmarks", lambda: child_activity.bookmarks(db, user_id, PARENT_LIMIT)),
              ("following", lambda: child_activity.follows(db, user_id, PARENT_LIMIT)),
              ("guilds", lambda: child_activity.guilds(db, user_id, PARENT_LIMIT)),
              ("private_guilds", lambda: [{"count": child_activity.private_guilds(db, user_id)}])]
    for name, read in wanted:
        savepoint = db.begin_nested()
        try:
            out[name] = read()
            savepoint.commit()
        except Exception:
            savepoint.rollback()
    return out


def as_json(data):
    return json.dumps(data, ensure_ascii=False, indent=1, default=str)
