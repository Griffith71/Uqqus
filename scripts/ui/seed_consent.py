"""Fills a scratch database with what the consent banner gates, for scripts/ui/consent.spec.ts: the fixtures have no players at all. Dev only.

    PYTHONPATH=. python scripts/ui/seed_consent.py            make them (and a fresh story, which lasts a day)
    PYTHONPATH=. python scripts/ui/seed_consent.py --remove   take them out again

`ui_alice` gets posts with players, as helpers/embed.py saves them: a YouTube video (starts as it scrolls into view), a Facebook video
(press-to-play by nature), a Spotify track and a Bandcamp album (music, 152px and the shortest box there is, 120px), a direct video file, all with
no thumbnail so that nothing but the player can reach another site, and a Vimeo video with one, which is the disclosed gap: a thumbnail loads with
no press. `ui_viewer` gets an ended call with a video replay on YouTube and a live YouTube story. No player address or picture is ever fetched: the spec answers them itself. It writes
`.fixtures_consent.json` beside this file and refuses any database but a scratch one (`ui_preview`, `ci_*` or `test*`): it writes rows straight
into the tables, past every rule. Run twice it adds nothing new but the story, when the last one has run out.
"""
import json
import os
import re
import sys
import time
from pathlib import Path

DB_NAME = re.compile(r"^(ui_preview|ci_\w+|test\w*)$")
PREFIX = "Consent fixture: "
VIDEO = "dQw4w9WgXcQ"
POSTS = (
    ("a YouTube video", "https://www.youtube.com/watch?v=" + VIDEO, "https://www.youtube-nocookie.com/embed/" + VIDEO, "iframe"),
    ("a Facebook video", "https://www.facebook.com/watch/?v=1", "https://www.facebook.com/plugins/video.php?href=https%3A%2F%2Fwww.facebook.com%2Fwatch%2F%3Fv%3D1", "iframe"),
    ("a Spotify track", "https://open.spotify.com/track/1", "https://open.spotify.com/embed/track/1", "audio"),
    ("a Bandcamp album", "https://example.bandcamp.com/album/1", "https://bandcamp.com/EmbeddedPlayer/album=1/", "audio"),
    ("a direct video file", "https://cdn.example.org/clip.mp4", "https://cdn.example.org/clip.mp4", "video"),
    ("a Vimeo video", "https://vimeo.com/1", "https://player.vimeo.com/video/1", "iframe", "https://i.vimeocdn.com/video/1_640.jpg"),
)
SPACE_TITLE = PREFIX + "a call with a video replay"


def refuse_unless_scratch():
    name = os.environ.get("DATABASE_CONNECTION_POOL_URL", os.environ.get("DATABASE_URL", "")).rsplit("/", 1)[-1].split("?", 1)[0]
    if not DB_NAME.match(name):
        sys.exit(f"Refusing to seed database {name!r}: only ui_preview, ci_* or test* (it writes past every rule).")


def main():
    refuse_unless_scratch()
    from ruqqus.__main__ import app
    with app.test_request_context("/", environ_base={"REMOTE_ADDR": "127.0.0.1"}):
        remove() if "--remove" in sys.argv else seed()


def _row(db, author, title, url, now, embed_url=None, embed_type=None, picture=None, **extra):
    from ruqqus.classes import Submission, SubmissionAux, Vote
    row = Submission(author_id=author.id, board_id=2, original_board_id=2, post_public=True, created_utc=now, language_code="en", **extra)
    db.add(row)
    db.flush()
    db.add(SubmissionAux(id=row.id, title=title, body="", body_html="", url=url, embed_url=embed_url, embed_type=embed_type, preview_image_url=picture))
    db.add(Vote(user_id=author.id, vote_type=1, submission_id=row.id))
    return row


def seed():
    from sqlalchemy import text

    from ruqqus.__main__ import db_session
    from ruqqus.classes import SubmissionAux, User
    db = db_session()
    now = int(time.time())
    alice = db.query(User).filter_by(username="ui_alice").first()
    viewer = db.query(User).filter_by(username="ui_viewer").first()
    if not alice or not viewer:
        sys.exit("Run scripts/seed_ui_fixtures.py first.")
    ids = {}
    for name, url, embed_url, kind, *picture in POSTS:
        title = PREFIX + name
        found = db.query(SubmissionAux.id).filter(SubmissionAux.title == title).first()
        ids[name.split()[1].lower()] = (found[0] if found else _row(db, alice, title, url, now, embed_url, kind, *picture).id)
    found = db.query(SubmissionAux.id).filter(SubmissionAux.title == SPACE_TITLE).first()
    space_post = found[0] if found else _row(db, viewer, SPACE_TITLE, None, now, is_space=True, comment_permission=5).id
    db.execute(text("INSERT INTO spaces (post_id, host_id, status, scheduled_utc, started_utc, ended_utc, record, video, kind, created_utc, replay_link) "
                    "VALUES (:p, :h, 'ended', :n, :n, :n, true, true, 'call', :n, :l) ON CONFLICT (post_id) DO NOTHING"),
               {"p": space_post, "h": viewer.id, "n": now, "l": "https://www.youtube.com/watch?v=" + VIDEO})
    live = db.execute(text("SELECT count(*) FROM stories WHERE user_id = :u AND kind = 'video' AND deleted_utc = 0 AND expires_utc > :n"),
                      {"u": viewer.id, "n": now + 3600}).scalar()
    if not live:
        db.execute(text("INSERT INTO stories (user_id, kind, video_ref, audience, created_utc, expires_utc) VALUES (:u, 'video', :v, 0, :n, :e)"),
                   {"u": viewer.id, "v": VIDEO, "n": now, "e": now + 86400})
    db.commit()
    host = "ui_viewer"
    from ruqqus.helpers.base36 import base36encode
    fixtures = {key: base36encode(value) for key, value in ids.items()}
    fixtures["space"] = f"/@{host}/space/{base36encode(space_post)}"
    Path(__file__).with_name(".fixtures_consent.json").write_text(json.dumps(fixtures))
    print("Seeded:", fixtures)


def remove():
    from sqlalchemy import text

    from ruqqus.__main__ import db_session
    from ruqqus.classes import User
    db = db_session()
    rows = [r[0] for r in db.execute(text("SELECT id FROM submissions_aux WHERE title LIKE :t"), {"t": PREFIX + "%"})]
    viewer = db.query(User).filter_by(username="ui_viewer").first()
    if rows:
        db.execute(text("DELETE FROM spaces WHERE post_id = ANY(:r)"), {"r": rows})
        db.execute(text("DELETE FROM votes WHERE submission_id = ANY(:r)"), {"r": rows})
        db.execute(text("DELETE FROM submissions_aux WHERE id = ANY(:r)"), {"r": rows})
        db.execute(text("DELETE FROM submissions WHERE id = ANY(:r)"), {"r": rows})
    if viewer:
        db.execute(text("DELETE FROM stories WHERE user_id = :u AND kind = 'video'"), {"u": viewer.id})
    db.commit()
    print(f"Removed {len(rows)} posts.")


if __name__ == "__main__":
    main()
