"""Fills a scratch database so the "What's happening" box and page have something to show (templates/partials/happening_box.html):
fresh posts for Trending and Hot threads, and demo events for Live and Today. Dev only. The accounts and the guilds are
the ones scripts/seed_ui_fixtures.py made, so run that first.

    PYTHONPATH=. python scripts/ui/seed_happening.py            add (or refresh) the demo rows
    PYTHONPATH=. python scripts/ui/seed_happening.py --remove   take the demo events out again
    PYTHONPATH=. python scripts/ui/seed_happening.py --admin    also make `ui_happening_admin` (level 4, password ui-Password-1)
                                                                to try /admin/happening with

It refuses any database but a scratch one (`ui_preview`, `ci_*` or `test*`), writes rows straight into the tables past every
rule, and leaves what it adds marked: events are source "demo", posts have "[demo]" nowhere (they are ordinary posts, as the
fixtures' are) but carry the phrase below so they are easy to find. Run twice it adds nothing new (the posts are found by
title, the events by key). Posts are made a few hours old so they sit inside Trending's 24 hours; the fixtures' own posts are
days old by now, which is why the box is empty on a database nobody has posted to lately.
"""
import os
import re
import sys
import time

DB_NAME = re.compile(r"^(ui_preview|ci_\w+|test\w*)$")
PHRASE = "total solar eclipse"
USERS = ("ui_alice", "ui_bob", "ui_carol", "ui_viewer")


def refuse_unless_scratch():
    name = os.environ.get("DATABASE_URL", "").rsplit("/", 1)[-1].split("?", 1)[0]
    if not DB_NAME.match(name):
        sys.exit(f"Refusing to seed database {name!r}: only ui_preview, ci_* or test* (it writes past every rule).")


def main():
    refuse_unless_scratch()
    from ruqqus.__main__ import app
    with app.test_request_context("/", environ_base={"REMOTE_ADDR": "127.0.0.1"}):
        if "--remove" in sys.argv:
            remove()
        else:
            seed()
            if "--admin" in sys.argv:
                make_admin()


def make_admin():
    from ruqqus.__main__ import db_session
    from ruqqus.classes import User
    db = db_session()
    name = "ui_happening_admin"
    if db.query(User).filter_by(username=name).first():
        print(f"{name} exists.")
        return
    now = int(time.time())
    db.add(User(age_declared=1, username=name, original_username=name, password="ui-Password-1", email=f"{name}@example.com",
                created_utc=now - 120 * 86400, creation_ip="127.0.0.1", tos_agreed_utc=now - 120 * 86400, admin_level=4))
    db.commit()
    print(f"Made {name} (admin level 4, password ui-Password-1).")


def remove():
    from sqlalchemy import text
    from ruqqus.__main__ import db_session
    db = db_session()
    ids = [r[0] for r in db.execute(text("SELECT id FROM site_events WHERE source = 'demo'"))]
    for event_id in ids:
        db.execute(text("DELETE FROM site_event_log WHERE event_id = :i"), {"i": event_id})
        db.execute(text("DELETE FROM site_events WHERE id = :i"), {"i": event_id})
    db.commit()
    print(f"Removed {len(ids)} demo events.")


def seed():
    from ruqqus.__main__ import db_session
    from ruqqus.classes import Comment, CommentAux, Submission, SubmissionAux, User, Vote
    from ruqqus.helpers import site_event_store as store
    from ruqqus.helpers import site_events as ev

    db = db_session()
    now = int(time.time())
    users = [db.query(User).filter_by(username=name).first() for name in USERS]
    if not all(users):
        sys.exit("Run scripts/seed_ui_fixtures.py first: this needs its accounts (ui_alice, ui_bob, ui_carol, ui_viewer).")

    def html(*paragraphs):
        return "".join(f"<p>{p}</p>" for p in paragraphs)

    def post(author, title, body, minutes_ago, ups=1, comments=0):
        found = db.query(SubmissionAux).filter(SubmissionAux.title == title).first()
        if found:
            return db.get(Submission, found.id)
        row = Submission(author_id=author.id, board_id=2, original_board_id=2, post_public=True,
                         created_utc=now - minutes_ago * 60, language_code="en")
        db.add(row)
        db.flush()
        db.add(SubmissionAux(id=row.id, title=title, body=body, body_html=html(body)))
        db.add(Vote(user_id=author.id, vote_type=1, submission_id=row.id))
        row.upvotes, row.downvotes = ups, 0
        for n in range(comments):
            talker = users[(n + 1) % len(users)]
            comment = Comment(author_id=talker.id, parent_submission=row.id, level=1, original_board_id=2,
                              created_utc=now - (minutes_ago - 5 - n) * 60, upvotes=1, downvotes=0, score_top=1)
            db.add(comment)
            db.flush()
            db.add(CommentAux(id=comment.id, body=f"Comment {n + 1}", body_html=html(f"Comment {n + 1}")))
        return row

    # Trending needs three different accounts on one phrase within a day (TRENDING_MIN_AUTHORS)
    for i, user in enumerate(users[:3]):
        post(user, f"Where will you watch the {PHRASE} from? ({i + 1})", f"Planning a trip for the {PHRASE}.", 30 + 20 * i, ups=3 + i)
    # a busy thread for Hot threads
    post(users[3], "What is the one thing you wish you had known before your first film roll?", "Asking for a friend.",
         90, ups=14, comments=6)
    db.commit()

    # demo events: a match under way, one about to start, a countdown, a news line, an alert (links out), a day-long event
    def rate(_text):
        return 0                                # nothing in the demo trips the word filter

    day = ev.utc_date(now).isoformat()
    demo = [
        ("demo-live-match", dict(kind="score", title="Cup final", starts=_when(now - 40 * 60), status="live",
                                 state_home="Reds", state_away="Blues", state_home_score="2", state_away_score="1",
                                 state_period="2nd half", state_clock="67'")),
        ("demo-soon-match", dict(kind="score", title="League, round 9", starts=_when(now + 90 * 60),
                                 state_home="Owls", state_away="Hawks")),
        ("demo-countdown", dict(kind="countdown", title="Launch of the new feed", starts=_when(now + 3 * 86400),
                                show_from=_when(now - 3600))),
        ("demo-news", dict(kind="news", title="Talks resume after a week-long pause", starts=_when(now - 3600),
                           link_url="https://en.wikipedia.org/wiki/Portal:Current_events", state_source="Wikipedia")),
        ("demo-alert", dict(kind="alert", title="M 6.1 earthquake near the coast", starts=_when(now - 1800),
                            expires=_when(now + 6 * 3600), link_url="https://earthquake.usgs.gov/earthquakes/map/",
                            state_source="USGS", state_severity="severe")),
        ("demo-day-event", dict(kind="event", title="Open day at the observatory", date_kind="civil", starts=day)),
    ]
    added = 0
    for key, form in demo:
        fields = ev.clean_event(dict(form, status=form.get("status", "scheduled")), now)
        added += store.upsert_source(db, "demo", key, fields, now, rate) == "inserted"
    db.commit()
    print(f"Demo posts ready; {added} new demo events. Trending refills when scripts/compute_trending.py next runs "
          f"(every 5 minutes), or run it once: PYTHONPATH=. python scripts/compute_trending.py --once")


def _when(ts):
    return time.strftime("%Y-%m-%d %H:%M", time.gmtime(ts))


if __name__ == "__main__":
    main()
