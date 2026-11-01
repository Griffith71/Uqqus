"""Fills a scratch database with the content the UI is checked against: ordinary posts and comments, and the cases
that break layouts (a 25-letter username, a 50-letter display name, a 1.2M score, a 12-deep thread, long titles, an
empty guild, a guild with a dark colour, right-to-left text). Used by the screenshot harness in scripts/ui/.

    PYTHONPATH=. python scripts/seed_ui_fixtures.py

It refuses any database but a scratch one (named `ui_preview`, `ci_*` or `test*`, as tests/pg/conftest.py does):
it writes rows straight into the tables, past every posting rule, so it must never touch a real site's data. Run it
again and it adds nothing (accounts it made are found by name). Every account's password is `ui-Password-1`.
"""
import json
import os
import re
import sys
import time

DB_NAME = re.compile(r"^(ui_preview|ci_\w+|test\w*)$")
PASSWORD = "ui-Password-1"


def database_name(url):
    return url.rsplit("/", 1)[-1].split("?", 1)[0]


def refuse_unless_scratch():
    name = database_name(os.environ.get("DATABASE_URL", ""))
    if not DB_NAME.match(name):
        sys.exit(f"Refusing to seed database {name!r}: only ui_preview, ci_* or test* (it writes past every rule).")


def detected(title, body=""):
    """A language as the routes detect it (helpers/languages.py), not a hard-coded "en": an Arabic post is Arabic.
    Without LibreTranslate's second opinion, so the seed gives the same answer whether or not it runs."""
    from ruqqus.helpers.languages import detect_language
    return detect_language(title, body, second_opinion=False)


def html(*paragraphs):
    return "".join(f"<p>{p}</p>" for p in paragraphs)


# Posts added after the first round (the owner's review, 2026-10-23): bodies long enough for the card's snippet, a
# picture with text above it, and a long title over a long body. Added by title, so a database seeded before gets them.
LONG_BODY = (
    "<p>I started shooting film three years ago and kept a notebook of everything that went wrong. Here is the "
    "short version, in the order it cost me money.</p>"
    "<ol><li><strong>Check the light seals.</strong> A cheap camera from a market stall leaked light on every frame "
    "for a month before I noticed the pattern.</li><li>Meter for the shadows on negative film, for the highlights on "
    "slide film.</li><li>Write the ISO on a strip of tape on the back of the camera.</li></ol>"
    "<pre><code>exiftool -ISO=400 -Make=Pentax scans/*.tif</code></pre>"
    "<p>The scanning workflow deserves its own post, but <a href=\"https://example.com/scanning\">this guide</a> "
    "got me most of the way there. Questions welcome.</p>"
)


def add_round_two(db, now):
    from ruqqus.classes import Submission, SubmissionAux, User, Vote

    def author(name):
        return db.query(User).filter_by(username=name).first()

    def make(user, title, body_html, url=None, image=False, hours_ago=1):
        if db.query(SubmissionAux.id).filter(SubmissionAux.title == title).first():
            return
        row = Submission(author_id=user.id, board_id=2, original_board_id=2, post_public=True,
                         created_utc=now - int(hours_ago * 3600), language_code=detected(title, body_html))
        row.is_image = image
        db.add(row)
        db.flush()
        db.add(SubmissionAux(id=row.id, title=title, body=body_html, body_html=body_html, url=url))
        db.add(Vote(user_id=user.id, vote_type=1, submission_id=row.id))

    make(author("ui_alice"), "What I wish I had known before my first year of shooting film", LONG_BODY, hours_ago=2)
    make(author("ui_carol"), "Golden hour on the pier, shot on a phone",
         "<p>No edits apart from a little crop. The light only lasted about four minutes, and I nearly missed it "
         "because I was arguing with a seagull over a chip.</p>",
         url="http://localhost/assets/images/jumbotron/docs_jumbotron_bg.jpg", image=True, hours_ago=4)
    make(author("ui_averyverylongusername"), "Long title and a long body: a stress case for the card, written the "
         "way people really write when they have a lot to say and want all of it in the title",
         LONG_BODY + LONG_BODY, hours_ago=6)
    db.commit()


