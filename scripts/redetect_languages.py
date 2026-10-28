"""Detect the language of every live post and comment again with the current rules (helpers/languages.py
detect_language: py3langid, with the site's LibreTranslate as a second opinion when LIBRETRANSLATE_URL is set).

Run after deploying a change to the detection rules, and once after the 2026-11-26 migration, which added
comments.language_code without filling it (the Translate button shows only under text whose language is known):

    PYTHONPATH=. python scripts/redetect_languages.py --once --dry-run     # what would change, as a count table
    PYTHONPATH=. python scripts/redetect_languages.py --once [--batch 500]

Originals are detected and their code is copied onto their guild copies (a copy is the same text); a post made from a
forwarded comment is an original of its own. Pages through by id and commits each batch, so it can be stopped and
run again; running it twice changes nothing the second time. A row edited while it runs is left to the edit's own
detection (the update only applies while the text is what was read), and while LibreTranslate is down a code is kept
rather than wiped (detect_language's `keep`).
"""
import sys
from collections import Counter

from sqlalchemy import text

from ruqqus.__main__ import db_session
from ruqqus.helpers.languages import detect_language

_POSTS = text("SELECT s.id, s.language_code, a.title, a.body FROM submissions s JOIN submissions_aux a ON a.id = s.id "
              "WHERE coalesce(s.repost_id, 0) = 0 AND s.deleted_utc = 0 AND s.id > :after ORDER BY s.id LIMIT :n")
# Only while the text is still what was read: an edit made meanwhile has detected its own language, which this must not
# overwrite with the old text's (an edit always changes body; a post's copies follow their original).
_SET_POST = text("UPDATE submissions SET language_code = :code WHERE (id = :id OR repost_id = :id) "
                 "AND EXISTS (SELECT 1 FROM submissions_aux a WHERE a.id = :id "
                 "AND a.title IS NOT DISTINCT FROM :title AND a.body IS NOT DISTINCT FROM :body)")
# not a system notice (a comment with no post): nothing translates one
_COMMENTS = text("SELECT c.id, c.language_code, a.body FROM comments c JOIN comments_aux a ON a.id = c.id "
                 "WHERE c.deleted_utc = 0 AND c.parent_submission IS NOT NULL AND c.id > :after ORDER BY c.id LIMIT :n")
_SET_COMMENT = text("UPDATE comments SET language_code = :code WHERE id = :id "
                    "AND EXISTS (SELECT 1 FROM comments_aux a WHERE a.id = :id AND a.body IS NOT DISTINCT FROM :body)")


def _pass(db, select, update, detect, texts, batch, dry_run, label):
    changes, after, seen = Counter(), 0, 0
    while True:
        rows = db.execute(select, {"after": after, "n": batch}).fetchall()
        if not rows:
            break
        for row in rows:
            after = row[0]
            seen += 1
            code = detect(row)
            if code != row[1]:
                changes[(row[1], code)] += 1
                if not dry_run:
                    db.execute(update, {"code": code, "id": row[0], **texts(row)})
        if dry_run:
            db.rollback()
        else:
            db.commit()
        print(f"{label}: {seen} read, {sum(changes.values())} to change", flush=True)
    return seen, changes


def _report(label, seen, changes):
    print(f"\n{label}: {seen} read, {sum(changes.values())} changed")
    for (old, new), count in sorted(changes.items(), key=lambda kv: -kv[1]):
        print(f"  {old or '(none)':>6} -> {new or '(none)':<6} {count}")


def run(batch=500, dry_run=False):
    db = db_session()
    try:
        # keep: a code is kept while LibreTranslate can't be asked, rather than wiped by its outage
        posts = _pass(db, _POSTS, _SET_POST, lambda r: detect_language(r[2], r[3], keep=r[1]),
                      lambda r: {"title": r[2], "body": r[3]}, batch, dry_run, "posts")
        comments = _pass(db, _COMMENTS, _SET_COMMENT, lambda r: detect_language(r[2], keep=r[1]),
                         lambda r: {"body": r[2]}, batch, dry_run, "comments")
        _report("posts (and their guild copies)", *posts)
        _report("comments", *comments)
        if dry_run:
            print("\ndry run: nothing was written")
    except Exception:
        db.rollback()
        raise
    finally:
        db_session.remove()


if __name__ == "__main__":
    if "--once" not in sys.argv:
        print(__doc__)
        sys.exit(1)
    size = 500
    if "--batch" in sys.argv:
        size = max(1, int(sys.argv[sys.argv.index("--batch") + 1]))
    run(batch=size, dry_run="--dry-run" in sys.argv)
