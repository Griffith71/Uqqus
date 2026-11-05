"""What a parent may see of a child's account (helpers/child_accounts.py has the rules; the owner, 2026-11-05: "activity but not chat content"). Plain `text()`
SQL, tested on SQLite (tests/test_child_accounts.py). Every statement is about ONE account, the child, and the route only ever passes the id of a child of the
signed-in parent (`routes/children_pages.py`: `child_store.child_of`), so a parent can never read another account through this.

Read: the child's own posts and comments, their votes and bookmarks (only on posts that are public: never a title another member kept to a circle or a private guild), their follows and guilds, the reports made against their account (that they were made and when, never the reason or who
made one), their recent sign-ins (the same list Settings > Security gives them: no address) and the **names** of who they chat with. **Never what a chat says**:
chats are end-to-end encrypted and the server holds no message text; nothing here reads a chat's room. An anonymous post or comment of theirs is shown to the
parent as theirs (the parent controls the account; anonymity hides an author from other users, not from the account's owner, and the child is told so), marked
as anonymous. Deleted items are left out. Every list is the newest `LIMIT` and plain text: the template escapes it."""
import html

from sqlalchemy import text

from ruqqus.helpers.base36 import base36encode

LIMIT = 50
EXCERPT = 240


# The posts the child voted on or bookmarked are other people's. The parent sees the child's own account, not what other people kept to a circle or a private
# guild: only a post that is public (`post_public`, set when it was made), live and not removed is named. The rest is left out, not hinted at.
_OTHERS_PUBLIC = ("s.post_public AND s.deleted_utc = 0 AND NOT COALESCE(s.is_banned, false) AND NOT COALESCE(s.hidden_by_guild, false) AND EXISTS "
                  "(SELECT 1 FROM boards b WHERE b.id = s.board_id AND NOT COALESCE(b.is_private, false) AND NOT COALESCE(b.is_circle, false) "
                  "AND NOT COALESCE(b.is_banned, false))")


def _plain(value):
    """Text as stored (titles are kept as escaped HTML) turned back into plain text, for a template that escapes it."""
    return html.unescape(value or "")


def _excerpt(value):
    value = " ".join(_plain(value).split())
    return value if len(value) <= EXCERPT else value[:EXCERPT].rstrip() + "…"


def posts(db, child_id, limit=LIMIT):
    rows = db.execute(text("""
        SELECT s.id, s.created_utc, a.title, s.is_anonymous, s.is_banned, s.audience
        FROM submissions s JOIN submissions_aux a ON a.id = s.id
        WHERE s.author_id = :c AND COALESCE(s.repost_id, 0) = 0 AND s.deleted_utc = 0 AND NOT s.is_space
        ORDER BY s.id DESC LIMIT :n"""), {"c": child_id, "n": limit})
    return [{"id": r[0], "b36": base36encode(r[0]), "created_utc": r[1], "title": _excerpt(r[2]), "anonymous": bool(r[3]), "removed": bool(r[4]),
             "for_circles": r[5] == 4} for r in rows]


def comments(db, child_id, limit=LIMIT):
    """The child's own comments. A comment on a post that other members kept to a circle or a private guild shows no words (they would say what that post is
    about): `text` is None and the page says it is in a private place."""
    rows = db.execute(text("""
        SELECT c.id, c.created_utc, c.parent_submission, a.body, c.is_anonymous,
               CASE WHEN """ + _OTHERS_PUBLIC + """ THEN 1 ELSE 0 END
        FROM comments c JOIN comments_aux a ON a.id = c.id LEFT JOIN submissions s ON s.id = c.parent_submission
        WHERE c.author_id = :c AND c.deleted_utc = 0 AND COALESCE(c.is_banned, false) = false
        ORDER BY c.id DESC LIMIT :n"""), {"c": child_id, "n": limit})
    return [{"id": r[0], "b36": base36encode(r[0]), "created_utc": r[1], "post_id": r[2], "text": _excerpt(r[3]) if r[5] else None, "anonymous": bool(r[4])} for r in rows]


def votes(db, child_id, limit=LIMIT):
    """The posts the child voted on and which way: [{post_id, title, up, created_utc}]."""
    rows = db.execute(text("""
        SELECT v.submission_id, a.title, v.vote_type, v.created_utc
        FROM votes v JOIN submissions s ON s.id = v.submission_id JOIN submissions_aux a ON a.id = v.submission_id
        WHERE v.user_id = :c AND v.vote_type IN (1, -1) AND """ + _OTHERS_PUBLIC + """ ORDER BY v.id DESC LIMIT :n"""), {"c": child_id, "n": limit})
    return [{"post_id": r[0], "b36": base36encode(r[0]), "title": _excerpt(r[1]), "up": r[2] == 1, "created_utc": r[3]} for r in rows]