def add_round_three(db, now):
    """Comments that exercise the fold (helpers/reply_fold.py): under Bob's 64-point comment, ones at 30 and 22 stay,
    ones at 21, 8 and 2 fold, and the reader's own at 1 stays; under a 2-point comment a 0 stays (the floor)."""
    from ruqqus.classes import Comment, CommentAux, User

    def author(name):
        return db.query(User).filter_by(username=name).first()

    parent = db.query(Comment).join(CommentAux, CommentAux.id == Comment.id) \
        .filter(CommentAux.body.startswith("Sekonic L-208")).first()
    if parent is None:
        return

    def add(under, user, text, ups, minutes_ago):
        if db.query(CommentAux.id).filter(CommentAux.body == text).first():
            return None
        row = Comment(author_id=user.id, parent_submission=under.parent_submission, parent_comment_id=under.id,
                      level=under.level + 1, original_board_id=under.original_board_id,
                      created_utc=now - minutes_ago * 60, upvotes=ups, downvotes=0, score_top=ups)
        db.add(row)
        db.flush()
        db.add(CommentAux(id=row.id, body=text, body_html=f"<p>{text}</p>"))
        row.language_code = detected(text)
        return row

    add(parent, author("ui_carol"), "Seconded. Mine has survived three years in a jacket pocket.", 30, 110)
    add(parent, author("ui_newcomer"), "Is the L-208 accurate enough for slide film?", 22, 105)
    add(parent, author("ui_rtl"), "I use the app on my phone and it works for me.", 21, 100)
    add(parent, author("ui_averyverylongusername"), "Gossen makes a nice one too, a bit more money.", 8, 95)
    add(parent, author("ui_carol"), "+1", 2, 90)
    add(parent, author("ui_viewer"), "Thanks, ordering one now.", 1, 85)
    low = add(parent, author("ui_bob"), "Small thing: mind the battery door, it is fiddly.", 2, 80)
    # a long comment, for "Show more": several paragraphs, so the clamp and the pinned "Show less" can be checked
    long_text = " ".join([
        "I went through three meters before settling, so here is the long version.",
        "The first was a phone app, which is fine in daylight but guesses badly in mixed light, under trees, or",
        "anywhere with a bright window in the frame. The second was a cheap reflected meter that clipped onto the",
        "camera; it was accurate but I kept forgetting to point it at something mid-grey, so my skies came out",
        "perfect and everything else was a stop under. The third, the one I kept, does incident readings with a",
        "dome, which means you meter the light falling on the subject rather than the light bouncing off it, and",
        "that single change fixed most of my exposures overnight. If you shoot negative film, err on the side of",
        "overexposure: it forgives a stop or two over far more gracefully than it forgives anything under, and the",
        "shadows are where the grain and the mud live. Slide film is the opposite and will punish you for it.",
        "Last tip: write your readings down for the first few rolls, alongside what came back from the lab.",
    ])
    add(parent, author("ui_alice"), long_text, 40, 70)
    if low is not None:
        add(low, author("ui_alice"), "Good to know.", 0, 75)
    db.commit()


FOOD_CATEGORY_ID = 18      # categories.id of Food (seed-db.sql)


def add_round_four(db):
    """A guild filed under a broad category alone (boards.cat_id, subcat_id 0), for the Categorical filter's category
    items: +ui_cooking under Food. Left alone once it has a category of any kind."""
    from ruqqus.classes import Board

    board = db.query(Board).filter_by(name="ui_cooking").first()
    if board is not None and board.cat_id is None and not board.subcat_id:
        board.cat_id = FOOD_CATEGORY_ID
        db.commit()


