"""What scripts/ui/ads.spec.ts needs, in a scratch database. Dev only.

    PYTHONPATH=. python scripts/ui/seed_ads.py            make them
    PYTHONPATH=. python scripts/ui/seed_ads.py --remove   take them out again

Accounts (password ui-Password-1; high, fixed ids, see seed_chat_session.py): `ui_ads_author` writes everything; `ui_ads_adult` said 18 or over,
`ui_ads_undeclared` hasn't answered, `ui_ads_child` is a child account, `ui_ads_premium` has Premium, `ui_ads_fresh` is signed in by the spec itself.
Guilds: +ui_ads_clean (60 safe posts, for the in-feed ads and their cap), +ui_ads_mixed (30, of which the 28th newest is sensitive, so the
continuous feed brings it in on page 2) and +ui_ads_unsafe (10, one sensitive, on page 1). Profile posts: text with a comment, a rated comment, a
picture in a comment, nothing to read. It writes `.fixtures_ads.json` beside this file: the post ids and, for each signed-in account, a session
cookie made here with the site's own key, so the spec needs no sign-ins (they are limited, and one made on a site with ads on holds the ads for 15
minutes, which is its own test). It refuses any database but a scratch one (`ui_preview`, `ci_*` or `test*`). Run twice it adds nothing new.
"""
import json
import os
import re
import secrets
import sys
import time
from pathlib import Path

DB_NAME = re.compile(r"^(ui_preview|ci_\w+|test\w*)$")
PASSWORD = "ui-Password-1"
AUTHOR = ("ui_ads_author", 90730)
ACCOUNTS = (("ui_ads_adult", 90731), ("ui_ads_undeclared", 90732), ("ui_ads_child", 90733), ("ui_ads_premium", 90734), ("ui_ads_fresh", 90735))
GUILDS = (("ui_ads_clean", 60, None), ("ui_ads_mixed", 30, 27), ("ui_ads_unsafe", 10, 3))
PREFIX = "Ad fixture: "
OLD_ENOUGH = 1500000000


def refuse_unless_scratch():
    name = os.environ.get("DATABASE_URL", "").rsplit("/", 1)[-1].split("?", 1)[0]
    if not DB_NAME.match(name):
        sys.exit(f"Refusing to seed database {name!r}: only ui_preview, ci_* or test*.")


def main():
    refuse_unless_scratch()
    from ruqqus.__main__ import app
    with app.test_request_context("/", environ_base={"REMOTE_ADDR": "127.0.0.1"}):
        remove() if "--remove" in sys.argv else seed(app)


def _post(db, author, title, body_html, board_id, created_utc, **flags):
    from ruqqus.classes import Submission, SubmissionAux, Vote
    row = Submission(author_id=author.id, board_id=board_id, original_board_id=board_id, post_public=True, created_utc=created_utc,
                     language_code="en", **flags)
    db.add(row)
    db.flush()
    db.add(SubmissionAux(id=row.id, title=title, body=re.sub("<[^>]+>", "", body_html), body_html=body_html))
    db.add(Vote(user_id=author.id, vote_type=1, submission_id=row.id))
    return row


def _comment(db, author, post, html, **flags):
    from ruqqus.classes import Comment, CommentAux
    row = Comment(author_id=author.id, parent_submission=post.id, level=1, original_board_id=post.board_id, created_utc=post.created_utc + 60, **flags)
    db.add(row)
    db.flush()
    db.add(CommentAux(id=row.id, body=re.sub("<[^>]+>", "", html), body_html=html))


