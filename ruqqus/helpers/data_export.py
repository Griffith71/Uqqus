"""Download your data (audit I-06): one JSON file of what the site keeps about an account, built per table like
helpers/account_cleanup.py. Plain `text()` SQL, each table in its own savepoint so one a database lacks is just left
out (tests/test_data_export.py). Chats aren't in it: they are end-to-end encrypted and the site can't read them
(each member's chat page can export its own history).

Only the account's own rows and columns are read; nothing about who else voted, viewed or reported.
"""
import json

from sqlalchemy import text

SECTIONS = (
    ("account", "SELECT id, username, display_name, email, bio, profile_job, profile_location, profile_website, "
                "created_utc, coin_balance, premium_expires_utc, filter_level, signin_alerts FROM users WHERE id = :u"),
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
    ("sign_ins", "SELECT created_utc, ip, cf_country, user_agent FROM login_events WHERE user_id = :u ORDER BY created_utc DESC"),
    # the codes themselves are never exported (the site keeps only a hash); this is how many are left
    ("two_factor", "SELECT (SELECT count(*) FROM mfa_backup_codes WHERE user_id = :u AND used_utc = 0) AS unused_backup_codes, "
                   "(mfa_secret IS NOT NULL) AS two_factor_on FROM users WHERE id = :u"),
    ("linked_storage", "SELECT provider, status, created_utc FROM media_accounts WHERE user_id = :u"),
    ("automated_posting", "SELECT b.status, b.purpose, b.created_utc, s.url, s.status AS feed_status FROM bots b "
                          "LEFT JOIN bot_sources s ON s.bot_id = b.user_id WHERE b.user_id = :u AND b.kind = 'self'"),
)


def build(db, user_id):
    """{section: [row dicts]} for one account; sections whose table is missing are left out."""
    out = {"note": "Chats are end-to-end encrypted and not included: the site can't read them."}
    for name, sql in SECTIONS:
        savepoint = db.begin_nested()
        try:
            result = db.execute(text(sql), {"u": user_id, "yes": True})
            out[name] = [dict(row._mapping) for row in result]
            savepoint.commit()
        except Exception:
            savepoint.rollback()
    return out


def as_json(data):
    return json.dumps(data, ensure_ascii=False, indent=1, default=str)