# the posts' scores, by the start of their titles. The site's re-score job (scripts/recomputes.py) works scores out
# from the votes table, which holds only the authors' own votes here, so each run of this script puts these back
SCORES = {
    "First roll through": (342, 12),
    "Ten-minute miso": (1280, 40),
    "Field guide to the night": (87, 3),
    "Morning fog": (2041, 18),
    "A title that keeps going": (1_204_877, 1_442),   # rank_disputed multiplies these as int4: keep the product < 2^31
    "Controversial": (410, 398),
    "Unbroken_string": (5, 9),
    "What I wish I had known": (523, 14),
    "Golden hour on the pier": (1873, 22),
    "Long title and a long body": (64, 5),
}


def restore_scores(db):
    from ruqqus.classes import Submission, SubmissionAux
    for start, (ups, downs) in SCORES.items():
        ids = [row.id for row in db.query(SubmissionAux.id).filter(SubmissionAux.title.startswith(start))]
        db.query(Submission).filter(Submission.id.in_(ids)).update(
            {Submission.upvotes: ups, Submission.downvotes: downs}, synchronize_session=False)
    db.commit()


# the posts the screenshot harness opens, found by the start of their titles (profile copies, board 2)
EXPORTED = {"thread": "First roll through", "long": "A title that keeps going", "rtl": "مرحبا"}


def export(db):
    """Writes scripts/ui/.fixtures.json: {name: post id in base 36}, for scripts/ui/pages.ts."""
    from ruqqus.classes import Submission, SubmissionAux
    ids = {}
    for name, start in EXPORTED.items():
        row = db.query(Submission).join(SubmissionAux, SubmissionAux.id == Submission.id) \
            .filter(Submission.board_id == 2, SubmissionAux.title.startswith(start)).first()
        ids[name] = row.base36id
    # and the thread's copy in +ui_photography: a post in a guild, whose sidebar is the guild's
    copy = db.query(Submission).filter(Submission.repost_id == int(ids["thread"], 36), Submission.board_id != 2).first()
    if copy is not None:
        ids["guild_post"] = copy.base36id
    out = os.path.join(os.path.dirname(os.path.abspath(__file__)), "ui", ".fixtures.json")
    with open(out, "w") as f:
        json.dump(ids, f)


def main():
    refuse_unless_scratch()
    from ruqqus.__main__ import app
    # the models read the request (the creation address) when they are made
    with app.test_request_context("/", environ_base={"REMOTE_ADDR": "127.0.0.1"}):
        seed()


