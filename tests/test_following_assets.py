"""The Following feed is only what the viewer follows: posts by followed accounts (and posts they co-author) and posts
in joined guilds. It used to fall through to All, still labelled Following, for anyone who had joined no guild."""
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def read(*parts):
    return ROOT.joinpath(*parts).read_text(encoding="utf-8")


FRONT = read("ruqqus", "routes", "front.py")
ROUTE = FRONT.split("def following(v):")[1].split("\n@app.route(")[0]


def test_following_never_falls_through_to_all():
    assert "front_all()" not in ROUTE and "frontlist(" not in ROUTE
    assert "v.subscriptions" not in ROUTE                                     # the old "joined a guild" condition
    assert "falls through to All" not in ROUTE and "never All" in ROUTE
    assert "ids=v.idlist(" in ROUTE and "if v is None:" in ROUTE
    assert "following_feed=True" in ROUTE


def test_an_empty_following_feed_says_how_to_fill_it():
    listing = read("ruqqus", "templates", "submission_listing.html")
    empty = listing.split("{% elif following_feed %}")[1].split("{% else %}\n<div class=\"row no-gutters\">")[0]
    assert 'id="following-empty"' in empty and 'href="/browse"' in empty and "/login?redirect=/following" in empty


def test_one_off_runs_wait_for_the_background_work_their_posts_started():
    for script in ("run_bots.py", "publish_scheduled.py"):
        source = read("scripts", script)
        once = source.split('if "--once" in sys.argv:')[1].split("return")[0]
        assert "gevent.wait(timeout=120)" in once, script
