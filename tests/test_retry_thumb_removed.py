"""Retry Thumbnail is gone: thumbnails are no longer a feature members manage, so neither the menu items nor
the route (and its API twin) may come back."""
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent / "ruqqus"


def _text(*parts):
    return ROOT.joinpath(*parts).read_text(encoding="utf-8")


def test_no_menu_item_or_route_offers_retrying_a_thumbnail():
    for name in ("submission_listing.html", "submission.html", "comments.html", "default.html"):
        html = _text("templates", name)
        assert "retry_thumb" not in html and "Retry Thumb" not in html, name
    posts = _text("routes", "posts.py")
    assert "retry_thumb" not in posts and "def retry_thumbnail" not in posts
    assert "/api/v2/submissions/<pid>/thumb" not in posts


def test_the_posting_path_still_makes_thumbnails():
    # only the manual retry went; the automatic scrape on submit is untouched
    assert "thumbnail_thread" in _text("routes", "posts.py")