def bookmarks(db, child_id, limit=LIMIT):
    rows = db.execute(text("""
        SELECT r.submission_id, a.title, r.created_utc
        FROM save_relationship r JOIN submissions s ON s.id = r.submission_id JOIN submissions_aux a ON a.id = r.submission_id
        WHERE r.user_id = :c AND """ + _OTHERS_PUBLIC + """ ORDER BY r.id DESC LIMIT :n"""), {"c": child_id, "n": limit})
    return [{"post_id": r[0], "b36": base36encode(r[0]), "title": _excerpt(r[1]), "created_utc": r[2]} for r in rows]


def follows(db, child_id, limit=LIMIT):
    """The accounts the child follows, newest first: [{username, created_utc}]."""
    rows = db.execute(text("""
        SELECT u.username, f.created_utc FROM follows f JOIN users u ON u.id = f.target_id
        WHERE f.user_id = :c AND NOT u.is_deleted ORDER BY f.id DESC LIMIT :n"""), {"c": child_id, "n": limit})
    return [{"username": r[0], "created_utc": r[1]} for r in rows]


def guilds(db, child_id, limit=LIMIT):
    """The open guilds the child has joined, by name. A private or Circle guild isn't named (its existence is hidden from non-members): `private_guilds` says how many."""
    rows = db.execute(text("""
        SELECT b.name, s.created_utc FROM subscriptions s JOIN boards b ON b.id = s.board_id
        WHERE s.user_id = :c AND s.is_active AND NOT COALESCE(b.is_private, false) AND NOT COALESCE(b.is_circle, false) ORDER BY s.id DESC LIMIT :n"""), {"c": child_id, "n": limit})
    return [{"name": r[0], "created_utc": r[1]} for r in rows]


def private_guilds(db, child_id):
    return db.execute(text("""
        SELECT COUNT(*) FROM subscriptions s JOIN boards b ON b.id = s.board_id
        WHERE s.user_id = :c AND s.is_active AND (COALESCE(b.is_private, false) OR COALESCE(b.is_circle, false))"""), {"c": child_id}).scalar() or 0


def reports_against(db, child_id, limit=LIMIT):
    """That other members reported their account, and when: [{created_utc, resolved}]. **Never what they wrote**: a reporter's words are written for the admins, can
    name the reporter ("I'm in his class") or the household, and nothing told the reporter that a parent would read them; and never who."""
    rows = db.execute(text("""
        SELECT created_utc, resolved_utc FROM user_reports WHERE user_id = :c ORDER BY id DESC LIMIT :n"""), {"c": child_id, "n": limit})
    return [{"created_utc": r[0], "resolved": bool(r[1])} for r in rows]


def chat_partners(db, child_id):
    """The names of who the child has a chat with and the groups they are in: ({usernames}, [group names]). Names only, never a message."""
    people = db.execute(text("""
        SELECT u.username FROM chat_conversations c
        JOIN users u ON u.id = CASE WHEN c.user_a_id = :c THEN c.user_b_id ELSE c.user_a_id END
        WHERE (c.user_a_id = :c OR c.user_b_id = :c) AND NOT u.is_deleted ORDER BY c.last_activity_utc DESC LIMIT :n"""), {"c": child_id, "n": LIMIT})
    groups = db.execute(text("""
        SELECT g.name, g.id FROM chat_group_members m JOIN chat_groups g ON g.id = m.group_id
        WHERE m.user_id = :c AND m.status = 'joined' AND g.deleted_utc = 0 ORDER BY g.last_activity_utc DESC LIMIT :n"""), {"c": child_id, "n": LIMIT})
    return [r[0] for r in people], [(r[0] or "", r[1]) for r in groups]


def everything(db, child_id):
    """All of it, for the child's page."""
    people, group_names = chat_partners(db, child_id)
    return {"posts": posts(db, child_id), "comments": comments(db, child_id), "votes": votes(db, child_id), "bookmarks": bookmarks(db, child_id),
            "follows": follows(db, child_id), "guilds": guilds(db, child_id), "private_guilds": private_guilds(db, child_id), "reports": reports_against(db, child_id),
            "chat_people": people, "chat_groups": group_names}