def seed(app):
    from sqlalchemy import text

    from ruqqus.__main__ import db_session
    from ruqqus.classes import Board, Submission, SubmissionAux, User
    from ruqqus.helpers.base36 import base36encode

    db = db_session()
    now = int(time.time())
    users = {}
    for name, user_id in (AUTHOR,) + ACCOUNTS:
        user = db.query(User).filter_by(id=user_id).first()
        if not user:
            user = User(id=user_id, username=name, original_username=name, password=PASSWORD, email=f"{name}@example.com", creation_ip="127.0.0.1",
                        tos_agreed_utc=now - 90 * 86400)
            db.add(user)
            db.flush()
        users[name] = user
    # in one statement each: the database's own rule won't let a child account off the Child filter otherwise
    for name, answer, level in (("ui_ads_author", 1, 1), ("ui_ads_adult", 1, 1), ("ui_ads_undeclared", None, 1), ("ui_ads_child", 2, 2),
                                ("ui_ads_premium", 1, 1), ("ui_ads_fresh", 1, 1)):
        db.query(User).filter_by(username=name).update({"created_utc": OLD_ENOUGH, "age_declared": answer, "age_declared_utc": 5 if answer else 0,
                                                        "filter_level": level, "hide_offensive": level == 2,
                                                        "premium_expires_utc": now + 30 * 86400 if name == "ui_ads_premium" else 0}, synchronize_session=False)
    db.flush()
    author = users["ui_ads_author"]

    for name, count, unsafe_at in GUILDS:
        board = db.query(Board).filter_by(name=name).first()
        if board:
            continue
        board = Board(name=name, description="A guild for the ads spec.", description_html="<p>A guild for the ads spec.</p>", creator_id=author.id, color="23a9f2",
                      subcat_id=0, created_utc=now - 200 * 86400)
        db.add(board)
        db.flush()
        for i in range(count):
            _post(db, author, f"{PREFIX}{name} {i + 1}", "<p>Plain words, for a card with nothing wrong with it.</p>", board.id, now - i * 600,
                  is_sensitive=(i == unsafe_at))
    fixtures = {}
    for key, body, comment in (("text", "<p>Plain words in a post.</p>", {"html": "<p>A fair point.</p>"}),
                               ("rated", "<p>Plain words in a post.</p>", {"html": "<p>A rated comment.</p>", "word_severity": 1}),
                               ("media", "<p>Plain words in a post.</p>", {"html": '<p><img src="/assets/images/emojis/marseylove.webp" alt=""></p>'}),
                               ("empty", "", None)):
        title = PREFIX + key
        found = db.query(SubmissionAux.id).filter(SubmissionAux.title == title).first()
        if found:
            fixtures[key] = base36encode(found[0])
            continue
        row = _post(db, author, title, body, 2, now)
        if comment:
            _comment(db, author, row, comment.pop("html"), **comment)
        fixtures[key] = base36encode(row.id)
    db.execute(text("SELECT 1"))
    db.commit()

    serializer = app.session_interface.get_signing_serializer(app)
    from ruqqus.helpers import matrix_client
    fixtures["cookies"], fixtures["mxids"] = {}, {}
    for name, user_id in ACCOUNTS:
        fixtures["mxids"][name] = matrix_client.mxid_for(user_id)
        nonce = db.query(User.login_nonce).filter_by(id=user_id).scalar()
        fixtures["cookies"][name] = serializer.dumps({"user_id": user_id, "session_id": secrets.token_hex(16), "login_nonce": nonce, "_permanent": True})
    Path(__file__).with_name(".fixtures_ads.json").write_text(json.dumps(fixtures))
    print("Seeded:", {k: v for k, v in fixtures.items() if k not in ("cookies", "mxids")})
    assert db.query(Submission.id).first()


def remove():
    from sqlalchemy import text

    from ruqqus.__main__ import db_session
    db = db_session()
    rows = [r[0] for r in db.execute(text("SELECT id FROM submissions_aux WHERE title LIKE :t"), {"t": PREFIX + "%"})]
    if rows:
        db.execute(text("DELETE FROM comments_aux WHERE id IN (SELECT id FROM comments WHERE parent_submission = ANY(:r))"), {"r": rows})
        db.execute(text("DELETE FROM comments WHERE parent_submission = ANY(:r)"), {"r": rows})
        db.execute(text("DELETE FROM votes WHERE submission_id = ANY(:r)"), {"r": rows})
        db.execute(text("DELETE FROM submissions_aux WHERE id = ANY(:r)"), {"r": rows})
        db.execute(text("DELETE FROM submissions WHERE id = ANY(:r)"), {"r": rows})
    db.execute(text("DELETE FROM boards WHERE name = ANY(:n)"), {"n": [g[0] for g in GUILDS]})
    db.commit()
    print("Removed", len(rows), "posts. The accounts stay: the dev Synapse remembers their ids.")


if __name__ == "__main__":
    main()