def seed():
    from ruqqus.__main__ import db_session
    from ruqqus.classes import Board, Comment, CommentAux, ModRelationship, Submission, SubmissionAux, Subscription, \
        User, Vote
    from ruqqus.classes.board_relationships import ForwardRelationship

    db = db_session()
    now = int(time.time())
    hour = 3600

    def account(username, display_name="", days=120, bio=""):
        found = db.query(User).filter_by(username=username).first()
        if found:
            return found
        user = User(age_declared=1, username=username, original_username=username, password=PASSWORD,
                    email=f"{username}@example.com", created_utc=now - days * 86400, creation_ip="127.0.0.1",
                    tos_agreed_utc=now - days * 86400, display_name=display_name, bio=bio,
                    following_visibility=0, followers_visibility=0, subscribers_visibility=0,
                    subscriptions_visibility=0)
        db.add(user)
        db.flush()
        return user

    if db.query(User).filter_by(username="ui_alice").first():
        add_round_two(db, now)
        add_round_three(db, now)
        add_round_four(db)
        restore_scores(db)
        export(db)
        print("Already seeded; scores put back.")
        return

    alice = account("ui_alice", "Alice Moreno", bio="Film photographer. Mostly 35mm, sometimes medium format.")
    bob = account("ui_bob", "Bob")
    carol = account("ui_carol")
    long_name = account("ui_averyverylongusername", "Maximiliana Konstantinopoulou-Vanderbergh the 3rd")
    newcomer = account("ui_newcomer", days=2)
    viewer = account("ui_viewer", "Sam (signed-in viewer)")
    rtl = account("ui_rtl", "نور الهدى")

    def guild(name, colour, creator, description, cat_id=None):
        board = Board(name=name, description=description, description_html=html(description), creator_id=creator.id,
                      color=colour, subcat_id=0, cat_id=cat_id, created_utc=now - 200 * 86400)
        db.add(board)
        db.flush()
        db.add(ModRelationship(user_id=creator.id, board_id=board.id, accepted=True, perm_full=True,
                               perm_access=True, perm_appearance=True, perm_config=True, perm_content=True))
        db.add(Subscription(user_id=creator.id, board_id=board.id))
        return board

    photography = guild("ui_photography", "2c7be5", alice, "Film, digital and everything between. Critique welcome.")
    cooking = guild("ui_cooking", "e85d04", bob, "Recipes that work on a weeknight.", FOOD_CATEGORY_ID)
    midnight = guild("ui_midnight", "1a1a2e", carol, "A guild with a very dark colour, to check contrast.")
    guild("ui_empty", "23a9f2", carol, "Nothing has been forwarded here yet.")
    for user in (bob, carol, viewer, long_name):
        db.add(Subscription(user_id=user.id, board_id=photography.id))
    db.add(Subscription(user_id=viewer.id, board_id=cooking.id))

    def post(author, title, body=(), url=None, hours_ago=1, image=False, to=()):
        """A post on the author's profile (board 2, as every post starts) and its copies forwarded to `to`."""
        made = now - int(hours_ago * hour)
        language = detected(title, "\n\n".join(body))
        primary = Submission(author_id=author.id, board_id=2, original_board_id=2, post_public=True,
                             created_utc=made, language_code=language)
        primary.is_image = image
        db.add(primary)
        db.flush()
        db.add(SubmissionAux(id=primary.id, title=title, body="\n\n".join(body), body_html=html(*body), url=url))
        db.add(Vote(user_id=author.id, vote_type=1, submission_id=primary.id))
        for board in to:
            copy = Submission(author_id=author.id, board_id=board.id, original_board_id=board.id, post_public=True,
                              created_utc=made, repost_id=primary.id, language_code=language)
            copy.is_image = image
            db.add(copy)
            db.flush()
            db.add(SubmissionAux(id=copy.id, title=title, body="\n\n".join(body), body_html=html(*body), url=url))
            db.add(ForwardRelationship(primary_submission_id=primary.id, board_id=board.id,
                                       forward_submission_id=copy.id, forwarded_by_id=author.id))
        return primary

    def comment(post_row, author, text, parent=None, ups=1, downs=0, minutes_ago=30):
        row = Comment(author_id=author.id, parent_submission=post_row.id,
                      parent_comment_id=parent.id if parent else None, level=(parent.level + 1) if parent else 1,
                      original_board_id=post_row.board_id, created_utc=now - minutes_ago * 60, upvotes=ups,
                      downvotes=downs, score_top=ups - downs)     # what the net score mode shows
        db.add(row)
        db.flush()
        db.add(CommentAux(id=row.id, body=text, body_html=html(text)))
        row.language_code = detected(text)
        return row

    # --- ordinary content -----------------------------------------------------------------------------------
    darkroom = post(alice, "First roll through a 1970s rangefinder: what I learned about metering",
                    ["I shot a whole roll of Portra 400 without trusting the built-in meter once. Sunny 16 got me "
                     "most of the way; the shadows under the bridge did not survive.",
                     "Next roll I'm trying incident metering. Any tips for a handheld meter under $100?"],
                    hours_ago=3, to=(photography,))
    post(bob, "Ten-minute miso butter noodles",
         ["Boil noodles. Melt butter, whisk in white miso and a splash of the pasta water. Toss. Scallions.",
          "That's the whole recipe and I refuse to make it longer."],
         hours_ago=5, to=(cooking,))
    post(carol, "Field guide to the night sky in late October", url="https://example.com/night-sky-october",
         hours_ago=8, to=(midnight,))
    post(alice, "Morning fog over the reservoir", url="http://localhost/assets/images/jumbotron/docs_jumbotron_bg.jpg",
         image=True, hours_ago=12, to=(photography,))
    post(newcomer, "Hi all, new here. Is it okay to post questions about scanning negatives?",
         hours_ago=0.2)

    # --- stress cases ---------------------------------------------------------------------------------------
    post(long_name, "A title that keeps going well past where any sensible headline would stop, because people "
                    "really do write titles like this when they are excited about something and want every detail "
                    "in the feed before anyone clicks through to read the actual post itself",
         ["Short body."], hours_ago=20, to=(photography,))
    post(bob, "Controversial: pineapple belongs on pizza", hours_ago=30, to=(cooking,))
    post(rtl, "مرحبا بالجميع، هذه أول مشاركة لي في هذا الموقع",
         ["نص تجريبي باللغة العربية للتأكد من أن الاتجاه من اليمين إلى اليسار يظهر بشكل صحيح."], hours_ago=40)
    post(carol, "Unbroken_string_without_any_spaces_to_check_that_long_words_wrap_instead_of_overflowing_the_card",
         hours_ago=50)
    # languages for the Translate button and the language rows (helpers/languages.py, helpers/translation.py):
    # Spanish and Chinese translate with the LibreTranslate the stack runs; Swedish only with Google (no button
    # without it); Macedonian reads as the Bulgarian row
    post(bob, "Receta fácil: tortilla de patatas para principiantes",
         ["Pela las patatas, córtalas finas y fríelas a fuego lento con la cebolla.",
          "Bate los huevos, mezcla todo y cuaja la tortilla por los dos lados."], hours_ago=60)
    post(alice, "今天去公园拍了很多照片，天气非常好", ["谢谢大家的建议，我用的是一台旧的胶片相机。"], hours_ago=62)
    post(carol, "Hej allesammans, vilken film rekommenderar ni för vinterfoto?",
         ["Jag har provat Portra 400 men vill testa något nytt i vinter."], hours_ago=64)
    post(bob, "Добро утро на сите, како сте денеска?", ["Денес одам на планина со стариот фотоапарат."], hours_ago=66)

    # a busy thread on the darkroom post: branches, a long comment, a collapsed-looking low score
    a = comment(darkroom, bob, "Sekonic L-208 is tiny and cheap. It clips onto the cold shoe.", ups=64, minutes_ago=150)
    b = comment(darkroom, alice, "Oh nice, I hadn't thought about the cold shoe. Does it read reflected only?",
                parent=a, ups=12, minutes_ago=140)
    comment(darkroom, bob, "Reflected and incident with the dome. Good enough for negative film.", parent=b, ups=9,
            minutes_ago=130)
    comment(darkroom, carol, "Honestly, Portra has so much latitude that Sunny 16 plus one stop over is fine. "
                             "The shadows probably died in the scan, not the exposure. Try scanning flat and "
                             "pulling the shadows up before you blame the meter. I spent a year chasing metering "
                             "problems that turned out to be my scanner's auto-exposure clipping the blacks.",
            ups=118, minutes_ago=120)
    comment(darkroom, long_name, "Phone light meter apps are free and surprisingly accurate.", ups=3, downs=11,
            minutes_ago=100)

    # a 12-deep chain, past the five levels the page draws
    parent = None
    people = (alice, bob, carol, long_name)
    for depth in range(12):
        parent = comment(darkroom, people[depth % 4], f"Level {depth + 1}: and another thing about light meters.",
                         parent=parent, ups=max(1, 20 - depth), minutes_ago=90 - depth)

    db.commit()
    add_round_two(db, now)
    add_round_three(db, now)
    add_round_four(db)
    restore_scores(db)
    export(db)
    print(f"Seeded: 7 accounts, 4 guilds, posts and comments. Sign in as ui_viewer / {PASSWORD}.")


if __name__ == "__main__":
    main()
